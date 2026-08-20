"""单次问答 Tab：数据工作台（表格管理 + 文档上下文 + Agent 工具链聊天）。

从 gradio_app.py 拆出，保持主入口只负责首页与 Tab 组装。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import gradio as gr

from ..core.session import Session
from .progress import progress_html


def _mode_bar(mode: str, detail: str) -> str:
    """工作台顶部模式指示条。"""
    return (
        f"<div class='mode-bar'><span class='mode-pill'>{mode}</span>"
        f"<span class='mode-active'>{detail}</span></div>"
    )


def _tool_panel_html(tool_log: list[tuple[str, str, str]]) -> str:
    """工具调用链可视化面板。tool_log: [(工具名, 参数, 结果), ...]"""
    import html as _html

    if not tool_log:
        return (
            "<div class='tool-chain'><div class='tool-chain-head'>🔧 分析过程</div>"
            "<div class='tool-chain-empty'>暂无工具调用（Agent 将在这里实时展示分析步骤）</div></div>"
        )
    steps = []
    for i, (name, args_txt, result_txt) in enumerate(tool_log, 1):
        if result_txt:
            is_err = result_txt.lstrip().startswith("Error") or '"error"' in result_txt
            result_html = (
                f"<div class='tool-step-result {'error' if is_err else 'success'}'>"
                f"结果：<code>{_html.escape(result_txt)}</code></div>"
            )
            status = "<span class='tool-step-status done'>✓ 完成</span>"
        else:
            result_html = "<div class='tool-step-result pending'>⏳ 运行中…</div>"
            status = "<span class='tool-step-status running'>⏳ 运行中</span>"
        steps.append(
            f"<div class='tool-step'><span class='tool-step-idx'>{i}</span>"
            f"<span class='tool-step-name'>{_html.escape(name)}</span>{status}"
            f"<div class='tool-step-args'>参数：<code>{_html.escape(args_txt)}</code></div>"
            f"{result_html}</div>"
        )
    return (
        "<div class='tool-chain'><div class='tool-chain-head'>🔧 分析过程"
        f"<span class='tool-chain-count'>{len(tool_log)} 步</span></div>"
        + "\n".join(steps)
        + "</div>"
    )


def _analysis_workspace(state: gr.State) -> None:
    """单次问答：左窗表格管理（智能分析）+ 文件发送，右窗问答（自动路由）。"""
    import json as _json

    with gr.Column():
        gr.Markdown(
            "<span class='section-title'>📊 单次问答</span>"
            "<span class='section-sub' style='display:block'>表格 → 结构化问数（工具调用 + 多表联表）；文档 → 文件进上下文问答</span>",
            elem_classes="section-head",
        )

        with gr.Column(elem_classes="two-pane"):
            # 左：侧栏（整体面板，内部分区）
            with gr.Column(scale=1, min_width=280, elem_classes="side-panel"):
                with gr.Column(elem_classes="side-section"):
                    gr.Markdown("### 📊 表格管理（智能分析）")
                    table_upload = gr.File(
                        label="CSV / Excel（可多选，支持多表联表）",
                        file_count="multiple",
                        file_types=[".csv", ".xlsx", ".xls"],
                        type="filepath",
                    )
                    with gr.Row():
                        add_table_btn = gr.Button("📥 添加到表", variant="primary", scale=2)
                        remove_table_btn = gr.Button("🗑 移除", scale=1)
                    table_status = gr.Markdown("")
                    table_list_md = gr.HTML("（尚未添加表格）")
                    active_dd = gr.Dropdown(
                        label="当前操作表", choices=[], interactive=True
                    )
                    with gr.Accordion("🔗 智能联表", open=False):
                        with gr.Row():
                            jt1 = gr.Dropdown(label="表 1", choices=[])
                            jt2 = gr.Dropdown(label="表 2", choices=[])
                        with gr.Row():
                            suggest_btn = gr.Button(
                                "🤖 AI 建议连接", variant="primary", scale=2
                            )
                            do_join_btn = gr.Button("⚡ 执行连接", scale=1)
                        suggest_md = gr.Markdown("（AI 建议将显示在这里）")
                        join_status = gr.Markdown("")
                        suggest_state = gr.State(None)

                with gr.Column(elem_classes="side-section"):
                    gr.Markdown("### 📤 发送文件（文档问答）")
                    upload = gr.File(
                        label="PDF / Word / MD / TXT / PPT 等",
                        file_count="multiple",
                        file_types=[
                            ".pdf",
                            ".docx",
                            ".pptx",
                            ".md",
                            ".markdown",
                            ".txt",
                            ".json",
                            ".parquet",
                        ],
                        type="filepath",
                    )
                    with gr.Row():
                        send_btn = gr.Button("📥 发送给 AI", variant="primary", scale=2)
                        clear_btn = gr.Button("🗑 清空", scale=1)
                    context_md = gr.Markdown("（尚未发送任何文件）")

# 右：数据工作台（模式条 + 分析过程面板 + 聊天区）
            with gr.Column(scale=3):
                mode_bar = gr.HTML(_mode_bar("文档问答", "尚未发送文件"))
                gr.Markdown("### 💬 问答")
                chat_empty = gr.HTML(
                    "<div class='chat-empty'><div class='chat-empty-icon'>💬</div>"
                    "<h3>开始对话</h3>"
                    "<p>左侧上传表格即可开始结构化问数（筛选 / 聚合 / 分组 / 联表），或发送文档进行内容问答</p></div>"
                )
                chatbot = gr.Chatbot(
                    height=460,
                    label="对话",
                    value=[
                        {
                            "role": "assistant",
                            "content": "👋 你好！我是 Excel Co-Pilot。\n\n左侧上传表格即可开始数据问答，或发送文档进行内容问答。",
                        }
                    ],
                )
                tool_panel = gr.HTML(
                    "<div class='tool-chain'><div class='tool-chain-head'>🔧 分析过程"
                    "</div><div class='tool-chain-empty'>暂无工具调用（Agent 将在这里实时展示分析步骤）</div></div>"
                )
                ai_progress = gr.HTML(progress_html("", None))
                with gr.Row(elem_classes="chat-input-sticky"):
                    msg = gr.Textbox(
                        placeholder="表格模式：如「按分公司统计移动新增，从高到低」；文档模式：如「3 月份的总销售额是多少？」",
                        scale=4,
                        show_label=False,
                    )
                    ask_btn = gr.Button("发送", variant="primary", scale=1)
                chat_history = gr.State([])  # [(user, assistant), ...]

        # ---- 辅助 ----

        def _tables_html(session: Session) -> str:
            """表格列表（ExcelMind 风格卡片：图标 + 名称 + meta，活跃表高亮）。"""
            import html as _html

            rows = session.tables.list_tables()
            if not rows:
                return "<div class='tables-empty'>（尚未添加表格）</div>"
            items = []
            for r in rows:
                name = _html.escape(r["filename"])
                meta = f"{r['total_rows']} 行 × {r['total_columns']} 列"
                joined = " <span class='joined-badge'>🔗</span>" if r["is_joined"] else ""
                cls = "table-item active" if r["is_active"] else "table-item"
                items.append(
                    f"<div class='{cls}'><span class='table-icon'>📊</span>"
                    f"<span class='table-info'><span class='table-name'>{name}{joined}</span>"
                    f"<span class='table-meta'>{meta}</span></span></div>"
                )
            return f"<div class='tables-list'>{''.join(items)}</div>"

        def _dd_choices(session: Session) -> list[tuple[str, str]]:
            return [
                (f"{r['filename']}（{r['total_rows']} 行）", r["id"])
                for r in session.tables.list_tables()
            ]

        def _active_id(session: Session) -> str | None:
            return session.tables.active_table_id

        def _dd_updates(session: Session):
            choices = _dd_choices(session)
            active = _active_id(session)
            if not choices:
                return (
                    gr.update(choices=[], value=None),
                    gr.update(choices=[], value=None),
                    gr.update(choices=[], value=None),
                )
            return (
                gr.update(choices=choices, value=active),
                gr.update(choices=choices, value=choices[0][1] if active else None),
                gr.update(choices=choices, value=choices[0][1] if active else None),
            )

        def on_add_table(files: Any, session: Session):
            if not files:
                return (
                    "（请先选择表格文件）",
                    _tables_html(session),
                    *_dd_updates(session),
                )
            if isinstance(files, str):
                files = [files]
            names = []
            try:
                for f in files:
                    info = session.add_table(Path(f))
                    names.append(info["name"])
            except Exception as exc:
                return (
                    f"❌ 添加失败：{exc}",
                    _tables_html(session),
                    *_dd_updates(session),
                )
            return (
                f"📥 已添加 {len(names)} 个表格（{'、'.join(names)}）",
                _tables_html(session),
                *_dd_updates(session),
            )

        def on_switch_table(table_id: str | None, session: Session):
            if table_id:
                session.tables.set_active_table(table_id)
            return _tables_html(session)

        def on_remove_table(table_id: str | None, session: Session):
            if not table_id:
                return (
                    "（未选择要移除的表）",
                    _tables_html(session),
                    *_dd_updates(session),
                )
            ok = session.tables.remove_table(table_id)
            status = "🗑 已移除" if ok else "❌ 移除失败（表不存在）"
            return status, _tables_html(session), *_dd_updates(session)

        def on_suggest(t1: str | None, t2: str | None, session: Session):
            if not t1 or not t2:
                return "（请先选择两张表）", "⚠️ 需要选择两张不同的表", None
            if t1 == t2:
                return "（请先选择两张表）", "⚠️ 请选择两张不同的表", None
            from ..core.join_service import suggest_join_config

            try:
                suggestion = suggest_join_config(
                    session.tables.get_table_summary(t1),
                    session.tables.get_table_summary(t2),
                )
            except Exception as exc:
                return "（AI 建议将显示在这里）", f"❌ 建议失败：{exc}", None
            text = (
                f"**建议**：`{suggestion.get('new_name')}`\n\n"
                f"- 连接键：`{suggestion.get('keys1')}` ↔ `{suggestion.get('keys2')}`\n"
                f"- 连接类型：`{suggestion.get('join_type')}`\n"
                f"- 理由：{suggestion.get('reason', '')}"
            )
            return text, "✅ 建议已生成，可点击「⚡ 执行连接」", suggestion

        def on_join(
            t1: str | None,
            t2: str | None,
            suggestion: dict | None,
            session: Session,
        ):
            if not t1 or not t2:
                return _tables_html(session), *_dd_updates(session), "⚠️ 需要选择两张表"
            if t1 == t2:
                return (
                    _tables_html(session),
                    *_dd_updates(session),
                    "⚠️ 请选择两张不同的表",
                )
            if not suggestion:
                return (
                    _tables_html(session),
                    *_dd_updates(session),
                    "⚠️ 请先点击「🤖 AI 建议连接」生成联表配置",
                )
            try:
                _new_id, _structure = session.tables.join_tables(
                    t1,
                    t2,
                    keys1=suggestion.get("keys1", []),
                    keys2=suggestion.get("keys2", []),
                    join_type=suggestion.get("join_type", "inner"),
                    new_name=suggestion.get("new_name", "连接表"),
                )
            except Exception as exc:
                return _tables_html(session), *_dd_updates(session), f"❌ 连接失败：{exc}"
            return (
                _tables_html(session),
                *_dd_updates(session),
                f"✅ 连接表已生成并设为当前操作表（{suggestion.get('new_name', '连接表')}），可以直接提问分析",
            )

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

        def _to_messages(history: list) -> list[dict]:
            """Gradio 6 Chatbot 只接受 messages 格式：[(u, a), ...] → dict 列表。"""
            msgs: list[dict] = []
            for u, a in history:
                msgs.append({"role": "user", "content": u})
                msgs.append({"role": "assistant", "content": a})
            return msgs

        def _proc_text(tool_log: list[str], tail: str) -> str:
            lines = [*tool_log, tail] if tool_log else [tail]
            return "<br>".join(lines)

        def _active_name(session: Session) -> str:
            for r in session.tables.list_tables():
                if r["is_active"]:
                    return r["filename"]
            return "（无）"

        def _mode_bar_html(session: Session) -> str:
            if session.tables.is_loaded:
                return _mode_bar(
                    "表格分析",
                    f"当前操作表：{_active_name(session)} · 可问筛选 / 聚合 / 分组 / 联表统计",
                )
            n = len(session.context)
            detail = f"{n} 个文件在上下文中" if n else "尚未发送文件"
            return _mode_bar("文档问答", detail)

        def _tool_card(tool_log: list[tuple[str, str, str]]) -> str:
            if not tool_log:
                return ""
            body = "\n\n".join(
                f"🔧 **{name}** `{args}`" + (f"\n  - 结果：`{result}`" if result else "")
                for name, args, result in tool_log
            )
            return (
                f"<details><summary>🔧 分析过程（{len(tool_log)} 步）</summary>\n\n"
                f"{body}\n\n</details>\n\n"
            )

        async def on_ask(question: str, history: list, session: Session):
            if not question or not question.strip():
                yield (
                    _to_messages(history or []),
                    progress_html("", None),
                    _tool_panel_html([]),
                    _mode_bar_html(session),
                    gr.update(visible=False),
                    "",
                    history or [],
                )
                return
            history = history or []
            llm_history = _to_messages(history)

            if session.tables.is_loaded:
                # ---- 表格分析模式（Agent + 工具调用链）----
                msgs = _to_messages(history)
                tool_log: list[tuple[str, str, str]] = []
                answer = ""
                error_msg = ""
                yield (
                    msgs,
                    progress_html("⏳ AI 正在规划分析…", 15, animate=True),
                    _tool_panel_html(tool_log),
                    _mode_bar_html(session),
                    gr.update(visible=False),
                    "",
                    history,
                )
                async for event in session.analyze_chat(
                    question.strip(), history=llm_history
                ):
                    etype = event["type"]
                    if etype == "thinking":
                        pass
                    elif etype == "thinking_done":
                        yield (
                            msgs,
                            progress_html(
                                _proc_text([t[0] for t in tool_log], "🔍 开始调用工具…"),
                                40,
                                animate=True,
                            ),
                            _tool_panel_html(tool_log),
                            _mode_bar_html(session),
                            gr.update(visible=False),
                            "",
                            history,
                        )
                    elif etype == "tool_call":
                        args_txt = _json.dumps(event["args"], ensure_ascii=False)[:200]
                        tool_log.append((event["name"], args_txt, ""))
                        yield (
                            msgs,
                            progress_html(
                                _proc_text(
                                    [t[0] for t in tool_log], "⏳ 工具执行中…"
                                ),
                                55,
                                animate=True,
                            ),
                            _tool_panel_html(tool_log),
                            _mode_bar_html(session),
                            gr.update(visible=False),
                            "",
                            history,
                        )
                    elif etype == "tool_result":
                        result_txt = _json.dumps(event["result"], ensure_ascii=False)[
                            :200
                        ]
                        tool_log[-1] = (tool_log[-1][0], tool_log[-1][1], result_txt)
                        yield (
                            msgs,
                            progress_html(
                                _proc_text(
                                    [t[0] for t in tool_log], "⏳ 继续分析…"
                                ),
                                70,
                                animate=True,
                            ),
                            _tool_panel_html(tool_log),
                            _mode_bar_html(session),
                            gr.update(visible=False),
                            "",
                            history,
                        )
                    elif etype == "token":
                        answer += event["content"]
                        yield (
                            _to_messages(history)
                            + [{"role": "assistant", "content": answer}],
                            progress_html(
                                _proc_text(
                                    [t[0] for t in tool_log], "✍️ 正在组织回答…"
                                ),
                                85,
                                animate=True,
                            ),
                            _tool_panel_html(tool_log),
                            _mode_bar_html(session),
                            gr.update(visible=False),
                            "",
                            history,
                        )
                    elif etype == "done":
                        if event["content"]:
                            answer = event["content"]
                    elif etype == "error":
                        error_msg = event["content"]
                if error_msg and not answer:
                    answer = f"❌ {error_msg}"
                final = _tool_card(tool_log) + (answer or "（AI 未生成回答）")
                new_history = history + [(question.strip(), final)]
                yield (
                    _to_messages(new_history),
                    progress_html("✅ 分析完成", 100),
                    _tool_panel_html(tool_log),
                    _mode_bar_html(session),
                    gr.update(visible=False),
                    "",
                    new_history,
                )
            else:
                # ---- 文档模式（文件进上下文问答）----
                msgs = _to_messages(history)
                yield (
                    msgs,
                    progress_html("⏳ 已收到问题，正在整理上下文…", 20, animate=True),
                    _tool_panel_html([]),
                    _mode_bar_html(session),
                    gr.update(visible=False),
                    "",
                    history,
                )
                yield (
                    msgs,
                    progress_html("⏳ AI 正在回答…", 60, animate=True),
                    _tool_panel_html([]),
                    _mode_bar_html(session),
                    gr.update(visible=False),
                    "",
                    history,
                )
                try:
                    reply = session.simple_chat(question.strip(), history=llm_history)
                except Exception as exc:
                    reply = f"❌ 出错了：{exc}"
                new_history = history + [(question.strip(), reply)]
                yield (
                    _to_messages(new_history),
                    progress_html("✅ 回答完成", 100),
                    _tool_panel_html([]),
                    _mode_bar_html(session),
                    gr.update(visible=False),
                    "",
                    new_history,
                )

        # ---- 事件 ----

        add_table_btn.click(
            on_add_table,
            [table_upload, state],
            [table_status, table_list_md, active_dd, jt1, jt2],
        )
        active_dd.change(on_switch_table, [active_dd, state], [table_list_md])
        remove_table_btn.click(
            on_remove_table,
            [active_dd, state],
            [table_status, table_list_md, active_dd, jt1, jt2],
        )
        suggest_btn.click(
            on_suggest, [jt1, jt2, state], [suggest_md, join_status, suggest_state]
        )
        do_join_btn.click(
            on_join,
            [jt1, jt2, suggest_state, state],
            [table_list_md, active_dd, jt1, jt2, join_status],
        )
        send_btn.click(on_send, [upload, state], [context_md, context_md])
        clear_btn.click(on_clear, [state], [context_md, context_md])
        ask_btn.click(
            on_ask,
            [msg, chat_history, state],
            [chatbot, ai_progress, tool_panel, mode_bar, chat_empty, msg, chat_history],
            show_progress="hidden",
        )
        msg.submit(
            on_ask,
            [msg, chat_history, state],
            [chatbot, ai_progress, tool_panel, mode_bar, chat_empty, msg, chat_history],
            show_progress="hidden",
        )
