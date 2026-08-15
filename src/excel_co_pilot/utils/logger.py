"""Loguru 配置：结构化日志，按天滚动，保留最近 7 天。"""

from __future__ import annotations

import sys

from loguru import logger

from ..config import settings

_LOG_DIR = settings.storage_dir / "logs"


def setup_logger() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format=(
            "<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | "
            "<cyan>{name}</cyan> - <level>{message}</level>"
        ),
    )
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.add(
        _LOG_DIR / "app_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        rotation="00:00",
        retention="7 days",
        encoding="utf-8",
        enqueue=True,
    )


__all__ = ["logger", "setup_logger"]
