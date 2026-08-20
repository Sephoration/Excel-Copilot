from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from excel_co_pilot.core.table_manager import (
    TableManager,
    reset_current_manager,
    set_current_manager,
)
from excel_co_pilot.core.tools import (
    aggregate_data,
    calculate,
    filter_data,
    generate_chart,
    get_column_stats,
    get_data_preview,
    get_unique_values,
    group_and_aggregate,
    search_data,
    sort_data,
)


@pytest.fixture
def bound_manager(tmp_path: Path):
    """注册一个含脏数据（NaN/中文/混合类型）的表，并绑定 contextvar。"""
    path = tmp_path / "sales.csv"
    pd.DataFrame(
        {
            "分公司": ["东城", "西城", "东城", "西城", "南城", "东城"],
            "月份": ["202511", "202511", "202512", "202512", "202511", "202512"],
            "移动新增": [100, 200, 150, 80, 300, None],
            "宽带新增": [10, 20, 15, 8, 30, 12],
        }
    ).to_csv(path, index=False)
    mgr = TableManager()
    mgr.add_table(path)
    token = set_current_manager(mgr)
    yield mgr
    reset_current_manager(token)


def test_filter_data(bound_manager):
    result = filter_data.invoke({"column": "分公司", "operator": "==", "value": "东城"})
    assert result["total_rows"] == 3
    assert all(r["分公司"] == "东城" for r in result["data"])


def test_filter_data_multi_and_sort(bound_manager):
    result = filter_data.invoke(
        {
            "filters": [{"column": "分公司", "operator": "==", "value": "西城"}],
            "sort_by": "移动新增",
            "ascending": False,
        }
    )
    assert result["total_rows"] == 2
    assert result["data"][0]["移动新增"] == 200


def test_aggregate_data(bound_manager):
    result = aggregate_data.invoke({"column": "移动新增", "agg_func": "sum"})
    assert result["function"] == "sum"
    assert result["result"] == 830  # None 被 sum 跳过


def test_group_and_aggregate(bound_manager):
    result = group_and_aggregate.invoke(
        {"group_by": "分公司", "agg_column": "移动新增", "agg_func": "sum", "limit": 10}
    )
    assert result["total_rows"] == 3
    by_name = {r["分公司"]: r["移动新增_sum"] for r in result["data"]}
    assert by_name["东城"] == 250
    assert by_name["西城"] == 280
    assert by_name["南城"] == 300


def test_sort_data(bound_manager):
    result = sort_data.invoke({"column": "宽带新增", "ascending": False, "limit": 10})
    assert result["data"][0]["宽带新增"] == 30


def test_search_data(bound_manager):
    result = search_data.invoke({"keyword": "南城"})
    assert result["total_rows"] == 1
    assert result["data"][0]["分公司"] == "南城"


def test_get_column_stats(bound_manager):
    stats = get_column_stats.invoke({"column": "移动新增"})
    assert stats["count"] == 5  # None 不计
    assert stats["null_count"] == 1
    assert stats["unique_count"] == 5


def test_get_unique_values(bound_manager):
    result = get_unique_values.invoke({"column": "分公司"})
    assert result["total_unique"] == 3


def test_get_data_preview(bound_manager):
    result = get_data_preview.invoke({"n_rows": 2})
    assert result["preview_rows"] == 2
    assert result["total_rows"] == 6


def test_calculate():
    result = calculate.invoke({"expressions": ["(100+200)*0.5", "500/2"]})
    assert result["results"]["(100+200)*0.5"] == 150
    assert result["results"]["500/2"] == 250


def test_calculate_rejects_unsafe():
    expr = "__import__('os').system('x')"
    result = calculate.invoke({"expressions": [expr]})
    assert "Error" in result["results"][expr]


def test_generate_chart_bar(bound_manager):
    result = generate_chart.invoke(
        {"x_column": "分公司", "y_column": "移动新增", "agg_func": "sum"}
    )
    assert "chart" in result
    assert result["chart"]["series"][0]["type"] == "bar"
    assert len(result["chart"]["xAxis"]["data"]) == 3


def test_result_json_serializable(bound_manager):
    import json

    result = group_and_aggregate.invoke(
        {"group_by": "分公司", "agg_column": "移动新增", "agg_func": "sum", "limit": 10}
    )
    json.dumps(result, ensure_ascii=False)  # 不应抛异常
