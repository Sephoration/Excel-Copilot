"""知识库（KB / RAG）子包：多格式摄入 → 父子块切分 → 混合检索 → RAG 问答。

由 yannick_vault 移植并适配进 Excel Co-Pilot：
- 复用 excel_co_pilot 的 LLM 配置（DEEPSEEK_API_KEY / LLM_BASE_URL / LLM_MODEL）
- 日志统一走 loguru（适配 structlog 风格调用）
- 存储目录统一在 settings.storage_dir / "kb" 下
"""

from .services import Services

__all__ = ["Services"]
