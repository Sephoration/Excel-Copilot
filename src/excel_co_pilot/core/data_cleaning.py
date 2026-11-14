"""Small, deterministic table cleaning operations used by preview and apply."""

from __future__ import annotations

from typing import Any

import pandas as pd


OPERATIONS = {"trim", "blank_to_null", "convert_numeric", "drop_duplicates"}


def clean_frame(frame: pd.DataFrame, steps: list[dict[str, Any]]) -> pd.DataFrame:
    """Return a cleaned copy. Invalid steps fail before the caller changes its table."""
    if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
        raise ValueError("请选择 1 到 20 个清洗步骤")
    result = frame.copy(deep=True)
    if result.columns.has_duplicates:
        raise ValueError("表格存在重复列名，请先在原文件中区分列名")
    for step in steps:
        if not isinstance(step, dict) or step.get("operation") not in OPERATIONS:
            raise ValueError("不支持的清洗操作")
        operation = step["operation"]
        if operation == "drop_duplicates":
            subset = step.get("columns")
            if subset is not None:
                if (not isinstance(subset, list) or not subset
                        or not all(isinstance(column, str) for column in subset)
                        or len(set(subset)) != len(subset)):
                    raise ValueError("去重列必须是不重复的列名列表")
                if any(column not in result.columns for column in subset):
                    raise ValueError("去重列不存在")
            result = result.drop_duplicates(subset=subset).reset_index(drop=True)
            continue
        column = step.get("column")
        if not isinstance(column, str) or column not in result.columns:
            raise ValueError(f"列不存在: {column}")
        series = result[column]
        if operation == "trim":
            result[column] = series.map(lambda value: value.strip() if isinstance(value, str) else value)
        elif operation == "blank_to_null":
            result[column] = series.map(
                lambda value: pd.NA if isinstance(value, str) and not value.strip() else value
            )
        elif operation == "convert_numeric":
            # Conversion must be explicit and lossless with respect to nonblank values.
            converted = pd.to_numeric(series, errors="coerce")
            invalid = series.notna() & converted.isna() & ~series.map(
                lambda value: isinstance(value, str) and not value.strip()
            )
            if invalid.any():
                raise ValueError(f"列 {column} 有 {int(invalid.sum())} 个非数值，无法转换")
            result[column] = converted
    return result
