"""会话门面：把 LLM 网关、多表管理与上下文记忆串成一个对 UI/API 友好的入口。

两种问答模式：
- 「简单问答」：文件解构成文本追加进上下文，直接发给大模型
- 「表格分析」：表格注册进 TableManager，由 LangGraph Agent 选工具分析（含多表联表）
"""

from __future__ import annotations

import uuid
from pathlib import Path

from ..config import apply_llm_config, settings
from ..services.llm_gateway import LLMGateway
from .agent import analyze_stream
from .data_loader import DataLoader
from .kb_context import retrieve_kb_context
from .table_manager import TableManager


class Session:
    """单个会话：持有 LLM 网关（可选）、上下文记忆与多表管理器。"""

    def __init__(self, session_id: str | None = None) -> None:
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self._loader = DataLoader()
        self._llm: LLMGateway | None = None
        self._llm_config: dict[str, str] = {}
        self.history: list[dict[str, str]] = []
        self.context: list[dict[str, str]] = []  # 上下文记忆：[{name, text}]
        self.tables = TableManager()  # 多表管理器（表格分析）

    # -- LLM -------------------------------------------------------------

    def configure_llm(
        self,
        api_key: str,
        base_url: str | None = None,
        model: str | None = None,
        persist: bool = False,
    ) -> None:
        """用前端填写的信息配置本会话的 LLM。

        立即生效（无需重启）；persist=True 时同时写入 .env，重启后仍可用。
        """
        api_key = (api_key or "").strip()
        if not api_key:
            raise ValueError("API Key 不能为空")
        self._llm_config = {
            "api_key": api_key,
            "base_url": base_url or settings.llm_base_url,
            "model": model or settings.llm_model,
        }
        self._llm = LLMGateway(**self._llm_config)
        # 立即同步到内存 settings（知识库 / REST API 读同一份配置），
        # persist=True 时额外写入 .env，重启后仍生效。
        apply_llm_config(
            api_key=api_key,
            base_url=self._llm_config["base_url"],
            model=self._llm_config["model"],
            persist=persist,
        )

    def _get_llm(self) -> LLMGateway:
        if self._llm is None:
            self._llm = (
                LLMGateway(**self._llm_config) if self._llm_config else LLMGateway()
            )
        return self._llm

    @property
    def llm_ready(self) -> bool:
        return bool(self._llm_config.get("api_key") or settings.deepseek_api_key)

    @property
    def llm_model_name(self) -> str:
        return self._llm_config.get("model") or settings.llm_model

    @property
    def llm_base_url(self) -> str:
        return self._llm_config.get("base_url") or settings.llm_base_url

    # -- 上下文记忆（单次问答） --------------------------------------------

    _MAX_FILE_CHARS = 30000  # 单文件进上下文的文本上限，超出截断

    def attach_files(self, paths: list[str | Path]) -> list[dict]:
        """把文件解构成文本，追加进上下文记忆。返回每文件 {name, chars} 摘要。"""
        from ..kb.config import kb_config
        from ..kb.loader import load_document

        results: list[dict] = []
        for raw in paths:
            p = Path(raw)
            ext = p.suffix.lower()
            name = p.name
            if ext in (".csv", ".xls", ".xlsx"):
                # 表格文件 → CSV 文本，便于大模型按列理解
                df = self._loader.load(p)
                text = df.to_csv(index=False)
            else:
                blocks = load_document(p, kb_config)
                text = "\n\n".join(b.text for b in blocks)
            if len(text) > self._MAX_FILE_CHARS:
                text = text[: self._MAX_FILE_CHARS] + "\n…（内容过长已截断）"
            self.context.append({"name": name, "text": text, "chars": len(text)})
            results.append({"name": name, "chars": len(text)})
        return results

    def clear_context(self) -> None:
        self.context.clear()

    # -- 多表管理（表格分析） ----------------------------------------------

    def add_table(self, path: str | Path, sheet_name: str | None = None) -> dict:
        """把表格文件（CSV/XLSX/XLS）注册进多表管理器，返回 {id, name, info}。"""
        table_id, structure = self.tables.add_table(path, sheet_name)
        info = self.tables.get_table_info(table_id)
        return {
            "id": table_id,
            "name": info.filename if info else Path(path).name,
            "info": structure,
        }

    async def analyze_chat(
        self, question: str, history: list[dict[str, str]] | None = None
    ):
        """表格分析问答：LangGraph Agent 事件流（工具调用 / 结果 / 流式回答）。

        使用说明：调用方以 `async for event in session.analyze_chat(...)` 消费事件。
        知识库检索是增强项：由 kb_context 注入器统一负责超时与失败降级。
        """
        knowledge_context = retrieve_kb_context(question)
        async for event in analyze_stream(
            question,
            history=history,
            manager=self.tables,
            knowledge_context=knowledge_context,
        ):
            yield event

    def simple_chat(self, question: str, history: list[dict] | None = None) -> str:
        """简单问答：上下文 + 最近若干轮历史 + 问题，直接发给大模型。"""
        if not self.llm_ready:
            raise RuntimeError("未配置 LLM：请先在「模型配置」填写 DEEPSEEK_API_KEY")
        context_text = "\n\n".join(
            f"【文件：{c['name']}】\n{c['text']}" for c in self.context
        )
        system = (
            "你是 Excel Co-Pilot，一个本地优先的 AI 数据助手。"
            "以下是用户已发送到上下文的文件内容，回答时以这些内容为准：\n\n"
            + (context_text or "（尚未添加任何文件）")
        )
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        if history:
            messages.extend(history[-8:])  # 只带最近几轮，防止上下文爆炸
        messages.append({"role": "user", "content": question})
        return self._get_llm().chat(messages)
