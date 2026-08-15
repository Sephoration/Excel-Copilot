"""Gradio 主界面：数据 Tab（表格分析）+ 知识库 Tab（RAG 问答）。

- Tab 1「数据分析」：文件上传、数据浏览、自然语言对话、图表、导出与回滚（原功能）
- Tab 2「知识库」：文档导入建索引、引用溯源问答（RAG）
共用顶部「模型配置」，同一份 DEEPSEEK_API_KEY。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import gradio as gr

from ..config import settings
from ..core.session import Session
from ..services.pdf_generator import export_html, export_pdf
from .kb_tab import build_kb_tab


def _status_badge(session: Session) -> str:
    if session.llm_ready:
        return f"✅ LLM 已就绪（{session.llm_model_name}）—— 可对话分析 / 知识库问答"
    return "⚠️ 未配置 LLM —— 上传/浏览数据可用；对话与问答请在「模型配置」填写 API Key"


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Excel Co-Pilot · 数据与知识问答工作站") as demo:
        session = Session()
        state = gr.State(session)

        gr.Markdown("# 📊 Excel Co-Pilot\n本地优先的 AI 数据分析 + 知识库问答工作站")
        badge_md = gr.Markdown(_status_badge(session))

        with gr.Accordion("⚙️ 模型配置", open=not session.llm_ready):
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

        with gr.Tabs():
            # ============ Tab 1：数据分析（原功能） ============
            with gr.Tab("📊 数据分析"):
                with gr.Row():
                    # 左侧：数据接入与管理
                    with gr.Column(scale=1):
                        gr.Markdown("### 1. 上传数据")
                        upload = gr.File(
                            label="支持 CSV / Excel / JSON / Parquet / PDF",
                            file_count="multiple",
                            file_types=[
                                ".csv",
                                ".xlsx",
                                ".xls",
                                ".json",
                                ".parquet",
                                ".pdf",
                            ],
                            type="filepath",
                        )
                        load_btn = gr.Button("加载到会话", variant="primary")
                        load_status = gr.Markdown("")

                        gr.Markdown("### 2. 浏览数据")
                        alias_dropdown = gr.Dropdown(
                            label="数据表", choices=[], interactive=True
                        )
                        viewer = gr.Dataframe(label="数据预览", interactive=False)

                        with gr.Row():
                            undo_btn = gr.Button("↩ 撤销上一步")
                            reset_btn = gr.Button("♻ 重置会话")

                        gr.Markdown("### 3. 导出报告")
                        export_btn = gr.Button("📄 导出 HTML + PDF 报告")
                        export_out = gr.File(label="报告下载")

                    # 右侧：对话与图表
                    with gr.Column(scale=2):
                        gr.Markdown("### 4. 对话分析")
                        chatbot = gr.Chatbot(height=420, label="对话")
                        with gr.Row():
                            msg = gr.Textbox(
                                placeholder="例如：合并两表，按城市统计金额，画柱状图",
                                scale=4,
                                show_label=False,
                            )
                            send_btn = gr.Button("发送", variant="primary", scale=1)

                        gr.Markdown("### 5. 图表")
                        plot_view = gr.Plot(label="交互式图表")

                # ---- Tab 1 事件绑定 ----------------------------------------------------

                def on_load(files: Any, session: Session):
                    if not files:
                        return (
                            gr.update(),
                            "（请先选择文件）",
                            gr.update(),
                        )
                    if isinstance(files, str):
                        files = [files]
                    try:
                        loaded = session.load_many([Path(f) for f in files])
                    except Exception as exc:
                        return gr.update(), f"❌ 加载失败：{exc}", gr.update()
                    names = ", ".join(f"{a}={n}" for a, n in loaded.items())
                    aliases = session.pool.aliases()
                    return (
                        gr.update(
                            choices=aliases, value=aliases[0] if aliases else None
                        ),
                        f"✅ 已加载 {len(loaded)} 个文件：{names}",
                        gr.update(),
                    )

                def on_select(alias: str | None, session: Session):
                    if not alias:
                        return gr.update()
                    df = session.get(alias)
                    return df if df is not None else gr.update()

                def on_chat(message: str, history: list, session: Session):
                    if not message or not message.strip():
                        return history, "", None, gr.update()
                    history = history or []
                    history.append({"role": "user", "content": message.strip()})
                    try:
                        result = session.chat(message.strip())
                    except Exception as exc:
                        result = {
                            "reply": f"❌ 出错了：{exc}",
                            "plot_html": None,
                            "plot_fig": None,
                        }
                    history.append(
                        {"role": "assistant", "content": result.get("reply", "")}
                    )
                    return (
                        history,
                        "",
                        result.get("plot_fig"),
                        gr.update(
                            choices=session.pool.aliases(),
                            value=session.pool.default_frame_alias(),
                        ),
                    )

                def on_undo(session: Session):
                    ok = session.undo()
                    return (
                        f"↩ {'已撤销上一步' if ok else '（无可撤销的历史）'}",
                        gr.update(choices=session.pool.aliases()),
                    )

                def on_reset(session: Session):
                    session.reset()
                    return (
                        "♻ 已重置会话",
                        gr.update(choices=[], value=None),
                    )

                def on_export(session: Session):
                    summary = session.summary()
                    history_text = (
                        "\n".join(
                            f"{m['role']}: {m['content']}" for m in session.history
                        )
                        or "（暂无对话）"
                    )
                    sections = [("数据概览", summary), ("对话记录", history_text)]
                    out_dir = settings.storage_dir / "exports" / session.session_id
                    out_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        (out_dir / "report.html").write_text(
                            export_html("数据分析报告", sections), encoding="utf-8"
                        )
                        (out_dir / "report.pdf").write_bytes(
                            export_pdf("数据分析报告", sections)
                        )
                    except Exception as exc:
                        return f"导出失败：{exc}"
                    return str(out_dir / "report.pdf")

                load_btn.click(
                    on_load, [upload, state], [alias_dropdown, load_status, viewer]
                )
                alias_dropdown.change(on_select, [alias_dropdown, state], [viewer])
                send_btn.click(
                    on_chat,
                    [msg, chatbot, state],
                    [chatbot, msg, plot_view, alias_dropdown],
                )
                msg.submit(
                    on_chat,
                    [msg, chatbot, state],
                    [chatbot, msg, plot_view, alias_dropdown],
                )
                undo_btn.click(on_undo, [state], [load_status, alias_dropdown])
                reset_btn.click(on_reset, [state], [load_status, alias_dropdown])
                export_btn.click(on_export, [state], [export_out])

            # ============ Tab 2：知识库问答（RAG） ============
            with gr.Tab("📚 知识库问答"):
                build_kb_tab().render()

        # ---- 全局事件 ----------------------------------------------------

        def on_apply_llm(
            api_key: str, base_url: str, model: str, persist: bool, session: Session
        ):
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
            on_apply_llm,
            [api_key, base_url, model, persist, state],
            [cfg_status, badge_md],
        )

    return demo


__all__ = ["build_ui"]
