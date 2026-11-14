from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from excel_co_pilot.core import workbook_io
from excel_co_pilot.core.workbook_io import (
    WorkbookIOError,
    list_sheets,
    load_sheet,
    write_export,
)


def test_csv_export_escapes_spreadsheet_formulas_without_mutating_source():
    frame = pd.DataFrame({"=危险列": ["=1+1", "  @cmd", "normal", None], "金额": [2, -4, 0, 1]})
    original = frame.copy(deep=True)
    output = io.BytesIO()
    write_export({"销售": frame}, output, "csv")
    text = output.getvalue().decode("utf-8-sig")
    assert text.startswith("'=危险列,金额\r\n")
    assert "'=1+1,2\r\n" in text
    assert "'  @cmd,-4\r\n" in text
    pd.testing.assert_frame_equal(frame, original)


def test_export_rejects_multiple_frames_as_csv_before_writing():
    output = io.BytesIO()
    with pytest.raises(WorkbookIOError, match="一张表"):
        write_export({"a": pd.DataFrame(), "b": pd.DataFrame()}, output, "csv")
    assert output.getvalue() == b""


def test_export_checks_cell_limit_before_writing(monkeypatch):
    monkeypatch.setattr(workbook_io, "MAX_EXPORT_CELLS", 2)
    output = io.BytesIO()
    with pytest.raises(WorkbookIOError, match="单元格"):
        write_export({"x": pd.DataFrame({"a": [1, 2]})}, output, "csv")
    assert output.getvalue() == b""


def test_sheet_names_are_sanitized_and_unique():
    used: set[str] = set()
    assert workbook_io._safe_sheet_name("a/b", used) == "a_b"
    assert workbook_io._safe_sheet_name("A_B", used) == "A_B_2"
    assert len(workbook_io._safe_sheet_name("x" * 100, used)) == 31


def test_workbook_requires_supported_file(tmp_path: Path):
    file = tmp_path / "book.csv"
    file.write_text("a,b\n1,2\n")
    with pytest.raises(WorkbookIOError, match="Excel"):
        list_sheets(file)


def test_xlsx_multi_sheet_export_has_no_formula_cells(tmp_path: Path):
    openpyxl = pytest.importorskip("openpyxl")
    output = io.BytesIO()
    write_export({
        "原始/数据": pd.DataFrame({"cell": ["=1+1", "@SUM(A1:A2)"]}),
        "原始:数据": pd.DataFrame({"cell": ["normal"]}),
    }, output, "xlsx")
    with zipfile.ZipFile(io.BytesIO(output.getvalue())) as archive:
        for filename in archive.namelist():
            if filename.startswith("xl/worksheets/") and filename.endswith(".xml"):
                assert b"<f>" not in archive.read(filename)
    path = tmp_path / "export.xlsx"
    path.write_bytes(output.getvalue())
    assert list_sheets(path) == ["原始_数据", "原始_数据_2"]
    assert load_sheet(path, "原始_数据").iloc[0, 0].startswith("'")
    with pytest.raises(WorkbookIOError, match="找不到"):
        load_sheet(path, "missing")


def test_csv_export_enforces_output_byte_limit(monkeypatch):
    monkeypatch.setattr(workbook_io, "MAX_EXPORT_BYTES", 8)
    with pytest.raises(WorkbookIOError, match="50 MB"):
        write_export({"x": pd.DataFrame({"name": ["long text"]})}, io.BytesIO(), "csv")
