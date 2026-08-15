"""KB 配置适配：把 excel_co_pilot 的 pydantic Settings 组装成 yannick 风格 Config。

yannick_vault 内部以 `cfg.retrieval.get("dense_top_k")` / `cfg.db_path` 方式访问配置，
这里提供一个接口等价的 KbConfig，使移植的 kb 模块几乎不需要改动配置访问代码。
存储目录统一收敛到 settings.storage_dir / "kb" 下。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import settings

KB_SCHEMA_VERSION = 2


class KbConfig:
    def __init__(self) -> None:
        kb_root = settings.storage_dir / "kb"

        # 路径
        self._paths = {
            "storage_dir": str(kb_root),
            "raw_dir": str(kb_root / "raw"),
            "chroma_dir": str(kb_root / "chroma_db"),
            "db_path": str(kb_root / "kb.db"),
            "logs_dir": str(kb_root / "logs"),
            "notes_dir": str(kb_root / "notes"),
            "userdict": str(kb_root / "userdict.txt"),
        }

        # 各段配置（与 yannick settings.yaml 结构一致）
        self.embedding = {
            "provider": settings.kb_embedding_provider,
            "batch_size": 64,
            "normalize": True,
            "dim": settings.kb_embedding_dim,
            "retries": 3,
            "model": settings.kb_embedding_model or "",
        }
        self.rerank = {
            "provider": settings.kb_rerank_provider,
            "top_k": 5,
        }
        self.llm = {
            "provider": "api",
            "stream": True,
            "temperature": 0.2,
            "max_tokens": 2048,
        }
        self.retrieval = {
            "dense_top_k": settings.kb_dense_top_k,
            "sparse_top_k": settings.kb_sparse_top_k,
            "rrf_k": settings.kb_rrf_k,
            "rrf_top_k": settings.kb_rrf_top_k,
            "final_top_k": settings.kb_final_top_k,
            "use_rerank": settings.kb_use_rerank,
            "query_rewrite_threshold": settings.kb_query_rewrite_threshold,
            "query_rewrite_variants": settings.kb_query_rewrite_variants,
        }
        self.refusal = {
            "absolute_threshold": settings.kb_refusal_absolute_threshold,
            "margin": settings.kb_refusal_margin,
        }
        self.splitter = {
            "chunk_size": settings.kb_chunk_size,
            "overlap": settings.kb_chunk_overlap,
            "min_chunk_size": settings.kb_min_chunk_size,
            "max_parent_tokens": settings.kb_max_parent_tokens,
        }
        self.ingest = {
            "workers": settings.kb_ingest_workers,
            "ocr_enabled": settings.kb_ocr_enabled,
        }
        self.chat = {
            "history_turns": settings.kb_history_turns,
            "max_context_chars": settings.kb_max_context_chars,
        }
        self.watch = {
            "enabled": settings.kb_watch_enabled,
            "directory": settings.kb_watch_directory,
            "interval_sec": settings.kb_watch_interval_sec,
        }
        self.web_search = {"enabled": settings.kb_web_search_enabled}
        self.agent = {
            "enabled": settings.kb_agent_enabled,
            "max_rounds": settings.kb_agent_max_rounds,
            "router": settings.kb_agent_router,
            "clarify": settings.kb_agent_clarify,
            "temperature": 0.2,
            "max_tokens": 2048,
        }
        self.schema_version = KB_SCHEMA_VERSION

    # ---------- 路径 ----------
    def _resolve(self, key: str) -> Path:
        return Path(self._paths[key])

    @property
    def raw_dir(self) -> Path:
        return self._resolve("raw_dir")

    @property
    def chroma_dir(self) -> Path:
        return self._resolve("chroma_dir")

    @property
    def db_path(self) -> Path:
        return self._resolve("db_path")

    @property
    def logs_dir(self) -> Path:
        return self._resolve("logs_dir")

    @property
    def storage_dir(self) -> Path:
        return self._resolve("storage_dir")

    @property
    def userdict_path(self) -> Path:
        return self._resolve("userdict")

    @property
    def notes_dir(self) -> Path:
        return self._resolve("notes_dir")

    def ensure_dirs(self) -> None:
        for p in (
            self.raw_dir,
            self.chroma_dir,
            self.db_path.parent,
            self.logs_dir,
            self.notes_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)

    def raw(self) -> dict[str, Any]:
        return {"paths": dict(self._paths)}


kb_config = KbConfig()
