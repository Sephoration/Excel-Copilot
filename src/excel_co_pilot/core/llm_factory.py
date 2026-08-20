"""LLM 客户端工厂：统一从全局 settings 构建 ChatOpenAI。

表格分析 Agent 与智能联表服务共用同一套 LLM 配置；
UI 热更新 settings（apply_llm_config）后，此处构建的客户端即时生效。
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from ..config import settings


def build_chat_openai() -> ChatOpenAI:
    """从全局 settings 构建 ChatOpenAI（temperature 固定 0.1 保证分析确定性）。"""
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.llm_base_url or None,
        temperature=0.1,
        timeout=settings.llm_timeout,
    )


def ensure_llm_ready() -> None:
    """检查 LLM 是否已配置，未配置时抛出可读异常。"""
    if not settings.deepseek_api_key:
        raise ValueError("未配置 LLM：请先在「模型配置」填写 API Key")


__all__ = ["build_chat_openai", "ensure_llm_ready"]