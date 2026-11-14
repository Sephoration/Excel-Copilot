from __future__ import annotations

import json
from pathlib import Path

import pytest

from excel_co_pilot.core.data_loader import DataLoader, LoadError


def test_load_csv(sample_csv: Path):
    df = DataLoader().load(sample_csv)
    assert df.shape == (3, 3)
    assert list(df.columns) == ["id", "name", "value"]


def test_load_missing_file(tmp_path: Path):
    with pytest.raises(LoadError):
        DataLoader().load(tmp_path / "nope.csv")


def test_load_unsupported_format(tmp_path: Path):
    p = tmp_path / "data.txt"
    p.write_text("hello")
    with pytest.raises(LoadError):
        DataLoader().load(p)


def test_load_json(tmp_path: Path):
    p = tmp_path / "data.json"
    p.write_text(json.dumps([{"a": 1}, {"a": 2}]))
    df = DataLoader().load(p)
    assert df.shape == (2, 1)


def test_load_gbk_csv(tmp_path: Path):
    p = tmp_path / "gbk.csv"
    content = "名称,值\n张三,1\n".encode("gbk")
    p.write_bytes(content)
    df = DataLoader().load(p)
    assert df.shape == (1, 2)


def test_single_column_csv_preserves_semicolon_in_quoted_header(tmp_path: Path):
    path = tmp_path / "single.csv"
    path.write_text('"name;extra"\n"a;b"\n', encoding="utf-8")
    df = DataLoader().load(path)
    assert list(df.columns) == ["name;extra"]
    assert df.iloc[0, 0] == "a;b"


@pytest.mark.parametrize("separator", [";", "\t"])
def test_csv_detects_delimiter_from_header(tmp_path: Path, separator: str):
    path = tmp_path / "records.csv"
    path.write_text(f'name{separator}value\nalpha{separator}10\n', encoding="utf-8")
    df = DataLoader().load(path)
    assert list(df.columns) == ["name", "value"]
    assert df.iloc[0, 1] == 10


def test_malformed_semicolon_csv_reports_error_instead_of_skipping(tmp_path: Path):
    path = tmp_path / "broken.csv"
    path.write_text('name;value\na;1\nb;2;unexpected\n', encoding="utf-8")
    with pytest.raises(LoadError):
        DataLoader().load(path)


def test_encoding_sample_ending_mid_utf8_character(tmp_path: Path):
    from excel_co_pilot.utils.encoding import detect_encoding

    path = tmp_path / "utf8.csv"
    path.write_text("a" * 7 + "中文", encoding="utf-8")
    assert detect_encoding(path, sample_bytes=8) == "utf-8-sig"
