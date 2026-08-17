"""RAG 问答：检索 → 改写反思 → 拒答判定 → LLM 生成 → 引用溯源。

移植自 yannick_vault core/qa_engine.py，仅调整导入路径。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field

from . import db as db_mod
from .logging import get_logger, set_trace_id
from .retriever import Hit, Retriever

log = get_logger("qa")

MAX_REFERENCE_CHARS = 800  # 每条引用的文本截断


@dataclass
class AnswerResult:
    question: str
    answer: str = ""
    citations: list[dict] = field(default_factory=list)
    refused: bool = False
    refuse_reason: str = ""
    hits: list[Hit] = field(default_factory=list)
    rewritten: list[str] = field(default_factory=list)
    trace_id: str = ""
    latency_ms: int = 0
    stages: dict = field(default_factory=dict)


class QAEngine:
    def __init__(
        self, cfg, database: db_mod.Database, retriever: Retriever, llm
    ) -> None:
        self.cfg = cfg
        self.db = database
        self.retriever = retriever
        self.llm = llm

    # ---------- 检索 + 改写反思 ----------
    def _retrieve_with_reflection(
        self, question: str, filters: dict | None, max_rounds: int = 2
    ) -> tuple[list[Hit], list[str]]:
        """首轮 Top-1 相似度过低时，用 LLM 改写生成变体问题再检，并集去重。"""
        hits = self.retriever.hybrid_search(question, filters=filters)
        rewritten: list[str] = []
        threshold = self.cfg.retrieval.get("query_rewrite_threshold", 0.62)
        for _ in range(max_rounds - 1):
            if not hits or hits[0].cosine >= threshold:
                break
            variants = self._generate_variants(question)
            if not variants:
                break
            rewritten.extend(variants)
            extra_hits: list[Hit] = []
            for v in variants:
                try:
                    extra_hits.extend(self.retriever.hybrid_search(v, filters=filters))
                except Exception as e:  # 单次改写失败不影响主流程
                    log.warning("rewrite_search_failed", error=str(e))
            merged = _merge_hits(hits + extra_hits)
            hits = merged
            if hits and hits[0].cosine >= threshold:
                break
        return hits, rewritten

    def _generate_variants(self, question: str) -> list[str]:
        n = self.cfg.retrieval.get("query_rewrite_variants", 3)
        prompt = (
            f"请针对用户问题生成 {n} 个同义/扩展的检索式问题，每行一个，只输出问题本身，"
            f"不要编号、不要多余文字。\n用户问题：{question}"
        )
        try:
            out = self.llm.complete(prompt, stream=False)
            if isinstance(out, Iterator):  # pragma: no cover
                out = "".join(out)
            lines = [
                ln.strip().strip("-").strip() for ln in out.splitlines() if ln.strip()
            ]
            return lines[:n]
        except Exception as e:
            log.warning("rewrite_llm_failed", error=str(e))
            return []

    # ---------- 拒答判定 ----------
    def should_refuse(self, hits: list[Hit]) -> tuple[bool, str]:
        if not hits:
            return True, "知识库中未找到相关文档。"
        cfg = self.cfg.refusal
        abs_th = cfg.get("absolute_threshold", 0.62)
        margin = cfg.get("margin", 0.10)
        top1 = hits[0].cosine
        if top1 < abs_th:
            diff = (hits[0].cosine - hits[1].cosine) if len(hits) > 1 else 0.0
            if diff < margin:
                return (
                    True,
                    f"相关内容置信度过低（Top-1 余弦 {top1:.2f}），为避免臆造已拒答。",
                )
        return False, ""

    # ---------- 上下文与 Prompt ----------
    def _build_context(self, hits: list[Hit]) -> tuple[str, list[dict]]:
        budget = self.cfg.chat.get("max_context_chars", 60000)
        parts: list[str] = []
        citations: list[dict] = []
        used = 0
        for i, h in enumerate(hits, start=1):
            text = h.parent.text if h.parent else h.leaf.text
            text = text[:MAX_REFERENCE_CHARS]
            page = f"第{h.page}页" if h.page else "无页码"
            item = f"[{i}] 《{h.file_name}》{page}:\n{text}"
            if used and used + len(item) > budget:
                break
            parts.append(item)
            used += len(item)
            citations.append(
                {
                    "id": i,
                    "file_name": h.file_name,
                    "page": h.page,
                    "chunk_index": h.chunk_index,
                    "text": text[:300],
                    "similarity": round(h.cosine, 4),
                    "chunk_id": h.chunk_id,
                }
            )
        context = "\n\n".join(parts)
        return context, citations

    def build_prompt(
        self, question: str, context: str, history: list[dict] | None = None
    ) -> str:
        return (
            "你是一个基于事实的知识助手。请根据【参考资料】回答，并在引用处标注 [1][2] 编号。\n"
            "若参考资料无相关信息，直接回答：知识库中没有找到相关信息，请上传相关文档后再提问。\n"
            "不得编造参考资料中没有的事实。回答使用与用户问题相同的语言。\n\n"
            f"【参考资料】\n{context}\n\n"
            f"【用户问题】\n{question}\n\n"
            "【回答】"
        )

    # ---------- 主入口 ----------
    def prepare(
        self,
        question: str,
        history: list[dict] | None = None,
        filters: dict | None = None,
    ) -> tuple[AnswerResult, str, list[dict]]:
        """返回 (result, prompt, context)；result 尚未填 answer。"""
        trace_id = uuid.uuid4().hex[:12]
        set_trace_id(trace_id)
        t0 = time.time()

        hits, rewritten = self._retrieve_with_reflection(question, filters)
        refused, reason = self.should_refuse(hits)

        result = AnswerResult(
            question=question,
            refused=refused,
            refuse_reason=reason,
            hits=hits,
            rewritten=rewritten,
            trace_id=trace_id,
        )
        result.stages["retrieve_ms"] = int((time.time() - t0) * 1000)
        if refused:
            result.latency_ms = int((time.time() - t0) * 1000)
            return result, "", ""

        context, citations = self._build_context(hits)
        result.citations = citations
        prompt = self.build_prompt(question, context, history)
        return result, prompt, context

    def finalize(self, result: AnswerResult, answer: str, t0: float) -> AnswerResult:
        result.answer = answer
        result.latency_ms = int((time.time() - t0) * 1000)
        result.stages["total_ms"] = result.latency_ms
        set_trace_id(None)
        return result

    def answer(
        self,
        question: str,
        history: list[dict] | None = None,
        filters: dict | None = None,
    ) -> AnswerResult:
        t0 = time.time()
        result, prompt, _ = self.prepare(question, history, filters)
        if result.refused:
            return self.finalize(result, result.refuse_reason, t0)
        answer = self.llm.complete(prompt, history=history, stream=False)
        if isinstance(answer, Iterator):  # pragma: no cover
            answer = "".join(answer)
        return self.finalize(result, answer, t0)

    def answer_stream(
        self,
        question: str,
        history: list[dict] | None = None,
        filters: dict | None = None,
    ) -> Iterator[AnswerResult | str]:
        """流式生成：先产出 delta 文本，最后产出 AnswerResult。"""
        t0 = time.time()
        result, prompt, _ = self.prepare(question, history, filters)
        if result.refused:
            result = self.finalize(result, result.refuse_reason, t0)
            log.info(
                "qa_completed",
                trace_id=result.trace_id,
                refused=True,
                stages=result.stages,
                hits=len(result.hits),
            )
            yield result
            return
        full: list[str] = []
        for delta in self.llm.complete(prompt, history=history, stream=True):
            if isinstance(delta, str):
                full.append(delta)
                yield delta
        result = self.finalize(result, "".join(full), t0)
        log.info(
            "qa_completed",
            trace_id=result.trace_id,
            refused=False,
            stages=result.stages,
            hits=len(result.hits),
        )
        yield result


def _merge_hits(hits: list[Hit]) -> list[Hit]:
    """按 chunk_id 去重并保持顺序（保留首个出现；score 与 cosine 各取最大）。"""
    best: dict[int, Hit] = {}
    for h in hits:
        if h.chunk_id not in best:
            best[h.chunk_id] = h
            continue
        cur = best[h.chunk_id]
        cur.score = max(cur.score, h.score)
        cur.cosine = max(cur.cosine, h.cosine)
    order = {h.chunk_id: i for i, h in enumerate(hits)}
    merged = sorted(best.values(), key=lambda h: order[h.chunk_id])
    return merged
