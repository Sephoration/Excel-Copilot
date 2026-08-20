"""文本文件编码探测（CSV 等无 BOM 文件的通用兜底）。"""

from __future__ import annotations

from pathlib import Path

_ENCODING_CANDIDATES = ("utf-8-sig", "utf-8", "gbk", "iso-8859-1")


def detect_encoding(path: Path, sample_bytes: int = 65536) -> str:
    """按候选编码依次尝试解码文件头部，返回第一个成功的编码。

    候选顺序：utf-8-sig（带 BOM）→ utf-8 → gbk → iso-8859-1（永不失败，兜底）。
    """
    raw = path.read_bytes()[:sample_bytes]
    for enc in _ENCODING_CANDIDATES:
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "utf-8"


__all__ = ["detect_encoding"]