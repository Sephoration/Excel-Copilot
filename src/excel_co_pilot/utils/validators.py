"""文件大小校验。"""

from __future__ import annotations

from pathlib import Path

from ..config import settings
from .exceptions import FileValidationError


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


__all__ = ["ensure_size_ok"]
