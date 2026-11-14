from __future__ import annotations

from excel_co_pilot.core.join_service import _extract_json


def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == '{"a": 1}'


def test_extract_json_from_markdown_block():
    content = '结果如下：\n```json\n{"new_name": "x", "keys1": ["a"]}\n```\n结束'
    assert _extract_json(content) == '{"new_name": "x", "keys1": ["a"]}'


def test_extract_json_embedded():
    content = '分析：{"join_type": "left"} 如上'
    assert _extract_json(content) == '{"join_type": "left"}'


def test_extract_json_no_json():
    assert _extract_json("没有任何 JSON") == "没有任何 JSON"


def test_suggest_join_rejects_invalid_model_output(monkeypatch):
    import pytest

    from excel_co_pilot.core import join_service

    class Message:
        content = '{"new_name":"x","keys1":"id","keys2":["id"],"join_type":"inner"}'

    class Client:
        def invoke(self, prompt):
            return Message()

    monkeypatch.setattr(join_service.settings, "deepseek_api_key", "test")
    monkeypatch.setattr(join_service, "_get_llm", lambda: Client())
    with pytest.raises(ValueError, match="非空列名列表"):
        join_service.suggest_join_config("left", "right")
