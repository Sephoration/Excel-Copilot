"""服务装配：从 KbConfig 构建全部组件，进程内复用（惰性初始化 + 缓存）。

移植自 yannick_vault core/services.py，适配 excel_co_pilot 配置与日志。
"""

from __future__ import annotations

from ..config import settings
from . import db as db_mod
from . import providers as pb
from .config import KbConfig, kb_config
from .indexer import Indexer
from .ingest import IngestEngine
from .logging import get_logger
from .qa_engine import QAEngine
from .retriever import Retriever

log = get_logger("services")


class Services:
    """持有所有运行时组件，惰性初始化 + 缓存。"""

    def __init__(self, cfg: KbConfig | None = None) -> None:
        self.cfg = cfg or kb_config
        self.cfg.ensure_dirs()
        self._db: db_mod.Database | None = None
        self._indexer: Indexer | None = None
        self._embedder = None
        self._reranker = None
        self._llm = None
        self._retriever: Retriever | None = None
        self._qa: QAEngine | None = None
        self._ingest: IngestEngine | None = None
        self._dim_warned = False

    @property
    def db(self) -> db_mod.Database:
        if self._db is None:
            self._db = db_mod.Database(self.cfg.db_path)
            self._db.set_meta("schema_version", self.cfg.schema_version)
        return self._db

    @property
    def embedder(self):
        if self._embedder is None:
            name = self.cfg.embedding.get("provider", "siliconflow")
            self._embedder = pb.build_embedding(
                name,
                dim=self.cfg.embedding.get("dim", 1024),
                batch_size=self.cfg.embedding.get("batch_size", 64),
                model=self.cfg.embedding.get("model", "") or None,
            )
            self._check_dim()
            log.info(
                "embedding_ready", provider=self._embedder.name, dim=self._embedder.dim
            )
        return self._embedder

    def _check_dim(self) -> None:
        """更换 embedding 模型（维度变化）时提示重建索引。"""
        if self._indexer is not None:
            return
        stored_dim = self.db.get_meta("embedding_dim")
        if (
            stored_dim
            and int(stored_dim) != int(self.cfg.embedding.get("dim", 0))
            and not self._dim_warned
        ):
            self._dim_warned = True
            log.warning(
                "embedding_dim_changed",
                stored=stored_dim,
                current=self.cfg.embedding.get("dim", 0),
                hint="更换模型后请删除 storage/kb 重建索引",
            )

    @property
    def indexer(self) -> Indexer:
        if self._indexer is None:
            self._indexer = Indexer(
                self.cfg.chroma_dir,
                dim=self.cfg.embedding.get("dim", 1024),
                schema_version=self.cfg.schema_version,
            )
        return self._indexer

    @property
    def reranker(self):
        if self._reranker is None:
            name = self.cfg.rerank.get("provider", "none")
            self._reranker = pb.build_reranker(name, **self.cfg.rerank)
        return self._reranker

    @property
    def llm(self):
        if self._llm is None:
            name = self.cfg.llm.get("provider", "api")
            self._llm = pb.build_llm(name, **self.cfg.llm)
        return self._llm

    @property
    def retriever(self) -> Retriever:
        if self._retriever is None:
            self._retriever = Retriever(
                self.cfg, self.db, self.indexer, self.embedder, self.reranker
            )
        return self._retriever

    @property
    def qa(self) -> QAEngine:
        if self._qa is None:
            self._qa = QAEngine(self.cfg, self.db, self.retriever, self.llm)
        return self._qa

    @property
    def ingest(self) -> IngestEngine:
        if self._ingest is None:
            self._ingest = IngestEngine(self.cfg, self.db, self.indexer, self.embedder)
        return self._ingest

    @property
    def llm_ready(self) -> bool:
        """是否具备问答能力：至少配置了 LLM API Key。"""
        return bool(settings.deepseek_api_key) or bool(settings.ollama_base_url)

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None


# 进程级单例：UI 与 API 共享
_services: Services | None = None


def get_services() -> Services:
    global _services
    if _services is None:
        _services = Services()
    return _services
