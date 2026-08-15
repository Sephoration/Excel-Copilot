"""LLM 实现：OpenAI 兼容 API（DeepSeek 等）与本地 Ollama。

适配 excel_co_pilot 配置：复用 settings.deepseek_api_key / llm_base_url / llm_model，
无需额外配置即可与表格分析 Agent 共享同一个 LLM。
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import requests
from openai import OpenAI

from ...config import settings
from .base import register_llm

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


@register_llm("api")
class OpenAICompatLLM:
    """OpenAI 兼容协议，复用 excel_co_pilot 的 DeepSeek 配置。"""

    name = "api"
    supports_tools = True

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

    @_retry
    def _complete(self, messages, kwargs) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=kwargs.get("temperature", self.temperature),
            max_tokens=kwargs.get("max_tokens", self.max_tokens),
            stream=False,
        )
        return resp.choices[0].message.content or ""

    @_retry
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

    # ---------- Function Calling ----------
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = True,
        **kwargs: Any,
    ) -> Iterator[dict] | dict:
        params = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
        }
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"
        if stream:
            return self._chat_stream(params)
        return self._chat_complete(params)

    @_retry
    def _chat_complete(self, params) -> dict:
        resp = self.client.chat.completions.create(stream=False, **params)
        msg = resp.choices[0].message
        return {
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": _normalize_tool_calls(msg.tool_calls),
        }

    @_retry
    def _chat_stream(self, params) -> Iterator[dict]:
        resp = self.client.chat.completions.create(stream=True, **params)
        acc: dict[int, dict] = {}
        order: list[int] = []
        for chunk in resp:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield {"type": "delta", "text": delta.content}
            for tc in delta.tool_calls or []:
                i = tc.index
                if i not in acc:
                    acc[i] = {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }
                    order.append(i)
                if tc.id:
                    acc[i]["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        acc[i]["function"]["name"] += tc.function.name
                    if tc.function.arguments:
                        acc[i]["function"]["arguments"] += tc.function.arguments
        if order:
            yield {"type": "tool_calls", "tool_calls": [acc[i] for i in order]}


def _normalize_tool_calls(tool_calls) -> list[dict] | None:
    """把 OpenAI SDK 的 tool_calls 归一化为标准 dict。"""
    if not tool_calls:
        return None
    out = []
    for tc in tool_calls:
        out.append(
            {
                "id": tc.id or "",
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                },
            }
        )
    return out


@register_llm("ollama")
class OllamaLLM:
    name = "ollama"
    supports_tools = True

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

    @_retry
    def _complete(self, payload) -> str:
        resp = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=180)
        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("content", "")

    @_retry
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

    # ---------- Function Calling ----------
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = True,
        **kwargs: Any,
    ) -> Iterator[dict] | dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "options": {"temperature": kwargs.get("temperature", self.temperature)},
        }
        if tools:
            payload["tools"] = tools
        if stream:
            return self._chat_stream(payload)
        return self._chat_complete(payload)

    @_retry
    def _chat_complete(self, payload) -> dict:
        resp = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=180)
        resp.raise_for_status()
        data = resp.json()
        msg = data.get("message", {}) or {}
        return {
            "role": "assistant",
            "content": msg.get("content", ""),
            "tool_calls": _ollama_tool_calls(msg),
        }

    @_retry
    def _chat_stream(self, payload) -> Iterator[dict]:
        tool_calls = None
        with requests.post(
            f"{self.base_url}/api/chat", json=payload, stream=True, timeout=300
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                data = json.loads(line)
                msg = data.get("message", {}) or {}
                chunk = msg.get("content", "")
                if chunk:
                    yield {"type": "delta", "text": chunk}
                calls = msg.get("tool_calls")
                if calls:
                    tool_calls = calls
                if data.get("done"):
                    break
        if tool_calls:
            yield {
                "type": "tool_calls",
                "tool_calls": _ollama_tool_calls({"tool_calls": tool_calls}),
            }


def _ollama_tool_calls(msg: dict) -> list[dict] | None:
    """把 Ollama 的 tool_calls 归一化为 OpenAI 风格。"""
    calls = msg.get("tool_calls")
    if not calls:
        return None
    out = []
    for tc in calls:
        fn = tc.get("function", {}) or {}
        args = fn.get("arguments", {}) or {}
        out.append(
            {
                "id": tc.get("id", "") or "",
                "type": "function",
                "function": {
                    "name": fn.get("name", ""),
                    "arguments": json.dumps(args, ensure_ascii=False)
                    if not isinstance(args, str)
                    else args,
                },
            }
        )
    return out
