from __future__ import annotations

from pathlib import Path

import pandas as pd

from excel_co_pilot.config import settings
from excel_co_pilot.core.session import Session, SessionManager


def _csv(tmp_path: Path) -> Path:
    p = tmp_path / "a.csv"
    pd.DataFrame({"x": [1, 2, 3]}).to_csv(p, index=False)
    return p


def test_session_load(tmp_path: Path):
    s = Session()
    alias = s.load(_csv(tmp_path))
    assert alias == "df_1"
    assert "df_1" in s.frames()


def test_session_load_many(tmp_path: Path):
    s = Session()
    loaded = s.load_many([_csv(tmp_path)])
    assert list(loaded) == ["df_1"]


def test_session_chat_without_llm(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    s = Session()
    result = s.chat("统计一下")
    assert "未配置" in result["reply"]
    assert result["plot_fig"] is None


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


def test_session_undo_reset(tmp_path: Path):
    s = Session()
    s.load(_csv(tmp_path))
    assert s.undo() is True
    assert s.frames() == {}
    s.load(_csv(tmp_path))
    s.reset()
    assert s.frames() == {}


def test_session_manager_isolation(tmp_path: Path):
    mgr = SessionManager()
    a = mgr.get("a")
    b = mgr.get("b")
    a.load(_csv(tmp_path))
    assert a.frames() != {}
    assert b.frames() == {}
    assert mgr.get("a") is a
