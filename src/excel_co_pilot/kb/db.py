"""SQLite 访问层 —— 单一事实源（SSOT）。

文件元数据、块、库记录都落在这里；ChromaDB 只是可重建的衍生索引。
移植自 yannick_vault core/db.py，无外部依赖改动。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 文件状态机
ST_PENDING = "pending"
ST_INDEXING = "indexing"
ST_INDEXED = "indexed"
ST_DUPLICATE = "duplicate"
ST_FAILED = "failed"
ST_EMBEDDING_FAILED = "embedding_failed"

DEFAULT_LIBRARY = "default"


@dataclass
class Chunk:
    id: int
    doc_id: int
    parent_id: int | None
    index: int
    text: str
    page: int | None = None
    token_len: int = 0
    meta: dict = field(default_factory=dict)


@dataclass
class FileRecord:
    id: int
    path: str
    name: str
    source_name: str | None = None
    ext: str = ""
    content_hash: str = ""
    mtime: float = 0.0
    status: str = ST_PENDING
    error_msg: str | None = None
    created_at: float = 0.0
    library: str = DEFAULT_LIBRARY


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # busy_timeout 必须先于 journal_mode=WAL：WAL 切换需要独占锁，
        # 若另一实例正在持有写锁，默认 0 超时会立即抛错而非等待；
        # 先设 busy_timeout 保证此时是「等锁」而不是「失败/挂起」。
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    # ---------- schema ----------
    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    name TEXT NOT NULL,
                    ext TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    mtime REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error_msg TEXT,
                    created_at REAL NOT NULL,
                    library TEXT NOT NULL DEFAULT 'default'
                );
                CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
                CREATE INDEX IF NOT EXISTS idx_files_hash ON files(content_hash);

                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    parent_id INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    page INTEGER,
                    token_len INTEGER NOT NULL DEFAULT 0,
                    meta TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
                CREATE INDEX IF NOT EXISTS idx_chunks_parent ON chunks(parent_id);

                CREATE TABLE IF NOT EXISTS kb_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS libraries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL
                );
                """
            )
            # 轻量迁移：老库补 source_name / library 列
            cols = [
                r["name"]
                for r in self._conn.execute("PRAGMA table_info(files)").fetchall()
            ]
            if "source_name" not in cols:
                self._conn.execute("ALTER TABLE files ADD COLUMN source_name TEXT")
            if "library" not in cols:
                self._conn.execute(
                    "ALTER TABLE files ADD COLUMN library TEXT NOT NULL DEFAULT 'default'"
                )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_library ON files(library)"
            )
            # 确保默认库存在
            self._conn.execute(
                "INSERT OR IGNORE INTO libraries (name, created_at) VALUES (?, ?)",
                (DEFAULT_LIBRARY, time.time()),
            )

    # ---------- 库 ----------
    def create_library(self, name: str) -> int:
        name = (name or "").strip()
        if not name:
            raise ValueError("库名不能为空")
        if name == DEFAULT_LIBRARY or any(c in name for c in '/\\:*?"<>|'):
            raise ValueError("库名不合法（不能包含 / \\ : * ? \" < > |）")
        with self._lock, self._conn:
            try:
                cur = self._conn.execute(
                    "INSERT INTO libraries (name, created_at) VALUES (?, ?)",
                    (name, time.time()),
                )
            except sqlite3.IntegrityError as e:
                raise ValueError(f"库「{name}」已存在") from e
            return int(cur.lastrowid)

    def list_libraries(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT l.name, COUNT(f.id) file_count FROM libraries l "
                "LEFT JOIN files f ON f.library = l.name GROUP BY l.id ORDER BY l.id"
            ).fetchall()
            return [{"name": r["name"], "file_count": r["file_count"]} for r in rows]

    def library_exists(self, name: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM libraries WHERE name=?", (name,)
            ).fetchone()
            return row is not None

    def library_file_ids(self, name: str) -> list[int]:
        """返回某库下全部文件 id（含非 indexed，供向量清理/过滤用）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM files WHERE library=?", (name,)
            ).fetchall()
            return [int(r["id"]) for r in rows]

    def delete_library(self, name: str) -> None:
        """删除库记录与库下文件元数据（chunks 级联删除；向量由调用方清理）。"""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM libraries WHERE name=?", (name,))
            self._conn.execute("DELETE FROM files WHERE library=?", (name,))

    # ---------- 文件 ----------
    def upsert_file(
        self,
        path: str,
        content_hash: str,
        mtime: float,
        ext: str,
        source_name: str | None = None,
        library: str = DEFAULT_LIBRARY,
    ) -> tuple[FileRecord, bool]:
        """返回 (记录, 是否新文件)。若同路径已存在且 hash 相同则视为已入库。"""
        now = time.time()
        name = Path(path).name
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM files WHERE path=? AND library=?",
                (path, library),
            ).fetchone()
            if (
                row
                and row["content_hash"] == content_hash
                and row["status"] == ST_INDEXED
            ):
                return self._to_file(row), False
            if row:
                self._conn.execute(
                    "UPDATE files SET content_hash=?, mtime=?, status=?, error_msg=NULL, "
                    "source_name=COALESCE(?, source_name) WHERE id=?",
                    (content_hash, mtime, ST_PENDING, source_name, row["id"]),
                )
                # 重新 SELECT 返回最新记录（status=pending），避免拿到更新前的旧状态
                updated = self._conn.execute(
                    "SELECT * FROM files WHERE id=?", (row["id"],)
                ).fetchone()
                return self._to_file(updated), False
            cur = self._conn.execute(
                "INSERT INTO files (path,name,source_name,ext,content_hash,mtime,status,created_at,library) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    path,
                    name,
                    source_name,
                    ext,
                    content_hash,
                    mtime,
                    ST_PENDING,
                    now,
                    library,
                ),
            )
            new_id = cur.lastrowid
        return self.get_file(new_id), True

    def mark_file_status(
        self, file_id: int, status: str, error_msg: str | None = None
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE files SET status=?, error_msg=? WHERE id=?",
                (status, error_msg, file_id),
            )

    def get_file(self, file_id: int) -> FileRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM files WHERE id=?", (file_id,)
            ).fetchone()
            return self._to_file(row) if row else None

    def list_files(
        self,
        status: str | None = None,
        limit: int = 1000,
        offset: int = 0,
        library: str | None = None,
    ) -> list[FileRecord]:
        with self._lock:
            where = []
            params: list[Any] = []
            if status:
                where.append("status=?")
                params.append(status)
            if library:
                where.append("library=?")
                params.append(library)
            clause = f"WHERE {' AND '.join(where)}" if where else ""
            params.extend([limit, offset])
            rows = self._conn.execute(
                f"SELECT * FROM files {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
            return [self._to_file(r) for r in rows]

    def find_indexed_by_hash(
        self, content_hash: str, exclude_id: int | None = None
    ) -> FileRecord | None:
        with self._lock:
            if exclude_id is None:
                row = self._conn.execute(
                    "SELECT * FROM files WHERE content_hash=? AND status=?",
                    (content_hash, ST_INDEXED),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM files WHERE content_hash=? AND status=? AND id!=?",
                    (content_hash, ST_INDEXED, exclude_id),
                ).fetchone()
            return self._to_file(row) if row else None

    def delete_file(self, file_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM files WHERE id=?", (file_id,))

    def reset_stale_indexing(self) -> int:
        """把中断在 indexing 状态的文件置回 pending（可重新索引）。

        进程崩溃 / 嵌入挂死会留下半截的 indexing 记录，向量与 BM25 都不含
        它们；启动时调用一次，让它们进入可续传集合。
        """
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE files SET status=? WHERE status=?",
                (ST_PENDING, ST_INDEXING),
            )
            return int(cur.rowcount)

    def files_needing_index(self) -> list[FileRecord]:
        """待续传：pending（含从 indexing 恢复的）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM files WHERE status=? ORDER BY id", (ST_PENDING,)
            ).fetchall()
            return [self._to_file(r) for r in rows]

    # ---------- 块 ----------
    def replace_chunks(self, doc_id: int, chunks: list[Chunk]) -> list[int]:
        """先删旧块再写新块，返回新块 id 列表。

        父子依赖：父块必须先于子块出现在 chunks 中（splitter 已保证）。
        父块用临时负 id 关联，此处映射为真实自增 id。
        """
        new_ids: list[int] = []
        id_map: dict[int, int] = {}
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
            for c in chunks:
                parent_id = id_map.get(c.parent_id) if c.parent_id is not None else None
                cur = self._conn.execute(
                    "INSERT INTO chunks (doc_id,parent_id,chunk_index,text,page,token_len,meta) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        doc_id,
                        parent_id,
                        c.index,
                        c.text,
                        c.page,
                        c.token_len,
                        json.dumps(c.meta, ensure_ascii=False),
                    ),
                )
                new_id = cur.lastrowid
                new_ids.append(new_id)
                id_map[c.id] = new_id
        return new_ids

    def get_chunk(self, chunk_id: int) -> Chunk | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM chunks WHERE id=?", (chunk_id,)
            ).fetchone()
            return self._to_chunk(row) if row else None

    def get_parent_chunk(self, chunk_id: int) -> Chunk | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT c.* FROM chunks c JOIN chunks me ON me.parent_id=c.id "
                "WHERE me.id=?",
                (chunk_id,),
            ).fetchone()
            return self._to_chunk(row) if row else None

    def all_chunks_for_bm25(self) -> list[Chunk]:
        """BM25 语料：只取已索引文件的块（与 count_indexed_chunks 口径一致）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT chunks.* FROM chunks JOIN files ON files.id=chunks.doc_id "
                "WHERE files.status=? ORDER BY chunks.id",
                (ST_INDEXED,),
            ).fetchall()
            return [self._to_chunk(r) for r in rows]

    def delete_chunks_of_file(self, file_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM chunks WHERE doc_id=?", (file_id,))

    def count_indexed_chunks(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) c FROM chunks JOIN files ON files.id=chunks.doc_id "
                "WHERE files.status=?",
                (ST_INDEXED,),
            ).fetchone()
            return int(row["c"])

    def count_doc_chunks(self, doc_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) c FROM chunks WHERE doc_id=?", (doc_id,)
            ).fetchone()
            return int(row["c"])

    # ---------- kb_meta ----------
    def set_meta(self, key: str, value: Any) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO kb_meta (key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def get_meta(self, key: str) -> Any | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM kb_meta WHERE key=?", (key,)
            ).fetchone()
            if not row:
                return None
            try:
                return json.loads(row["value"])
            except json.JSONDecodeError:
                return row["value"]

    # ---------- 统计 ----------
    def stats(self) -> dict:
        with self._lock:
            files = self._conn.execute(
                "SELECT status, COUNT(*) c FROM files GROUP BY status"
            ).fetchall()
            total_chunks = self._conn.execute(
                "SELECT COUNT(*) c FROM chunks"
            ).fetchone()["c"]
            last_indexed = self._conn.execute(
                "SELECT MAX(created_at) t FROM files WHERE status=?", (ST_INDEXED,)
            ).fetchone()["t"]
        return {
            "files": {r["status"]: r["c"] for r in files},
            "chunks": total_chunks,
            "last_indexed_at": last_indexed,
        }

    # ---------- 工具 ----------
    @staticmethod
    def _to_file(row) -> FileRecord:
        keys = row.keys()
        return FileRecord(
            id=row["id"],
            path=row["path"],
            name=row["name"],
            source_name=row["source_name"] if "source_name" in keys else None,
            ext=row["ext"],
            content_hash=row["content_hash"],
            mtime=row["mtime"],
            status=row["status"],
            error_msg=row["error_msg"],
            created_at=row["created_at"],
            library=row["library"] if "library" in keys else DEFAULT_LIBRARY,
        )

    @staticmethod
    def _to_chunk(row) -> Chunk:
        meta = row["meta"]
        try:
            meta = json.loads(meta) if meta else {}
        except json.JSONDecodeError:
            meta = {}
        return Chunk(
            id=row["id"],
            doc_id=row["doc_id"],
            parent_id=row["parent_id"],
            index=row["chunk_index"],
            text=row["text"],
            page=row["page"],
            token_len=row["token_len"],
            meta=meta,
        )

    def vacuum(self) -> None:
        """压缩 SQLite 文件：重写回收空闲页 + 截断 WAL（删除后调用）。

        顺序必须 VACUUM 在前、checkpoint(TRUNCATE) 在后：WAL 模式下
        VACUUM 的页重写会写进 WAL，若先截断再 VACUUM，WAL 会重新涨回来
        （实测残留 17MB）。VACUUM 后截断才能把文件压到最小。
        """
        with self._lock, self._conn:
            self._conn.execute("VACUUM")
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.OperationalError:
                pass  # 无 WAL 或处于写事务中时可忽略

    def close(self) -> None:
        with self._lock:
            self._conn.close()
