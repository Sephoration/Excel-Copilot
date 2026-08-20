"""KB 配置适配：把 excel_co_pilot 的 pydantic Settings 组装成 yannick 风格 Config。

yannick_vault 内部以 `cfg.retrieval.get("dense_top_k")` / `cfg.db_path` 方式访问配置，
这里提供一个接口等价的 KbConfig，使移植的 kb 模块几乎不需要改动配置访问代码。
存储目录统一收敛到 settings.storage_dir / "kb" 下。
"""

from __future__ import annotations

import os
from pathlib import Path

from ..config import settings

KB_SCHEMA_VERSION = 2


def _threshold(env_key: str, local_default: float, default: float) -> float:
    """取阈值：优先用户显式 .env；否则 local 嵌入时用更低的适配值。

    bge-small（512 维）等本地模型的余弦量纲整体偏低（真实命中 ~0.45），
    而 bge-m3 等 API 模型能到 0.6+，0.62 的默认拒答阈值会让 local 模式
    几乎每次都「置信度过低」拒答。
    """
    raw = os.environ.get(env_key)
    if raw:
        return float(raw)
    if settings.kb_embedding_provider == "local":
        return local_default
    return default


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
            "provider": settings.kb_llm_provider,
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
            "query_rewrite_threshold": _threshold(
                "KB_QUERY_REWRITE_THRESHOLD",
                0.45,
                settings.kb_query_rewrite_threshold,
            ),
            "query_rewrite_variants": settings.kb_query_rewrite_variants,
        }
        self.refusal = {
            "absolute_threshold": _threshold(
                "KB_REFUSAL_ABSOLUTE_THRESHOLD",
                0.42,
                settings.kb_refusal_absolute_threshold,
            ),
            "margin": _threshold("KB_REFUSAL_MARGIN", 0.05, settings.kb_refusal_margin),
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


kb_config = KbConfig()
