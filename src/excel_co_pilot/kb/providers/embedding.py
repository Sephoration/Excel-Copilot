"""Embedding 实现：OpenAI 兼容（硅基流动 / OpenAI）+ 本地兜底。

适配 excel_co_pilot 配置：从 settings 读取 API Key 与模型，而非 yannick 的 os.environ 独立变量。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import numpy as np
from openai import OpenAI

from ...config import settings
from ...utils.retry import retry
from .base import register_embedding


class OpenAICompatibleEmbedding:
    name = "openai-compatible"
    normalize = True

    def __init__(self, api_key: str, base_url: str, model: str, dim: int) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=60)
        self.model = model
        self.dim = dim

    @retry
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

    # 国内网络通常连不上 huggingface.co，默认走 hf-mirror.com 镜像；
    # 用户已显式设置 HF_ENDPOINT 时尊重其选择。
    HF_MIRROR = "https://hf-mirror.com"
    MODEL = "BAAI/bge-small-zh-v1.5"

    def __init__(self, **kwargs: Any) -> None:
        self.dim = kwargs.get("dim", 512)
        self._model = None
        # 下载/加载只允许一个线程进行，其余线程在此等待，避免并发重复下载
        self._load_lock = threading.Lock()

    @classmethod
    def is_cached(cls) -> bool:
        """模型是否已下载到 HF 缓存（判断能否安全地后台预载，避免联网挂死）。"""
        name = cls.MODEL.replace("/", "--")
        root = Path(os.path.expanduser("~")) / ".cache" / "huggingface" / "hub" / (
            f"models--{name}"
        )
        return (root / "snapshots").is_dir()

    def _ensure(self):
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            import os

            if "HF_ENDPOINT" not in os.environ:
                os.environ["HF_ENDPOINT"] = self.HF_MIRROR
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:  # pragma: no cover
                raise RuntimeError(
                    "本地嵌入需要安装 sentence-transformers（uv pip install sentence-transformers，"
                    "或 uv sync --extra local）"
                ) from e
            self._model = SentenceTransformer(self.MODEL)
            get_dim = getattr(
                self._model, "get_embedding_dimension", None
            ) or self._model.get_sentence_embedding_dimension
            self.dim = get_dim()
        return self._model

    def embed(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        model = self._ensure()
        vecs = model.encode(texts, batch_size=batch_size, normalize_embeddings=True)
        return [v.tolist() for v in vecs]
