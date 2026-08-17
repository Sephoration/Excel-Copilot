"""摄入管道：批量 / 增量 / MD5 去重 / 容错隔离 / 断点续传。

数据流：解析 → 切分 → 写 SQLite(indexing) → Embedding(批量+重试) → 写 ChromaDB → 置 indexed。
任一步失败 → 置 failed/embedding_failed，可从断点继续。

移植自 yannick_vault core/ingest.py，仅调整导入路径。
"""

from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import db as db_mod
from .db import ST_EMBEDDING_FAILED, ST_FAILED, ST_INDEXED, ST_INDEXING
from .indexer import Indexer
from .loader import load_document, supported_exts
from .logging import get_logger
from .splitter import split_blocks

log = get_logger("ingest")

# 嵌入请求全局串行，避免撞 API 限速
_embed_lock = threading.Lock()


class IngestEngine:
    def __init__(
        self, cfg, database: db_mod.Database, indexer: Indexer, embedder
    ) -> None:
        self.cfg = cfg
        self.db = database
        self.indexer = indexer
        self.embedder = embedder

    # ---------- 单文件处理 ----------
    def process_file(
        self,
        path: Path,
        source_name: str | None = None,
        library: str = db_mod.DEFAULT_LIBRARY,
    ) -> dict:
        """返回处理结果摘要。异常不抛出，记录到 file.status。"""
        start = time.time()
        path = Path(path)
        ext = path.suffix.lower()
        display_name = source_name or Path(path).name
        try:
            if ext not in self.cfg.ingest.get("allowed_exts", supported_exts()):
                return {
                    "path": str(path),
                    "source_name": display_name,
                    "library": library,
                    "status": "skipped",
                    "reason": "ext_not_allowed",
                }

            content_hash, mtime = self._hash_file(path)
            file, is_new = self.db.upsert_file(
                str(path),
                content_hash,
                mtime,
                ext,
                source_name=source_name,
                library=library,
            )

            if not is_new and file.status == ST_INDEXED:
                return {
                    "path": str(path),
                    "source_name": display_name,
                    "library": library,
                    "status": ST_INDEXED,
                    "skipped": True,
                }

            # 内容级去重：同一 hash 已被其它文件索引过
            dup = self.db.find_indexed_by_hash(content_hash, exclude_id=file.id)
            if dup:
                self.db.mark_file_status(
                    file.id,
                    db_mod.ST_DUPLICATE,
                    f"内容与 {getattr(dup, 'source_name', None) or dup.name} 重复",
                )
                return {
                    "path": str(path),
                    "source_name": display_name,
                    "library": library,
                    "status": db_mod.ST_DUPLICATE,
                }

            self.db.mark_file_status(file.id, ST_INDEXING)
            blocks = load_document(path, self.cfg)
            chunks = split_blocks(
                blocks,
                chunk_size=self.cfg.splitter.get("chunk_size", 800),
                overlap=self.cfg.splitter.get("overlap", 100),
                min_chunk_size=self.cfg.splitter.get("min_chunk_size", 60),
                max_parent_tokens=self.cfg.splitter.get("max_parent_tokens", 2000),
            )
            new_ids = self.db.replace_chunks(file.id, chunks)
            for c, new_id in zip(chunks, new_ids):
                c.id = new_id

            # 嵌入（批量 + 串行 + 指数退避重试）
            try:
                with _embed_lock:
                    embs = self.embedder.embed(
                        [c.text for c in chunks],
                        batch_size=self.cfg.embedding.get("batch_size", 64),
                    )
            except Exception as e:
                self.db.mark_file_status(file.id, ST_EMBEDDING_FAILED, str(e))
                log.error("embed_failed", file=str(path), error=str(e))
                return {
                    "path": str(path),
                    "source_name": display_name,
                    "library": library,
                    "status": ST_EMBEDDING_FAILED,
                    "error": str(e),
                }

            if len(embs) != len(chunks):
                raise ValueError(f"嵌入数量不匹配: {len(embs)} vs {len(chunks)}")
            # 先清旧向量再写新，避免幽灵块：文件重索引后旧向量不能留在向量库参与检索
            self.indexer.delete_doc(file.id)
            display_name = source_name or file.source_name or Path(path).name
            self.indexer.add_chunks(
                file.id, display_name, chunks, embs, library=library
            )
            self.db.mark_file_status(file.id, ST_INDEXED)
            self.db.set_meta("embedding_model", self.embedder.name)
            self.db.set_meta("embedding_dim", self.embedder.dim)
            dt = (time.time() - start) * 1000
            log.info(
                "file_indexed", file=str(path), chunks=len(chunks), ms=round(dt, 1)
            )
            return {
                "path": str(path),
                "source_name": display_name,
                "library": library,
                "status": ST_INDEXED,
                "chunks": len(chunks),
                "ms": round(dt, 1),
            }
        except Exception as e:
            # upsert_file 之前抛错时 file 未定义，不能用 file.id
            file = locals().get("file")
            if file is not None:
                self.db.mark_file_status(file.id, ST_FAILED, str(e))
            log.error("file_failed", path=str(path), error=str(e))
            return {
                "path": str(path),
                "source_name": display_name,
                "library": library,
                "status": ST_FAILED,
                "error": str(e),
            }

    # ---------- 批量 ----------
    def ingest_paths(
        self,
        paths: list[Path],
        source_names: list[str] | None = None,
        library: str = db_mod.DEFAULT_LIBRARY,
    ) -> list[dict]:
        results: list[dict] = []
        names = source_names if source_names is not None else [None] * len(paths)
        if len(names) != len(paths):
            names = [None] * len(paths)
        workers = self.cfg.ingest.get("workers", 8)
        if workers > 1 and len(paths) > 1:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {
                    ex.submit(self.process_file, p, names[i], library): (p, names[i])
                    for i, p in enumerate(paths)
                }
                for f in as_completed(futures):
                    try:
                        results.append(f.result())
                    except Exception as e:  # 理论不会发生
                        results.append(
                            {
                                "path": str(futures[f][0]),
                                "source_name": futures[f][1],
                                "library": library,
                                "status": ST_FAILED,
                                "error": str(e),
                            }
                        )
        else:
            for p, sn in zip(paths, names):
                results.append(self.process_file(p, sn, library))
        return results

    def scan_dir(self, directory: Path) -> list[Path]:
        exts = tuple(self.cfg.ingest.get("allowed_exts", supported_exts()))
        return sorted(
            p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() in exts
        )

    def ingest_directory(self, directory: Path) -> list[dict]:
        files = self.scan_dir(directory)
        log.info("ingest_dir_scan", dir=str(directory), files=len(files))
        return self.ingest_paths(files)

    def resume_failed(self) -> list[dict]:
        """断点续传：重试 failed / embedding_failed。"""
        files = self.db.files_needing_index()
        return self.ingest_paths([Path(f.path) for f in files])

    # ---------- 删除 ----------
    def delete_file(self, file_id: int) -> None:
        file = self.db.get_file(file_id)
        if not file:
            return
        self.indexer.delete_doc(file_id)
        self.db.delete_chunks_of_file(file_id)
        self.db.delete_file(file_id)
        if file.path.startswith(str(self.cfg.raw_dir)):
            try:
                Path(file.path).unlink(missing_ok=True)
            except OSError as e:  # pragma: no cover
                log.warning("raw_delete_failed", path=file.path, error=str(e))
        log.info("file_deleted", file_id=file_id, path=file.path)

    def delete_library(self, name: str) -> int:
        """删除整个库：向量 + 元数据 + 原始文件。返回删除的文件数。"""
        doc_ids = self.db.library_file_ids(name)
        raw_dir = self.cfg.raw_dir
        paths = []
        for fid in doc_ids:
            f = self.db.get_file(fid)
            if f and f.path.startswith(str(raw_dir)):
                paths.append(f.path)
        self.indexer.delete_docs(doc_ids)
        self.db.delete_library(name)
        for p in paths:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError as e:  # pragma: no cover
                log.warning("raw_delete_failed", path=p, error=str(e))
        log.info("library_deleted", library=name, files=len(doc_ids))
        return len(doc_ids)

    # ---------- 工具 ----------
    @staticmethod
    def _hash_file(path: Path) -> tuple[str, float]:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest(), path.stat().st_mtime
