"""文档解析：注册表驱动，输出统一 ContentBlock 结构。

移植自 yannick_vault core/loader.py，仅调整导入路径与日志适配。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .logging import get_logger

log = get_logger("loader")

# 扫描件判定：整页可提取文本少于该字符数则视为无文本层
MIN_TEXT_LAYER_CHARS = 20


@dataclass
class ContentBlock:
    text: str
    page: int | None = None
    heading_level: int = 0  # 0=正文, 1-6=markdown/word 标题层级
    kind: str = "paragraph"  # paragraph | heading | code | table
    meta: dict = field(default_factory=dict)


def _read_text(path: Path) -> str:
    """按 utf-8 读取，失败回退 gbk（Windows 中文文档常见），再失败用 replace。"""
    data = path.read_bytes()
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _clean(text: str) -> str:
    """去空白行、合并多余空行。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    out, blank = [], 0
    for ln in lines:
        if not ln.strip():
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        out.append(ln)
    return "\n".join(out).strip()


# ---------------- 各格式解析 ----------------


def load_pdf(path: Path, cfg) -> list[ContentBlock]:
    import fitz

    blocks: list[ContentBlock] = []
    skipped_pages = 0
    with fitz.open(path) as doc:
        total_pages = doc.page_count
        for page_num, page in enumerate(doc, start=1):
            text = _clean(page.get_text("text"))
            if len(text) < MIN_TEXT_LAYER_CHARS:
                if cfg.ingest.get("ocr_enabled", False):
                    text = _ocr_page(page, page_num)
                else:
                    log.info("pdf_no_text_layer", file=str(path), page=page_num)
                    skipped_pages += 1
                    continue
            if text:
                for para in _split_paragraphs(text):
                    blocks.append(ContentBlock(text=para, page=page_num))
    if not blocks:
        raise ValueError(
            f"未解析出任何文本内容（{skipped_pages} 页为扫描件，无文本层）"
            "——请在设置里开启 OCR 或换有文字层的文档"
        )
    if skipped_pages:
        log.info(
            "pdf_pages_skipped",
            file=str(path),
            skipped=skipped_pages,
            total=total_pages,
        )
    return blocks


def _ocr_page(page, page_num: int) -> str:
    try:
        import pytesseract
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("OCR 需要安装 pytesseract 与系统 Tesseract") from e
    pix = page.get_pixmap(dpi=200)
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img, lang="chi_sim+eng")


def load_md(path: Path, cfg) -> list[ContentBlock]:
    text = _read_text(path)
    blocks: list[ContentBlock] = []
    code_fence = False
    buf: list[str] = []
    buf_type = "paragraph"

    def flush():
        nonlocal buf
        if not buf:
            return
        joined = _clean("\n".join(buf))
        if joined:
            blocks.append(ContentBlock(text=joined, kind=buf_type))
        buf = []

    for line in text.split("\n"):
        if line.strip().startswith("```"):
            flush()
            if code_fence:
                code_fence = False
                buf_type = "paragraph"
            else:
                code_fence = True
                buf_type = "code"
            continue
        if code_fence:
            buf.append(line)
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush()
            level = len(m.group(1))
            blocks.append(
                ContentBlock(
                    text=m.group(2).strip(), heading_level=level, kind="heading"
                )
            )
            buf_type = "paragraph"
            continue
        if not line.strip():
            flush()
            continue
        buf.append(line)
    flush()
    return blocks


def load_txt(path: Path, cfg) -> list[ContentBlock]:
    text = _read_text(path)
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    return [ContentBlock(text=_clean(p)) for p in paras]


