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
