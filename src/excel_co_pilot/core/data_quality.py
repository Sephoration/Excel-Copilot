"""Data quality summary for an in-memory table, with no model or file writes."""

from __future__ import annotations

from typing import Any

import pandas as pd
from pandas.api.types import (
    is_bool_dtype,
    is_datetime64_any_dtype,
    is_numeric_dtype,
    is_string_dtype,
)


def _kind(series: pd.Series) -> str:
    dtype = series.dtype
    if is_bool_dtype(dtype):
        return "布尔"
    if is_numeric_dtype(dtype):
        return "数值"
    if is_datetime64_any_dtype(dtype):
        return "日期时间"
    if is_string_dtype(dtype) and dtype != object:
        return "文本"
    if dtype == object:
        nonnull = series.dropna()
        if nonnull.empty:
            return "未知"
        if nonnull.map(lambda value: isinstance(value, str)).all():
            return "文本"
        return "混合或其他"
    return "其他"


def profile_dataframe(df: pd.DataFrame, max_columns: int = 100) -> dict[str, Any]:
    """Return aggregate counts only; blank strings are reported separately from nulls.

    Duplicate row counts represent extra occurrences after the first. Unsupported
    unhashable object values are reported as unavailable rather than guessed.
    """
    if not isinstance(max_columns, int) or isinstance(max_columns, bool) or not 1 <= max_columns <= 500:
        raise ValueError("max_columns 必须在 1 到 500 之间")
    row_count = len(df)
    try:
        duplicate_rows: int | None = int(df.duplicated().sum()) if len(df.columns) else 0
    except (TypeError, ValueError):
        duplicate_rows = None
    columns: list[dict[str, Any]] = []
    for position in range(min(len(df.columns), max_columns)):
        series = df.iloc[:, position]
        null_count = int(series.isna().sum())
        blank_count = int(series.map(lambda value: isinstance(value, str) and not value.strip()).sum())
        try:
            unique_count: int | None = int(series.nunique(dropna=True))
        except (TypeError, ValueError):
            unique_count = None
        columns.append({
            "name": str(df.columns[position]),
            "dtype": str(series.dtype),
            "kind": _kind(series),
            "null_count": null_count,
            "blank_count": blank_count,
            "missing_percent": round((null_count + blank_count) * 100 / row_count, 2) if row_count else 0.0,
            "unique_count": unique_count,
        })
    return {
        "total_rows": row_count,
        "total_columns": len(df.columns),
        "duplicate_rows": duplicate_rows,
        "columns": columns,
        "columns_truncated": len(df.columns) > len(columns),
    }
