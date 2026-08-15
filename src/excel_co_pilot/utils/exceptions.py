"""自定义异常层次。"""

from __future__ import annotations


class AppError(Exception):
    """应用级基础异常。"""


class FileValidationError(AppError):
    """文件校验失败（类型/大小不合法）。"""


class LLMNotConfiguredError(AppError):
    """未配置 LLM 凭证。"""


__all__ = ["AppError", "FileValidationError", "LLMNotConfiguredError"]
