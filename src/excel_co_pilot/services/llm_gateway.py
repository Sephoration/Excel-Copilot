from __future__ import annotations

import time

from loguru import logger

from ..config import settings


class LLMError(Exception):
    pass


class LLMGateway:
    """Unified LLM gateway. Default: DeepSeek (OpenAI-compatible).

    Future adapters (Ollama / GPT) plug in here without touching agent code.
    参数可显式传入（前端配置），缺省回落到 `.env` / settings。
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = (api_key or settings.deepseek_api_key).strip()
        self.base_url = base_url or settings.llm_base_url
        self.model = model or settings.llm_model
        self.timeout = timeout or settings.llm_timeout
        if not self.api_key:
            raise LLMError(
                "未配置 DEEPSEEK_API_KEY，请在前端「模型配置」填写，或在项目根目录 .env 填入"
            )
        from openai import OpenAI

        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
        )

    @classmethod
    def available(cls, api_key: str | None = None) -> bool:
        return bool((api_key or settings.deepseek_api_key).strip())

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        retries: int = 2,
    ) -> str:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                content = response.choices[0].message.content
                if content is None:
                    raise LLMError("LLM 返回了空内容")
                return content
            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    logger.warning(
                        f"LLM 调用失败（第 {attempt + 1} 次）: {exc}，重试中..."
                    )
                    time.sleep(1.5 * (attempt + 1))
        raise LLMError(f"LLM 调用失败: {last_error}")

    # 注：LLM 结构化输出（JSON 抽取等）由知识库 providers/llm.py 处理。
