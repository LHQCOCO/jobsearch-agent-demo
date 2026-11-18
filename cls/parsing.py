"""简历文件解析：MD / TXT / DOCX / PDF。全部在本地内存中完成，不写临时文件。

展示版只保留求职场景需要的能力：不做 OCR，扫描件 PDF 会直接报错提示。
"""
from __future__ import annotations

import io
import re
from pathlib import Path

MAX_BYTES = 25 * 1024 * 1024
MAX_CHARS = 120_000
SUPPORTED = {".md", ".txt", ".pdf", ".docx"}


class ParseError(ValueError):
    pass


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _pdf(data: bytes) -> tuple[str, int, list[str]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ParseError("缺少 pypdf 依赖，无法解析 PDF") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ParseError("该 PDF 已加密，请另存为不加密的 PDF 后再上传。")
        pages = len(reader.pages)
        chunks = [page.extract_text() or "" for page in reader.pages]
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError("无法按标准 PDF 解析，请用阅读器另存为 PDF 后重试。") from exc
    text = normalize("\n".join(chunks))
    warnings = []
    if len(text) < 80:
        warnings.append("PDF 中可提取的文字过少，可能是扫描件；展示版不含 OCR，请改用可复制文字的版本。")
    return text, pages, warnings


def _docx(data: bytes) -> tuple[str, int, list[str]]:
    try:
        import docx
    except ImportError as exc:
        raise ParseError("缺少 python-docx 依赖，无法解析 DOCX") from exc
    try:
        d = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise ParseError("无法按标准 DOCX 解析，请用 Word / WPS 另存为 .docx 后重试。") from exc
    lines = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            line = " | ".join(c for c in cells if c)
            if line:
                lines.append(line)
    return normalize("\n".join(lines)), 0, []


def _txt(data: bytes) -> tuple[str, int, list[str]]:
    for enc in ("utf-8", "gb18030", "utf-16", "latin-1"):
        try:
            return normalize(data.decode(enc)), 0, []
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ParseError("无法识别 TXT 文件编码，请另存为 UTF-8 后重试。")


def parse(filename: str, data: bytes) -> dict:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise ParseError(f"不支持的格式 {ext or '（无扩展名）'}，请上传 MD / TXT / PDF / DOCX。")
    if not data:
        raise ParseError("文件为空。")
    if len(data) > MAX_BYTES:
        raise ParseError(f"文件超过 {MAX_BYTES // 1024 // 1024} MB 限制。")

    if ext == ".pdf":
        text, pages, warnings = _pdf(data)
    elif ext == ".docx":
        text, pages, warnings = _docx(data)
    else:
        text, pages, warnings = _txt(data)

    if not text:
        raise ParseError("未能从文件中提取到文字，请改用粘贴。")
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
        warnings.append(f"文本已截断至 {MAX_CHARS} 字。")

    return {
        "filename": filename,
        "ext": ext.lstrip("."),
        "text": text,
        "chars": len(text),
        "pages": pages,
        "warnings": warnings,
        "size": len(data),
    }
