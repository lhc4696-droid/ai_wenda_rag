"""受限解析子进程。只接收内部文件路径，不导入应用配置或读取 API 密钥。"""
import json
import logging
from pathlib import Path
import sys
import warnings
import zipfile

MAX_TEXT = 16_000
MAX_PAGES = 40
MAX_PIXELS = 16_000_000
DOCUMENT_WARNING = "仅读取正文文字，不分析文档内图片、图表、公式或完整排版。"


class ParseError(Exception):
    pass


def checked_text(text):
    text = text.strip()
    if not text:
        raise ParseError("未提取到可读文字；扫描件需要先进行 OCR，本版暂不支持。")
    if len(text) > MAX_TEXT:
        raise ParseError("文档文字超过 16000 字符，请拆分后上传；本版不会只读取前半部分冒充全文。")
    if any(ord(c) < 32 and c not in "\n\r\t" for c in text):
        raise ParseError("文件含有不支持的控制字符，可能不是有效文本。")
    return text


def parse(path: Path, extension: str):
    if extension in (".jpg", ".jpeg", ".png", ".webp"):
        from PIL import Image, ImageOps
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as picture:
            expected = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}[extension]
            if picture.format != expected or picture.width * picture.height > MAX_PIXELS:
                raise ParseError("图片实际格式与扩展名不符，或像素超过 1600 万。")
            if getattr(picture, "n_frames", 1) != 1:
                raise ParseError("暂不支持动图，请上传静态图片。")
            picture.verify()
        with Image.open(path) as picture:
            picture = ImageOps.exif_transpose(picture)
            picture.thumbnail((2048, 2048))
            rgba = picture.convert("RGBA")
            background = Image.new("RGB", rgba.size, "white")
            background.paste(rgba, mask=rgba.getchannel("A"))
            # 重编码只保存像素，去除 EXIF/GPS 等元数据；模型和预览只接触此副本。
            background.save(path.with_suffix(".jpg"), format="JPEG", quality=85)
        return {"media_type": "image/jpeg", "warning": "图片已去除元数据并压缩，最长边不超过 2048 像素。"}
    if extension in (".txt", ".md"):
        raw = path.read_bytes()
        try:
            if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
                text = raw.decode("utf-16")
            else:
                try:
                    text = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = raw.decode("gb18030")
        except UnicodeDecodeError:
            raise ParseError("文本编码无法识别，请另存为 UTF-8 后重试。") from None
        return {"text": checked_text(text), "media_type": "text/plain", "warning": ""}
    if extension == ".pdf":
        from pypdf import PdfReader
        if not path.read_bytes()[:1024].lstrip().startswith(b"%PDF-"):
            raise ParseError("不是有效的 PDF 文件。")
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ParseError("不支持加密 PDF，请解除密码后上传。")
        if len(reader.pages) > MAX_PAGES:
            raise ParseError("PDF 超过 40 页，请拆分后上传。")
        parts, blanks = [], []
        for index, page in enumerate(reader.pages, 1):
            stream = page.get_contents()
            if stream is not None and len(stream.get_data()) > 8 * 1024 * 1024:
                raise ParseError("PDF 页面过于复杂，请导出为文字文档后上传。")
            content = (page.extract_text() or "").strip()
            if not content:
                blanks.append(index)
            parts.append(f"[第 {index} 页]\n{content or '（本页没有可提取文字，未进行图片识别）'}")
            if sum(map(len, parts)) > MAX_TEXT:
                raise ParseError("PDF 文字超过 16000 字符，请拆分后上传。")
        if len(blanks) == len(reader.pages):
            raise ParseError("PDF 没有可提取文字，可能是扫描件；本版暂不支持 OCR。")
        extra = " 无文字页：" + "、".join(map(str, blanks)) if blanks else ""
        return {"text": checked_text("\n\n".join(parts)), "media_type": "application/pdf", "warning": DOCUMENT_WARNING + extra}
    if extension == ".docx":
        from defusedxml.ElementTree import fromstring
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(e.file_size for e in entries) > 30 * 1024 * 1024:
                raise ParseError("DOCX 解压后过大或结构过于复杂。")
            if any(e.flag_bits & 1 or e.file_size > max(1, e.compress_size) * 200 for e in entries):
                raise ParseError("DOCX 已加密或压缩比例异常。")
            names = {e.filename for e in entries}
            if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                raise ParseError("不是有效的 DOCX 文件。")
            if any("vbaProject" in name for name in names):
                raise ParseError("不支持包含宏的文档。")
            xml = archive.read("word/document.xml")
            if len(xml) > 8 * 1024 * 1024:
                raise ParseError("DOCX 正文结构过大，请拆分文档。")
            root = fromstring(xml, forbid_dtd=True)
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            lines = []
            for paragraph in root.iter(ns + "p"):
                lines.append("".join(element.text or "" for element in paragraph.iter(ns + "t")))
        return {"text": checked_text("\n".join(lines)), "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "warning": DOCUMENT_WARNING + " 不读取页眉、页脚、批注和嵌入对象。"}
    raise ParseError("文件类型不支持。")


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        result = parse(Path(sys.argv[1]), sys.argv[2])
    except ParseError as error:
        result = {"error": str(error)}
    except ImportError:
        result = {"error": "后端附件依赖未安装，请安装 requirements.txt 后重试。"}
    except Exception:
        result = {"error": "文件损坏、结构异常或不受支持，请重新导出后上传。"}
    print(json.dumps(result, ensure_ascii=True))
