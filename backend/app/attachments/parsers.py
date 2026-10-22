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
MAX_PDF_STREAM_BYTES = 8 * 1024 * 1024
MAX_PDF_TOTAL_STREAM_BYTES = 32 * 1024 * 1024
MAX_DOCX_XML_BYTES = 8 * 1024 * 1024
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


def _check_ascii_output(data, filter_name, limit, message):
    """ASCII85's 'z' expands fourfold; pypdf's decoder has no output-size knob."""
    whitespace = b" \t\r\n\f\v\x00"
    if filter_name in {"/ASCIIHexDecode", "/AHx"}:
        digits = 0
        for char in data:
            if char == ord(">"):
                break
            if char not in whitespace:
                digits += 1
                if (digits + 1) // 2 > limit:
                    raise ParseError(message)
        return
    data = data.strip(whitespace)
    if data.startswith(b"<~"):
        data = data[2:]
    size, group = 0, 0
    for char in data:
        if char in whitespace:
            continue
        if char == ord("~"):
            break
        if char == ord("z") and not group:
            size += 4
        elif ord("!") <= char <= ord("u"):
            group += 1
            if group == 5:
                size += 4
                group = 0
        else:
            raise ParseError("PDF ASCII85 编码异常，请重新导出后上传。")
        if size > limit:
            raise ParseError(message)
    if size + max(0, group - 1) > limit:
        raise ParseError(message)


def _pdf_stream_data(member, limit, limits, message):
    from pypdf import apply_configuration
    from pypdf.filters import decode_stream_data
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NullObject, StreamObject

    # The public base method returns encoded bytes without triggering a decoder.
    data = StreamObject.get_data(member)
    filters = member.get("/Filter", ArrayObject()).get_object()
    filters = filters if isinstance(filters, ArrayObject) else [filters]
    if len(filters) > 16:
        raise ParseError("PDF 编码层数过于复杂，请重新导出后上传。")
    params = member.get("/DecodeParms")
    if params is None:
        params = [DictionaryObject() for _ in filters]
    else:
        params = params.get_object()
        params = params if isinstance(params, ArrayObject) else [params]
    for filter_name, parameters in zip(filters, params):
        filter_name = filter_name.get_object()
        if filter_name in {"/ASCII85Decode", "/A85", "/ASCIIHexDecode", "/AHx"}:
            _check_ascii_output(data, filter_name, limit, message)
        stage = DecodedStreamObject()
        stage.set_data(data)
        stage[NameObject("/Filter")] = filter_name
        stage[NameObject("/DecodeParms")] = DictionaryObject() if isinstance(parameters, NullObject) else parameters
        with apply_configuration(**{key: limit for key in limits}):
            data = decode_stream_data(stage)
        if len(data) > limit:
            raise ParseError(message)
    if len(data) > limit:
        raise ParseError(message)
    return data


def _pdf_contents_size(contents, limit, limits, message):
    from pypdf.generic import ArrayObject, NullObject, StreamObject

    if contents is None or isinstance(contents.get_object(), NullObject):
        return 0
    contents = contents.get_object()
    array = isinstance(contents, ArrayObject)
    size = 0
    # Check each member against the remaining page budget before merging an array.
    for member in contents if array else [contents]:
        member = member.get_object()
        if isinstance(member, NullObject):
            continue
        if not isinstance(member, StreamObject):
            raise ParseError("PDF 页面内容结构异常，请重新导出后上传。")
        remaining = max(1, limit - size)
        data = _pdf_stream_data(member, remaining, limits, message)
        size += len(data)
        # pypdf inserts a newline between array members when necessary.
        if array and (size == 0 or (data and not data.endswith(b"\n"))):
            size += 1
        if size > limit:
            raise ParseError(message)
    return size


def _pdf_page_size(page, limit, limits, message):
    from pypdf.generic import ContentStream

    total = _pdf_contents_size(page.get("/Contents"), limit, limits, message)
    active, visited = set(), set()
    reader = page.indirect_reference.pdf

    def forms(contents, resources, depth=0):
        nonlocal total
        if contents is None:
            return
        if depth > 40:
            raise ParseError("PDF 表单嵌套过于复杂，请拆分后上传。")
        if resources is None:
            return
        resources = resources.get_object()
        objects = resources["/XObject"].get_object() if "/XObject" in resources else {}
        # Decode was checked above; now inspect only actually invoked form objects.
        for operands, operator in ContentStream(contents, reader).operations:
            if operator != b"Do" or not operands or operands[0] not in objects:
                continue
            form = objects[operands[0]].get_object()
            if form.get("/Subtype") != "/Form":
                continue
            key = (id(form), id(resources))
            if id(form) in active:
                raise ParseError("PDF 表单包含循环引用，请重新导出后上传。")
            if key in visited:
                continue
            visited.add(key)
            if len(visited) > 5000:
                raise ParseError("PDF 表单数量过多，请拆分后上传。")
            active.add(id(form))
            total += _pdf_contents_size(form, max(1, limit - total), limits, message)
            if total > limit:
                raise ParseError(message)
            forms(form, form.get("/Resources", resources), depth + 1)
            active.remove(id(form))

    forms(page.get("/Contents"), page.get("/Resources"))
    return total


