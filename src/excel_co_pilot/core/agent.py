"""表格分析 Agent：基于 LangChain 1.x `create_agent`（LangGraph）的轻量工具调用循环。

对话时把工具调用 / 结果 / 思考 / 流式回答转成事件流，供 UI 实时展示
（移植自 ExcelMind 的 stream.py 事件解析思路，API 升级为 LangChain 1.x）。
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    ToolMessage,
)

from ..config import settings
from .llm_factory import build_chat_openai, ensure_llm_ready
from .table_manager import (
    TableManager,
    get_current_manager,
    reset_current_manager,
    set_current_manager,
)
from .tools import ALL_TOOLS

SYSTEM_PROMPT = """你是一个专业的 Excel 数据分析助手，帮助用户分析和查询表格数据。

## 当前表格信息
{excel_summary}

## 相关知识参考
{knowledge_context}

## 工作原则
1. 根据用户问题，判断是否需要使用工具；不需要时可直接回答
2. 先理解数据结构，再进行分析；复杂问题分步骤解决
3. 涉及精确数字的内容，一定要调用工具获取，不要自己推算
4. 如果工具返回错误，尝试换一种方式（如换列名、换运算符）重试，实在无法解决再提醒用户
5. 如果取出的数据明显有误，必须重新调用工具，不能使用错误数据
6. 回答紧紧围绕用户的问题，核心问题回答完后再作简要补充
7. 回答中使用中文，用表格展示关键数据，语气友好并给出简要的数据分析建议
"""


def _message_text(content: Any) -> str:
    """Extract visible text from either plain or structured model content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part if isinstance(part, str) else str(part.get("text", ""))
            for part in content
            if isinstance(part, str)
            or (isinstance(part, dict) and part.get("type") == "text")
        )
    return ""


async def analyze_stream(
    question: str,
    history: list[dict[str, str]] | None = None,
    manager: TableManager | None = None,
    knowledge_context: str = "（暂无相关知识参考）",
) -> AsyncGenerator[dict[str, Any]]:
    """执行一次表格分析对话，产出事件流。

    事件类型:
    - thinking: 模型思考过程文本
    - tool_call: {"name", "args"} 工具调用开始
    - tool_result: {"name", "result"} 工具执行结果
    - token: 最终回答的流式文本增量
    - done: {"content"} 回答完成
    - error: {"content"} 出错
    """
    manager = manager or get_current_manager()
    if not manager.is_loaded:
        yield {"type": "error", "content": "请先上传 Excel 表格文件"}
        return

    token = set_current_manager(manager)
    try:
        async for event in _run(question, history, manager, knowledge_context):
            yield event
    except Exception as exc:
        yield {"type": "error", "content": str(exc)}
    finally:
        reset_current_manager(token)


