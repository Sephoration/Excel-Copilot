from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from excel_co_pilot.core.table_manager import (
    TableManager,
    get_current_manager,
    reset_current_manager,
    set_current_manager,
)


@pytest.fixture
def sales_csv(tmp_path: Path) -> Path:
    path = tmp_path / "sales.csv"
    pd.DataFrame(
        {
            "分公司": ["东城", "西城", "东城", "西城", "南城"],
            "月份": ["202511", "202511", "202512", "202512", "202511"],
            "移动新增": [100, 200, 150, 80, 300],
            "宽带新增": [10, 20, 15, 8, 30],
        }
    ).to_csv(path, index=False)
    return path


@pytest.fixture
def dept_csv(tmp_path: Path) -> Path:
    path = tmp_path / "dept.csv"
    pd.DataFrame(
        {"分公司ID": ["D01", "D02", "D03"], "分公司": ["东城", "西城", "南城"]}
    ).to_csv(path, index=False)
    return path


@pytest.fixture
def num_csv(tmp_path: Path) -> Path:
    path = tmp_path / "num.csv"
    pd.DataFrame(
        {"分公司ID": ["D01", "D02", "D03", "D01"], "移动新增": [100, 200, 300, 150]}
    ).to_csv(path, index=False)
    return path


def test_add_table(sales_csv: Path):
    mgr = TableManager()
    table_id, structure = mgr.add_table(sales_csv)
    assert mgr.is_loaded
    assert mgr.active_table_id == table_id
    assert structure["total_rows"] == 5
    assert structure["total_columns"] == 4
    cols = {c["name"] for c in structure["columns"]}
    assert cols == {"分公司", "月份", "移动新增", "宽带新增"}


def test_add_table_unsupported(tmp_path: Path):
    p = tmp_path / "data.txt"
    p.write_text("hello")
    with pytest.raises(ValueError):
        TableManager().add_table(p)


def test_active_switch_and_remove(sales_csv: Path, dept_csv: Path):
    mgr = TableManager()
    id1, _ = mgr.add_table(sales_csv)
    id2, _ = mgr.add_table(dept_csv)
    assert mgr.active_table_id == id2

    assert mgr.set_active_table(id1)
    assert mgr.active_table_id == id1
    assert not mgr.set_active_table("nope")

    assert mgr.remove_table(id1)
    assert mgr.active_table_id == id2
    assert not mgr.remove_table("nope")


def test_list_tables(sales_csv: Path, dept_csv: Path):
    mgr = TableManager()
    _id1, _ = mgr.add_table(sales_csv)
    id2, _ = mgr.add_table(dept_csv)
    rows = mgr.list_tables()
    assert len(rows) == 2
    active = [r for r in rows if r["is_active"]]
    assert len(active) == 1
    assert active[0]["id"] == id2  # 最后添加的自动设为活跃表


def test_join_tables_inner(dept_csv: Path, num_csv: Path):
    mgr = TableManager()
    id1, _ = mgr.add_table(dept_csv)
    id2, _ = mgr.add_table(num_csv)
    new_id, structure = mgr.join_tables(
        id1, id2, keys1=["分公司ID"], keys2=["分公司ID"], join_type="inner"
    )
    assert structure["total_rows"] == 4  # D01 出现两次
    info = mgr.get_table_info(new_id)
    assert info is not None and info.is_joined
    assert mgr.active_table_id == new_id


def test_join_tables_missing_key(dept_csv: Path, num_csv: Path):
    mgr = TableManager()
    id1, _ = mgr.add_table(dept_csv)
    id2, _ = mgr.add_table(num_csv)
    with pytest.raises(ValueError):
        mgr.join_tables(id1, id2, keys1=["不存在"], keys2=["分公司ID"])


def test_active_summary_contains_schema(sales_csv: Path):
    mgr = TableManager()
    mgr.add_table(sales_csv)
    summary = mgr.get_active_summary()
    assert "分公司" in summary
    assert "移动新增" in summary
    assert "5 行" in summary


def test_current_manager_contextvar(sales_csv: Path):
    mgr = TableManager()
    mgr.add_table(sales_csv)
    token = set_current_manager(mgr)
    try:
        assert get_current_manager() is mgr
    finally:
        reset_current_manager(token)
    with pytest.raises(ValueError):
        get_current_manager()


@pytest.mark.parametrize("join_type", ["inner", "left", "right", "outer"])
def test_estimated_join_rows_matches_pandas(join_type):
    from excel_co_pilot.core.table_manager import _estimate_join_rows

    left = pd.DataFrame({"_left_count": [1.0, 1.0, 2.0, None, None]})
    right = pd.DataFrame({"key": [1.0, 1.0, 1.0, 3.0, None]})
    expected = pd.merge(left, right, left_on="_left_count", right_on="key", how=join_type)
    assert _estimate_join_rows(left, right, ["_left_count"], ["key"], join_type) == len(expected)


def test_join_rejects_large_result_without_changing_session(tmp_path, monkeypatch):
    import excel_co_pilot.core.table_manager as manager_module

    left, right = tmp_path / "left.csv", tmp_path / "right.csv"
    pd.DataFrame({"key": [1, 1, 1]}).to_csv(left, index=False)
    pd.DataFrame({"key": [1, 1, 1]}).to_csv(right, index=False)
    mgr = TableManager()
    left_id, _ = mgr.add_table(left)
    right_id, _ = mgr.add_table(right)
    monkeypatch.setattr(manager_module, "_MAX_JOIN_ROWS", 8)
    with pytest.raises(ValueError, match="预计连接结果有 9 行"):
        mgr.join_tables(left_id, right_id, ["key"], ["key"])
    assert mgr.active_table_id == right_id
    assert len(mgr.list_tables()) == 2


def test_join_rejects_repeated_keys(dept_csv: Path, num_csv: Path):
    mgr = TableManager()
    id1, _ = mgr.add_table(dept_csv)
    id2, _ = mgr.add_table(num_csv)
    with pytest.raises(ValueError, match="不能重复"):
        mgr.join_tables(id1, id2, ["分公司ID", "分公司ID"], ["分公司ID", "分公司ID"])


def test_table_import_preserves_single_column_csv(tmp_path: Path):
    path = tmp_path / "single.csv"
    path.write_text('name\n"a;b"\n', encoding="utf-8")
    manager = TableManager()
    manager.add_table(path)
    assert manager.get_active_df().iloc[0, 0] == "a;b"
    assert list(manager.get_active_df().columns) == ["name"]


def test_estimated_join_rows_with_empty_table_and_compound_null_keys():
    from excel_co_pilot.core.table_manager import _estimate_join_rows

    empty = pd.DataFrame({"a": pd.Series([], dtype="float64"), "b": pd.Series([], dtype="object")})
    rows = pd.DataFrame({"a": [1.0, None, None], "b": ["x", None, None]})
    for how in ("inner", "left", "right", "outer"):
        expected = pd.merge(empty, rows, on=["a", "b"], how=how)
        assert _estimate_join_rows(empty, rows, ["a", "b"], ["a", "b"], how) == len(expected)
