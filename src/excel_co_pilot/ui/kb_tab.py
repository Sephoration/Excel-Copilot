"""知识库（KB / RAG）Gradio Tab：环境导航、数据库详情、引用溯源问答。

- 左窗顶部：拉伸的环境窗格（点击切换数据库环境）+「新建环境」「数据库设置」按键
- 左窗中部：当前环境简介（仅显示文件数量）
- 左窗详情（点「数据库设置」展开）：文件列表 / 导入 / 新建 / 删除环境
- 右窗：问答窗口（带边框美化 + 百分比进度提示：检索 / 生成各阶段实时刷新）
- 与单次问答共用同一 LLM 配置（DEEPSEEK_API_KEY）
"""

from __future__ import annotations

import queue
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import gradio as gr

from ..config import settings
from ..kb.services import get_services
from ..utils.validators import ensure_size_ok
from .progress import progress_html


def _lib_choices() -> list[str]:
    return [lib["name"] for lib in get_services().db.list_libraries()]


def _brief_markdown(library: str) -> str:
    """环境简介：只显示文件数量，不显示具体文件名。"""
    svc = get_services()
    total = len(svc.db.list_files(limit=100000, library=library))
    return (
        f"📊 当前环境 **{library}** · 共 **{total}** 个文件\n"
        "（点下方「⚙️ 数据库设置」查看每个文件详情）"
    )


def _files_markdown(library: str) -> str:
    svc = get_services()
    files = svc.db.list_files(limit=50, library=library)
    if not files:
        return "（当前环境为空，请先导入文档）"
    lines = ["| 文件 | 状态 | 块数 |", "| :--- | :--- | :--- |"]
    for f in files:
        chunks = svc.db.count_doc_chunks(f.id)
        display = f.source_name or f.name
        lines.append(f"| {display} | {f.status} | {chunks} |")
    return "\n".join(lines)


def _stats_markdown(library: str) -> str:
    """库统计：单行规整摘要，避免散落的多个数值。"""
    svc = get_services()
    files = svc.db.list_files(limit=100000, library=library)
    total = len(files)
    indexed = sum(1 for f in files if f.status == "indexed")
    failed = sum(1 for f in files if f.status == "failed")
    dup = sum(1 for f in files if f.status == "duplicate")
    chunks = sum(svc.db.count_doc_chunks(f.id) for f in files)
    return (
        f"📚 文档 **{total}**（✅ {indexed} / ❌ {failed} / 🔁 {dup}）"
        f" · 🧩 分块 **{chunks}** · {_llm_status()}"
    )


