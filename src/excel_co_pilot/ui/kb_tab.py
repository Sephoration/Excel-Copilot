"""知识库（KB / RAG）Gradio Tab：文档导入、索引管理、引用溯源问答。

与表格分析 Tab 共用同一 LLM 配置（DEEPSEEK_API_KEY），互不干扰。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import gradio as gr

from ..config import settings
from ..kb.services import get_services


def _files_markdown() -> str:
    svc = get_services()
    files = svc.db.list_files(limit=50)
    if not files:
        return "（知识库为空，请先导入文档）"
    lines = ["| 文件 | 状态 | 块数 |", "| :--- | :--- | :--- |"]
    for f in files:
        chunks = svc.db.count_doc_chunks(f.id)
        lines.append(f"| {f.name} | {f.status} | {chunks} |")
    return "\n".join(lines)


def _stats_markdown() -> str:
    svc = get_services()
    s = svc.db.stats()
    total = sum(s["files"].values())
    return (
        f"- 文档总数：**{total}**（已索引 **{s['files'].get('indexed', 0)}**，"
        f"失败 **{s['files'].get('failed', 0)}**，重复 **{s['files'].get('duplicate', 0)}**）\n"
        f"- 分块总数：**{s['chunks']}**\n"
        f"- LLM：{'✅ 已就绪（' + settings.llm_model + '）' if settings.deepseek_api_key else '⚠️ 未配置（问答不可用，导入/检索可用）'}"
    )


def _render_answer(answer: str, result: Any) -> str:
    """把回答 + 引用列表渲染成 Markdown（带溯源）。"""
    out = answer or ""
    if result is not None and getattr(result, "citations", None):
        out += "\n\n---\n\n**📎 引用溯源**\n"
        for c in result.citations:
            page = f"第 {c['page']} 页" if c.get("page") else ""
            out += (
                f"- **[{c['id']}]** 《{c['file_name']}》{page}"
                f"（相似度 {c['similarity']:.3f}）\n"
            )
    return out


def build_kb_tab() -> gr.Blocks:
    """知识库 Tab 组件：返回嵌套 Blocks，由 build_ui 用 .render() 挂载。"""
    with gr.Blocks() as kb:
        gr.Markdown(
            "### 📚 知识库问答（RAG）\n上传 PDF / Word / Markdown / TXT / PPT / Excel，自动建索引后用中文提问，回答带引用溯源。"
        )

        stats_md = gr.Markdown(_stats_markdown())

        with gr.Row():
            upload = gr.File(
                label="导入文档（可多选）",
                file_count="multiple",
                file_types=[
                    ".pdf",
                    ".md",
                    ".markdown",
                    ".txt",
                    ".docx",
                    ".pptx",
                    ".xlsx",
                    ".csv",
                ],
                type="filepath",
            )
        with gr.Row():
            ingest_btn = gr.Button("🚀 导入并建立索引", variant="primary")
            refresh_btn = gr.Button("🔄 刷新")
        ingest_status = gr.Markdown("")

        with gr.Row():
            file_table = gr.Markdown(_files_markdown())

        gr.Markdown("### 💬 提问")
        with gr.Row():
            kb_msg = gr.Textbox(
                placeholder="例如：文档里关于 XX 是怎么说的？",
                scale=4,
                show_label=False,
            )
            kb_send = gr.Button("提问", variant="primary", scale=1)
        kb_answer = gr.Markdown("（回答会显示在这里，并附引用来源）")

        # ---- 事件 ----

        def on_ingest(files: Any):
            if not files:
                return "（请先选择文件）", gr.update()
            if isinstance(files, str):
                files = [files]
            svc = get_services()
            results = svc.ingest.ingest_paths([Path(f) for f in files])
            ok = sum(1 for r in results if r.get("status") == "indexed")
            lines = [f"✅ 成功导入 {ok}/{len(results)} 个文件："]
            for r in results:
                mark = (
                    "✅"
                    if r.get("status") == "indexed"
                    else "⚠️"
                    if r.get("status") == "duplicate"
                    else "❌"
                )
                lines.append(
                    f"- {mark} {Path(r['path']).name} → {r.get('status')}"
                    + (f"（{r.get('error')}）" if r.get("error") else "")
                )
            return "\n".join(lines), gr.update()

        def on_refresh():
            return _files_markdown(), _stats_markdown()

        def on_ask(question: str):
            if not question or not question.strip():
                return "（请输入问题）"
            svc = get_services()
            if not svc.llm_ready:
                return "⚠️ 未配置 LLM：请先在上方「模型配置」填写 DEEPSEEK_API_KEY。"
            try:
                result = svc.qa.answer(question.strip())
            except Exception as exc:
                return f"❌ 出错了：{exc}"
            if result.refused:
                return (
                    f"🤔 {result.answer or '知识库中未找到相关内容，已拒答以免臆造。'}"
                )
            return _render_answer(result.answer, result)

        ingest_btn.click(on_ingest, [upload], [ingest_status, file_table])
        refresh_btn.click(on_refresh, [], [file_table, stats_md])
        kb_send.click(on_ask, [kb_msg], [kb_answer])
        kb_msg.submit(on_ask, [kb_msg], [kb_answer])

    return kb
