"""AI 智能联表服务（移植自 ExcelMind）：LLM 分析两表结构并建议连接配置。"""

from __future__ import annotations

import json
import re
from typing import Any

from ..config import settings
from .llm_factory import build_chat_openai

JOIN_SUGGEST_PROMPT = """你是一个数据分析专家。请分析以下两张表的结构信息，建议如何连接这两张表。

## 表1信息
{table1_summary}

## 表2信息
{table2_summary}

## 任务
请分析这两张表的列结构，找出可用于连接的字段（类似数据库外键关系），并给出连接建议。

## 输出要求
请严格以如下JSON格式返回（不要有其他任何内容）：
```json
{{
  "new_name": "建议的新表名称（简洁有意义）",
  "keys1": ["表1用于连接的字段名"],
  "keys2": ["表2用于连接的字段名（与keys1一一对应）"],
  "join_type": "inner",
  "reason": "简要说明为什么选择这些字段进行连接"
}}
```

注意：
1. keys1和keys2的长度必须相同，且一一对应
2. join_type只能是: inner, left, right, outer 之一
3. 优先选择看起来像主键/外键的字段（如ID、编号、代码等）
4. 如果有多个可能的连接字段，都列出来
"""


def _get_llm() -> Any:
    """从全局 settings 构建 LLM（UI 热更新后即时生效）。"""
    return build_chat_openai()


def _extract_json(content: str) -> str:
    """从 LLM 输出中提取 JSON 字符串（支持 markdown 代码块包裹）。"""
    match = re.search(r"```json\s*([\s\S]*?)\s*```", content)
    if match:
        return match.group(1)
    match = re.search(r"\{[\s\S]*\}", content)
    if match:
        return match.group(0)
    return content


def suggest_join_config(table1_summary: str, table2_summary: str) -> dict[str, Any]:
    """使用 AI 分析两表结构并建议联表配置。

    Returns:
        建议配置字典 {new_name, keys1, keys2, join_type, reason}

    Raises:
        ValueError: AI 返回格式错误或缺少必要字段
    """
    if not settings.deepseek_api_key:
        raise ValueError("未配置 LLM：请先在「模型配置」填写 API Key")
    prompt = JOIN_SUGGEST_PROMPT.format(
        table1_summary=table1_summary, table2_summary=table2_summary
    )
    response = _get_llm().invoke(prompt)
    content = (
        response.content if isinstance(response.content, str) else str(response.content)
    )

    try:
        suggestion = json.loads(_extract_json(content))
    except json.JSONDecodeError as e:
        raise ValueError(f"AI 返回格式解析失败: {e}") from e

    required_fields = ["new_name", "keys1", "keys2", "join_type"]
    for field in required_fields:
        if field not in suggestion:
            raise ValueError(f"AI 返回缺少必要字段: {field}")
    return suggestion
