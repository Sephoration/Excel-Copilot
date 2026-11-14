"""Session-scoped data quality tool for the spreadsheet analysis agent."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from .data_quality import profile_dataframe
from .table_manager import get_current_manager


@tool
def inspect_data_quality(max_columns: int = 100) -> dict[str, Any]:
    """检查当前表的缺失、空白、重复行和列类型，不修改表格。

    Args:
        max_columns: 最多返回多少列的概况，范围 1 到 500。
    """
    try:
        return profile_dataframe(get_current_manager().get_active_df(), max_columns)
    except (TypeError, ValueError) as exc:
        return {"error": str(exc)}
