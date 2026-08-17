"""Gradio 主界面：首页模式选择 → 单次问答 / 知识库问答（Tabs 导航）。

- 🏠 首页：产品 Hero + 模型配置 + 两个模式卡片，点击卡片切换到对应工作区
- 📊 单次问答：把文件作为上下文发送给 AI（发送几个文件，上下文就加几个），然后简单问答
- 📚 知识库：RAG 数据库 —— 批量导入文档，提问先检索再交给大模型（kb_tab.py）

用 gr.Tabs 而非 visible 切换；单次问答为单列流式布局，避免长列与闪烁。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import gradio as gr

from ..config import settings
from ..core.session import Session
from .kb_tab import build_kb_tab
from .progress import PROGRESS_CSS, progress_html

_CSS = """
/* ---------- 全局 ---------- */
.gradio-container { max-width: 880px !important; margin: 0 auto !important; }
body { background: linear-gradient(160deg, #0f172a 0%, #1e293b 100%) fixed !important; }
footer { display: none !important; }
.gradio-container .tabs { border: none !important; background: transparent !important; }

/* ---------- 首页 ---------- */
.hero-box { text-align: center; padding: 3.2rem 1rem 1.6rem; }
.hero-title {
    font-size: 2.9rem !important; font-weight: 800 !important; letter-spacing: 1px;
    background: linear-gradient(90deg, #60a5fa, #a78bfa, #f472b6);
    -webkit-background-clip: text; background-clip: text; color: transparent;
    line-height: 1.3 !important;
}
.hero-sub { color: #94a3b8 !important; font-size: 1.05rem !important; margin-top: .8rem !important; }
.hero-badge { text-align: center; margin: .6rem 0 1.2rem; }

.mode-grid { display: grid !important; grid-template-columns: 1fr 1fr; gap: 1.4rem; padding: 1rem 0 2rem; }
@media (max-width: 800px) { .mode-grid { grid-template-columns: 1fr; } }

.mode-card {
    border-radius: 18px !important; padding: 1.6rem 1.4rem !important;
    background: rgba(30, 41, 59, .92) !important; border: 1px solid rgba(148, 163, 184, .18) !important;
    box-shadow: 0 10px 30px rgba(0, 0, 0, .35) !important;
    cursor: pointer; text-align: left;
    transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
    min-height: 195px;
}
.mode-card:hover { transform: translateY(-5px); box-shadow: 0 18px 42px rgba(0,0,0,.45) !important; }
.mode-card .mode-emoji { font-size: 2.2rem; display: block; margin-bottom: .6rem; }
.mode-card .mode-name { font-size: 1.35rem !important; font-weight: 700 !important; color: #e2e8f0 !important; }
.mode-card .mode-desc { color: #94a3b8 !important; font-size: .92rem !important; margin-top: .4rem !important; }
.mode-card .mode-tag { display: inline-block; margin-top: .8rem; font-size: .78rem; color: #60a5fa; }
#analysis_card { border-left: 4px solid #60a5fa; }
#kb_card { border-left: 4px solid #a78bfa; }

/* 工作区左右两栏（用 CSS grid 强制并排，避免 Gradio Row 换行） */
.two-pane { display: grid !important; grid-template-columns: minmax(280px, 1fr) 2.4fr; gap: 1.2rem; align-items: start !important; }
@media (max-width: 900px) { .two-pane { grid-template-columns: 1fr; } }

/* ---------- 工作区标题 ---------- */
.section-head { padding: .8rem 0 .4rem; }
.section-title { font-size: 1.5rem !important; font-weight: 700 !important; color: #e2e8f0 !important; }
.section-sub { color: #94a3b8 !important; font-size: .95rem !important; }

/* 组件配色（深色面板下保持可读） */
.gr-box, .gr-panel { background: rgba(30, 41, 59, .85) !important; border-color: rgba(148,163,184,.15) !important; border-radius: 12px !important; }
label, .gr-text, .gr-markdown { color: #e2e8f0; }
.gr-button-primary { background: linear-gradient(90deg, #3b82f6, #8b5cf6) !important; border: none !important; }
""" + PROGRESS_CSS


def _status_badge(session: Session) -> str:
    if session.llm_ready:
        return f"✅ LLM 已就绪（{session.llm_model_name}）—— 单次问答 / 知识库问答均可用"
    return "⚠️ 未配置 LLM —— 请先在下方向「模型配置」填写 API Key"


def _home_view(session: Session, tabs: gr.Tabs) -> gr.Markdown:
    """首页 Tab：Hero + 模型配置 + 模式卡片（点击卡片切到对应 Tab）。"""
    with gr.Column(elem_classes="hero-box"):
        gr.Markdown(
            "# Excel Co-Pilot\n"
            "<span class='hero-sub'>本地优先 · AI 数据工作台 —— 数据不出域，全链路本地/自主可控</span>",
            elem_classes="hero-title",
        )
        badge = gr.Markdown(_status_badge(session), elem_classes="hero-badge")

        with gr.Accordion("⚙️ 模型配置（DeepSeek 或任意 OpenAI 兼容端点）", open=False):
            with gr.Row():
                api_key = gr.Textbox(
                    label="DEEPSEEK_API_KEY",
                    type="password",
                    value=settings.deepseek_api_key or "",
                    scale=2,
                )
                base_url = gr.Textbox(
                    label="Base URL", value=settings.llm_base_url, scale=2
                )
                model = gr.Textbox(label="模型", value=settings.llm_model, scale=1)
            with gr.Row():
                persist = gr.Checkbox(label="保存到 .env（重启后仍生效）", value=False)
                apply_btn = gr.Button("应用并连接", variant="primary")
            cfg_status = gr.Markdown("")

        gr.Markdown("### 选择工作模式")
        with gr.Row(elem_classes="mode-grid"):
            with gr.Column(elem_id="analysis_card", elem_classes="mode-card", scale=1):
                gr.Markdown(
                    "<span class='mode-emoji'>📊</span>"
                    "<span class='mode-name'>单次问答</span>"
                    "<div class='mode-desc'>把文件直接发送给 AI 作为上下文，"
                    "发送几个文件，上下文就加几个，然后围绕它们简单问答。</div>"
                    "<span class='mode-tag'>文件进上下文 · 即时问答</span>"
                )
                to_analysis = gr.Button("进入单次问答", variant="primary")
            with gr.Column(elem_id="kb_card", elem_classes="mode-card", scale=1):
                gr.Markdown(
                    "<span class='mode-emoji'>📚</span>"
                    "<span class='mode-name'>知识库问答</span>"
                    "<div class='mode-desc'>把文档批量导入 RAG 数据库，提问先检索"
                    "再交给大模型，长期可用，回答带引用溯源。</div>"
                    "<span class='mode-tag'>批量文档 · 入库后长期问答</span>"
                )
                to_kb = gr.Button("进入知识库", variant="primary")

    def _apply_llm(api_key: str, base_url: str, model: str, persist: bool):
        try:
            session.configure_llm(
                api_key=api_key, base_url=base_url, model=model, persist=persist
            )
        except Exception as exc:
            return f"❌ 配置失败：{exc}", _status_badge(session)
        note = "，已保存到 .env" if persist else ""
        return (
            f"✅ 已连接 {session.llm_model_name} @ {session.llm_base_url}{note}",
            _status_badge(session),
        )

    apply_btn.click(
        _apply_llm,
        [api_key, base_url, model, persist],
        [cfg_status, badge],
    )

    def _goto(tab_id: str):
        return gr.update(selected=tab_id)

    to_analysis.click(lambda: _goto("analysis"), None, tabs)
    to_kb.click(lambda: _goto("kb"), None, tabs)
    return badge


def _analysis_workspace(state: gr.State) -> None:
    """单次问答：左窗发送文件，右窗简单问答 + 回答进度条。"""
    with gr.Column():
        gr.Markdown(
            "<span class='section-title'>📊 单次问答</span>"
            "<span class='section-sub' style='display:block'>把文件发送给 AI 作为上下文，然后直接提问</span>",
            elem_classes="section-head",
        )

        with gr.Column(elem_classes="two-pane"):
            # 左：发送文件（小窗）
            with gr.Column(scale=1, min_width=280):
                gr.Markdown("### 📤 发送文件")
                upload = gr.File(
                    label="支持 CSV / Excel / PDF / Word / MD / TXT / PPT",
                    file_count="multiple",
                    file_types=[
                        ".csv",
                        ".xlsx",
                        ".xls",
                        ".pdf",
                        ".docx",
                        ".pptx",
                        ".md",
                        ".markdown",
                        ".txt",
                    ],
                    type="filepath",
                )
                with gr.Row():
                    send_btn = gr.Button("📥 发送给 AI", variant="primary", scale=2)
                    clear_btn = gr.Button("🗑 清空", scale=1)
                context_md = gr.Markdown("（尚未发送任何文件）")

            # 右：简单问答（大窗）
            with gr.Column(scale=3):
                gr.Markdown("### 💬 简单问答")
                chatbot = gr.Chatbot(height=460, label="对话")
                ai_progress = gr.HTML(progress_html("", None))
                with gr.Row():
                    msg = gr.Textbox(
                        placeholder="例如：这些文件里，3 月份的总销售额是多少？",
                        scale=4,
                        show_label=False,
                    )
                    ask_btn = gr.Button("发送", variant="primary", scale=1)
                chat_history = gr.State([])  # [(user, assistant), ...]

        # ---- 事件 ----

        def _context_md(results: list[dict]) -> str:
            if not results:
                return "（尚未发送任何文件）"
            lines = ["✅ 已发送到上下文："]
            for r in results:
                lines.append(f"- **{r['name']}**（{r['chars']} 字符）")
            return "\n".join(lines)

        def on_send(files: Any, session: Session):
            if not files:
                return "（请先选择文件）", _context_md(session.context)
            if isinstance(files, str):
                files = [files]
            try:
                results = session.attach_files([Path(f) for f in files])
            except Exception as exc:
                return f"❌ 发送失败：{exc}", _context_md(session.context)
            names = "、".join(r["name"] for r in results)
            return (
                f"📥 已发送 {len(results)} 个文件（{names}），现在可以提问了。",
                _context_md(session.context),
            )

        def on_clear(session: Session):
            session.clear_context()
            return "🗑 上下文已清空", "（尚未发送任何文件）"

        def on_ask(question: str, history: list, session: Session):
            if not question or not question.strip():
                yield history, progress_html("", None), ""
                return
            history = history or []
            llm_history = []
            for u, a in history:
                llm_history.append({"role": "user", "content": u})
                llm_history.append({"role": "assistant", "content": a})
            yield history, progress_html("⏳ 已收到问题，正在整理上下文…", 20), ""
            yield history, progress_html("⏳ AI 正在回答…", 60), ""
            try:
                reply = session.simple_chat(question.strip(), history=llm_history)
            except Exception as exc:
                reply = f"❌ 出错了：{exc}"
            new_history = history + [(question.strip(), reply)]
            yield new_history, progress_html("✅ 回答完成", 100), ""

        send_btn.click(on_send, [upload, state], [context_md, context_md])
        clear_btn.click(on_clear, [state], [context_md, context_md])
        ask_btn.click(
            on_ask, [msg, chat_history, state], [chatbot, ai_progress, msg]
        )
        msg.submit(
            on_ask, [msg, chat_history, state], [chatbot, ai_progress, msg]
        )


def _kb_workspace() -> None:
    """知识库工作区：RAG 数据库（多库管理 + 导入 + 引用溯源问答）。"""
    build_kb_tab()


def build_ui() -> gr.Blocks:
    """构建主界面：首页模式选择 + 两个工作区（Tabs 导航）。

    css / theme 在 Gradio 6 中属于 launch 参数，由 main.py 启动时传入
    （APP_CSS / APP_THEME）。
    """
    with gr.Blocks(title="Excel Co-Pilot · 本地 AI 数据工作台") as demo:
        session = Session()
        state = gr.State(session)

        with gr.Tabs() as tabs:
            with gr.Tab("🏠 首页", id="home"):
                _home_view(session, tabs)
            with gr.Tab("📊 单次问答", id="analysis"):
                _analysis_workspace(state)
            with gr.Tab("📚 知识库", id="kb"):
                _kb_workspace()

    return demo


# Gradio 6：css / theme 需在 launch() 时传入（见 main.py）
APP_CSS = _CSS
APP_THEME = gr.themes.Soft(primary_hue="blue", neutral_hue="slate")


__all__ = ["build_ui", "APP_CSS", "APP_THEME"]