from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go

from excel_co_pilot.agent.graph import build_graph
from excel_co_pilot.agent.prompts import (
    PLANNER_SYSTEM,
    REFLECTOR_SYSTEM,
    RESPONDER_SYSTEM,
)
from excel_co_pilot.agent.state import initial_state
from excel_co_pilot.core.dataframe_pool import DataFramePool


class FakeLLM:
    """离线假 LLM，跑通整个 Agent 图而无需真实 API Key。"""

    def __init__(
        self, code: str = "", plot: dict | None = None, reply: str = "好的，已完成。"
    ):
        self.code = code
        self.plot = plot
        self.reply = reply

    def chat(self, messages: list[dict]) -> str:
        system = messages[0]["content"]
        if system == PLANNER_SYSTEM:
            return json.dumps(
                {"explanation": "plan", "code": self.code, "plot": self.plot},
                ensure_ascii=False,
            )
        if system == REFLECTOR_SYSTEM:
            return json.dumps(
                {"explanation": "fix", "code": self.code}, ensure_ascii=False
            )
        if system == RESPONDER_SYSTEM:
            return self.reply
        raise AssertionError(f"未知 system prompt: {system[:40]}")

    def extract_json(self, content: str) -> dict:
        return json.loads(content)


def _pool() -> DataFramePool:
    pool = DataFramePool()
    df = pd.DataFrame({"city": ["a", "a", "b"], "amount": [10, 20, 30]})
    pool.add("orders.csv", df)
    return pool


def test_agent_computation_writes_back():
    pool = _pool()
    llm = FakeLLM(code="result = df.groupby('city')['amount'].sum().reset_index()")
    graph = build_graph(llm)  # type: ignore[arg-type]
    final = graph.invoke(initial_state(pool, "按城市求和", []))
    assert final["reply"] == "好的，已完成。"
    assert "result" in pool.aliases()
    assert final["result_summary"] is not None


def test_agent_plot_produces_figure():
    pool = _pool()
    llm = FakeLLM(
        code="result = df.groupby('city')['amount'].sum().reset_index()",
        plot={"kind": "bar", "x": "city", "y": "amount", "title": "城市金额"},
    )
    graph = build_graph(llm)  # type: ignore[arg-type]
    final = graph.invoke(initial_state(pool, "画柱状图", []))
    assert isinstance(final["plot_fig"], go.Figure)
    assert final["plot_html"] is not None


def test_agent_reflector_retries():
    pool = _pool()

    class FailThenFix(FakeLLM):
        def chat(self, messages: list[dict]) -> str:
            system = messages[0]["content"]
            if system == PLANNER_SYSTEM:
                return '{"explanation":"x","code":"result = df[\\"nonexistent_col\\"]"}'
            if system == REFLECTOR_SYSTEM:
                return '{"explanation":"fix","code":"result = df.groupby(\\"city\\")[\\"amount\\"].sum().reset_index()"}'
            if system == RESPONDER_SYSTEM:
                return "已修复"
            raise AssertionError

    graph = build_graph(FailThenFix())  # type: ignore[arg-type]
    final = graph.invoke(initial_state(pool, "测试", []))
    assert final["reply"] == "已修复"
    assert "result" in pool.aliases()
