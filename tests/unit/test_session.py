from __future__ import annotations

import pandas as pd

from excel_co_pilot.config import settings
from excel_co_pilot.core.session import Session


def test_session_create(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    s = Session()
    assert s.session_id
    assert not s.llm_ready
    assert s.context == []


def test_session_configure_llm(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    s = Session()
    assert not s.llm_ready
    s.configure_llm(api_key="sk-test", base_url="https://x.example", model="m")
    assert s.llm_ready
    assert s.llm_model_name == "m"
    assert s.llm_base_url == "https://x.example"


def test_session_configure_llm_requires_key(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    s = Session()
    try:
        s.configure_llm(api_key="   ")
    except ValueError:
        assert not s.llm_ready
    else:
        raise AssertionError("空 API Key 应抛 ValueError")


def test_attach_files_csv(tmp_path):
    p = tmp_path / "a.csv"
    pd.DataFrame({"x": [1, 2, 3]}).to_csv(p, index=False)
    s = Session()
    results = s.attach_files([p])
    assert len(results) == 1
    assert results[0]["name"] == "a.csv"
    assert results[0]["chars"] > 0
    assert len(s.context) == 1


def test_clear_context():
    s = Session()
    s.context.append({"name": "a.txt", "text": "hello", "chars": 5})
    s.clear_context()
    assert s.context == []
