"""服务装配：从 KbConfig 构建全部组件，进程内复用（惰性初始化 + 缓存）。

移植自 yannick_vault core/services.py，适配 excel_co_pilot 配置与日志。
"""

from __future__ import annotations

import threading

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
        self._llm_sig: tuple | None = None

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
        # 界面「模型配置」可运行时改 key；provider/本地地址变化时同样重建客户端，
        # 并丢弃持有旧引用的 QA 引擎
        sig = (
            settings.deepseek_api_key,
            settings.llm_base_url,
            settings.llm_model,
            settings.ollama_base_url,
            settings.ollama_model,
            self.cfg.llm.get("provider"),
        )
        if self._llm is None or sig != self._llm_sig:
            name = self.cfg.llm.get("provider", "api")
            self._llm = pb.build_llm(name, **self.cfg.llm)
            self._llm_sig = sig
            self._qa = None
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
        """是否具备问答能力：api 模式需要 OpenAI 兼容 API Key；ollama 模式完全本地。"""
        if self.cfg.llm.get("provider") == "ollama":
            return True
        return bool(settings.deepseek_api_key)

    # ---------- 索引热状态 ----------

    def warm_up(self) -> None:
        """启动预热：恢复中断导入 + 预建 BM25 +（本地模型已缓存时）后台预载。

        保证「第一次提问」不用现场做这些重活——数据库在对话前就绪。
        设计为在后台线程调用；失败只记日志，不影响后续懒加载兜底。

        1. 把崩溃/嵌入挂死遗留的 indexing 记录置回 pending，后台续传；
        2. 预建 BM25 关键词索引；
        3. 本地嵌入（local）模型已缓存 → 后台预载，首次提问免现场 ~20s 加载；
           未缓存 → 跳过预载，避免启动时联网下载挂死（历史日志出现过 37 分钟）。
           即便预载未完成，提问也会在 _ensure 锁上等待同一份加载，不会重复下载。
        """
        try:
            self.db.reset_stale_indexing()
            if self.cfg.embedding.get("provider", "api") == "local":
                self._preload_local_embedder()
            else:
                # API 嵌入：预热一条建立连接
                self.embedder.embed(["预热"])
            self.retriever.sparse.rebuild()  # 预建 BM25 关键词索引
            self._resume_pending_async()  # 后台续传被中断的导入
            log.info("kb_warm_up_done", chunks=self.retriever.sparse._known_count)
        except Exception as e:  # 预热失败不致命，查询时仍会懒加载
            log.warning("kb_warm_up_failed", error=str(e))

    def _resume_pending_async(self) -> None:
        """后台续传 pending 文件；完成后刷新 BM25 索引。"""

        def _run() -> None:
            try:
                results = self.ingest.resume_pending()
                if results:
                    self.retriever.sparse.rebuild()
                    ok = sum(1 for r in results if r.get("status") == "indexed")
                    log.info("kb_resume_done", n=len(results), indexed=ok)
            except Exception as e:
                log.warning("kb_resume_failed", error=str(e))

        threading.Thread(target=_run, daemon=True).start()

    def _preload_local_embedder(self) -> None:
        """本地模型已缓存 → 后台线程预载；未缓存则跳过。"""
        try:
            from .providers.embedding import LocalEmbedding

            if not LocalEmbedding.is_cached():
                return
        except Exception:
            return

        def _load() -> None:
            try:
                self.embedder.embed(["预热"])
            except Exception as e:  # 预载失败不致命，查询时懒加载兜底
                log.warning("local_embed_preload_failed", error=str(e))

        threading.Thread(target=_load, daemon=True).start()

    def refresh_indexes(self) -> None:
        """导入 / 删除文档后调用：后台重建 BM25，不阻塞 UI / API 请求。

        BM25 是懒构建的（_ensure 会按 chunk 数自动重建），所以后台重建只是
        提前预热，即使没来得及完成，首次提问也会按需重建。
        """
        try:
            threading.Thread(target=self._refresh_bm25, daemon=True).start()
        except RuntimeError:  # pragma: no cover
            self._refresh_bm25()  # 极少数无线程环境退化为同步

    def _refresh_bm25(self) -> None:
        try:
            self.retriever.sparse.rebuild()
        except Exception as e:
            log.warning("sparse_refresh_failed", error=str(e))

    def compact(self) -> dict:
        """压缩存储：重建 Chroma 集合去掉重复全文，并对 SQLite VACUUM。

        返回各步骤结果摘要。任一步失败不影响其它步骤（各 try/except 隔离）。
        注意：Chroma 重建期间向量库短暂为空，请勿与提问并发调用。
        """
        result: dict = {}
        try:
            result.update(self.indexer.compact())
        except Exception as e:
            log.warning("chroma_compact_failed", error=str(e))
            result["chroma"] = f"failed: {e}"
        try:
            self.db.vacuum()
            result["kb_db"] = "vacuumed"
        except Exception as e:
            log.warning("kb_vacuum_failed", error=str(e))
            result["kb_db"] = f"failed: {e}"
        return result

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
