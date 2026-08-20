"""Gradio 主界面：首页模式选择 → 单次问答 / 知识库问答（Tabs 导航）。

- 🏠 首页：产品 Hero + 模型配置 + 两个模式卡片，点击卡片切换到对应工作区
- 📊 单次问答：把文件作为上下文发送给 AI（发送几个文件，上下文就加几个），然后简单问答
- 📚 知识库：RAG 数据库 —— 批量导入文档，提问先检索再交给大模型（kb_tab.py）

用 gr.Tabs 而非 visible 切换；单次问答为单列流式布局，避免长列与闪烁。
"""

from __future__ import annotations

import gradio as gr

from ..config import settings
from ..core.session import Session
from .analysis_tab import _analysis_workspace
from .kb_tab import build_kb_tab
from .progress import PROGRESS_CSS

_CSS = (
    """
/* ================= 全局（浅色 Linear/Notion 风格） ================= */
.gradio-container { max-width: 1100px !important; margin: 0 auto !important; }
html { scrollbar-gutter: stable; }
.tabs { min-height: 620px !important; }
body { background: #f7f8fa !important; }
footer { display: none !important; }
.gradio-container .tabs { border: none !important; background: transparent !important; }

/* ================= 首页 ================= */
.hero-box { text-align: center; padding: 3rem 1rem 1.2rem; }
.hero-title {
    font-size: 2.7rem !important; font-weight: 800 !important;
    letter-spacing: -.5px; color: #1a1d21 !important; line-height: 1.25 !important;
}
.hero-sub { color: #6b7280 !important; font-size: 1.05rem !important; margin-top: .8rem !important; }
.hero-badge { text-align: center; margin: .9rem 0 1rem; }
.badge-pill {
    display: inline-block; border-radius: 999px; padding: .3rem .9rem;
    font-size: .82rem !important; font-weight: 600 !important;
}
.badge-pill.ok { background: #ecfdf5 !important; color: #047857 !important; border: 1px solid #a7f3d0 !important; }
.badge-pill.warn { background: #fffbeb !important; color: #b45309 !important; border: 1px solid #fde68a !important; }
.hero-feats {
    display: flex !important; gap: .6rem !important; justify-content: center !important;
    flex-wrap: wrap !important; margin: .2rem 0 1.8rem !important;
}
.hero-feats span {
    background: #fff !important; border: 1px solid #e6e8ec !important;
    border-radius: 999px !important; padding: .25rem .8rem !important;
    font-size: .8rem !important; color: #6b7280 !important;
}

.mode-grid { display: grid !important; grid-template-columns: 1fr 1fr; gap: 1.2rem; padding: 1rem 0 2rem; }
@media (max-width: 800px) { .mode-grid { grid-template-columns: 1fr; } }

.mode-card {
    border-radius: 14px !important; padding: 1.5rem 1.4rem !important;
    background: #fff !important; border: 1px solid #e6e8ec !important;
    box-shadow: 0 1px 3px rgba(16, 24, 40, .06) !important;
    cursor: pointer; text-align: left;
    transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
    min-height: 185px;
}
.mode-card:hover {
    transform: translateY(-3px); border-color: #c7d2fe !important;
    box-shadow: 0 10px 26px rgba(16, 24, 40, .10) !important;
}
.mode-card .mode-emoji { font-size: 2rem; display: block; margin-bottom: .6rem; }
.mode-card .mode-name { font-size: 1.3rem !important; font-weight: 700 !important; color: #1a1d21 !important; }
.mode-card .mode-desc { color: #6b7280 !important; font-size: .92rem !important; margin-top: .4rem !important; line-height: 1.6 !important; }
.mode-card .mode-tag {
    display: inline-block; margin-top: .8rem; font-size: .78rem; font-weight: 600;
    color: #4f46e5; background: #eef2ff; border-radius: 999px; padding: .15rem .6rem;
}
#analysis_card { border-top: 3px solid #4f46e5; }
#kb_card { border-top: 3px solid #7c3aed; }

/* 工作区左右两栏：左栏固定宽度 + 右栏自适应（flex 语义的 grid） */
.two-pane { display: grid !important; grid-template-columns: 300px 1fr; gap: 1rem; align-items: start !important; }
@media (max-width: 900px) { .two-pane { grid-template-columns: 1fr; } }

/* ================= 工作区标题 ================= */
.section-head { padding: .9rem 0 .4rem; }
.section-title { font-size: 1.45rem !important; font-weight: 700 !important; color: #1a1d21 !important; }
.section-sub { color: #6b7280 !important; font-size: .93rem !important; }

/* ================= 侧栏面板（整体一张卡片，内部分割线） ================= */
.side-panel {
    background: #fff !important; border: 1px solid #e6e8ec !important;
    border-radius: 12px !important; padding: 1rem 1.05rem !important;
    box-shadow: 0 1px 3px rgba(16, 24, 40, .05) !important;
    align-self: start !important;
}
.side-panel .side-section { padding: .3rem 0; }
.side-panel .side-section + .side-section {
    border-top: 1px solid #eef0f3; margin-top: .9rem; padding-top: 1.05rem;
}
.side-panel h3 {
    font-size: .95rem !important; font-weight: 700 !important; color: #1a1d21 !important;
    margin: 0 0 .6rem !important;
}
.side-panel label, .side-panel .label-wrap { font-size: .82rem !important; color: #6b7280 !important; }

/* ================= 工作台模式条 ================= */
.mode-bar {
    display: flex !important; align-items: center !important; gap: .6rem !important;
    padding: .5rem .8rem !important; background: #eef2ff !important;
    border: 1px solid #c7d2fe !important; border-radius: 10px !important;
    margin-bottom: .6rem !important; font-size: .88rem !important; color: #3730a3 !important;
}
.mode-bar .mode-pill {
    background: #4f46e5 !important; color: #fff !important; border-radius: 999px !important;
    padding: .1rem .55rem !important; font-size: .75rem !important; font-weight: 600 !important;
    white-space: nowrap !important;
}
.mode-bar .mode-active { font-weight: 600 !important; }

/* ================= 工具链面板 ================= */
.tool-chain {
    border: 1px solid #e6e8ec !important; border-radius: 12px !important;
    background: #fff !important; padding: .7rem .85rem !important;
    margin: .4rem 0 .8rem !important; font-size: .84rem !important;
    max-height: 190px !important; overflow-y: auto !important;
}
.tool-chain-head {
    font-weight: 700 !important; color: #1a1d21 !important; margin-bottom: .45rem !important;
    display: flex !important; align-items: center !important; gap: .45rem !important;
}
.tool-chain-count {
    background: #eef2ff !important; color: #4f46e5 !important; border-radius: 999px !important;
    padding: .05rem .5rem !important; font-size: .72rem !important; font-weight: 600 !important;
}
.tool-chain-empty { color: #9ca3af !important; font-size: .84rem !important; }
.tool-step {
    border: 1px solid #eef0f3 !important; border-left: 3px solid #ed8936 !important;
    border-radius: 9px !important; padding: .45rem .6rem !important;
    margin: .35rem 0 !important; background: #fafbfc !important;
}
.tool-step-idx {
    display: inline-flex !important; align-items: center !important; justify-content: center !important;
    width: 18px !important; height: 18px !important; border-radius: 50% !important;
    background: #667eea !important; color: #fff !important;
    font-size: .68rem !important; font-weight: 700 !important; margin-right: .4rem !important;
}
.tool-step-name {
    font-weight: 600 !important; font-size: .76rem !important;
    background: #fef3cd !important; color: #856404 !important;
    padding: 2px 8px !important; border-radius: 4px !important;
    font-family: ui-monospace, monospace !important;
}
.tool-step-status { float: right !important; font-size: .72rem !important; font-weight: 600 !important; }
.tool-step-status.running { color: #ed8936 !important; }
.tool-step-status.done { color: #48bb78 !important; }
.tool-step-args, .tool-step-result {
    color: #6b7280 !important; margin: .25rem 0 0 !important;
    word-break: break-all !important; font-size: .76rem !important;
    background: #fff !important; border-radius: 6px !important;
    padding: 4px 8px !important; border: 1px solid #f1f3f5 !important;
}
.tool-step-result.success { border-left: 2px solid #48bb78 !important; }
.tool-step-result.error { border-left: 2px solid #f56565 !important; }
.tool-step-result.pending { border-left: 2px solid #ed8936 !important; }

/* ================= 组件配色（浅色） ================= */
.gr-box, .gr-panel { background: #fff !important; border-color: #e6e8ec !important; border-radius: 12px !important; }
label, .gr-text, .gr-markdown { color: #1a1d21; }
.gr-button-primary { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important; border: none !important; }
.gr-button-primary:hover { filter: brightness(1.08) !important; }

/* ================= 细滚动条 ================= */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #d1d5db; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #9ca3af; }

/* ================= 状态点（pulse 动画） ================= */
.badge-pill .dot {
    display: inline-block !important; width: 7px !important; height: 7px !important;
    border-radius: 50% !important; background: #22c55e !important;
    margin-right: 6px !important; animation: dot-pulse 2s infinite !important;
}
@keyframes dot-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .45; } }

/* ================= 聊天气泡（ExcelMind 风格） ================= */
.gr-chatbot .message {
    max-width: 82% !important;
    animation: msg-in .25s ease !important;
}
@keyframes msg-in {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
}
.gr-chatbot .message.user { justify-content: flex-end !important; }
.gr-chatbot .message.user .message-content {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
    color: #fff !important; border: none !important;
    border-radius: 14px 14px 4px 14px !important;
    box-shadow: 0 2px 8px rgba(102, 126, 234, .28) !important;
}
.gr-chatbot .message.bot .message-content {
    background: #fff !important;
    border: 1px solid #e6e8ec !important;
    border-radius: 14px 14px 14px 4px !important;
    box-shadow: 0 1px 3px rgba(16, 24, 40, .05) !important;
}

/* ================= 表格列表（ExcelMind 风格卡片） ================= */
.tables-list { display: flex !important; flex-direction: column !important; gap: 6px !important; }
.table-item {
    display: flex !important; align-items: center !important; gap: 10px !important;
    padding: 9px 12px !important; background: #fafbfc !important;
    border: 1px solid #eef0f3 !important; border-radius: 8px !important;
    transition: all .18s ease !important;
}
.table-item:hover { background: #f5f5ff !important; border-color: #a5b4fc !important; }
.table-item.active {
    background: linear-gradient(135deg, #667eea12 0%, #764ba212 100%) !important;
    border-color: #667eea !important; box-shadow: 0 1px 4px rgba(102, 126, 234, .18) !important;
}
.table-icon { font-size: 1rem !important; flex-shrink: 0 !important; }
.table-info { flex: 1 !important; min-width: 0 !important; }
.table-name {
    font-weight: 600 !important; font-size: .82rem !important; color: #1a1d21 !important;
    white-space: nowrap !important; overflow: hidden !important; text-overflow: ellipsis !important;
    display: block !important;
}
.table-meta { font-size: .68rem !important; color: #8e8ea0 !important; margin-top: 2px !important; display: block !important; }
.joined-badge { font-size: .72rem !important; }
.tables-empty { text-align: center !important; padding: 18px !important; color: #9ca3af !important; font-size: .82rem !important; }

/* ================= 聊天空状态 ================= */
.chat-empty {
    display: flex !important; flex-direction: column !important; align-items: center !important;
    justify-content: center !important; padding: 2.2rem 1rem !important;
    color: #9ca3af !important; text-align: center !important;
}
.chat-empty-icon { font-size: 2.6rem !important; margin-bottom: .6rem !important; opacity: .6 !important; }
.chat-empty h3 { color: #6b7280 !important; font-size: 1rem !important; margin: 0 0 .3rem !important; }
.chat-empty p { font-size: .82rem !important; margin: 0 !important; }

/* ================= 首页：隐私标签 + 折叠面板增强 ================= */
.privacy-tag {
    display: inline-block !important; margin-bottom: .6rem !important;
    background: #ecfdf5 !important; color: #047857 !important;
    border: 1px solid #a7f3d0 !important; border-radius: 999px !important;
    padding: .25rem .85rem !important; font-size: .8rem !important; font-weight: 600 !important;
}
.gr-accordion {
    border: 1px solid #e6e8ec !important; border-radius: 10px !important;
    background: #fff !important; margin: .8rem auto 1.2rem !important;
    max-width: 640px !important;
}
.gr-accordion summary {
    padding: .7rem 1rem !important; font-weight: 600 !important;
    cursor: pointer !important; color: #1a1d21 !important;
}
.gr-accordion summary:hover { color: #4f46e5 !important; }

/* ================= 上传组件：虚线框 + hover 反馈 ================= */
.gr-file, .upload-container {
    border-style: dashed !important; border-color: #d1d5db !important;
    transition: all .15s ease !important; background: #fff !important;
}
.gr-file:hover, .upload-container:hover {
    border-color: #4f46e5 !important; background: #f5f5ff !important;
}

/* ================= 聊天输入行：吸底常驻 ================= */
.chat-input-sticky {
    position: sticky !important; bottom: 0 !important; z-index: 5 !important;
    background: #fff !important; border: 1px solid #e6e8ec !important;
    border-radius: 12px !important; padding: .55rem .7rem !important;
    box-shadow: 0 -4px 16px rgba(16, 24, 40, .05) !important;
    margin-top: .6rem !important;
}

/* ================= 知识库 ================= */
.kb-side {
    gap: .5rem !important; background: #fff !important; border: 1px solid #e6e8ec !important;
    border-radius: 12px !important; padding: .8rem .9rem !important;
    box-shadow: 0 1px 3px rgba(16, 24, 40, .05) !important;
}
.kb-side-head { margin: .2rem 0 0 !important; }
.kb-side-head h3 { font-size: 1rem !important; margin: .3rem 0 .15rem !important; color: #1a1d21 !important; }
.kb-side label, .kb-side .label-wrap { font-size: .82rem !important; color: #6b7280 !important; }
.kb-side .gr-button { flex-shrink: 1 !important; }
.env-panes label {
    display: block !important;
    border: 1px solid #e6e8ec !important;
    border-radius: 10px !important;
    margin: .3rem 0 !important;
    padding: .55rem .75rem !important;
    cursor: pointer !important;
    transition: all .15s ease !important;
    background: #fff !important;
    font-size: .95rem !important;
}
.env-panes label:hover { border-color: #a5b4fc !important; transform: translateX(3px); }
.env-panes label:has(input:checked) {
    border-color: #7c3aed !important;
    background: #f5f3ff !important;
    box-shadow: 0 0 0 1px #7c3aed !important;
}
.env-panes input[type="radio"] { accent-color: #7c3aed !important; }
.kb-detail {
    border: 1px solid #e6e8ec !important;
    border-radius: 12px !important;
    padding: .4rem .7rem .6rem !important;
    margin-top: .5rem !important;
    background: #fafbfc !important;
}
.kb-chat-box {
    border: 1px solid #ddd6fe !important;
    border-radius: 14px !important;
    padding: .8rem .9rem .9rem !important;
    background: #fff !important;
    box-shadow: 0 1px 4px rgba(16, 24, 40, .05) !important;
}
.kb-chat-box .gr-chatbot { border-radius: 10px !important; border: 1px solid #e6e8ec !important; }
.kb-answer {
    border-left: 4px solid #7c3aed !important;
    background: #f5f3ff !important;
    border-radius: 0 10px 10px 0 !important;
    padding: .7rem .9rem !important;
    margin-top: .5rem !important;
    font-size: .95rem !important;
    line-height: 1.65 !important;
}
"""
    + PROGRESS_CSS
)


