from __future__ import annotations

import pandas as pd

from excel_co_pilot.core.data_profiler import DataProfiler


def test_profile_shape(sample_df: pd.DataFrame):
    prof = DataProfiler().profile(sample_df)
    assert prof["rows"] == 5
    assert prof["cols"] == 3
    assert len(prof["columns"]) == 3


def test_profile_numeric_stats(sample_df: pd.DataFrame):
    prof = DataProfiler().profile(sample_df)
    amount = next(c for c in prof["columns"] if c["name"] == "amount")
    assert amount["inferred_type"] == "数字"
    assert "mean" in amount
    assert amount["min"] == 80
    assert amount["max"] == 250


def test_profile_detects_issue():
    df = pd.DataFrame({"bad col": [1, 2, 3], "ok": [4, 5, 6]})
    prof = DataProfiler().profile(df)
    assert any("bad col" in issue for issue in prof["issues"])


def test_summary_text(sample_df: pd.DataFrame):
    text = DataProfiler().summary_text(sample_df, label="orders")
    assert "orders" in text
    assert "amount" in text
