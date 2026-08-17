"""文件大小 / 类型校验。"""

from __future__ import annotations

from pathlib import Path

from ..config import settings
from .exceptions import FileValidationError

SUPPORTED_SUFFIXES = {".csv", ".xlsx", ".xls", ".json", ".parquet", ".pdf"}


def ensure_size_ok(path: str | Path) -> Path:
    """仅做大小上限校验（不限格式），返回原路径。超限抛 FileValidationError。"""
    path = Path(path)
    size = path.stat().st_size
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    if size > max_bytes:
        raise FileValidationError(
            f"文件 {path.name} 大小为 {size / 1024 / 1024:.1f}MB，"
            f"超过上限 {settings.max_file_size_mb}MB"
        )
    return path


def validate_file(path: str | Path) -> Path:
    path = Path(path)
    if not path.exists():
        raise FileValidationError(f"文件不存在: {path}")
    if not path.is_file():
        raise FileValidationError(f"不是文件: {path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise FileValidationError(
            f"不支持的格式 {path.suffix}，支持: {', '.join(sorted(SUPPORTED_SUFFIXES))}"
        )
    return ensure_size_ok(path)


__all__ = ["SUPPORTED_SUFFIXES", "ensure_size_ok", "validate_file"]
