"""KB Providers：embedding / rerank / llm 可插拔层。"""

# 导入各实现以触发注册
from . import embedding as _embedding  # noqa: F401
from . import llm as _llm  # noqa: F401
from . import rerank as _rerank  # noqa: F401
from .base import (
    _EMBEDDING_REGISTRY,
    _LLM_REGISTRY,
    _RERANK_REGISTRY,
    LLM,
    EmbeddingProvider,
    Reranker,
    build_embedding,
    build_llm,
    build_reranker,
    register_embedding,
    register_llm,
    register_reranker,
)

__all__ = [
    "LLM",
    "_EMBEDDING_REGISTRY",
    "_LLM_REGISTRY",
    "_RERANK_REGISTRY",
    "EmbeddingProvider",
    "Reranker",
    "build_embedding",
    "build_llm",
    "build_reranker",
    "register_embedding",
    "register_llm",
    "register_reranker",
]
