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
        for msg in history or []:
            if msg.get("role") == "user":
                messages.append(HumanMessage(content=msg.get("content", "")))
            elif msg.get("role") == "assistant":
                messages.append(AIMessage(content=msg.get("content", "")))
        messages.append(
            HumanMessage(content=f"[当前操作表: {current_table}] {question}")
        )

        # 累积工具调用信息（args 按 index 累积，名称按 id 记录）
        tool_names_by_id: dict[str, str] = {}
        args_by_index: dict[int, str] = {}
        tool_call_order: list[tuple[str, int]] = []
        yielded_tool_ids: set[str] = set()
        thinking_content = ""
        final_content = ""
        tool_call_yielded = False
        thinking_done_sent = False

        async for chunk in agent.astream(
            {"messages": messages}, stream_mode="messages", config=config
        ):
            if not (isinstance(chunk, tuple) and len(chunk) >= 2):
                continue
            msg, _meta = chunk[0], chunk[1]

            if isinstance(msg, AIMessageChunk):
                content = getattr(msg, "content", "") or ""
                tool_call_chunks = getattr(msg, "tool_call_chunks", []) or []

                if tool_call_chunks:
                    for tcc in tool_call_chunks:
                        tc_id = tcc.get("id")
                        tc_name = tcc.get("name", "")
                        tc_args = tcc.get("args", "")
                        tc_index = tcc.get("index", 0)
                        if tc_id and tc_name:
                            if tc_id not in tool_names_by_id:
                                tool_call_order.append((tc_id, tc_index))
                                if not thinking_done_sent:
                                    yield {"type": "thinking_done"}
                                    thinking_done_sent = True
                            tool_names_by_id[tc_id] = tc_name
                        if tc_args:
                            args_by_index[tc_index] = (
                                args_by_index.get(tc_index, "") + tc_args
                            )

                if content:
                    if tool_call_yielded:
                        final_content += content
                        yield {"type": "token", "content": content}
                    else:
                        thinking_content += content
                        yield {"type": "thinking", "content": thinking_content}

            elif isinstance(msg, ToolMessage):
                tool_call_id = getattr(msg, "tool_call_id", None)
                tool_name = getattr(msg, "name", "tool")
                tool_content = getattr(msg, "content", "")

                if tool_call_id and tool_call_id not in yielded_tool_ids:
                    yielded_tool_ids.add(tool_call_id)
                    tool_call_yielded = True
                    tc_index = 0
                    for tid, idx in tool_call_order:
                        if tid == tool_call_id:
                            tc_index = idx
                            break
                    args_str = args_by_index.get(tc_index, "{}")
                    try:
                        args = json.loads(args_str)
                    except json.JSONDecodeError:
                        last_brace = args_str.rfind('{"')
                        if last_brace >= 0:
                            try:
                                args = json.loads(args_str[last_brace:])
                            except json.JSONDecodeError:
                                args = {"raw": args_str}
                        else:
                            args = {"raw": args_str}
                    yield {
                        "type": "tool_call",
                        "name": tool_names_by_id.get(tool_call_id, tool_name),
                        "args": args,
                    }
                    args_by_index.pop(tc_index, None)

                try:
                    result = json.loads(tool_content)
                except (json.JSONDecodeError, TypeError):
                    result = {"result": tool_content}
                yield {"type": "tool_result", "name": tool_name, "result": result}

        # 流式结束
        if not tool_call_yielded and thinking_content:
            if not thinking_done_sent:
                yield {"type": "thinking_done"}
            yield {"type": "clear_thinking"}
            yield {"type": "token", "content": thinking_content}
            yield {"type": "done", "content": thinking_content}
        elif final_content:
            yield {"type": "done", "content": final_content}
        else:
            yield {"type": "done", "content": ""}

    except Exception as e:
        yield {"type": "thinking_done"}
        yield {"type": "error", "content": f"处理出错: {e!s}"}
