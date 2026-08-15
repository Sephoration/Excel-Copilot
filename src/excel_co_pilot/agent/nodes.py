from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..config import settings
from ..core.dataframe_pool import DataFramePool
from ..core.safe_executor import SafeExecutor
from ..core.visualizer import Visualizer
from ..services.llm_gateway import LLMError, LLMGateway
from .prompts import PLANNER_SYSTEM, REFLECTOR_SYSTEM, RESPONDER_SYSTEM
from .state import AgentState


def _executor() -> SafeExecutor:
    return SafeExecutor(timeout=settings.executor_timeout)


def _summarize_result(result: Any) -> str:
    if isinstance(result, pd.DataFrame):
        if result.empty:
            return "结果为空 DataFrame"
        head = result.head(8).to_string(index=False)
        return (
            f"结果 DataFrame：{result.shape[0]} 行 x {result.shape[1]} 列\n"
            f"列：{list(result.columns)}\n"
            f"前 8 行预览：\n{head}"
        )
    if isinstance(result, pd.Series):
        return f"结果 Series（{len(result)} 项）：\n{result.to_string()}"
    if isinstance(result, np.generic):
        return f"结果：{result.item()}"
    return f"结果：{result}"


def planner_node(state: AgentState, llm: LLMGateway) -> dict[str, Any]:
    pool: DataFramePool = state["df_pool"]
    context = f"数据摘要：\n{pool.summary_text()}"
    history = state.get("history") or []
    if history:
        recent = history[-6:]
        turns = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in recent)
        context += f"\n\n历史对话（最近 {len(recent)} 条，仅作理解上下文用）：\n{turns}"
    messages = [
        {"role": "system", "content": PLANNER_SYSTEM},
        {"role": "user", "content": f"{context}\n\n用户指令：{state['user_input']}"},
    ]
    content = llm.chat(messages)
    plan = llm.extract_json(content)
    plot = plan.get("plot")
    if plot is not None and not isinstance(plot, dict):
        plot = None
    code = str(plan.get("code", "")).strip()
    return {
        "plan": str(plan.get("explanation", "")),
        "code": code,
        "plot": plot,
        "error": None if code else "规划器未生成可执行代码，请换一种表述重试",
        "result": None,
        "result_summary": None,
        "plot_html": None,
        "plot_fig": None,
    }


def executor_node(state: AgentState) -> dict[str, Any]:
    # 上游（planner/reflector）已报错则不再执行，避免覆盖错误信息
    if state.get("error"):
        return {}
    pool: DataFramePool = state["df_pool"]
    ns = {**pool.to_namespace(), "pd": pd, "np": np}
    try:
        result = _executor().execute(state["code"], ns)
        # 结果若是 DataFrame，写回池中供后续多步操作引用
        if isinstance(result, pd.DataFrame) and not result.empty:
            pool.update_result(result)
        return {
            "result": result,
            "result_summary": _summarize_result(result),
            "error": None,
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def visualizer_node(state: AgentState) -> dict[str, Any]:
    """根据 plan 中的绘图参数，把执行结果渲染成交互式 HTML。"""
    result = state.get("result")
    plot = state.get("plot") or {}
    if not isinstance(result, pd.DataFrame) or result.empty:
        return {"plot_html": None, "plot_fig": None}

    visualizer = Visualizer()
    kind = str(plot.get("kind") or visualizer.recommend(result))
    x = plot.get("x")
    y = plot.get("y")
    title = str(plot.get("title") or "")
    try:
        fig = visualizer.build(result, kind=kind, x=x, y=y, title=title)
        return {"plot_html": visualizer.render(fig, full_html=True), "plot_fig": fig}
    except Exception as exc:
        return {"plot_html": f"<!-- 绘图失败: {exc} -->", "plot_fig": None}


def reflector_node(state: AgentState, llm: LLMGateway) -> dict[str, Any]:
    pool: DataFramePool = state["df_pool"]
    context = f"数据摘要：\n{pool.summary_text()}"
    messages = [
        {"role": "system", "content": REFLECTOR_SYSTEM},
        {
            "role": "user",
            "content": (
                f"{context}\n\n"
                f"用户指令：{state['user_input']}\n"
                f"原始代码：\n```python\n{state['code']}\n```\n"
                f"执行错误：\n{state['error']}"
            ),
        },
    ]
    content = llm.chat(messages)
    fix = llm.extract_json(content)
    new_code = str(fix.get("code", "")).strip()
    if not new_code:
        return {
            "error": None,
            "reply": f"无法修复此错误：{fix.get('explanation', state['error'])}",
        }
    return {
        "code": new_code,
        "plan": f"{state['plan']}（修正：{fix.get('explanation', '')}）",
        "retries": state["retries"] + 1,
        "error": None,
        "result": None,
        "result_summary": None,
    }


def responder_node(state: AgentState, llm: LLMGateway) -> dict[str, Any]:
    result_text = state.get("result_summary") or f"执行失败：{state.get('error')}"
    user_content = (
        f"用户指令：{state['user_input']}\n"
        f"执行计划：{state.get('plan', '')}\n"
        f"执行结果：\n{result_text}"
    )
    if state.get("plot_html"):
        user_content += "\n\n（已生成一张图表，请一并简要说明。）"
    messages = [
        {"role": "system", "content": RESPONDER_SYSTEM},
        {"role": "user", "content": user_content},
    ]
    try:
        reply = llm.chat(messages)
    except LLMError as exc:
        reply = f"（LLM 生成回答失败：{exc}）\n\n{result_text}"
    return {"reply": reply}
