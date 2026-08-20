"""Excel 数据分析工具集（移植自 ExcelMind，基于 LangChain @tool）。

所有工具通过 `get_current_manager()` 读取**当前会话**的活跃表（contextvar 隔离），
避免 ExcelMind 原版的全局单例在多会话下的串扰。
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import pandas as pd
from langchain_core.tools import tool

from ..config import settings
from .table_manager import get_current_manager


def _safe_value(value: Any) -> Any:
    """把 pandas / numpy 值转成可 JSON 序列化的原生类型。"""
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, pd.Timedelta):
        return value.total_seconds()
    if value is pd.NaT:
        return None
    if isinstance(value, (float, int, str, bool)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    if hasattr(value, "item"):
        return _safe_value(value.item())
    if isinstance(value, list):
        return [_safe_value(v) for v in value]
    if isinstance(value, tuple):
        return [_safe_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _safe_value(v) for k, v in value.items()}
    return str(value)


def _active_df() -> pd.DataFrame:
    """当前会话活跃表的 DataFrame。"""
    return get_current_manager().get_active_df()


def _limit_result(df: pd.DataFrame, limit: int | None = None) -> pd.DataFrame:
    """限制返回结果行数。"""
    if limit is None:
        limit = settings.excel_default_result_limit
    limit = min(int(limit), settings.excel_max_result_limit)
    return df.head(limit)


def _df_to_result(
    df: pd.DataFrame,
    limit: int | None = None,
    select_columns: list[str] | None = None,
) -> dict[str, Any]:
    """DataFrame → 结果字典（含序列化安全转换）。"""
    if select_columns:
        available_cols = [c for c in select_columns if c in df.columns]
        if available_cols:
            df = df[available_cols]
    limited_df = _limit_result(df, limit)
    return {
        "total_rows": len(df),
        "returned_rows": len(limited_df),
        "columns": list(limited_df.columns),
        "data": [
            {str(k): _safe_value(v) for k, v in row.items()}
            for row in limited_df.to_dict(orient="records")
        ],
    }


def _get_filter_mask(
    df: pd.DataFrame, column: str, operator: str, value: Any
) -> pd.Series:
    """生成单个筛选条件的布尔掩码。"""
    if column not in df.columns:
        raise ValueError(f"列 '{column}' 不存在，可用列: {list(df.columns)}")

    col = df[column]
    try:
        numeric_value = float(value)
    except (ValueError, TypeError):
        numeric_value = None
    compare_value = numeric_value if numeric_value is not None else value

    if operator == "==":
        return col == compare_value
    if operator == "!=":
        return col != compare_value
    if operator == ">":
        return col > compare_value
    if operator == "<":
        return col < compare_value
    if operator == ">=":
        return col >= compare_value
    if operator == "<=":
        return col <= compare_value
    if operator == "contains":
        return col.astype(str).str.contains(str(value), case=False, na=False)
    if operator == "startswith":
        return col.astype(str).str.startswith(str(value), na=False)
    if operator == "endswith":
        return col.astype(str).str.endswith(str(value), na=False)
    raise ValueError(f"不支持的运算符: {operator}")


def _apply_filters(
    df: pd.DataFrame, filters: list[dict[str, Any]] | None
) -> pd.DataFrame:
    """应用筛选条件列表，出错时抛出带上下文的 ValueError。"""
    if not filters:
        return df
    final_mask = pd.Series([True] * len(df))
    for f in filters:
        f_col, f_op, f_val = f.get("column"), f.get("operator"), f.get("value")
        if f_col and f_op and f_val is not None:
            final_mask &= _get_filter_mask(df, f_col, f_op, f_val)
    return df[final_mask]


@tool
def filter_data(
    column: str | None = None,
    operator: str | None = None,
    value: Any = None,
    filters: list[dict[str, Any]] | None = None,
    select_columns: list[str] | None = None,
    sort_by: str | None = None,
    ascending: bool = True,
    limit: int = 20,
) -> dict[str, Any]:
    """按条件筛选数据，支持多条件 AND、排序和指定返回列。

    Args:
        column: 单条件筛选时的列名
        operator: 比较运算符，仅支持: ==, !=, >, <, >=, <=, contains, startswith, endswith。
                  注意: 不支持 between/equals，请用多个 >= 和 <= 条件代替 between
        value: 单条件筛选时的比较值（支持字符串、数值等任意类型）
        filters: 多条件筛选列表，每个元素为 {"column": "...", "operator": "...", "value": ...}
        select_columns: 指定返回的列名列表，为空则返回所有列
        sort_by: 排序列名，可选
        ascending: 排序方向，True为升序，False为降序
        limit: 返回结果数量限制，默认20
    """
    try:
        df = _active_df().copy()
        final_mask = pd.Series([True] * len(df))
        if column and operator and value is not None:
            final_mask &= _get_filter_mask(df, column, operator, value)
        if filters:
            for f in filters:
                f_col, f_op, f_val = f.get("column"), f.get("operator"), f.get("value")
                if f_col and f_op and f_val is not None:
                    final_mask &= _get_filter_mask(df, f_col, f_op, f_val)
        result_df = df[final_mask]
        if sort_by:
            if sort_by not in result_df.columns:
                return {
                    "error": f"排序列 '{sort_by}' 不存在，可用列: {list(result_df.columns)}"
                }
            result_df = result_df.sort_values(by=sort_by, ascending=ascending)
        return _df_to_result(result_df, limit, select_columns)
    except Exception as e:
        return {"error": f"筛选出错: {e!s}"}


@tool
def aggregate_data(
    column: str,
    agg_func: str,
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """对指定列进行聚合统计。可选先筛选数据再聚合。

    Args:
        column: 要统计的列名
        agg_func: 聚合函数，可选值: sum, mean, count, min, max, median, std
        filters: 可选的筛选条件列表（operator 同 filter_data）
    """
    try:
        df = _apply_filters(_active_df().copy(), filters)
        if column not in df.columns:
            return {"error": f"列 '{column}' 不存在，可用列: {list(df.columns)}"}
        col = df[column]
        if agg_func == "sum":
            result = col.sum()
        elif agg_func == "mean":
            result = col.mean()
        elif agg_func == "count":
            result = col.count()
        elif agg_func == "min":
            result = col.min()
        elif agg_func == "max":
            result = col.max()
        elif agg_func == "median":
            result = col.median()
        elif agg_func == "std":
            result = col.std()
        else:
            return {"error": f"不支持的聚合函数: {agg_func}"}
        return {
            "column": column,
            "function": agg_func,
            "filtered_rows": len(df),
            "result": _safe_value(result),
        }
    except Exception as e:
        return {"error": f"聚合计算出错: {e!s}"}


@tool
def group_and_aggregate(
    group_by: str,
    agg_column: str,
    agg_func: str,
    filters: list[dict[str, Any]] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """按列分组并进行聚合统计。可选先筛选数据再分组。

    Args:
        group_by: 分组列名
        agg_column: 要聚合的列名
        agg_func: 聚合函数，可选值: sum, mean, count, min, max
        filters: 可选的筛选条件列表
        limit: 返回结果数量限制，默认20
    """
    try:
        df = _apply_filters(_active_df().copy(), filters)
        if group_by not in df.columns:
            return {"error": f"分组列 '{group_by}' 不存在，可用列: {list(df.columns)}"}
        if agg_column not in df.columns:
            return {
                "error": f"聚合列 '{agg_column}' 不存在，可用列: {list(df.columns)}"
            }
        grouped = df.groupby(group_by)[agg_column].agg(agg_func).reset_index()
        grouped.columns = [group_by, f"{agg_column}_{agg_func}"]
        grouped = grouped.sort_values(by=grouped.columns[1], ascending=False)
        result = _df_to_result(grouped, limit)
        result["filtered_rows"] = len(df)
        return result
    except Exception as e:
        return {"error": f"分组聚合出错: {e!s}"}


@tool
def sort_data(
    column: str,
    ascending: bool = True,
    filters: list[dict[str, Any]] | None = None,
    select_columns: list[str] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """按指定列排序数据。可选先筛选、指定返回列。

    Args:
        column: 排序列名
        ascending: 是否升序排列，默认True
        filters: 可选的筛选条件列表
        select_columns: 指定返回的列名列表
        limit: 返回结果数量限制，默认20
    """
    try:
        df = _apply_filters(_active_df().copy(), filters)
        if column not in df.columns:
            return {"error": f"列 '{column}' 不存在，可用列: {list(df.columns)}"}
        return _df_to_result(
            df.sort_values(by=column, ascending=ascending), limit, select_columns
        )
    except Exception as e:
        return {"error": f"排序出错: {e!s}"}


@tool
def search_data(
    keyword: str,
    columns: list[str] | None = None,
    select_columns: list[str] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """在指定列或所有列中搜索关键词。

    Args:
        keyword: 搜索关键词
        columns: 要搜索的列名列表，为空则搜索所有列
        select_columns: 指定返回的列名列表
        limit: 返回结果数量限制，默认20
    """
    try:
        df = _active_df()
        search_cols = columns if columns else df.columns
        mask = pd.Series([False] * len(df))
        for col in search_cols:
            if col in df.columns:
                mask |= df[col].astype(str).str.contains(keyword, case=False, na=False)
        return _df_to_result(df[mask], limit, select_columns)
    except Exception as e:
        return {"error": f"搜索出错: {e!s}"}


@tool
def get_column_stats(
    column: str,
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """获取指定列的详细统计信息。可选先筛选数据再统计。

    Args:
        column: 列名
        filters: 可选的筛选条件列表
    """
    try:
        df = _apply_filters(_active_df().copy(), filters)
        if column not in df.columns:
            return {"error": f"列 '{column}' 不存在，可用列: {list(df.columns)}"}
        col = df[column]
        stats: dict[str, Any] = {
            "column": column,
            "filtered_rows": len(df),
            "dtype": str(col.dtype),
            "count": int(col.count()),
            "null_count": int(col.isna().sum()),
            "unique_count": int(col.nunique()),
        }
        if pd.api.types.is_numeric_dtype(col):
            stats.update(
                {
                    "min": float(col.min()) if not col.isna().all() else None,
                    "max": float(col.max()) if not col.isna().all() else None,
                    "mean": float(col.mean()) if not col.isna().all() else None,
                    "median": float(col.median()) if not col.isna().all() else None,
                }
            )
        return stats
    except Exception as e:
        return {"error": f"统计出错: {e!s}"}


@tool
def get_unique_values(
    column: str,
    filters: list[dict[str, Any]] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """获取指定列的唯一值列表（带出现次数）。可选先筛选数据。

    Args:
        column: 列名
        filters: 可选的筛选条件列表
        limit: 返回唯一值数量限制，默认50
    """
    try:
        df = _apply_filters(_active_df().copy(), filters)
        if column not in df.columns:
            return {"error": f"列 '{column}' 不存在，可用列: {list(df.columns)}"}
        value_counts = df[column].value_counts()
        total_unique = len(value_counts)
        if limit:
            value_counts = value_counts.head(limit)
        values = [
            {"value": str(idx), "count": int(count)}
            for idx, count in value_counts.items()
        ]
        return {
            "column": column,
            "filtered_rows": len(df),
            "total_unique": total_unique,
            "returned_unique": len(values),
            "values": values,
        }
    except Exception as e:
        return {"error": f"获取唯一值出错: {e!s}"}


@tool
def get_data_preview(n_rows: int = 10) -> dict[str, Any]:
    """获取当前活跃表的数据预览。

    Args:
        n_rows: 预览行数，默认10行
    """
    try:
        df = _active_df()
        preview_df = df.head(n_rows)
        return {
            "columns": list(df.columns),
            "data": [
                {str(k): _safe_value(v) for k, v in row.items()}
                for row in preview_df.to_dict(orient="records")
            ],
            "preview_rows": len(preview_df),
            "total_rows": len(df),
        }
    except Exception as e:
        return {"error": f"获取预览出错: {e!s}"}


@tool
def get_current_time() -> dict[str, Any]:
    """获取当前系统时间（处理相对时间查询）。"""
    now = datetime.now().astimezone()
    return {
        "current_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "weekday": now.strftime("%A"),
        "timestamp": now.timestamp(),
    }


@tool
def calculate(expressions: list[str]) -> dict[str, Any]:
    """批量执行数学表达式计算（安全环境，禁内置函数）。

    Args:
        expressions: 数学表达式列表，例如 ["(100+200)*0.5", "500/2"]
    """
    safe_env = {
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
        "sum": sum,
        "pow": pow,
        "math": math,
    }
    results: dict[str, Any] = {}
    for expr in expressions:
        try:
            if any(char in expr for char in ["__", "import", "eval", "exec", "open"]):
                results[expr] = "Error: Unsafe expression"
                continue
            results[expr] = eval(expr, {"__builtins__": None}, safe_env)
        except Exception as e:
            results[expr] = f"Error: {e!s}"
    return {"results": results}


# ---- 图表生成（移植自 ExcelMind；当前仅输出 ECharts 配置数据） ----


@tool
def generate_chart(
    chart_type: str | None = None,
    x_column: str | None = None,
    y_column: str | None = None,
    agg_column: str | None = None,
    group_by: str | None = None,
    agg_func: str = "sum",
    title: str = "",
    filters: list[dict[str, Any]] | None = None,
    series_columns: list[str] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """生成 ECharts 图表配置（供可视化展示）。

    Args:
        chart_type: 图表类型: bar, line, pie, scatter, radar, funnel。为空或"auto"时自动推荐
        x_column: X轴数据列名（分类轴）
        y_column: Y轴数据列名（数值轴，单系列时使用）
        agg_column: 聚合列名（y_column 的别名，用于饼图等场景）
        group_by: 分组列名（用于饼图和多系列图）
        agg_func: 聚合函数: sum, mean, count, min, max
        title: 图表标题
        filters: 筛选条件列表（operator 同 filter_data）
        series_columns: 多系列Y轴列名列表
        limit: 数据点数量限制，默认20
    """
    if agg_column and not y_column:
        y_column = agg_column

    try:
        df = _apply_filters(_active_df().copy(), filters)
    except Exception as e:
        return {"error": f"筛选条件错误: {e!s}"}
    if len(df) == 0:
        return {"error": "筛选后无数据，无法生成图表"}

    def recommend_chart_type() -> str:
        if group_by and y_column:
            unique_groups = df[group_by].nunique() if group_by in df.columns else 0
            return "pie" if unique_groups <= 8 else "bar"
        if x_column and y_column:
            x_dtype = df[x_column].dtype if x_column in df.columns else None
            y_dtype = df[y_column].dtype if y_column in df.columns else None
            if pd.api.types.is_numeric_dtype(x_dtype) and pd.api.types.is_numeric_dtype(
                y_dtype
            ):
                return "scatter"
            if pd.api.types.is_datetime64_any_dtype(x_dtype):
                return "line"
            return "bar"
        return "pie" if group_by else "bar"

    final_chart_type = (
        chart_type if chart_type and chart_type != "auto" else recommend_chart_type()
    )
    try:
        chart_data = _prepare_chart_data(
            df,
            final_chart_type,
            x_column,
            y_column,
            group_by,
            agg_func,
            series_columns,
            limit,
        )
        if "error" in chart_data:
            return chart_data
        chart_config = _build_echart_config(final_chart_type, chart_data, title)
        chart_type_names = {
            "bar": "柱状图",
            "line": "折线图",
            "pie": "饼图",
            "scatter": "散点图",
            "radar": "雷达图",
            "funnel": "漏斗图",
        }
        return {
            "chart": chart_config,
            "chart_type": final_chart_type,
            "message": f"已生成{chart_type_names.get(final_chart_type, final_chart_type)}，共 {chart_data.get('data_count', 0)} 个数据点。",
        }
    except Exception as e:
        return {"error": f"生成图表出错: {e!s}"}


def _prepare_chart_data(
    df: pd.DataFrame,
    chart_type: str,
    x_column: str | None,
    y_column: str | None,
    group_by: str | None,
    agg_func: str,
    series_columns: list[str] | None,
    limit: int,
) -> dict[str, Any]:
    """准备图表数据（与 ExcelMind 保持一致）。"""
    if chart_type == "pie":
        if group_by and group_by in df.columns:
            if y_column and y_column in df.columns:
                grouped = df.groupby(group_by)[y_column].agg(agg_func).reset_index()
                grouped.columns = ["name", "value"]
            else:
                grouped = df[group_by].value_counts().reset_index()
                grouped.columns = ["name", "value"]
            grouped = grouped.head(limit)
            data = [
                {"name": str(row["name"]), "value": float(row["value"])}
                for _, row in grouped.iterrows()
            ]
            return {"data": data, "data_count": len(data)}
        return {"error": "饼图需要指定 group_by 分组列"}

    if chart_type == "scatter":
        if not x_column or not y_column:
            return {"error": "散点图需要指定 x_column 和 y_column"}
        if x_column not in df.columns or y_column not in df.columns:
            return {"error": f"列不存在: {x_column} 或 {y_column}"}
        scatter_df = df[[x_column, y_column]].dropna().head(limit * 5)
        return {
            "data": scatter_df.values.tolist(),
            "x_name": x_column,
            "y_name": y_column,
            "data_count": len(scatter_df),
        }

    if chart_type == "radar":
        if not series_columns or len(series_columns) < 3:
            return {"error": "雷达图需要至少3个 series_columns 指标列"}
        valid_cols = [
            c
            for c in series_columns
            if c in df.columns and pd.api.types.is_numeric_dtype(df[c])
        ]
        if len(valid_cols) < 3:
            return {"error": "雷达图需要至少3个有效的数值列"}
        if group_by and group_by in df.columns:
            grouped = df.groupby(group_by)[valid_cols].agg(agg_func).head(limit)
            indicators = [
                {"name": col, "max": float(df[col].max() * 1.2)} for col in valid_cols
            ]
            series_data = [
                {"name": str(name), "value": [float(row[col]) for col in valid_cols]}
                for name, row in grouped.iterrows()
            ]
            return {
                "indicators": indicators,
                "series": series_data,
                "data_count": len(series_data),
            }
        indicators = [
            {"name": col, "max": float(df[col].max() * 1.2)} for col in valid_cols
        ]
        values = [float(df[col].agg(agg_func)) for col in valid_cols]
        return {
            "indicators": indicators,
            "series": [{"name": "数据", "value": values}],
            "data_count": 1,
        }

    if chart_type == "funnel":
        if group_by and group_by in df.columns:
            if y_column and y_column in df.columns:
                grouped = df.groupby(group_by)[y_column].agg(agg_func).reset_index()
                grouped.columns = ["name", "value"]
            else:
                grouped = df[group_by].value_counts().reset_index()
                grouped.columns = ["name", "value"]
            grouped = grouped.sort_values("value", ascending=False).head(limit)
            data = [
                {"name": str(row["name"]), "value": float(row["value"])}
                for _, row in grouped.iterrows()
            ]
            return {"data": data, "data_count": len(data)}
        return {"error": "漏斗图需要指定 group_by 分组列"}

    # bar / line
    if not x_column:
        return {"error": f"{chart_type}图需要指定 x_column"}
    if x_column not in df.columns:
        return {"error": f"列 '{x_column}' 不存在"}
    if series_columns:
        valid_series = [c for c in series_columns if c in df.columns]
        if not valid_series:
            return {"error": "series_columns 中没有有效的列"}
        grouped = df.groupby(x_column)[valid_series].agg(agg_func).head(limit)
        categories = [str(idx) for idx in grouped.index]
        series = [{"name": col, "data": grouped[col].tolist()} for col in valid_series]
        return {
            "categories": categories,
            "series": series,
            "data_count": len(categories),
        }
    if y_column and y_column in df.columns:
        grouped = df.groupby(x_column)[y_column].agg(agg_func).reset_index()
        grouped.columns = ["category", "value"]
        grouped = grouped.sort_values("value", ascending=False).head(limit)
        categories = [str(c) for c in grouped["category"]]
        values = grouped["value"].tolist()
    else:
        grouped = df[x_column].value_counts().head(limit)
        categories = [str(idx) for idx in grouped.index]
        values = grouped.values.tolist()
    return {"categories": categories, "values": values, "data_count": len(categories)}


def _build_echart_config(
    chart_type: str, data: dict[str, Any], title: str
) -> dict[str, Any]:
    """构建 ECharts 配置（与 ExcelMind 保持一致）。"""
    base_config = {
        "title": {"text": title, "left": "center", "textStyle": {"color": "#e5e7eb"}},
        "tooltip": {
            "trigger": "item" if chart_type in ["pie", "scatter", "funnel"] else "axis"
        },
        "backgroundColor": "transparent",
    }

    if chart_type == "pie":
        return {
            **base_config,
            "legend": {
                "orient": "vertical",
                "left": "left",
                "textStyle": {"color": "#9ca3af"},
            },
            "series": [
                {
                    "type": "pie",
                    "radius": ["40%", "70%"],
                    "avoidLabelOverlap": True,
                    "itemStyle": {
                        "borderRadius": 10,
                        "borderColor": "#1f2937",
                        "borderWidth": 2,
                    },
                    "label": {"color": "#e5e7eb"},
                    "emphasis": {
                        "label": {"show": True, "fontSize": 16, "fontWeight": "bold"}
                    },
                    "data": data["data"],
                }
            ],
        }
    if chart_type == "scatter":
        return {
            **base_config,
            "xAxis": {
                "type": "value",
                "name": data.get("x_name", ""),
                "axisLabel": {"color": "#9ca3af"},
                "axisLine": {"lineStyle": {"color": "#4b5563"}},
            },
            "yAxis": {
                "type": "value",
                "name": data.get("y_name", ""),
                "axisLabel": {"color": "#9ca3af"},
                "axisLine": {"lineStyle": {"color": "#4b5563"}},
            },
            "series": [
                {
                    "type": "scatter",
                    "symbolSize": 10,
                    "data": data["data"],
                    "itemStyle": {"color": "#6366f1"},
                }
            ],
        }
    if chart_type == "radar":
        return {
            **base_config,
            "legend": {
                "data": [s["name"] for s in data["series"]],
                "bottom": 0,
                "textStyle": {"color": "#9ca3af"},
            },
            "radar": {
                "indicator": data["indicators"],
                "axisName": {"color": "#9ca3af"},
                "splitLine": {"lineStyle": {"color": "#4b5563"}},
                "splitArea": {
                    "areaStyle": {
                        "color": ["rgba(99,102,241,0.1)", "rgba(99,102,241,0.05)"]
                    }
                },
            },
            "series": [{"type": "radar", "data": data["series"]}],
        }
    if chart_type == "funnel":
        return {
            **base_config,
            "legend": {
                "data": [d["name"] for d in data["data"]],
                "bottom": 0,
                "textStyle": {"color": "#9ca3af"},
            },
            "series": [
                {
                    "type": "funnel",
                    "left": "10%",
                    "width": "80%",
                    "label": {"show": True, "position": "inside", "color": "#fff"},
                    "labelLine": {"show": False},
                    "itemStyle": {"borderColor": "#1f2937", "borderWidth": 1},
                    "emphasis": {"label": {"fontSize": 16}},
                    "data": data["data"],
                }
            ],
        }

    config: dict[str, Any] = {
        **base_config,
        "grid": {"left": "3%", "right": "4%", "bottom": "3%", "containLabel": True},
        "xAxis": {
            "type": "category",
            "data": data["categories"],
            "axisLabel": {
                "color": "#9ca3af",
                "rotate": 30 if len(data["categories"]) > 8 else 0,
            },
            "axisLine": {"lineStyle": {"color": "#4b5563"}},
        },
        "yAxis": {
            "type": "value",
            "axisLabel": {"color": "#9ca3af"},
            "axisLine": {"lineStyle": {"color": "#4b5563"}},
            "splitLine": {"lineStyle": {"color": "#374151"}},
        },
    }
    if "series" in data:
        config["legend"] = {
            "data": [s["name"] for s in data["series"]],
            "bottom": 0,
            "textStyle": {"color": "#9ca3af"},
        }
        config["series"] = [
            {
                "name": s["name"],
                "type": chart_type,
                "data": s["data"],
                "smooth": chart_type == "line",
            }
            for s in data["series"]
        ]
    else:
        config["series"] = [
            {
                "type": chart_type,
                "data": data["values"],
                "smooth": chart_type == "line",
                "itemStyle": {"color": "#6366f1"},
                "areaStyle": {"color": "rgba(99,102,241,0.2)"}
                if chart_type == "line"
                else None,
            }
        ]
    return config


# 工具注册表：Agent 与前端共用
ALL_TOOLS = [
    filter_data,
    aggregate_data,
    group_and_aggregate,
    sort_data,
    search_data,
    get_column_stats,
    get_unique_values,
    get_data_preview,
    get_current_time,
    calculate,
    generate_chart,
]
