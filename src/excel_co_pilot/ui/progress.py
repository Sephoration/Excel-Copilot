"""AI 回答进度条（HTML 自绘，Gradio 6 无内置 Bar 组件）。

用法：问答开始前 `yield progress_html("正在回答…", 20, animate=True)`，
完成时 `yield progress_html("", None)`。
animate=True 时进度条带流动光效，表示"正在工作中"而非卡死。
样式由 gradio_app.APP_CSS 提供（.ai-progress 系列）。
"""

from __future__ import annotations


def progress_html(text: str, pct: int | None, animate: bool = False) -> str:
    """渲染进度条 HTML。pct=None 时隐藏进度条。"""
    if pct is None:
        return "<div class='ai-progress' style='display:none'></div>"
    width = max(0, min(100, pct))
    cls = "ai-progress-bar active" if animate else "ai-progress-bar"
    return (
        "<div class='ai-progress'>"
        f"<div class='{cls}' style='width:{width}%'></div>"
        f"<div class='ai-progress-text'>{text}</div>"
        "</div>"
    )


PROGRESS_CSS = """
.ai-progress { margin: .4rem 0 .8rem; }
.ai-progress-bar {
    height: 6px; border-radius: 3px;
    background: linear-gradient(90deg, #4f46e5, #7c3aed);
    transition: width .45s ease;
    box-shadow: 0 0 8px rgba(79, 70, 229, .35);
}
.ai-progress-bar.active {
    background: linear-gradient(90deg, #4f46e5, #818cf8, #c7d2fe, #4f46e5);
    background-size: 300% 100%;
    animation: ai-pulse 1.1s linear infinite;
}
@keyframes ai-pulse {
    0% { background-position: 0% 0; }
    100% { background-position: 300% 0; }
}
.ai-progress-text { color: #6b7280 !important; font-size: .85rem !important; margin-top: .35rem !important; }
"""
