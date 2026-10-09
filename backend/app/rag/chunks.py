"""优先在页、标题和段落处分块；长段落才按句子附近切开。"""

import re


PAGE = re.compile(r"^\[第\s*(\d+)\s*页\]$")
HEADING = re.compile(r"^(?:#{1,6}\s+.+|第[一二三四五六七八九十\d]+[章节][^。！？.!?]{0,30}|[^。！？.!?]{1,30}[：:])$")


def split_text(text: str, size: int = 850, overlap: int = 100) -> list[dict]:
    if not 0 <= overlap < size:
        raise ValueError("重叠长度必须小于片段长度")
    result, buffer, page, heading = [], "", None, ""

    def flush():
        nonlocal buffer
        if buffer.strip():
            result.append({"text": buffer.strip(), "page": page, "heading": heading})
        buffer = ""

    def append_block(block):
        nonlocal buffer
        prefix = f"{heading}\n" if heading and not block.startswith(heading) else ""
        remaining = block.strip()
        while remaining:
            capacity = size - len(buffer) - (1 if buffer else 0)
            if capacity <= 0:
                flush()
                capacity = size
            if len(remaining) <= capacity:
                buffer += ("\n" if buffer else prefix) + remaining
                break
            if buffer:
                flush()
                continue
            # 在句末附近断开；找不到则保证每段仍不超过上限。
            cut = size - len(prefix)
            matches = list(re.finditer(r"[。！？；.!?;]\s*", remaining[:cut]))
            if matches and matches[-1].end() >= cut // 2:
                cut = matches[-1].end()
            piece = remaining[:cut].strip()
            if not piece:
                break
            result.append({"text": prefix + piece, "page": page, "heading": heading})
            remaining = remaining[max(0, cut - overlap):].lstrip()

    blocks = re.split(r"\n+", text.strip())
    for raw in blocks:
        block = raw.strip()
        if not block:
            continue
        page_match = PAGE.fullmatch(block)
        if page_match:
            flush()
            page, heading = int(page_match.group(1)), ""
            continue
        if block == "（本页没有可提取文字，未进行图片识别）":
            continue
        if HEADING.match(block):
            flush()
            heading = block[:100]
            continue
        append_block(block)
    flush()
    return result
