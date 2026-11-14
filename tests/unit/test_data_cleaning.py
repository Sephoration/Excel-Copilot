from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from excel_co_pilot.core.data_cleaning import clean_frame
from excel_co_pilot.core.table_manager import TableManager


def test_preview_apply_undo_preserve_source(tmp_path: Path):
    source = tmp_path / "dirty.csv"
    original = "name,amount\n A ,1\n A ,1\n ,2\n"
    source.write_text(original, encoding="utf-8")
    manager = TableManager()
    table_id, _ = manager.add_table(source)
    before = manager.get_active_df().copy(deep=True)
    steps = [
        {"operation": "trim", "column": "name"},
        {"operation": "blank_to_null", "column": "name"},
        {"operation": "drop_duplicates"},
    ]
    preview = manager.preview_cleaning(steps)
    assert preview["before_rows"] == 3 and preview["after_rows"] == 2
    pd.testing.assert_frame_equal(manager.get_active_df(), before)
    applied = manager.apply_cleaning(steps, expected_revision=preview["revision"])
    assert applied["after_rows"] == 2
    assert manager.get_table_info(table_id).total_rows == 2
    assert source.read_text(encoding="utf-8") == original
    with pytest.raises(ValueError, match="重新预览"):
        manager.apply_cleaning(steps, expected_revision=preview["revision"])
    manager.undo_cleaning()
    pd.testing.assert_frame_equal(manager.get_active_df(), before)
    assert manager.get_table_info(table_id).total_rows == 3
    with pytest.raises(ValueError, match="没有可撤销"):
        manager.undo_cleaning()


def test_invalid_numeric_conversion_does_not_change_table(tmp_path: Path):
    source = tmp_path / "mixed.csv"
    source.write_text("amount\n1\noops\n", encoding="utf-8")
    manager = TableManager()
    manager.add_table(source)
    before = manager.get_active_df().copy(deep=True)
    with pytest.raises(ValueError, match="非数值"):
        manager.apply_cleaning([{"operation": "convert_numeric", "column": "amount"}])
    pd.testing.assert_frame_equal(manager.get_active_df(), before)


def test_clean_frame_rejects_unknown_column_and_keeps_input():
    frame = pd.DataFrame({"code": [" 001 ", " 002 "]})
    original = frame.copy(deep=True)
    assert clean_frame(frame, [{"operation": "trim", "column": "code"}])["code"].tolist() == ["001", "002"]
    with pytest.raises(ValueError):
        clean_frame(frame, [{"operation": "trim", "column": "missing"}])
    pd.testing.assert_frame_equal(frame, original)