def _status_badge(session: Session) -> str:
    if session.llm_ready:
        return (
            f"<span class='badge-pill ok'><span class='dot'></span>LLM 已就绪 · {session.llm_model_name}</span>"
        )
    return "<span class='badge-pill warn'><span class='dot'></span>未配置 LLM —— 请先在下方向「模型配置」填写 API Key</span>"


def _home_view(session: Session, tabs: gr.Tabs) -> gr.Markdown:
    """首页 Tab：Hero + 模型配置 + 模式卡片（点击卡片切到对应 Tab）。"""
    with gr.Column(elem_classes="hero-box"):
        gr.Markdown(
            "<div class='privacy-tag'>🏠 本地运行 · 数据 100% 私有</div>",
            elem_classes="hero-badge",
        )
        gr.Markdown(
            "# Excel Co-Pilot\n"
            "<span class='hero-sub'>本地优先 · AI 数据工作台 —— 数据不出域，全链路本地/自主可控</span>",
            elem_classes="hero-title",
        )
        badge = gr.Markdown(_status_badge(session), elem_classes="hero-badge")

        gr.Markdown(
            "<div class='hero-feats'>"
            "<span>🔒 本地优先 · 数据不出域</span>"
            "<span>📊 结构化问数 · 多表联表</span>"
            "<span>📚 RAG 知识库 · 引用溯源</span>"
            "</div>"
        )

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
                    "<div class='mode-desc'>表格智能分析：上传多张表，AI 自动筛选、聚合、联表；"
                    "文档可直接发送进上下文问答。</div>"
                    "<span class='mode-tag'>结构化问数 · 多表联表 · 工具调用</span>"
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

        # 启动时后台预热知识库：预加载嵌入模型 + 预建 BM25 索引，
        # 让用户第一次提问时数据库已就绪，不用现场构建
        import threading

        def _warm_up():
            from ..kb.services import get_services

            get_services().warm_up()

        threading.Thread(target=_warm_up, daemon=True).start()

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
APP_THEME = gr.themes.Soft(primary_hue="indigo", neutral_hue="slate")


__all__ = ["APP_CSS", "APP_THEME", "build_ui"]

