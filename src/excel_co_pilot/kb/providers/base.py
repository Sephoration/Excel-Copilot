"""Provider 接口定义（Protocol）与注册表。

移植自 yannick_vault providers/base.py，原样保留。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol


class EmbeddingProvider(Protocol):
    """文本嵌入。实现方通过 register_embedding() 注册。"""

    name: str
    dim: int
    normalize: bool = True

    def embed(self, texts: list[str], batch_size: int = 64) -> list[list[float]]: ...


class Reranker(Protocol):
    """对 (text, score) 对重排，返回按相关度降序的索引列表。"""

    name: str

    def rerank(
        self, query: str, candidates: list[tuple[str, float]], top_k: int = 5
    ) -> list[tuple[int, float]]: ...


class LLM(Protocol):
    """文本生成。stream=True 时逐片段产出。"""

    name: str

    def complete(
        self,
        prompt: str,
        history: list[dict] | None = None,
        stream: bool = True,
        **kwargs,
    ) -> Iterator[str] | str: ...


# ---------- 注册表 ----------
_EMBEDDING_REGISTRY: dict[str, type] = {}
_RERANK_REGISTRY: dict[str, type] = {}
_LLM_REGISTRY: dict[str, type] = {}


def register_embedding(name: str):
    def deco(cls):
        _EMBEDDING_REGISTRY[name] = cls
        return cls

    return deco


def register_reranker(name: str):
    def deco(cls):
        _RERANK_REGISTRY[name] = cls
        return cls

    return deco


def register_llm(name: str):
    def deco(cls):
        _LLM_REGISTRY[name] = cls
        return cls

    return deco


def build_embedding(name: str, **kwargs) -> EmbeddingProvider:
    return _EMBEDDING_REGISTRY[name](**kwargs)


def build_reranker(name: str, **kwargs) -> Reranker:
    return _RERANK_REGISTRY[name](**kwargs)


def build_llm(name: str, **kwargs) -> LLM:
    return _LLM_REGISTRY[name](**kwargs)