async def _run(
    question: str,
    history: list[dict[str, str]] | None,
    manager: TableManager,
    knowledge_context: str,
) -> AsyncGenerator[dict[str, Any]]:
    thinking_done_sent = False
    try:
        excel_summary = manager.get_active_summary()
        ensure_llm_ready()
        llm = build_chat_openai()
        agent = create_agent(
            llm,
            ALL_TOOLS,
            system_prompt=SYSTEM_PROMPT.format(
                excel_summary=excel_summary, knowledge_context=knowledge_context
            ),
        )
        # LangGraph 每次工具循环 = LLM 节点 + 工具节点 2 步；+8 为初始与收尾余量
        config = {"recursion_limit": settings.agent_max_iterations * 2 + 8}

        yield {"type": "thinking", "content": "正在规划解答…"}

        active_info = manager.get_active_table_info()
        current_table = active_info.filename if active_info else "未知表"
        messages: list[Any] = []
        # Only recent, non-empty conversation is relevant to the current table.
        for msg in (history or [])[-16:]:
            content = msg.get("content", "")
            if not isinstance(content, str) or not content.strip():
                continue
            if msg.get("role") == "user":
                messages.append(HumanMessage(content=content))
            elif msg.get("role") == "assistant":
                messages.append(AIMessage(content=content))
        messages.append(
            HumanMessage(content=f"[当前操作表: {current_table}] {question}")
        )

        # 工具调用的 id、名称和参数可能分散在多个 chunk 中；按 id 累积。
        call_by_id: dict[str, dict[str, str]] = {}
        call_id_by_index: dict[int, str] = {}
        pending_by_index: dict[int, dict[str, str]] = {}
        final_content = ""
        had_tool_result = False

        async for chunk in agent.astream(
            {"messages": messages}, stream_mode="messages", config=config
        ):
            if not (isinstance(chunk, tuple) and len(chunk) >= 2):
                continue
            msg, _meta = chunk[0], chunk[1]

            if isinstance(msg, AIMessageChunk):
                content = _message_text(getattr(msg, "content", ""))
                tool_call_chunks = getattr(msg, "tool_call_chunks", []) or []

                if tool_call_chunks:
                    # An intermediate assistant message may have text preceding a
                    # tool call. It is not the final answer to show the user.
                    final_content = ""
                    for tcc in tool_call_chunks:
                        tc_id = tcc.get("id")
                        tc_index = tcc.get("index")
                        if tc_index is None:
                            tc_index = 0
                        if tc_id:
                            call_id_by_index[tc_index] = tc_id
                            state = call_by_id.setdefault(
                                tc_id, {"name": "", "args": ""}
                            )
                            previous = pending_by_index.pop(tc_index, None)
                            if previous:
                                state["name"] = previous["name"] or state["name"]
                                state["args"] = previous["args"] + state["args"]
                        else:
                            mapped_id = call_id_by_index.get(tc_index)
                            if mapped_id:
                                state = call_by_id.setdefault(
                                    mapped_id, {"name": "", "args": ""}
                                )
                            else:
                                state = pending_by_index.setdefault(
                                    tc_index, {"name": "", "args": ""}
                                )
                        if tcc.get("name"):
                            state["name"] = tcc["name"]
                        if tcc.get("args"):
                            state["args"] += tcc["args"]

                if content:
                    final_content += content

            elif isinstance(msg, ToolMessage):
                tool_call_id = getattr(msg, "tool_call_id", None)
                tool_name = getattr(msg, "name", "tool")
                tool_content = getattr(msg, "content", "")

                if not thinking_done_sent:
                    yield {"type": "thinking_done"}
                    thinking_done_sent = True
                state = call_by_id.pop(tool_call_id, None) if tool_call_id else None
                if state is None:
                    state = {"name": tool_name, "args": "{}"}
                args_str = state["args"] or "{}"
                try:
                    args = json.loads(args_str)
                except (json.JSONDecodeError, TypeError):
                    args = {"raw": args_str[:500]}
                yield {
                    "type": "tool_call",
                    "name": state["name"] or tool_name,
                    "args": args,
                }

                try:
                    result = json.loads(tool_content)
                except (json.JSONDecodeError, TypeError):
                    result = {"result": _message_text(tool_content)}
                yield {"type": "tool_result", "name": tool_name, "result": result}
                had_tool_result = True
                final_content = ""
                call_id_by_index.clear()
                pending_by_index.clear()

        # 流式结束
        if not thinking_done_sent:
            yield {"type": "thinking_done"}
        if final_content.strip():
            yield {"type": "clear_thinking"}
            yield {"type": "token", "content": final_content}
            yield {"type": "done", "content": final_content}
        else:
            detail = (
                "工具已执行，但模型没有给出最终回答"
                if had_tool_result else "模型没有返回回答"
            )
            yield {"type": "error", "content": detail}

    except Exception as e:
        if not thinking_done_sent:
            yield {"type": "thinking_done"}
        yield {"type": "error", "content": f"处理出错: {e!s}"}
