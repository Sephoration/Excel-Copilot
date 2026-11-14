"""混合检索：语义(向量) + 关键词(BM25) → RRF 融合 → 重排 → 父子块展开。

移植自 yannick_vault core/retriever.py，仅调整导入路径。
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path

import jieba

from . import db as db_mod
from .db import Chunk
from .logging import get_logger

log = get_logger("retriever")

_STOP = {
    "的",
    "了",
    "和",
    "是",
    "在",
    "我",
    "有",
    "就",
    "不",
    "人",
    "都",
    "一",
    "一个",
    "上",
    "也",
    "很",
    "到",
    "说",
    "要",
    "去",
    "你",
    "会",
    "着",
    "没有",
    "看",
    "好",
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "to",
    "of",
    "and",
    "in",
    "on",
    "for",
    "with",
    "it",
    "this",
    "that",
    "at",
    "by",
    "from",
}


def _tokenize(text: str) -> list[str]:
    return [w for w in jieba.lcut(text) if w.strip() and not _is_punct(w)]


def _is_punct(w: str) -> bool:
    return bool(re.fullmatch(r"[\s\W_]+", w, flags=re.UNICODE))


class SparseIndex:
    """BM25 稀疏索引，懒构建 + 增量追加。

    线程安全：重建加锁 + 双检，避免「启动预热」与「首次提问」同时重建
    （旧实现会并发重建两份 35s+ 的索引，导致首问长时间无响应）。
    """

    def __init__(self, database: db_mod.Database) -> None:
        self.db = database
        self._bm25 = None
        self._chunk_ids: list[int] = []
        self._known_count: int | None = None
        self._rebuild_lock = threading.Lock()
        self._load_userdict()

    def _load_userdict(self) -> None:
        try:
            ud = Path(self.db.db_path).parent.parent / "userdict.txt"
            if ud.exists():
                jieba.load_userdict(str(ud))
        except Exception:  # pragma: no cover
            pass

    def _ensure(self) -> None:
        with self._rebuild_lock:
            count = self.db.count_indexed_chunks()
            if self._bm25 is None or count != self._known_count:
                self._rebuild_unlocked()

    def rebuild(self) -> None:
        """全量重建；若其他线程已重建到最新则直接跳过。"""
        with self._rebuild_lock:
            count = self.db.count_indexed_chunks()
            if self._bm25 is not None and count == self._known_count:
                return
            self._rebuild_unlocked()

    def _rebuild_unlocked(self) -> None:
        from rank_bm25 import BM25Okapi

        chunks = self.db.all_chunks_for_bm25()
        self._chunk_ids = [c.id for c in chunks]
        corpus = [_tokenize(c.text) for c in chunks]
        self._bm25 = BM25Okapi(corpus) if corpus else None
        self._known_count = len(chunks)
        log.info("bm25_built", chunks=len(chunks))

    def search(self, query: str, k: int | None = 20) -> list[tuple[int, float]]:
        self._ensure()
        if self._bm25 is None:
            return []
        tokens = _tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [
            (self._chunk_ids[i], float(scores[i])) for i in ranked[:k] if scores[i] > 0
        ]


@dataclass
class Hit:
    chunk_id: int
    doc_id: int
    file_name: str
    page: int | None
    chunk_index: int
    score: float  # 排序分：重排分；未重排/Noop 重排时为 RRF 分
    leaf: Chunk
    parent: Chunk | None
    cosine: float = (
        0.0  # 置信度：真实重排返回置信度分数时取重排分，否则 dense 余弦相似度
    )


def rrf_fuse(
    dense: list[tuple[int, float]], sparse: list[tuple[int, float]], k: int = 60
) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion。输入为 (chunk_id, score) 有序列表。"""
    scores: dict[int, float] = {}
    for i, (cid, _) in enumerate(dense):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + i + 1)
    for i, (cid, _) in enumerate(sparse):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + i + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


