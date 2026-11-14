from __future__ import annotations

import json

import pandas as pd
import pytest

from excel_co_pilot.core.data_quality import profile_dataframe


def test_profile_counts_null_blank_duplicates_without_mutating_input():
    frame = pd.DataFrame({
        "姓名": ["张三", " ", None, "张三"],
        "金额": [10, 20, None, 10],
    }, index=[4, 7, 8, 9])
    original = frame.copy(deep=True)
    result = profile_dataframe(frame)
    assert result["total_rows"] == 4
    assert result["duplicate_rows"] == 1
    name, amount = result["columns"]
    assert (name["null_count"], name["blank_count"], name["missing_percent"]) == (1, 1, 50.0)
    assert (amount["null_count"], amount["kind"]) == (1, "数值")
    pd.testing.assert_frame_equal(frame, original)
    json.dumps(result, allow_nan=False, ensure_ascii=False)


def test_profile_empty_and_duplicate_column_names():
    empty = profile_dataframe(pd.DataFrame({"name": pd.Series(dtype="str")}))
    assert empty["total_rows"] == 0
    assert empty["columns"][0]["missing_percent"] == 0
    assert profile_dataframe(pd.DataFrame())["duplicate_rows"] == 0
    duplicate_columns = pd.DataFrame([[1, None], [1, 2]], columns=["x", "x"])
    columns = profile_dataframe(duplicate_columns)["columns"]
    assert len(columns) == 2
    assert [column["null_count"] for column in columns] == [0, 1]


def test_profile_limits_and_non_hashable_objects():
    frame = pd.DataFrame({"list": [[1], [1]], "text": ["a", "b"]})
    result = profile_dataframe(frame, max_columns=1)
    assert result["columns_truncated"] is True
    assert result["columns"][0]["unique_count"] is None
    assert result["duplicate_rows"] is None
    for bad in (0, -1, 501, True):
        with pytest.raises(ValueError):
            profile_dataframe(frame, max_columns=bad)
