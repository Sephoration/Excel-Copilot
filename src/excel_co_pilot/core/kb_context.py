"""知识库检索注入器：把 RAG 命中结果转成提示词上下文。

表格分析时把知识库作为增强项注入（可选）：带超时、失败即降级，
进程内仅尝试一次，避免网络卡死阻塞表格分析主流程。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

_KB_SEARCH_TIMEOUT = 8  # 检索超时（秒）
_kb_retrieval_failed = False
_kb_executor: ThreadPoolExecutor | None = None

_FALLBACK = "（暂无相关知识参考）"


def _search(question: str) -> str:
    """在线程池中执行知识库检索，返回注入提示词的上下文（失败抛出异常）。"""
    from ..kb.services import get_services

    hits = get_services().retriever.hybrid_search(question, top_k=3)
    if not hits:
        return ""
    parts = [
        f"《{h.file_name}》{('第' + str(h.page) + '页') if h.page else ''}: {h.leaf.text[:300]}"
        for h in hits
    ]
    return "\n\n".join(parts)


def retrieve_kb_context(question: str) -> str:
    """检索知识库并返回注入上下文；失败/超时/未命中时返回兜底文案。

    进程级降级：首次失败后本进程不再重试（网络不可用时快速跳过）。
    """
    global _kb_retrieval_failed, _kb_executor
    if _kb_retrieval_failed:
        return _FALLBACK
    try:
        if _kb_executor is None:
            _kb_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="kb-search"
            )
        future = _kb_executor.submit(_search, question)
        ctx = future.result(timeout=_KB_SEARCH_TIMEOUT)
    except Exception:
        _kb_retrieval_failed = True
        return _FALLBACK
    return ctx or _FALLBACK


__all__ = ["retrieve_kb_context"]