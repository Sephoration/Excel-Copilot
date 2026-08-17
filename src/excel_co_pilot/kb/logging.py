"""KB 日志适配：把 structlog 风格的调用（log.info("event", key=val)）映射到 loguru。

保留 trace_id 上下文（contextvar），与 yannick_vault 原 logging_setup 行为一致，
但不引入 structlog 依赖。
"""

from __future__ import annotations

import contextvars
from typing import Any

from loguru import logger as _loguru

_trace_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "trace_id", default=None
)


def set_trace_id(trace_id: str | None) -> None:
    _trace_var.set(trace_id)


class _KbLogger:
    """structlog 风格的最小 logger 适配：event 为消息，kwargs 并入 extra。"""

    def __init__(self, name: str) -> None:
        self._name = name

    def _emit(self, level: str, event: str, **kwargs: Any) -> None:
        tid = _trace_var.get()
        extra = dict(kwargs)
        if tid:
            extra["trace_id"] = tid
        getattr(_loguru.bind(module=self._name), level)(event, **extra)

    def info(self, event: str, **kwargs: Any) -> None:
        self._emit("info", event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._emit("warning", event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._emit("error", event, **kwargs)


def get_logger(name: str = "kb") -> _KbLogger:
    return _KbLogger(name)


__all__ = ["get_logger", "set_trace_id"]
