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
    import html

    return (
        f"<div class='mode-bar'><span class='mode-pill'>{html.escape(mode)}</span>"
        f"<span class='mode-active'>{html.escape(detail)}</span></div>"
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
            "<span class='section-title'>📊 数据工作台</span>"
            "<span class='section-sub' style='display:block'>导入表格 → 检查与清洗 → 分析问答 → 导出结果</span>",
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
                        file_types=[".csv", ".xlsx", ".xlsm"],
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
                    with gr.Accordion("🔎 数据质量检查", open=False):
                        quality_btn = gr.Button("检查当前表")
                        quality_report = gr.HTML("（点击检查当前表；不会修改数据）")
                    with gr.Accordion("📑 工作簿与导出", open=False):
                        sheet_file = gr.File(label="选择 Excel 工作簿", file_types=[".xlsx", ".xlsm"], type="filepath")
                        sheet_scan = gr.Button("读取工作表列表")
                        sheet_choice = gr.Dropdown(label="工作表", choices=[])
                        sheet_import = gr.Button("导入所选工作表")
                        sheet_status = gr.Markdown("")
                        with gr.Row():
                            export_csv = gr.Button("导出当前表 CSV")
                            export_xlsx = gr.Button("导出全部表 XLSX")
                        export_status = gr.Markdown("")
                        export_file = gr.File(label="下载导出文件", interactive=False)
                    with gr.Accordion("🧹 数据清洗", open=False):
                        gr.Markdown("按顺序添加步骤，先预览再应用；只修改当前会话中的表格。")
                        cleaning_operation = gr.Dropdown(
                            label="操作", choices=[("清理首尾空格", "trim"),
                            ("空白转为空值", "blank_to_null"),
                            ("转换为数值", "convert_numeric"),
                            ("删除重复行", "drop_duplicates")], value="trim"
                        )
                        cleaning_column = gr.Textbox(label="列名（删除重复行时留空）")
                        with gr.Row():
                            cleaning_add = gr.Button("添加步骤")
                            cleaning_reset = gr.Button("清空步骤")
                        cleaning_steps = gr.State([])
                        cleaning_preview_state = gr.State(None)
                        cleaning_plan = gr.JSON(label="清洗步骤", value=[])
                        with gr.Row():
                            cleaning_preview = gr.Button("预览变化")
                            cleaning_apply = gr.Button("应用清洗", variant="primary")
                            cleaning_undo = gr.Button("撤销上次应用")
                        cleaning_status = gr.Markdown("")
                        cleaning_diff = gr.JSON(label="前后示例")
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
            return _tables_html(session), "（当前表已切换，请重新检查）"

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

        def on_quality(session: Session) -> str:
            import html

            from ..core.data_quality import profile_dataframe

            if not session.tables.is_loaded:
                return "请先添加表格。"
            try:
                info = session.tables.get_active_table_info()
                report = profile_dataframe(session.tables.get_active_df())
                name = html.escape(info.filename if info else "当前表")
                duplicate = report["duplicate_rows"]
                dup_text = "无法计算" if duplicate is None else str(duplicate)
                rows = []
                for column in report["columns"]:
                    unique = column["unique_count"]
                    rows.append(
                        "<tr>"
                        f"<td>{html.escape(column['name'])}</td>"
                        f"<td>{html.escape(column['kind'])} ({html.escape(column['dtype'])})</td>"
                        f"<td>{column['null_count']}</td><td>{column['blank_count']}</td>"
                        f"<td>{column['missing_percent']:.2f}%</td>"
                        f"<td>{unique if unique is not None else '无法计算'}</td></tr>"
                    )
                truncated = "（仅显示前 100 列）" if report["columns_truncated"] else ""
                return (
                    f"<p><strong>{name}</strong>：{report['total_rows']} 行 × "
                    f"{report['total_columns']} 列；重复行（不含首次）：{dup_text}</p>"
                    f"<p>缺失率包含空值和空白字符串。{truncated}</p>"
                    "<div style='overflow-x:auto'><table><thead><tr><th>列</th><th>类型</th>"
                    "<th>空值</th><th>空白</th><th>缺失率</th><th>不同值</th></tr></thead><tbody>"
                    + "".join(rows) + "</tbody></table></div>"
                )
            except Exception as exc:
                return f"检查失败：{html.escape(str(exc))}"

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

        def on_sheet_scan(path: str | None):
            from ..core.workbook_io import list_sheets
            if not path:
                return gr.update(choices=[], value=None), "请先选择工作簿。"
            try:
                names = list_sheets(path)
                return gr.update(choices=names, value=names[0] if names else None), f"找到 {len(names)} 张工作表。"
            except Exception as exc:
                return gr.update(choices=[], value=None), f"读取失败：{exc}"

        def on_sheet_import(path: str | None, sheet: str | None, session: Session):
            if not path or not sheet:
                return "请选择工作簿和工作表。", _tables_html(session), *_dd_updates(session), "（请重新检查当前表）"
            try:
                info = session.add_table(path, sheet_name=sheet)
                status = f"已导入 {info['name']} / {sheet}。"
            except Exception as exc:
                status = f"导入失败：{exc}"
            return status, _tables_html(session), *_dd_updates(session), "（请重新检查当前表）"

        def on_export(session: Session, format_name: str):
            import os
            import tempfile
            from ..core.workbook_io import write_export
            if not session.tables.is_loaded:
                return None, "请先添加表格。"
            try:
                if format_name == "csv":
                    info = session.tables.get_active_table_info()
                    frames = {info.filename if info else "当前表": session.tables.get_active_df().copy(deep=True)}
                else:
                    rows = session.tables.list_tables()
                    frames = {f"{i+1}_{row['filename']}": session.tables.get_table_df(row["id"])
                              for i, row in enumerate(rows)}
                fd, path = tempfile.mkstemp(prefix="excel-copilot-", suffix=f".{format_name}")
                try:
                    with os.fdopen(fd, "wb") as output:
                        write_export(frames, output, format_name)
                except Exception:
                    os.unlink(path)
                    raise
                return path, f"已生成 {format_name.upper()} 文件，可点击下载。"
            except Exception as exc:
                return None, f"导出失败：{exc}"

        def on_clean_add(operation: str, column: str, steps: list[dict] | None):
            steps = list(steps or [])
            if len(steps) >= 10:
                return steps, steps, None, "最多设置 10 个步骤。"
            if operation not in {"trim", "blank_to_null", "convert_numeric", "drop_duplicates"}:
                return steps, steps, None, "请选择有效操作。"
            if operation != "drop_duplicates" and not column.strip():
                return steps, steps, None, "请输入列名。"
            step = {"operation": operation}
            if operation != "drop_duplicates":
                step["column"] = column.strip()
            steps.append(step)
            return steps, steps, None, "已添加步骤；请重新预览。"

        def on_clean_preview(steps: list[dict] | None, session: Session):
            if not steps:
                return None, None, "请先添加清洗步骤。"
            try:
                table_id = session.tables.active_table_id
                result = session.tables.preview_cleaning(steps, table_id=table_id)
                token = {"table_id": table_id, "revision": result["revision"], "steps": steps}
                return token, result, "预览完成；确认示例后可应用。"
            except Exception as exc:
                return None, None, f"预览失败：{exc}"

        def on_clean_apply(preview: dict | None, session: Session):
            if not preview or preview["table_id"] != session.tables.active_table_id:
                return None, "请对当前表重新预览。", _tables_html(session), None
            try:
                result = session.tables.apply_cleaning(
                    preview["steps"], table_id=preview["table_id"],
                    expected_revision=preview["revision"],
                )
                return result, "已应用清洗。", _tables_html(session), None
            except Exception as exc:
                return None, f"应用失败：{exc}", _tables_html(session), None

        def on_clean_undo(session: Session):
            try:
                result = session.tables.undo_cleaning()
                return result, "已撤销上次清洗。", _tables_html(session), None
            except Exception as exc:
                return None, f"撤销失败：{exc}", _tables_html(session), None

        # 数据源变化后清除旧报告，避免把旧表的检查结果当成当前表。
        def on_add_table_with_quality(files: Any, session: Session):
            return (*on_add_table(files, session), "（表格已更新，请重新检查）")

        def on_remove_table_with_quality(table_id: str | None, session: Session):
            return (*on_remove_table(table_id, session), "（表格已更新，请重新检查）")

        def on_join_with_quality(t1: str | None, t2: str | None, suggestion: dict | None, session: Session):
            return (*on_join(t1, t2, suggestion, session), "（表格已更新，请重新检查）")

        sheet_scan.click(on_sheet_scan, [sheet_file], [sheet_choice, sheet_status])
        sheet_import.click(on_sheet_import, [sheet_file, sheet_choice, state],
            [table_status, table_list_md, active_dd, jt1, jt2, quality_report])
        export_csv.click(lambda session: on_export(session, "csv"), [state], [export_file, export_status])
        export_xlsx.click(lambda session: on_export(session, "xlsx"), [state], [export_file, export_status])
        cleaning_add.click(on_clean_add,
            [cleaning_operation, cleaning_column, cleaning_steps],
            [cleaning_steps, cleaning_plan, cleaning_preview_state, cleaning_status])
        cleaning_reset.click(lambda: ([], [], None, "步骤已清空。"),
            outputs=[cleaning_steps, cleaning_plan, cleaning_preview_state, cleaning_status])
        cleaning_preview.click(on_clean_preview, [cleaning_steps, state],
            [cleaning_preview_state, cleaning_diff, cleaning_status])
        cleaning_apply.click(on_clean_apply, [cleaning_preview_state, state],
            [cleaning_diff, cleaning_status, table_list_md, cleaning_preview_state])
        cleaning_undo.click(on_clean_undo, [state],
            [cleaning_diff, cleaning_status, table_list_md, cleaning_preview_state])

        # ---- 事件 ----

        add_table_btn.click(
            on_add_table_with_quality,
            [table_upload, state],
            [table_status, table_list_md, active_dd, jt1, jt2, quality_report],
        )
        active_dd.change(on_switch_table, [active_dd, state], [table_list_md, quality_report])
        remove_table_btn.click(
            on_remove_table_with_quality,
            [active_dd, state],
            [table_status, table_list_md, active_dd, jt1, jt2, quality_report],
        )
        quality_btn.click(on_quality, [state], [quality_report])
        suggest_btn.click(
            on_suggest, [jt1, jt2, state], [suggest_md, join_status, suggest_state]
        )
        do_join_btn.click(
            on_join_with_quality,
            [jt1, jt2, suggest_state, state],
            [table_list_md, active_dd, jt1, jt2, join_status, quality_report],
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
