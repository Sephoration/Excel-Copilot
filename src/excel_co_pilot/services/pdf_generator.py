"""报告导出：自包含 HTML + PDF（reportlab）。

PDF 使用 reportlab 内置的 CID 字体（STSong-Light）以支持中文。
"""

from __future__ import annotations

import html
from datetime import datetime
from io import BytesIO
from pathlib import Path

from loguru import logger


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def export_html(title: str, sections: list[tuple[str, str]]) -> str:
    """生成自包含的 HTML 报告。sections: [(小节标题, 文本内容), ...]"""
    body = []
    for heading, text in sections:
        body.append(f"<h2>{html.escape(heading)}</h2>")
        body.append(f"<pre>{html.escape(text)}</pre>")
    return (
        "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:system-ui,'Microsoft YaHei',sans-serif;"
        "max-width:900px;margin:2rem auto;padding:0 1rem;color:#222}"
        "h1{border-bottom:2px solid #eee;padding-bottom:.5rem}"
        "pre{background:#f6f8fa;padding:1rem;border-radius:6px;overflow:auto;white-space:pre-wrap}"
        "</style></head><body>"
        f"<h1>{html.escape(title)}</h1><p><small>生成于 {_now()}</small></p>"
        + "".join(body)
        + "</body></html>"
    )


def export_pdf(title: str, sections: list[tuple[str, str]]) -> bytes:
    """生成 PDF 报告字节流，支持中文。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        cjk_font = "STSong-Light"
    except Exception as exc:
        logger.warning(f"中文字体注册失败，回退到默认字体: {exc}")
        cjk_font = "Helvetica"

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    title_style.fontName = cjk_font
    heading_style = styles["Heading2"]
    heading_style.fontName = cjk_font
    body_style = styles["BodyText"]
    body_style.fontName = cjk_font

    story = [
        Paragraph(html.escape(title), title_style),
        Paragraph(f"<small>生成于 {_now()}</small>", body_style),
        Spacer(1, 0.5 * cm),
    ]
    for heading, text in sections:
        story.append(Paragraph(html.escape(heading), heading_style))
        for line in text.splitlines() or [""]:
            story.append(Paragraph(html.escape(line) or "&nbsp;", body_style))
        story.append(Spacer(1, 0.4 * cm))

    doc.build(story)
    return buf.getvalue()


def save_report(
    content: bytes | str,
    path: str | Path,
) -> Path:
    """把报告内容写入磁盘。content 为 bytes 时按二进制写，否则按 UTF-8 文本写。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path
