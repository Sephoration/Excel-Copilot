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


def test_filter_and_search_respect_index_and_literal_patterns(bound_manager):
    df = bound_manager.get_active_df()
    df.index = [10, 20, 30, 40, 50, 60]
    df["编号"] = ["001", "1", "002", "1", None, "003"]
    df["备注"] = ["a.b", "axb", "a[b", "a.b", None, "text"]

    result = filter_data.invoke({"column": "编号", "operator": "==", "value": "001"})
    assert result["total_rows"] == 1
    assert result["data"][0]["编号"] == "001"
    result = aggregate_data.invoke({"column": "移动新增", "agg_func": "sum", "filters": [
        {"column": "编号", "operator": "==", "value": "1"}
    ]})
    assert result["result"] == 280
    result = filter_data.invoke({"column": "备注", "operator": "contains", "value": "."})
    assert result["total_rows"] == 2
    assert search_data.invoke({"keyword": "[", "columns": ["备注"]})["total_rows"] == 1
    assert search_data.invoke({"keyword": "nan", "columns": ["备注"]})["total_rows"] == 0


def test_filter_numeric_input_validation_and_nulls(bound_manager):
    df = bound_manager.get_active_df()
    df["large"] = [9007199254740993, 9007199254740992, 0, 1, 2, 3]
    result = filter_data.invoke({"column": "large", "operator": "==", "value": "9007199254740993"})
    assert result["total_rows"] == 1
    assert result["data"][0]["large"] == 9007199254740993
    assert "error" in filter_data.invoke({"column": "large", "operator": ">", "value": "nan"})
    result = filter_data.invoke({"column": "移动新增", "operator": "!=", "value": 100})
    assert result["total_rows"] == 4


def test_calculator_restricted_arithmetic_and_resource_limits():
    expressions = ["math.sqrt(25) + pow(2, 3)", "sum([1,2,3])", "math.pi * 2",
                   "(1).__class__", "2 ** 1000000000", "math.factorial(1000000000)",
                   "[1] * 100000000", "1/0"]
    results = calculate.invoke({"expressions": expressions})["results"]
    assert results[expressions[0]] == 13
    assert results[expressions[1]] == 6
    assert results[expressions[2]] == pytest.approx(6.283185307179586)
    for expression in expressions[3:]:
        assert results[expression].startswith("Error:")
    assert "error" in calculate.invoke({"expressions": ["1"] * 51})
