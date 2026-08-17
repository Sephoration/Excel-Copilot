"""LLM 实现：OpenAI 兼容 API（DeepSeek 等）与本地 Ollama。

适配 excel_co_pilot 配置：复用 settings.deepseek_api_key / llm_base_url / llm_model，
无需额外配置即可与表格分析共享同一个 LLM。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import requests
from openai import OpenAI

from ...config import settings
from ...utils.retry import retry
from .base import register_llm


@register_llm("api")
class OpenAICompatLLM:
    """OpenAI 兼容协议，复用 excel_co_pilot 的 DeepSeek 配置。"""

    name = "api"

    def __init__(self, **kwargs: Any) -> None:
        self.api_key = kwargs.get("api_key") or settings.deepseek_api_key
        self.base_url = (kwargs.get("base_url") or settings.llm_base_url).rstrip("/")
        self.model = kwargs.get("model") or settings.llm_model
        self.temperature = kwargs.get("temperature", 0.2)
        self.max_tokens = kwargs.get("max_tokens", 2048)
        if not self.api_key:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY（或在界面「模型配置」填写）")
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=120)

    def complete(
        self,
        prompt: str,
        history: list[dict] | None = None,
        stream: bool = True,
        **kwargs: Any,
    ) -> Iterator[str] | str:
        messages = [{"role": "system", "content": "你是一个严谨、简洁的助手。"}]
        messages.extend(history or [])
        messages.append({"role": "user", "content": prompt})

        if stream:
            return self._stream(messages, kwargs)
        return self._complete(messages, kwargs)

    @retry
    def _complete(self, messages, kwargs) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=kwargs.get("temperature", self.temperature),
            max_tokens=kwargs.get("max_tokens", self.max_tokens),
            stream=False,
        )
        return resp.choices[0].message.content or ""

    @retry
    def _stream(self, messages, kwargs) -> Iterator[str]:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=kwargs.get("temperature", self.temperature),
            max_tokens=kwargs.get("max_tokens", self.max_tokens),
            stream=True,
        )
        for chunk in resp:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta


@register_llm("ollama")
class OllamaLLM:
    name = "ollama"

    def __init__(self, **kwargs: Any) -> None:
        self.base_url = (kwargs.get("base_url") or settings.ollama_base_url).rstrip("/")
        self.model = kwargs.get("model") or settings.ollama_model
        self.temperature = kwargs.get("temperature", 0.2)

    def complete(
        self,
        prompt: str,
        history: list[dict] | None = None,
        stream: bool = True,
        **kwargs: Any,
    ) -> Iterator[str] | str:
        messages = [{"role": "system", "content": "你是一个严谨、简洁的助手。"}]
        messages.extend(history or [])
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "options": {
                "temperature": kwargs.get("temperature", self.temperature),
            },
        }
        if stream:
            return self._stream(payload)
        return self._complete(payload)

    @retry
    def _complete(self, payload) -> str:
        resp = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=180)
        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("content", "")

    @retry
    def _stream(self, payload) -> Iterator[str]:
        with requests.post(
            f"{self.base_url}/api/chat", json=payload, stream=True, timeout=300
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                data = json.loads(line)
                if data.get("done"):
                    break
                chunk = data.get("message", {}).get("content", "")
                if chunk:
                    yield chunk
