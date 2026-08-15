"""SQLite 访问层 —— 单一事实源（SSOT）。

所有元数据、块、会话、消息都落在这里；ChromaDB 只是可重建的衍生索引。
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

SCHEMA_VERSION = 2


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


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=10000")
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
                    created_at REAL NOT NULL
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

                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    citations_json TEXT,
                    trace_id TEXT,
                    latency_ms INTEGER,
                    meta_json TEXT,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
                """
            )
            # 轻量迁移：老库补 source_name / meta_json 列（不改 SCHEMA_VERSION）
            cols = [
                r["name"]
                for r in self._conn.execute("PRAGMA table_info(files)").fetchall()
            ]
            if "source_name" not in cols:
                self._conn.execute("ALTER TABLE files ADD COLUMN source_name TEXT")
            mcols = [
                r["name"]
                for r in self._conn.execute("PRAGMA table_info(messages)").fetchall()
            ]
            if "meta_json" not in mcols:
                self._conn.execute("ALTER TABLE messages ADD COLUMN meta_json TEXT")

    # ---------- 文件 ----------
    def upsert_file(
        self,
        path: str,
        content_hash: str,
        mtime: float,
        ext: str,
        source_name: str | None = None,
    ) -> tuple[FileRecord, bool]:
        """返回 (记录, 是否新文件)。若同路径已存在且 hash 相同则视为已入库。"""
        now = time.time()
        name = Path(path).name
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM files WHERE path=?", (path,)
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
                "INSERT INTO files (path,name,source_name,ext,content_hash,mtime,status,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (path, name, source_name, ext, content_hash, mtime, ST_PENDING, now),
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
        self, status: str | None = None, limit: int = 1000, offset: int = 0
    ) -> list[FileRecord]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM files WHERE status=? ORDER BY id DESC LIMIT ? OFFSET ?",
                    (status, limit, offset),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM files ORDER BY id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
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

    def files_needing_index(self) -> list[FileRecord]:
        """断点续传：从 failed / embedding_failed / pending 继续。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM files WHERE status IN (?,?,?) ORDER BY id",
                (ST_PENDING, ST_FAILED, ST_EMBEDDING_FAILED),
            ).fetchall()
            return [self._to_file(r) for r in rows]

    def mark_all_indexed_as_pending(self) -> None:
        """重建索引用：把已索引文件全部置回 pending。"""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE files SET status=? WHERE status=?",
                (ST_PENDING, ST_INDEXED),
            )

    def delete_file(self, file_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM files WHERE id=?", (file_id,))

    def all_indexed_files(self) -> list[FileRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM files WHERE status=?", (ST_INDEXED,)
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

    def get_doc_chunks(self, doc_id: int) -> list[Chunk]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM chunks WHERE doc_id=? ORDER BY chunk_index", (doc_id,)
            ).fetchall()
            return [self._to_chunk(r) for r in rows]

    def get_parent_chunk(self, chunk_id: int) -> Chunk | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT c.* FROM chunks c JOIN chunks me ON me.parent_id=c.id "
                "WHERE me.id=?",
                (chunk_id,),
            ).fetchone()
            return self._to_chunk(row) if row else None

    def all_chunks_for_bm25(self) -> list[Chunk]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM chunks ORDER BY id").fetchall()
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

    # ---------- 会话 ----------
    def create_session(self, title: str = "新对话") -> int:
        now = time.time()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO sessions (title,created_at,updated_at) VALUES (?,?,?)",
                (title, now, now),
            )
            return int(cur.lastrowid)

    def list_sessions(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) msg_count "
                "FROM sessions s ORDER BY s.updated_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def rename_session(self, session_id: int, title: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sessions SET title=? WHERE id=?", (title, session_id)
            )

    def touch_session(self, session_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), session_id)
            )

    def delete_session(self, session_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))

    # ---------- 消息 ----------
    def add_message(
        self,
        session_id: int,
        role: str,
        content: str,
        citations: list[dict] | None = None,
        trace_id: str | None = None,
        latency_ms: int | None = None,
        meta: dict | None = None,
    ) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO messages (session_id,role,content,citations_json,trace_id,latency_ms,meta_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    role,
                    content,
                    json.dumps(citations, ensure_ascii=False) if citations else None,
                    trace_id,
                    latency_ms,
                    json.dumps(meta, ensure_ascii=False) if meta else None,
                    time.time(),
                ),
            )
            return int(cur.lastrowid)

    def get_messages(self, session_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY id", (session_id,)
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["citations"] = (
                    json.loads(d.pop("citations_json"))
                    if d.get("citations_json")
                    else []
                )
                d["meta"] = json.loads(d.pop("meta_json")) if d.get("meta_json") else {}
                out.append(d)
            return out

    def last_messages(self, session_id: int, n: int) -> list[dict]:
        return self.get_messages(session_id)[-n:]

    def recent_latencies(self, n: int = 100) -> list[int]:
        """返回最近 n 条 assistant 消息的 latency_ms（升序时间）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT latency_ms FROM messages WHERE role='assistant' "
                "AND latency_ms IS NOT NULL ORDER BY id DESC LIMIT ?",
                (n,),
            ).fetchall()
            return [int(r["latency_ms"]) for r in rows]

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

    def close(self) -> None:
        with self._lock:
            self._conn.close()
