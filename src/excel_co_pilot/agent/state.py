from __future__ import annotations

from typing import Any, TypedDict

from ..core.dataframe_pool import DataFramePool


class AgentState(TypedDict, total=False):
    df_pool: DataFramePool
    user_input: str
    history: list[dict[str, str]]
    plan: str
    code: str
    error: str | None
    result: Any  # 执行产出的原始对象（DataFrame/Series/标量/字符串）
    result_summary: str | None
    plot: dict | None  # 绘图参数 {"kind", "x", "y", "title"}
    plot_html: str | None  # 渲染后的交互式 HTML
    plot_fig: Any  # 渲染后的 Plotly Figure（供 UI gr.Plot 直接使用）
    retries: int
    reply: str | None


def initial_state(
    pool: DataFramePool, user_input: str, history: list[dict[str, str]]
) -> dict[str, Any]:
    return {
        "df_pool": pool,
        "user_input": user_input,
        "history": history,
        "plan": "",
        "code": "",
        "error": None,
        "result": None,
        "result_summary": None,
        "plot": None,
        "plot_html": None,
        "plot_fig": None,
        "retries": 0,
        "reply": None,
    }
