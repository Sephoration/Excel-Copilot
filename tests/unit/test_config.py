from __future__ import annotations

from pathlib import Path

from excel_co_pilot import config


def test_save_env_writes_file_and_syncs_settings(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / ".env"
    monkeypatch.setattr(config.settings, "deepseek_api_key", "")
    config.save_env(
        {"DEEPSEEK_API_KEY": "sk-123", "LLM_MODEL": "deepseek-chat"},
        path=cfg_path,
    )
    content = cfg_path.read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=sk-123" in content
    assert "LLM_MODEL=deepseek-chat" in content
    # 内存中的 settings 单例同步更新，无需重启即生效
    assert config.settings.deepseek_api_key == "sk-123"


def test_save_env_updates_existing_key(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(config.settings, "deepseek_api_key", "")
    cfg_path = tmp_path / ".env"
    cfg_path.write_text(
        "DEEPSEEK_API_KEY=old\nLLM_BASE_URL=https://api.deepseek.com\n",
        encoding="utf-8",
    )
    config.save_env({"DEEPSEEK_API_KEY": "sk-new"}, path=cfg_path)
    content = cfg_path.read_text(encoding="utf-8")
    assert content.count("DEEPSEEK_API_KEY") == 1
    assert "DEEPSEEK_API_KEY=sk-new" in content
    assert "LLM_BASE_URL=https://api.deepseek.com" in content
