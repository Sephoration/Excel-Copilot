"""Workbook sheet discovery and bounded exports for in-memory tables."""

from __future__ import annotations

import csv
from itertools import chain
import math
import re
from datetime import date, datetime
from pathlib import Path
from typing import BinaryIO, Literal, Mapping

import pandas as pd

MAX_IMPORT_BYTES = 200 * 1024 * 1024
MAX_EXPORT_BYTES = 50 * 1024 * 1024
MAX_EXPORT_CELLS = 1_000_000
MAX_SHEETS = 20
MAX_CELL_CHARS = 32_767
_EXCEL_SUFFIXES = {".xlsx", ".xlsm"}
_INVALID_SHEET_CHARS = re.compile(r"[\\/*?:\[\]]")


class WorkbookIOError(ValueError):
    """A workbook cannot be read or exported within the supported limits."""


def _check_workbook(path: str | Path) -> Path:
    path = Path(path)
    if path.suffix.lower() not in _EXCEL_SUFFIXES:
        raise WorkbookIOError("请选择 Excel 工作簿（.xlsx 或 .xlsm）")
    if not path.is_file():
        raise WorkbookIOError("工作簿不存在")
    if path.stat().st_size > MAX_IMPORT_BYTES:
        raise WorkbookIOError("工作簿超过 200 MB 导入限制")
    return path


def list_sheets(path: str | Path) -> list[str]:
    """Return names without keeping the Excel handle open."""
    path = _check_workbook(path)
    with pd.ExcelFile(path) as book:
        return list(book.sheet_names)


def load_sheet(path: str | Path, sheet_name: str) -> pd.DataFrame:
    """Load an explicitly selected sheet; reject stale or invalid selections."""
    path = _check_workbook(path)
    with pd.ExcelFile(path) as book:
        if sheet_name not in book.sheet_names:
            raise WorkbookIOError(f"找不到工作表：{sheet_name}")
        return pd.read_excel(book, sheet_name=sheet_name)


class _BoundedWriter:
    """Write-only file adapter accepted by zipfile and checked as bytes arrive."""

    def __init__(self, output: BinaryIO) -> None:
        self.output = output
        self.size = 0

    def write(self, data: bytes) -> int:
        if self.size + len(data) > MAX_EXPORT_BYTES:
            raise WorkbookIOError("导出结果超过 50 MB 限制")
        written = self.output.write(data)
        if written != len(data):
            raise OSError("导出文件写入不完整")
        self.size += written
        return written

    def flush(self) -> None:
        self.output.flush()


class _CsvTextSink:
    def __init__(self, output: _BoundedWriter) -> None:
        self.output = output

    def write(self, text: str) -> int:
        self.output.write(text.encode("utf-8"))
        return len(text)


def _safe_scalar(value: object) -> object:
    if value is None or value is pd.NA or value is pd.NaT:
        return ""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return ""
    if isinstance(value, (pd.Timestamp, datetime, date)):
        value = value.isoformat()
    if isinstance(value, str):
        # A tab/newline or leading spaces may hide a formula trigger in CSV.
        needs_escape = value.lstrip().startswith(("=", "+", "-", "@"))
        if len(value) + int(needs_escape) > MAX_CELL_CHARS:
            raise WorkbookIOError("单元格文本超过 Excel 的长度限制")
        if needs_escape:
            return "'" + value
    return value


def _safe_sheet_name(name: str, used: set[str]) -> str:
    base = _INVALID_SHEET_CHARS.sub("_", str(name)).strip(" '")[:31] or "Sheet"
    candidate = base
    index = 2
    while candidate.casefold() in used:
        suffix = f"_{index}"
        candidate = base[: 31 - len(suffix)] + suffix
        index += 1
    used.add(candidate.casefold())
    return candidate


def write_export(
    frames: Mapping[str, pd.DataFrame],
    output: BinaryIO,
    format: Literal["csv", "xlsx"],
) -> None:
    """Write tables to a caller-owned binary stream; never alter source frames.

    CSV accepts one table; XLSX accepts up to 20. The stream may contain partial
    output if an IO or size error occurs, so callers should use a temporary file
    and expose it only after this function succeeds.
    """
    if not frames or len(frames) > MAX_SHEETS:
        raise WorkbookIOError("请选择 1 至 20 张表导出")
    if format not in ("csv", "xlsx"):
        raise WorkbookIOError("只支持 CSV 或 XLSX 导出")
    if format == "csv" and len(frames) != 1:
        raise WorkbookIOError("CSV 一次只能导出一张表")
    cells = 0
    for frame in frames.values():
        if not isinstance(frame, pd.DataFrame):
            raise WorkbookIOError("导出数据必须是表格")
        if frame.shape[1] > 16_384 or frame.shape[0] + 1 > 1_048_576:
            raise WorkbookIOError("表格超出 Excel 的行列限制")
        cells += (frame.shape[0] + 1) * frame.shape[1]
        if cells > MAX_EXPORT_CELLS:
            raise WorkbookIOError("导出数据超过 100 万个单元格")
    bounded = _BoundedWriter(output)
    if format == "csv":
        bounded.write(b"\xef\xbb\xbf")
        writer = csv.writer(_CsvTextSink(bounded), lineterminator="\r\n")
        frame = next(iter(frames.values()))
        writer.writerow([_safe_scalar(str(column)) for column in frame.columns])
        for row in frame.itertuples(index=False, name=None):
            writer.writerow([_safe_scalar(value) for value in row])
    else:
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell

        book = Workbook(write_only=True)
        used: set[str] = set()
        for name, frame in frames.items():
            sheet = book.create_sheet(_safe_sheet_name(name, used))
            for row in chain(([str(column) for column in frame.columns],), frame.itertuples(index=False, name=None)):
                cells_out = []
                for value in row:
                    cell = WriteOnlyCell(sheet, value=_safe_scalar(value))
                    if isinstance(cell.value, str):
                        cell.data_type = "s"  # Never write user text as a formula.
                    cells_out.append(cell)
                sheet.append(cells_out)
        book.save(bounded)
    bounded.flush()
