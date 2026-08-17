"""ChromaDB 索引管理 —— 可重建的衍生索引（SSOT 在 SQLite）。

移植自 yannick_vault core/indexer.py，仅调整导入路径。
"""

from __future__ import annotations

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
        docs = [c.text for c in chunks]
        self._collection.upsert(
            ids=ids, embeddings=embeddings, documents=docs, metadatas=metas
        )

    def delete_doc(self, doc_id: int) -> None:
        self._collection.delete(where={"doc_id": doc_id})

    def delete_docs(self, doc_ids: list[int]) -> None:
        """批量删除向量（删除整个知识库时用）。"""
        if not doc_ids:
            return
        self._collection.delete(where={"doc_id": {"$in": doc_ids}})

    def delete_chunk(self, chunk_id: int) -> None:
        """按 chunk_id 删除向量（chunk_id 在 DB 内全局唯一）。"""
        self._collection.delete(where={"chunk_id": chunk_id})

    def reset(self) -> None:
        self._client.delete_collection(COLLECTION)
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )

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
