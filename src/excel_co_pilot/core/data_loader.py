from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pandas as pd

from ..utils.encoding import detect_encoding


class LoadError(Exception):
    pass


class DataLoader:
    """Unified loader for CSV / Excel / JSON / Parquet / PDF."""

    SUPPORTED: ClassVar[set[str]] = {
        ".csv",
        ".xlsx",
        ".xls",
        ".json",
        ".parquet",
        ".pdf",
    }

    def load(self, path: str | Path) -> pd.DataFrame:
        path = Path(path)
        if not path.exists():
            raise LoadError(f"文件不存在: {path}")
        suffix = path.suffix.lower()
        if suffix not in self.SUPPORTED:
            raise LoadError(
                f"不支持的格式 {suffix}，支持: {', '.join(sorted(self.SUPPORTED))}"
            )
        try:
            return self._dispatch(path, suffix)
        except LoadError:
            raise
        except Exception as exc:
            raise LoadError(f"读取 {path.name} 失败: {exc}") from exc

    def _dispatch(self, path: Path, suffix: str) -> pd.DataFrame:
        if suffix == ".csv":
            return self._load_csv(path)
        if suffix in (".xlsx", ".xls"):
            return self._load_excel(path)
        if suffix == ".json":
            return pd.read_json(path)
        if suffix == ".parquet":
            return pd.read_parquet(path)
        if suffix == ".pdf":
            return self._load_pdf(path)
        raise LoadError(f"不支持的格式: {suffix}")

    @staticmethod
    def _load_csv(path: Path) -> pd.DataFrame:
        encoding = detect_encoding(path)
        df = pd.read_csv(path, encoding=encoding)
        if df.shape[1] == 1:
            df = pd.read_csv(path, encoding=encoding, sep=";", on_bad_lines="skip")
        return df

    @staticmethod
    def _load_excel(path: Path) -> pd.DataFrame:
        return pd.read_excel(path)

    @staticmethod
    def _load_pdf(path: Path) -> pd.DataFrame:
        import pdfplumber

        frames: list[pd.DataFrame] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                table = page.extract_table()
                if not table:
                    continue
                header = table[0]
                rows = table[1:]
                frames.append(pd.DataFrame(rows, columns=header))
        if not frames:
            raise LoadError(f"PDF 中未提取到表格: {path.name}")
        return pd.concat(frames, ignore_index=True)