class Retriever:
    def __init__(
        self, cfg, database: db_mod.Database, indexer, embedder, reranker
    ) -> None:
        self.cfg = cfg
        self.db = database
        self.indexer = indexer
        self.embedder = embedder
        self.reranker = reranker
        self.sparse = SparseIndex(database)

    def _metadata_where(self, filters: dict | None) -> dict | None:
        """把高层过滤器转成 ChromaDB where。支持 library / file_name / file_names / doc_ids。"""
        if not filters:
            return None
        clauses = []
        if filters.get("library"):
            clauses.append({"library": {"$eq": filters["library"]}})
        if filters.get("file_name"):
            clauses.append({"file_name": {"$eq": filters["file_name"]}})
        if filters.get("file_names"):
            clauses.append({"file_name": {"$in": list(filters["file_names"])}})
        if filters.get("doc_ids"):
            clauses.append({"doc_id": {"$in": [int(d) for d in filters["doc_ids"]]}})
        if len(clauses) == 0:
            return None
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}

    def _resolve_allowed_doc_ids(self, filters: dict | None) -> set[int] | None:
        """Resolve all supplied filters with AND semantics, matching vector metadata."""
        if not filters:
            return None
        allowed: set[int] | None = None

        def intersect(ids: set[int]) -> None:
            nonlocal allowed
            allowed = ids if allowed is None else allowed & ids

        if filters.get("library"):
            intersect(set(self.db.library_file_ids(filters["library"])))
        if "doc_ids" in filters and filters["doc_ids"] is not None:
            values = filters["doc_ids"]
            if not isinstance(values, (list, tuple, set)):
                raise ValueError("doc_ids 必须为列表")
            intersect({int(value) for value in values})

        names: set[str] | None = None
        if filters.get("file_name"):
            names = {str(filters["file_name"])}
        if "file_names" in filters and filters["file_names"] is not None:
            values = filters["file_names"]
            if not isinstance(values, (list, tuple, set)):
                raise ValueError("file_names 必须为列表")
            candidates = {str(value) for value in values}
            names = candidates if names is None else names & candidates
        if names is not None:
            matches: set[int] = set()
            offset = 0
            while names:
                batch = self.db.list_files(status=db_mod.ST_INDEXED, limit=1000, offset=offset)
                for record in batch:
                    display = getattr(record, "source_name", None) or record.name
                    if display in names:
                        matches.add(record.id)
                if len(batch) < 1000:
                    break
                offset += len(batch)
            intersect(matches)
        return allowed

    def hybrid_search(
        self,
        query: str,
        top_k: int | None = None,
        filters: dict | None = None,
        use_rerank: bool | None = None,
    ) -> list[Hit]:
        r = self.cfg.retrieval
        top_k = top_k or r.get("final_top_k", 5)
        use_rerank = r.get("use_rerank", True) if use_rerank is None else use_rerank
        allowed_doc_ids = self._resolve_allowed_doc_ids(filters)
        if allowed_doc_ids is not None and not allowed_doc_ids:
            return []
        where = self._metadata_where(filters)

        # 1) 语义 Top-K（score 即余弦相似度）
        emb = self.embedder.embed([query])[0]
        dense_raw = self.indexer.query(
            emb, n_results=r.get("dense_top_k", 20), where=where
        )
        dense = [(d["chunk_id"], d["score"]) for d in dense_raw]
        cosine_map = {d["chunk_id"]: d["score"] for d in dense_raw}

        # 2) 关键词 Top-K（BM25 不支持下推 where，按允许 doc_id 后过滤）
        sparse_limit = r.get("sparse_top_k", 20)
        sparse = self.sparse.search(query, k=None if allowed_doc_ids is not None else sparse_limit)
        if allowed_doc_ids is not None:
            if not allowed_doc_ids:
                return []  # 指定了 doc_ids/file_names 但无匹配文档
            sparse = [
                x
                for x in sparse
                if (c := self.db.get_chunk(x[0])) is not None
                and c.doc_id in allowed_doc_ids
            ][:sparse_limit]
            dense = [
                pair for pair in dense
                if (chunk := self.db.get_chunk(pair[0])) is not None
                and chunk.doc_id in allowed_doc_ids
            ]

        # 3) RRF 融合取 Top-15
        fused = rrf_fuse(dense, sparse, k=r.get("rrf_k", 60))[: r.get("rrf_top_k", 15)]
        # 4) 重排
        conf_reranker = use_rerank and getattr(
            self.reranker, "confidence_scores", False
        )
        if use_rerank and fused:
            fetched = {}
            for cid, _s in fused:
                fetched[cid] = self.db.get_chunk(cid)
            pairs = [(fetched[cid].text if fetched[cid] else "", s) for cid, s in fused]
            reranked = self.reranker.rerank(query, pairs, top_k=min(top_k, len(fused)))
            chosen = [(fused[i][0], fused[i][1], rs) for i, rs in reranked]
        else:
            chosen = [(cid, s, s) for cid, s in fused[:top_k]]

        # 5) 父子块展开
        hits: list[Hit] = []
        for cid, rrf_score, final_score in chosen:
            leaf = self.db.get_chunk(cid)
            if not leaf:
                continue
            file = self.db.get_file(leaf.doc_id)
            if file is None or file.status != db_mod.ST_INDEXED:
                continue
            parent = self.db.get_parent_chunk(cid)
            hits.append(
                Hit(
                    chunk_id=cid,
                    doc_id=leaf.doc_id,
                    file_name=(getattr(file, "source_name", None) or file.name)
                    if file
                    else str(leaf.doc_id),
                    page=leaf.page,
                    chunk_index=leaf.index,
                    score=final_score,
                    leaf=leaf,
                    parent=parent,
                    cosine=(final_score if conf_reranker else cosine_map.get(cid, 0.0)),
                )
            )
        return hits
