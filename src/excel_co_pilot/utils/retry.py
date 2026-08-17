"""指数退避重试装饰器（替代 tenacity，避免额外依赖）。"""

from __future__ import annotations

import functools
import time
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

_DEFAULT_MAX_RETRIES = 3


def retry(
    fn: F | None = None, *, max_retries: int = _DEFAULT_MAX_RETRIES
) -> F | Callable[[F], F]:
    """重试被装饰函数：失败后按 1.5s * attempt 指数退避，超过次数抛最后一次异常。"""

    def decorate(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last: Exception | None = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last = e
                    if attempt < max_retries - 1:
                        time.sleep(1.5 * (attempt + 1))
            if last is not None:
                raise last
            return None

        return wrapper  # type: ignore[return-value]

    if fn is not None:
        return decorate(fn)
    return decorate


__all__ = ["retry"]
