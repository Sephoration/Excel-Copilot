"""ChromaDB 索引管理 —— 可重建的衍生索引（SSOT 在 SQLite）。

移植自 yannick_vault core/indexer.py，仅调整导入路径。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .db import Chunk
from .logging import get_logger

log = get_logger("indexer")

COLLECTION = "excel_copilot_kb"


class Indexer:
    def __init__(self, chroma_dir: Path, dim: int, schema_version: int) -> None:
        import chromadb

        self._client = chromadb.PersistentClient(path=str(chroma_dir))
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION,
            metadata={"hnsw:space": "cosine", "schema_version": schema_version},
        )
        self.dim = dim
        self._sqlite_path = Path(chroma_dir) / "chroma.sqlite3"

    # ---------- 增删 ----------
    def add_chunks(
        self,
        doc_id: int,
        file_name: str,
        chunks: list[Chunk],
        embeddings: list[list[float]],
        library: str = "default",
    ) -> None:
        if not chunks:
            return
        ids = [f"{doc_id}:{c.id}" for c in chunks]
        metas = [
            {
                "doc_id": doc_id,
                "chunk_id": c.id,
                "file_name": file_name,
                "chunk_index": c.index,
                "page": c.page,
                "library": library,
            }
            for c in chunks
        ]
        # 不存 documents：全文文本的 SSOT 在 kb.db，检索时经 get_chunk 取回。
        # 旧实现把 chunks 全文复制进 Chroma 的 FTS5 索引，chroma.sqlite3 体积
        # 膨胀到 KB 级（实测 99MB，向量本体仅 5MB），且无人读取该副本。
        self._collection.upsert(ids=ids, embeddings=embeddings, metadatas=metas)

    def delete_doc(self, doc_id: int) -> None:
        self._collection.delete(where={"doc_id": doc_id})

    def delete_docs(self, doc_ids: list[int]) -> None:
        """批量删除向量（删除整个知识库时用）。"""
        if not doc_ids:
            return
        self._collection.delete(where={"doc_id": {"$in": doc_ids}})

    def _has_documents(self) -> bool:
        """chroma.sqlite3 是否还存有旧版遗留的 documents（全文副本）。

        直接数 FTS5 表行数：O(1) 且精确，不受 `get(limit=1)` 返回顺序影响
        （混合状态下首条可能是无 documents 的记录，会误判跳过压缩）。
        """
        try:
            con = sqlite3.connect(f"file:{self._sqlite_path}?mode=ro", uri=True)
            try:
                row = con.execute(
                    "SELECT COUNT(*) FROM embedding_fulltext_search"
                ).fetchone()
                return bool(row and row[0] > 0)
            finally:
                con.close()
        except sqlite3.Error:
            return False  # 表不存在或读失败 → 视为无 documents

    def compact(self) -> dict:
        """压缩 chroma.sqlite3：重建集合，丢弃旧版遗留的 documents（全文副本）。

        旧版本 add_chunks 会把 chunk 全文写进 Chroma 的 FTS5 索引，导致
        sqlite 膨胀到 KB 级；SSOT 在 kb.db，此处向量+元数据原样重放、
        去掉 documents，再对 sqlite VACUUM 回收空闲页。
        向量直接复用已存 embedding，无需重新调用嵌入服务。
        """
        if not self._has_documents():
            return {"chroma": "already_compact", "removed": 0}

        data = self._collection.get(include=["embeddings", "metadatas"])
        ids, embs, metas = data["ids"], data["embeddings"], data["metadatas"]
        old_count = len(ids)
        if not old_count:
            return {"chroma": "empty", "removed": 0}

        self._client.delete_collection(COLLECTION)
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION,
            metadata={"hnsw:space": "cosine", "schema_version": 2},
        )
        # 分批重放，避免一次性大事务
        for i in range(0, old_count, 500):
            self._collection.add(
                ids=ids[i : i + 500],
                embeddings=embs[i : i + 500],
                metadatas=metas[i : i + 500],
            )
        # 回收 Chroma 删除旧集合后留下的空闲页（Chroma 自身不 VACUUM）
        try:
            con = sqlite3.connect(str(self._sqlite_path))
            try:
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                con.execute("VACUUM")
            finally:
                con.close()
        except sqlite3.OperationalError as e:  # pragma: no cover
            log.warning("chroma_vacuum_skipped", error=str(e))
        log.info(
            "chroma_compact_done",
            removed=old_count,
            sqlite_mb=round(self._sqlite_path.stat().st_size / 1e6, 1),
        )
        return {"chroma": "rebuilt", "removed": old_count}

    # ---------- 查询 ----------
    def query(
        self, embedding: list[float], n_results: int = 20, where: dict | None = None
    ) -> list[dict]:
        """返回 [{chunk_id, doc_id, file_name, page, chunk_index, score}]，score 为余弦相似度。"""
        if self._collection.count() == 0:
            return []
        kw = {}
        if where:
            kw["where"] = where
        resp = self._collection.query(
            query_embeddings=[embedding],
            n_results=n_results,
            include=["metadatas", "distances"],
            **kw,
        )
        out = []
        metas = resp.get("metadatas") or [[]]
        dists = resp.get("distances") or [[]]
        for m, d in zip(metas[0], dists[0]):
            out.append(
                {
                    "chunk_id": m["chunk_id"],
                    "doc_id": m["doc_id"],
                    "file_name": m.get("file_name"),
                    "page": m.get("page"),
                    "chunk_index": m.get("chunk_index"),
                    "score": 1.0 - float(d),  # cosine distance -> similarity
                }
            )
        return out

    def count(self) -> int:
        return self._collection.count()
