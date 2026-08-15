"""Embedding 实现：OpenAI 兼容（硅基流动 / OpenAI）+ 本地兜底。

适配 excel_co_pilot 配置：从 settings 读取 API Key 与模型，而非 yannick 的 os.environ 独立变量。
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
from openai import OpenAI

from ...config import settings
from .base import register_embedding

_MAX_RETRIES = 3


def _retry(fn):
    """指数退避重试，替代 tenacity（避免额外依赖）。"""

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


class OpenAICompatibleEmbedding:
    name = "openai-compatible"
    normalize = True

    def __init__(self, api_key: str, base_url: str, model: str, dim: int) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=60)
        self.model = model
        self.dim = dim

    @_retry
    def embed(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            resp = self.client.embeddings.create(input=batch, model=self.model)
            vectors = [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
            if self.normalize:
                vectors = [self._normalize(v) for v in vectors]
            out.extend(vectors)
        return out

    @staticmethod
    def _normalize(vec: list[float]) -> list[float]:
        arr = np.asarray(vec, dtype=np.float32)
        norm = np.linalg.norm(arr)
        if norm == 0:
            return vec
        return (arr / norm).tolist()


@register_embedding("siliconflow")
class SiliconFlowEmbedding(OpenAICompatibleEmbedding):
    def __init__(self, **kwargs: Any) -> None:
        api_key = settings.siliconflow_api_key or settings.deepseek_api_key
        base_url = "https://api.siliconflow.cn/v1"
        model = kwargs.get("model") or "BAAI/bge-m3"
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            model=model,
            dim=kwargs.get("dim", 1024),
        )
        self.name = "siliconflow"


@register_embedding("openai")
class OpenAIEmbedding(OpenAICompatibleEmbedding):
    def __init__(self, **kwargs: Any) -> None:
        api_key = settings.openai_api_key or settings.deepseek_api_key
        base_url = "https://api.openai.com/v1"
        model = kwargs.get("model") or "text-embedding-3-small"
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            model=model,
            dim=kwargs.get("dim", 1536),
        )
        self.name = "openai"


@register_embedding("local")
class LocalEmbedding:
    """本地兜底（sentence-transformers）。重依赖，懒加载，未安装时报错。"""

    name = "local"
    normalize = True

    def __init__(self, **kwargs: Any) -> None:
        self.dim = kwargs.get("dim", 512)
        self._model = None

    def _ensure(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:  # pragma: no cover
                raise RuntimeError(
                    "本地嵌入需要安装 sentence-transformers（重依赖，默认未安装）"
                ) from e
            self._model = SentenceTransformer("BAAI/bge-small-zh-v1.5")
            self.dim = self._model.get_sentence_embedding_dimension()
        return self._model

    def embed(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        model = self._ensure()
        vecs = model.encode(texts, batch_size=batch_size, normalize_embeddings=True)
        return [v.tolist() for v in vecs]
