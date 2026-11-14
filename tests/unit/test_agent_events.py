from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessageChunk, ToolMessage

from excel_co_pilot.core import agent as agent_module


class FakeAgent:
    def __init__(self, messages):
        self.messages = messages
        self.input_messages = None

    async def astream(self, state, **kwargs):
        self.input_messages = state["messages"]
        for message in self.messages:
            yield message, {}


class FakeManager:
    is_loaded = True

    def get_active_summary(self):
        return "2 rows"

    def get_active_table_info(self):
        return SimpleNamespace(filename="sales.csv")


def collect(monkeypatch, streamed, history=None):
    fake = FakeAgent(streamed)
    monkeypatch.setattr(agent_module, "ensure_llm_ready", lambda: None)
    monkeypatch.setattr(agent_module, "build_chat_openai", lambda: object())
    monkeypatch.setattr(agent_module, "create_agent", lambda *a, **kw: fake)

    async def run():
        return [event async for event in agent_module._run("合计多少", history, FakeManager(), "无")]

    return asyncio.run(run()), fake


def test_final_answer_is_not_mixed_with_intermediate_text(monkeypatch):
    events, _ = collect(monkeypatch, [
        AIMessageChunk(content="先看看数据"),
        AIMessageChunk(content="", tool_call_chunks=[{"id": "call-1", "name": "aggregate_data", "args": '{"column":"金额"', "index": 0}]),
        AIMessageChunk(content="", tool_call_chunks=[{"id": None, "name": None, "args": ',"agg_func":"sum"}', "index": 0}]),
        ToolMessage(content='{"result": 12}', name="aggregate_data", tool_call_id="call-1"),
        AIMessageChunk(content="金额合计为 12。"),
    ])
    assert [e["type"] for e in events].count("tool_call") == 1
    assert [e["type"] for e in events].count("tool_result") == 1
    call = next(e for e in events if e["type"] == "tool_call")
    assert call["args"] == {"column": "金额", "agg_func": "sum"}
    assert events[-1] == {"type": "done", "content": "金额合计为 12。"}
    assert "先看看数据" not in events[-1]["content"]


def test_multiple_tool_results_pair_with_their_own_call(monkeypatch):
    events, _ = collect(monkeypatch, [
        AIMessageChunk(content="", tool_call_chunks=[{"id": "a", "name": "first", "args": '{"x":1}', "index": 0}]),
        AIMessageChunk(content="", tool_call_chunks=[{"id": "b", "name": "second", "args": '{"y":2}', "index": 1}]),
        ToolMessage(content='{"value":1}', name="first", tool_call_id="a"),
        ToolMessage(content='{"value":2}', name="second", tool_call_id="b"),
        AIMessageChunk(content="完成。"),
    ])
    calls = [e for e in events if e["type"] == "tool_call"]
    assert [(c["name"], c["args"]) for c in calls] == [
        ("first", {"x": 1}), ("second", {"y": 2})
    ]
    assert events[-1]["content"] == "完成。"


def test_empty_model_answer_is_error_and_history_is_bounded(monkeypatch):
    history = [{"role": "user", "content": f"old {i}"} for i in range(30)]
    events, fake = collect(monkeypatch, [], history)
    assert events[-1]["type"] == "error"
    assert len(fake.input_messages) == 17
    assert fake.input_messages[0].content == "old 14"
