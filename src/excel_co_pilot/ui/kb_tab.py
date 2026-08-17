"""知识库（KB / RAG）Gradio Tab：多库管理、文档导入、引用溯源问答。

- 左窗：库管理（新建 / 选择 / 删除）+ 文档导入 + 文件列表
- 右窗：引用溯源问答（带回答进度条）
- 与单次问答共用同一 LLM 配置（DEEPSEEK_API_KEY）
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import gradio as gr

from ..config import settings
from ..kb.services import get_services
from ..utils.validators import ensure_size_ok
from .progress import progress_html


def _lib_choices() -> list[str]:
    return [lib["name"] for lib in get_services().db.list_libraries()]


def _files_markdown(library: str) -> str:
    svc = get_services()
    files = svc.db.list_files(limit=50, library=library)
    if not files:
        return "（当前库为空，请先导入文档）"
    lines = ["| 文件 | 状态 | 块数 |", "| :--- | :--- | :--- |"]
    for f in files:
        chunks = svc.db.count_doc_chunks(f.id)
        display = f.source_name or f.name
        lines.append(f"| {display} | {f.status} | {chunks} |")
    return "\n".join(lines)


def _stats_markdown(library: str) -> str:
    svc = get_services()
    files = svc.db.list_files(limit=100000, library=library)
    total = len(files)
    indexed = sum(1 for f in files if f.status == "indexed")
    failed = sum(1 for f in files if f.status == "failed")
    dup = sum(1 for f in files if f.status == "duplicate")
    chunks = sum(svc.db.count_doc_chunks(f.id) for f in files)
    llm_ok = bool(settings.deepseek_api_key)
    return (
        f"- 当前库 **{library}**：文档 **{total}**（已索引 **{indexed}**，"
        f"失败 **{failed}**，重复 **{dup}**）\n"
        f"- 分块总数：**{chunks}**\n"
        f"- LLM：{'✅ 已就绪（' + settings.llm_model + '）' if llm_ok else '⚠️ 未配置（问答不可用，导入/检索可用）'}"
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


def build_kb_tab() -> None:
    """知识库 Tab 组件：在调用方上下文直接构建（左右布局）。"""
    gr.Markdown("### 💬 知识库问答")
    with gr.Column(elem_classes="two-pane"):
        # ---- 左窗：库管理 + 导入 ----
        with gr.Column(scale=1, min_width=320):
            gr.Markdown("### 🗂 知识库管理")
            with gr.Row():
                lib_dropdown = gr.Dropdown(
                    label="当前知识库（数据库）",
                    choices=_lib_choices(),
                    value=_lib_choices()[0] if _lib_choices() else None,
                    interactive=True,
                )
            with gr.Row():
                lib_new = gr.Textbox(
                    label="新建知识库名称", placeholder="例如：公司制度", scale=2
                )
                lib_create = gr.Button("➕ 新建", variant="primary", scale=1)
            with gr.Row():
                lib_delete = gr.Button("🗑 删除当前库")

            stats_md = gr.Markdown(_stats_markdown(lib_dropdown.value or "default"))

            gr.Markdown("### 📤 导入文档")
            upload = gr.File(
                label="可多选，导入当前库",
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
            file_table = gr.Markdown(_files_markdown(lib_dropdown.value or "default"))

        # ---- 右窗：问答 ----
        with gr.Column(scale=2):
            chatbot = gr.Chatbot(height=460, label="对话（支持追问）")
            ai_progress = gr.HTML(progress_html("", None))
            with gr.Row():
                kb_msg = gr.Textbox(
                    placeholder="例如：文档里关于 XX 是怎么说的？",
                    scale=4,
                    show_label=False,
                )
                kb_send = gr.Button("提问", variant="primary", scale=1)
                kb_clear = gr.Button("🗑 清空", scale=1)
            kb_answer = gr.Markdown("（回答会显示在这里，并附引用来源）")
            kb_history = gr.State([])  # [(user, assistant), ...]，供多轮追问上下文

    # ---- 事件 ----

    def _refresh_all(library: str):
        return (
            gr.update(choices=_lib_choices(), value=library),
            _files_markdown(library),
            _stats_markdown(library),
        )

    def on_lib_change(library: str):
        if not library:
            library = "default"
        return (
            _files_markdown(library),
            _stats_markdown(library),
            gr.update(choices=_lib_choices(), value=library),
        )

    def on_lib_create(name: str, current: str):
        if not name or not name.strip():
            return "（请输入库名）", gr.update()
        try:
            get_services().db.create_library(name.strip())
        except ValueError as exc:
            return f"❌ {exc}", gr.update()
        return (
            f"✅ 已创建知识库「{name.strip()}」，导入文档前请切换到该库。",
            gr.update(choices=_lib_choices(), value=current),
        )

    def on_lib_delete(library: str):
        if not library or library == "default":
            return "❌ 默认库不可删除", gr.update()
        count = get_services().ingest.delete_library(library)
        libs = _lib_choices()
        target = libs[0] if libs else "default"
        return (
            f"🗑 已删除知识库「{library}」（{count} 个文件）",
            gr.update(choices=libs, value=target),
            _files_markdown(target),
            _stats_markdown(target),
        )

    def on_ingest(files: Any, library: str):
        if not files:
            return "（请先选择文件）", gr.update()
        if isinstance(files, str):
            files = [files]
        svc = get_services()
        # 上传文件来自 gradio 临时目录（会被定期清理），先复制进 raw_dir
        raw_dir = svc.cfg.raw_dir
        raw_dir.mkdir(parents=True, exist_ok=True)
        staged: list[tuple[Path, str]] = []
        try:
            for f in files:
                src = Path(f)
                ensure_size_ok(src)
                display = src.name
                dest = raw_dir / display
                if dest.exists() and dest.stat().st_size == src.stat().st_size:
                    pass  # 同名同大小文件已存在，直接复用
                else:
                    dest = raw_dir / (
                        f"{src.stem}__{src.name}" if dest.exists() else display
                    )
                    shutil.copy2(src, dest)
                staged.append((dest, display))
        except Exception as exc:
            return f"❌ 文件暂存失败：{exc}", gr.update()
        results = svc.ingest.ingest_paths(
            [p for p, _ in staged], [n for _, n in staged], library=library
        )
        ok = sum(1 for r in results if r.get("status") == "indexed")
        lines = [f"✅ 成功导入 {ok}/{len(results)} 个文件 → 库「{library}」："]
        for r in results:
            mark = (
                "✅"
                if r.get("status") == "indexed"
                else "⚠️"
                if r.get("status") == "duplicate"
                else "❌"
            )
            display = r.get("source_name") or Path(r["path"]).name
            lines.append(
                f"- {mark} {display} → {r.get('status')}"
                + (f"（{r.get('error')}）" if r.get("error") else "")
            )
        return "\n".join(lines), _files_markdown(library)

    def on_refresh(library: str):
        return (
            _files_markdown(library),
            _stats_markdown(library),
            gr.update(choices=_lib_choices(), value=library),
        )

    def on_ask(question: str, history: list, library: str):
        if not question or not question.strip():
            yield gr.update(), history, progress_html("", None), ""
            return
        history = history or []
        yield gr.update(), history, progress_html("⏳ 正在检索知识库…", 20), ""
        svc = get_services()
        if not svc.llm_ready:
            yield gr.update(), history, progress_html("", None), (
                "⚠️ 未配置 LLM：请先在上方「模型配置」填写 DEEPSEEK_API_KEY。"
            )
            return
        # 只传最近 N 轮给 LLM 作上下文，避免历史无限膨胀
        turns = settings.kb_history_turns
        llm_history = [
            {"role": r, "content": c} for r, c in history[-turns * 2 :]
        ]
        yield gr.update(), history, progress_html("⏳ 已检索到相关内容，AI 正在回答…", 60), ""
        try:
            result = svc.qa.answer(
                question.strip(),
                history=llm_history,
                filters={"library": library},
            )
        except Exception as exc:
            yield gr.update(), history, progress_html("", None), f"❌ 出错了：{exc}"
            return
        if result.refused:
            answer = (
                f"🤔 {result.answer or '知识库中未找到相关内容，已拒答以免臆造。'}"
            )
        else:
            answer = _render_answer(result.answer, result)
        new_history = history + [(question.strip(), answer)]
        yield (
            gr.update(choices=_lib_choices(), value=library),
            new_history,
            progress_html("✅ 回答完成", 100),
            "",
        )

    def on_clear():
        return [], progress_html("", None), "（对话已清空）"

    lib_dropdown.change(
        on_lib_change, [lib_dropdown], [file_table, stats_md, lib_dropdown]
    )
    lib_create.click(
        on_lib_create, [lib_new, lib_dropdown], [ingest_status, lib_dropdown]
    )
    lib_delete.click(
        on_lib_delete,
        [lib_dropdown],
        [ingest_status, lib_dropdown, file_table, stats_md],
    )
    ingest_btn.click(on_ingest, [upload, lib_dropdown], [ingest_status, file_table])
    refresh_btn.click(on_refresh, [lib_dropdown], [file_table, stats_md, lib_dropdown])
    kb_send.click(
        on_ask,
        [kb_msg, kb_history, lib_dropdown],
        [lib_dropdown, chatbot, ai_progress, kb_answer],
    )
    kb_msg.submit(
        on_ask,
        [kb_msg, kb_history, lib_dropdown],
        [lib_dropdown, chatbot, ai_progress, kb_answer],
    )
    kb_clear.click(on_clear, [], [kb_history, chatbot, ai_progress, kb_answer])