def _parse_pdf(path):
    from pypdf import PdfReader
    from pypdf.errors import LimitReachedError

    with path.open("rb") as source:
        if not source.read(1024).lstrip().startswith(b"%PDF-"):
            raise ParseError("不是有效的 PDF 文件。")
    limits = {
        "array_based_stream_maximum_output_length": MAX_PDF_STREAM_BYTES,
        "zlib_maximum_output_length": MAX_PDF_STREAM_BYTES,
        "lzw_maximum_output_length": MAX_PDF_STREAM_BYTES,
        "run_length_maximum_output_length": MAX_PDF_STREAM_BYTES,
        "jbig2_maximum_output_length": MAX_PDF_STREAM_BYTES,
        "image_maximum_buffer_size": MAX_PDF_STREAM_BYTES,
    }
    message = "PDF 页面或解压内容过于复杂，请拆分或导出为文字文档后上传。"
    reader = None
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ParseError("不支持加密 PDF，请解除密码后上传。")
        if len(reader.pages) > MAX_PAGES:
            raise ParseError("PDF 超过 40 页，请拆分后上传。")
        parts, blanks, total_bytes = [], [], 0
        for index, page in enumerate(reader.pages, 1):
            remaining = MAX_PDF_TOTAL_STREAM_BYTES - total_bytes
            limit = max(1, min(MAX_PDF_STREAM_BYTES, remaining))
            if remaining < MAX_PDF_STREAM_BYTES:
                message = "PDF 累计页面内容超过 32 MiB，请拆分后上传。"
            # Scope decode limits to validated page/form contents; preserve font handling.
            size = _pdf_page_size(page, limit, limits, message)
            total_bytes += size
            if total_bytes > MAX_PDF_TOTAL_STREAM_BYTES:
                raise ParseError("PDF 累计页面内容超过 32 MiB，请拆分后上传。")
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
    except LimitReachedError:
        raise ParseError(message) from None
    finally:
        if reader is not None:
            reader.close()


class _DocxText:
    """Stream paragraph text without a tree; bound output and pending whitespace."""
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    def __init__(self):
        self.parts = []
        self.length = 0
        self.pending = ""
        self.paragraph_depth = 0
        self.text_depth = 0

    def _append(self, text):
        if not self.length:
            text = text.lstrip()
        core = text.rstrip()
        if core:
            size = self.length + len(self.pending) + len(core)
            if size > MAX_TEXT:
                raise ParseError("DOCX 文字超过 16000 字符，请拆分后上传；本版不会截断正文。")
            self.parts.extend((self.pending, core))
            self.length = size
            self.pending = text[len(core):][:MAX_TEXT + 1]
        else:
            # Only retain a bounded suffix; future non-whitespace would exceed the limit.
            self.pending = (self.pending + text)[:MAX_TEXT + 1]

    def start(self, tag, attributes):
        if tag == self.namespace + "p":
            self.paragraph_depth += 1
            self._append("\n")
        elif tag == self.namespace + "t":
            self.text_depth += 1

    def data(self, text):
        if self.paragraph_depth and self.text_depth:
            self._append(text)

    def end(self, tag):
        if tag == self.namespace + "p":
            self.paragraph_depth -= 1
        elif tag == self.namespace + "t":
            self.text_depth -= 1

    def close(self):
        return checked_text("".join(self.parts))


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
        return _parse_pdf(path)
    if extension == ".docx":
        from defusedxml.ElementTree import XMLParser
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(e.file_size for e in entries) > 30 * 1024 * 1024:
                raise ParseError("DOCX 解压后过大或结构过于复杂。")
            if any(e.flag_bits & 1 or e.file_size > max(1, e.compress_size) * 200 for e in entries):
                raise ParseError("DOCX 已加密或压缩比例异常。")
            names = {e.filename for e in entries}
            if len(names) != len(entries):
                raise ParseError("DOCX 包含重复条目，请重新导出文档后上传。")
            if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                raise ParseError("不是有效的 DOCX 文件。")
            if any("vbaProject" in name for name in names):
                raise ParseError("不支持包含宏的文档。")
            body = archive.getinfo("word/document.xml")
            if body.file_size > MAX_DOCX_XML_BYTES:
                raise ParseError("DOCX 正文结构过大，请拆分文档。")
            parser = XMLParser(target=_DocxText(), forbid_dtd=True)
            total = 0
            with archive.open(body) as source:
                while chunk := source.read(min(16 * 1024, MAX_DOCX_XML_BYTES - total + 1)):
                    total += len(chunk)
                    if total > MAX_DOCX_XML_BYTES:
                        raise ParseError("DOCX 正文结构过大，请拆分文档。")
                    parser.feed(chunk)
                text = parser.close()
        return {"text": text, "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "warning": DOCUMENT_WARNING + " 不读取页眉、页脚、批注和嵌入对象。"}
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
