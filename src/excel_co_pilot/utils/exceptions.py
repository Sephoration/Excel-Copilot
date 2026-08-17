"""自定义异常层次。"""

from __future__ import annotations


class FileValidationError(Exception):
    """文件校验失败（类型/大小不合法）。"""


__all__ = ["FileValidationError"]
