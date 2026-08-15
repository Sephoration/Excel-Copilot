"""重排实现：Jina / Cohere / 硅基流动（HTTP API）+ 本地 cross-encoder 兜底 + none 直通。

适配 excel_co_pilot 配置：从 settings 读取 API Key。
"""

from __future__ import annotations

import time
from typing import Any

import requests

from ...config import settings
from .base import register_reranker

_MAX_RETRIES = 3


def _retry(fn):
    """指数退避重试，替代 tenacity。"""

    def wrapper(*args, **kwargs):
        last = None
        for attempt in range(_MAX_RETRIES):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                last = e
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(1.5 * (attempt + 1))
        raise last

    return wrapper


@register_reranker("jina")
class JinaReranker:
    name = "jina"
    confidence_scores = True  # Jina relevance_score 为 0~1 置信度

    def __init__(self, **kwargs: Any) -> None:
        self.api_key = settings.jina_api_key
        self.model = kwargs.get("model", "jina-reranker-v2-base-multilingual")
        if not self.api_key:
            raise RuntimeError("JINA_API_KEY 未设置")

    @_retry
    def rerank(
        self, query: str, candidates: list[tuple[str, float]], top_k: int = 5
    ) -> list[tuple[int, float]]:
        resp = requests.post(
            "https://api.jina.ai/v1/rerank",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "query": query,
                "documents": [t for t, _ in candidates],
                "top_n": min(top_k, len(candidates)),
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            (r["index"], float(r["relevance_score"])) for r in data.get("results", [])
        ]


@register_reranker("cohere")
class CohereReranker:
    name = "cohere"
    confidence_scores = True  # Cohere relevance_score 为 0~1 置信度

    def __init__(self, **kwargs: Any) -> None:
        self.api_key = settings.cohere_api_key
        self.model = kwargs.get("model", "rerank-multilingual-v3.0")
        if not self.api_key:
            raise RuntimeError("COHERE_API_KEY 未设置")

    @_retry
    def rerank(
        self, query: str, candidates: list[tuple[str, float]], top_k: int = 5
    ) -> list[tuple[int, float]]:
        resp = requests.post(
            "https://api.cohere.com/v1/rerank",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "query": query,
                "documents": [t for t, _ in candidates],
                "top_n": min(top_k, len(candidates)),
                "return_documents": False,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            (r["index"], float(r["relevance_score"])) for r in data.get("results", [])
        ]


@register_reranker("siliconflow")
class SiliconFlowReranker:
    """硅基流动重排（BAAI/bge-reranker-v2-m3），复用 SILICONFLOW_API_KEY。"""

    name = "siliconflow"
    confidence_scores = True  # bge-reranker 输出 relevance_score 为 0~1 置信度

    def __init__(self, **kwargs: Any) -> None:
        self.api_key = settings.siliconflow_api_key
        self.base_url = "https://api.siliconflow.cn/v1"
        self.model = kwargs.get("model", "BAAI/bge-reranker-v2-m3")
        if not self.api_key:
            raise RuntimeError("SILICONFLOW_API_KEY 未设置")

    @_retry
    def rerank(
        self, query: str, candidates: list[tuple[str, float]], top_k: int = 5
    ) -> list[tuple[int, float]]:
        resp = requests.post(
            f"{self.base_url}/rerank",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "query": query,
                "documents": [t for t, _ in candidates],
                "top_n": min(top_k, len(candidates)),
                "return_documents": False,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            (r["index"], float(r["relevance_score"])) for r in data.get("results", [])
        ]


@register_reranker("local")
class LocalReranker:
    """本地 cross-encoder 兜底（CPU 慢，离线用）。cross-encoder 输出为原始 logit，
    量纲非 0~1，置信度判定仍需 dense 余弦，故 confidence_scores=False。"""

    name = "local"
    confidence_scores = False

    def __init__(self, **kwargs: Any) -> None:
        self.model = kwargs.get("model", "cross-encoder/ms-marco-MiniLM-L-6-v2")
        self._pipe = None

    def _ensure(self):
        if self._pipe is None:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as e:  # pragma: no cover
                raise RuntimeError(
                    "本地重排需要安装 sentence-transformers / cross-encoder"
                ) from e
            self._pipe = CrossEncoder(self.model)
        return self._pipe

    def rerank(
        self, query: str, candidates: list[tuple[str, float]], top_k: int = 5
    ) -> list[tuple[int, float]]:
        pipe = self._ensure()
        scores = pipe.predict([(query, t) for t, _ in candidates])
        ranked = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)
        return [(i, float(scores[i])) for i in ranked[:top_k]]


@register_reranker("none")
class NoopReranker:
    """直通：保持候选顺序，分数透传。"""

    name = "none"
    confidence_scores = False

    def __init__(self, **kwargs: Any) -> None:
        pass

    def rerank(
        self, query: str, candidates: list[tuple[str, float]], top_k: int = 5
    ) -> list[tuple[int, float]]:
        return list(enumerate([s for _, s in candidates]))[:top_k]
