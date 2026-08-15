from __future__ import annotations

import pandas as pd

from excel_co_pilot.core.dataframe_pool import DataFramePool


def _df() -> pd.DataFrame:
    return pd.DataFrame({"x": [1, 2, 3]})


def test_add_assigns_aliases():
    pool = DataFramePool()
    assert pool.add("a.csv", _df()) == "df_1"
    assert pool.add("b.csv", _df()) == "df_2"
    assert pool.aliases() == ["df_1", "df_2"]


def test_update_result_writes_result():
    pool = DataFramePool()
    pool.add("a.csv", _df())
    pool.update_result(_df())
    assert "result" in pool.aliases()
    assert pool.default_frame_alias() == "result"


def test_undo_restores_previous():
    pool = DataFramePool()
    pool.add("a.csv", _df())
    pool.update_result(pd.DataFrame({"y": [9]}))
    assert "result" in pool.aliases()
    assert pool.undo() is True
    assert "result" not in pool.aliases()
    assert pool.aliases() == ["df_1"]


def test_undo_empty_returns_false():
    pool = DataFramePool()
    assert pool.undo() is False


def test_reset_clears():
    pool = DataFramePool()
    pool.add("a.csv", _df())
    pool.reset()
    assert pool.aliases() == []


def test_to_namespace_has_df_default():
    pool = DataFramePool()
    pool.add("a.csv", _df())
    ns = pool.to_namespace()
    assert ns["df_1"] is not None
    assert ns["df"] is ns["df_1"]