def load_docx(path: Path, cfg) -> list[ContentBlock]:
    import docx
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = docx.Document(path)
    blocks: list[ContentBlock] = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            para = Paragraph(child, doc)
            t = _clean(para.text)
            if not t:
                continue
            style = (para.style.name or "").lower()
            m = re.match(r"(?:heading|标题)\s*(\d)", style)
            if m:
                blocks.append(
                    ContentBlock(text=t, heading_level=int(m.group(1)), kind="heading")
                )
            else:
                blocks.append(ContentBlock(text=t))
        elif child.tag == qn("w:tbl"):
            table = Table(child, doc)
            rows = []
            for row in table.rows:
                cells = [_clean(c.text) for c in row.cells]
                rows.append(" | ".join(cells))
            if rows:
                blocks.append(ContentBlock(text="\n".join(rows), kind="table"))
    return blocks


def _collect_shape_text(shape, lines: list[str]) -> None:
    """递归提取 shape 文本（含 GroupShape 组内文本框）。"""
    try:
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                t = _clean("".join(run.text for run in para.runs) or para.text or "")
                if t:
                    lines.append(t)
    except Exception:  # 部分 shape 类型无 text_frame 属性
        pass
    for child in getattr(shape, "shapes", []):
        _collect_shape_text(child, lines)


def load_pptx(path: Path, cfg) -> list[ContentBlock]:
    from pptx import Presentation

    prs = Presentation(path)
    blocks: list[ContentBlock] = []
    for slide_idx, slide in enumerate(prs.slides, start=1):
        lines: list[str] = []
        for shape in slide.shapes:
            _collect_shape_text(shape, lines)
        if lines:
            blocks.append(ContentBlock(text="\n".join(lines), page=slide_idx))
    return blocks


def load_xlsx(path: Path, cfg) -> list[ContentBlock]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    blocks: list[ContentBlock] = []
    for sheet in wb.worksheets:
        rows: list[str] = []
        for row in sheet.iter_rows(values_only=True):
            cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
            if cells:
                rows.append(" | ".join(cells))
            if len(rows) >= 60:
                blocks.append(
                    ContentBlock(
                        text="\n".join(rows),
                        kind="table",
                        meta={"sheet": sheet.title},
                    )
                )
                rows = []
        if rows:
            blocks.append(
                ContentBlock(
                    text="\n".join(rows), kind="table", meta={"sheet": sheet.title}
                )
            )
    wb.close()
    return blocks


def load_csv(path: Path, cfg) -> list[ContentBlock]:
    """CSV → 表格文本块（每 60 行一块，每块带表头，便于检索后理解列含义）。"""
    import pandas as pd

    from ..utils.encoding import detect_encoding

    df = pd.read_csv(path, encoding=detect_encoding(path))
    header = " | ".join(str(c) for c in df.columns)
    blocks: list[ContentBlock] = []
    rows: list[str] = []
    for _, row in df.iterrows():
        rows.append(" | ".join("" if pd.isna(v) else str(v) for v in row))
        if len(rows) >= 60:
            blocks.append(
                ContentBlock(text=f"{header}\n" + "\n".join(rows), kind="table")
            )
            rows = []
    if rows:
        blocks.append(ContentBlock(text=f"{header}\n" + "\n".join(rows), kind="table"))
    return blocks


# ---------------- 注册表 ----------------


def _split_paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


LOADERS: dict[str, object] = {
    ".pdf": load_pdf,
    ".md": load_md,
    ".markdown": load_md,
    ".txt": load_txt,
    ".docx": load_docx,
    ".pptx": load_pptx,
    ".xlsx": load_xlsx,
    ".csv": load_csv,
}


def supported_exts() -> list[str]:
    return list(LOADERS.keys())


def load_document(path: Path, cfg) -> list[ContentBlock]:
    ext = path.suffix.lower()
    loader = LOADERS.get(ext)
    if loader is None:
        raise ValueError(f"不支持的文件类型: {ext}")
    blocks = loader(path, cfg)
    if not blocks:
        raise ValueError("未解析出任何文本内容（可能是纯扫描件，需开启 OCR）")
    # 压缩连续标题间的空档、合并过短正文块到上一块（交给 splitter 处理细节）
    return blocks