def _llm_status() -> str:
    svc = get_services()
    if not svc.llm_ready:
        return "LLM ⚠️ 未配置"
    if settings.kb_llm_provider == "ollama":
        return f"LLM ✅ 本地 {settings.ollama_model}"
    return f"LLM ✅ {settings.llm_model}"


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
    gr.Markdown(
        "<span class='section-title'>📚 知识库问答</span>"
        "<span class='section-sub' style='display:block'>把文档批量导入 RAG 数据库，提问先检索再回答，带引用溯源</span>",
        elem_classes="section-head",
    )
    with gr.Column(elem_classes="two-pane"):
        # ---- 左窗：环境导航 + 数据库详情 ----
        with gr.Column(scale=1, min_width=300, elem_classes="kb-side"):
            gr.Markdown("### 🗂 环境", elem_classes="kb-side-head")
            # 拉伸的环境窗格：每个环境一个 pane，点击切换
            env_radio = gr.Radio(
                choices=_lib_choices(),
                value=_lib_choices()[0] if _lib_choices() else "default",
                label="点击窗格切换环境",
                elem_classes="env-panes",
            )
            with gr.Row():
                new_open_btn = gr.Button("➕ 新建环境", size="sm", scale=1)
                settings_btn = gr.Button("⚙️ 数据库设置", size="sm", scale=1)
            create_row = gr.Row(visible=False)
            with create_row:
                new_name = gr.Textbox(
                    placeholder="新环境名称，例如：公司制度",
                    show_label=False,
                    scale=4,
                )
                new_ok = gr.Button("创建", size="sm", scale=1)
            # 当前环境简介（仅文件数量）
            brief_md = gr.Markdown(_brief_markdown(env_radio.value or "default"))
            # 数据库详情（默认隐藏，点「数据库设置」展开）
            detail_visible = gr.State(False)
            with gr.Column(visible=False, elem_classes="kb-detail") as detail_col:
                gr.Markdown("### 📄 数据库详情", elem_classes="kb-side-head")
                stats_md = gr.Markdown(_stats_markdown(env_radio.value or "default"))
                detail_table = gr.Markdown(
                    _files_markdown(env_radio.value or "default")
                )
                gr.Markdown("### 📤 导入文档", elem_classes="kb-side-head")
                upload = gr.File(
                    label="可多选，导入当前环境",
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
                    ingest_btn = gr.Button(
                        "🚀 导入并建立索引", variant="primary", size="sm"
                    )
                    compact_btn = gr.Button("🧹 压缩", size="sm")
                    refresh_btn = gr.Button("🔄 刷新", size="sm")
                ingest_status = gr.Markdown("")
                delete_btn = gr.Button("🗑 删除当前环境", variant="stop", size="sm")

        # ---- 右窗：问答（美化边框） ----
        with gr.Column(scale=2):
            gr.Markdown("### 💬 知识库问答", elem_classes="kb-side-head")
            with gr.Column(elem_classes="kb-chat-box"):
                chatbot = gr.Chatbot(height=460, label="对话（支持追问）")
                ai_progress = gr.HTML(progress_html("", None))
                with gr.Row(elem_classes="chat-input-sticky"):
                    kb_msg = gr.Textbox(
                        placeholder="例如：文档里关于 XX 是怎么说的？",
                        scale=5,
                        show_label=False,
                    )
                    kb_send = gr.Button("发送", variant="primary", scale=1)
                    kb_clear = gr.Button("清空", size="sm", scale=0)
                kb_answer = gr.Markdown(
                    "（回答会显示在这里，并附引用来源）", elem_classes="kb-answer"
                )
            kb_history = gr.State([])  # [(user, assistant), ...]，供多轮追问上下文

    # ---- 事件 ----

    def on_new_toggle(visible: bool):
        return gr.update(visible=not visible), not visible

    def on_create(name: str, current: str):
        if not name or not name.strip():
            return (
                "（请输入环境名）",
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
            )
        try:
            get_services().db.create_library(name.strip())
        except ValueError as exc:
            return (
                f"❌ {exc}",
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
            )
        libs = _lib_choices()
        new_val = name.strip()
        return (
            f"✅ 已创建环境「{new_val}」，导入文档前请先切换到该环境。",
            gr.update(choices=libs, value=new_val),
            gr.update(visible=False),
            gr.update(value=""),
            _brief_markdown(new_val),
            _files_markdown(new_val),
            _stats_markdown(new_val),
        )

    def on_delete(library: str):
        if not library or library == "default":
            return (
                "❌ 默认环境不可删除",
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
            )
        count = get_services().ingest.delete_library(library)
        get_services().refresh_indexes()  # 删除后同步刷新检索索引
        libs = _lib_choices()
        target = libs[0] if libs else "default"
        return (
            f"🗑 已删除环境「{library}」（{count} 个文件）",
            gr.update(choices=libs, value=target),
            _brief_markdown(target),
            _files_markdown(target),
            _stats_markdown(target),
        )

    def on_env_change(library: str):
        if not library:
            library = "default"
        return (
            _brief_markdown(library),
            _files_markdown(library),
            _stats_markdown(library),
            gr.update(choices=_lib_choices(), value=library),
        )

    def on_refresh(library: str):
        return on_env_change(library)

    def on_ingest(files: Any, library: str):
        """导入并建立索引：逐文件产出进度百分比。"""
        if not files:
            yield "（请先选择文件）", gr.update()
            return
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
            yield f"❌ 文件暂存失败：{exc}", gr.update()
            return
        paths = [p for p, _ in staged]
        names = [n for _, n in staged]
        total = len(paths)
        results: list[dict] = []
        for done, total_n, r in svc.ingest.ingest_paths_iter(
            paths, names, library=library
        ):
            results.append(r)
            pct = int(done / total_n * 100)
            yield (
                f"⏳ 正在索引 **{done}/{total_n}** 个文件…（{pct}%）",
                gr.update(),
            )
        # 导入完成立即重建 BM25：数据库时刻保持可检索状态，不用等下次提问
        svc.refresh_indexes()
        ok = sum(1 for r in results if r.get("status") == "indexed")
        lines = [f"✅ 成功导入 {ok}/{total} 个文件 → 环境「{library}」："]
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
        yield "\n".join(lines), _files_markdown(library)

    def on_compact(library: str):
        """压缩存储：重建 Chroma 集合去掉重复全文 + SQLite VACUUM。"""
        try:
            result = get_services().compact()
        except Exception as exc:
            return f"❌ 压缩失败：{exc}", gr.update(), gr.update(), gr.update()
        chroma = result.get("chroma", "-")
        kb_db = result.get("kb_db", "-")
        return (
            f"🧹 压缩完成：Chroma={chroma}，kb.db={kb_db}"
            + (
                f"，移除 {result['removed']} 条重复全文"
                if result.get("removed")
                else ""
            ),
            _files_markdown(library),
            _stats_markdown(library),
            gr.update(choices=_lib_choices(), value=library),
        )

    def _to_messages(history: list) -> list[dict]:
        """Gradio 6 Chatbot 只接受 messages 格式：[(u, a), ...] → dict 列表。"""
        msgs: list[dict] = []
        for u, a in history:
            msgs.append({"role": "user", "content": u})
            msgs.append({"role": "assistant", "content": a})
        return msgs

    def on_ask(question: str, history: list, library: str):
        """问答：后台线程跑检索+生成，主线程每秒刷新百分比进度。

        - 检索阶段：10% 起步随耗时推进到 45%，每秒显示「已用 Ns」；
        - 生成阶段：50% 起步随耗时推进到 95%，同时显示生成字数；
        - 动画进度条表示"正在工作中"，避免误以为卡死。
        """
        if not question or not question.strip():
            yield (
                gr.update(),
                _to_messages(history or []),
                progress_html("", None),
                "",
                history or [],
            )
            return
        history = history or []
        yield (
            gr.update(),
            _to_messages(history),
            progress_html("⏳ 正在检索知识库… 10%", 10, animate=True),
            "",
            history,
        )
        svc = get_services()
        if not svc.llm_ready:
            yield (
                gr.update(),
                _to_messages(history),
                progress_html("", None),
                ("⚠️ 未配置 LLM：请先在上方「模型配置」填写 DEEPSEEK_API_KEY。"),
                history,
            )
            return
        # 只传最近 N 轮给 LLM 作上下文，避免历史无限膨胀
        turns = settings.kb_history_turns
        llm_history = [{"role": r, "content": c} for r, c in history[-turns * 2 :]]

        _END = object()
        q: queue.Queue = queue.Queue()

        def _run() -> None:
            try:
                for item in svc.qa.answer_stream(
                    question.strip(),
                    history=llm_history,
                    filters={"library": library},
                ):
                    q.put(item)
                q.put(_END)
            except Exception as exc:
                q.put(exc)

        threading.Thread(target=_run, daemon=True).start()

        t0 = time.time()
        partial = ""
        result = None

        # 阶段 1：检索（等首个输出；refused 时首个即 AnswerResult）
        while result is None and not partial:
            try:
                first = q.get(timeout=1.0)
            except queue.Empty:
                el = int(time.time() - t0)
                pct = min(45, 10 + el)  # 10% 起，随耗时推进到 45%
                yield (
                    gr.update(),
                    _to_messages(history),
                    progress_html(
                        f"⏳ 正在检索知识库… {pct}%（已用 {el}s）", pct, animate=True
                    ),
                    "",
                    history,
                )
                continue
            if first is _END:
                break
            if isinstance(first, Exception):
                yield (
                    gr.update(),
                    _to_messages(history),
                    progress_html("", None),
                    (f"❌ 出错了：{first}"),
                    history,
                )
                return
            if isinstance(first, str):
                partial = first
            else:
                result = first

        if result is not None:
            # 已拒答（检索未命中）
            reason = result.refuse_reason or "知识库中未找到相关内容，已拒答以免臆造。"
            answer = f"🤔 {reason}"
            new_history = history + [(question.strip(), answer)]
            yield (
                gr.update(choices=_lib_choices(), value=library),
                _to_messages(new_history),
                progress_html("✅ 回答完成 100%", 100),
                answer,
                new_history,
            )
            return

        # 阶段 2：流式生成
        while True:
            try:
                item = q.get(timeout=0.4)
            except queue.Empty:
                el = int(time.time() - t0)
                pct = min(95, 50 + el)  # 50% 起，随耗时推进
                yield (
                    gr.update(),
                    _to_messages(history + [(question.strip(), partial)]),
                    progress_html(
                        f"⏳ AI 正在生成回答… {pct}%（已用 {el}s，{len(partial)} 字）",
                        pct,
                        animate=True,
                    ),
                    "",
                    history,
                )
                continue
            if item is _END:
                break
            if isinstance(item, Exception):
                partial += f"\n\n❌ 生成出错：{item}"
                break
            if isinstance(item, str):
                partial += item
            else:
                result = item
            yield (
                gr.update(),
                _to_messages(history + [(question.strip(), partial)]),
                progress_html(
                    f"⏳ AI 正在生成回答… 55%（{len(partial)} 字）", 55, animate=True
                ),
                "",
                history,
            )

        if result is None:
            answer = partial or "（生成中断）"
        elif result.refused:
            answer = f"🤔 {result.refuse_reason or '未找到相关内容，已拒答。'}"
        else:
            answer = _render_answer(result.answer or partial, result)
        new_history = history + [(question.strip(), answer)]
        yield (
            gr.update(choices=_lib_choices(), value=library),
            _to_messages(new_history),
            progress_html("✅ 回答完成 100%", 100),
            answer,
            new_history,
        )

    def on_clear():
        return [], [], progress_html("", None), "（对话已清空）"

    # ---- 绑定 ----
    create_row_visible = gr.State(False)
    env_radio.change(
        on_env_change,
        [env_radio],
        [brief_md, detail_table, stats_md, env_radio],
    )
    new_open_btn.click(
        on_new_toggle, [create_row_visible], [create_row, create_row_visible]
    )
    new_ok.click(
        on_create,
        [new_name, env_radio],
        [
            ingest_status,
            env_radio,
            create_row,
            new_name,
            brief_md,
            detail_table,
            stats_md,
        ],
    )
    settings_btn.click(
        lambda v: (gr.update(visible=not v), not v),
        [detail_visible],
        [detail_col, detail_visible],
    )
    delete_btn.click(
        on_delete,
        [env_radio],
        [ingest_status, env_radio, brief_md, detail_table, stats_md],
    )
    # 导入是分钟级长任务：逐文件产出进度（generator），不靠 Gradio 角标
    ingest_btn.click(
        on_ingest,
        [upload, env_radio],
        [ingest_status, detail_table],
        show_progress="hidden",
    )
    refresh_btn.click(
        on_refresh, [env_radio], [brief_md, detail_table, stats_md, env_radio]
    )
    compact_btn.click(
        on_compact,
        [env_radio],
        [ingest_status, detail_table, stats_md, env_radio],
    )
    kb_send.click(
        on_ask,
        [kb_msg, kb_history, env_radio],
        [env_radio, chatbot, ai_progress, kb_answer, kb_history],
        show_progress="hidden",
    )
    kb_msg.submit(
        on_ask,
        [kb_msg, kb_history, env_radio],
        [env_radio, chatbot, ai_progress, kb_answer, kb_history],
        show_progress="hidden",
    )
    kb_clear.click(on_clear, [], [kb_history, chatbot, ai_progress, kb_answer])
