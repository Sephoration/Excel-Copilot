"""会话门面：把数据池、LLM 网关与 Agent 图串成一个对 UI/API 友好的入口。

拆成两层：
- `Session`：单个会话的完整状态与操作。
- `SessionManager`：多会话注册表（按 session_id 隔离）。
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from ..agent.graph import build_graph
from ..agent.state import initial_state
from ..config import apply_llm_config, settings
from ..services.llm_gateway import LLMGateway
from .data_loader import DataLoader
from .dataframe_pool import DataFramePool


class Session:
    """单个会话：持有数据池、LLM 网关（可选）与编译后的 Agent 图。"""

    def __init__(self, session_id: str | None = None) -> None:
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.pool = DataFramePool()
        self._loader = DataLoader()
        self._llm: LLMGateway | None = None
        self._llm_config: dict[str, str] = {}
        self._graph = None
        self.history: list[dict[str, str]] = []
        self.context: list[dict[str, str]] = []  # 上下文记忆：[{name, text}]

    # -- LLM / 图 ---------------------------------------------------------

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

    def _get_graph(self):
        if self._graph is None:
            self._graph = build_graph(self._get_llm())
        return self._graph

    @property
    def llm_ready(self) -> bool:
        return bool(self._llm_config.get("api_key") or settings.deepseek_api_key)

    @property
    def llm_model_name(self) -> str:
        return self._llm_config.get("model") or settings.llm_model

    @property
    def llm_base_url(self) -> str:
        return self._llm_config.get("base_url") or settings.llm_base_url

    # -- 数据加载 ---------------------------------------------------------

    def load(self, path: str | Path) -> str:
        """加载单个文件，返回分配的别名（df_1, df_2, ...）。"""
        df = self._loader.load(path)
        return self.pool.add(Path(path).name, df)

    def load_many(self, paths: list[str | Path]) -> dict[str, str]:
        """批量加载，返回 {别名: 原始文件名}。"""
        result: dict[str, str] = {}
        for p in paths:
            alias = self.load(p)
            result[alias] = Path(p).name
        return result

    def frames(self) -> dict[str, pd.DataFrame]:
        return self.pool.to_dict()

    def get(self, alias: str) -> pd.DataFrame | None:
        return self.pool.get(alias)

    def summary(self) -> str:
        return self.pool.summary_text()

    # -- 对话 -------------------------------------------------------------

    def chat(self, user_input: str) -> dict[str, Any]:
        """处理一条自然语言指令，返回 {reply, plot_html, plot_fig}。"""
        if not self.llm_ready:
            return {
                "reply": "（未配置 LLM：请在界面「模型配置」填写 DEEPSEEK_API_KEY）",
                "plot_html": None,
                "plot_fig": None,
            }
        graph = self._get_graph()
        state = initial_state(self.pool, user_input, self.history)
        final = graph.invoke(state)
        self.history.append({"role": "user", "content": user_input})
        self.history.append({"role": "assistant", "content": final.get("reply", "")})
        return {
            "reply": final.get("reply", ""),
            "plot_html": final.get("plot_html"),
            "plot_fig": final.get("plot_fig"),
        }

    # -- 上下文记忆（简单问答） -------------------------------------------
    # 架构：发送多少文件，就把多少文件解构成文本追加进上下文，
    # 问答时把「上下文 + 问题」直接发给大模型，不做数据分析 Agent。

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
            self.context.append({"name": name, "text": text})
            results.append({"name": name, "chars": len(text)})
        return results

    def clear_context(self) -> None:
        self.context.clear()

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

    # -- 回滚 -------------------------------------------------------------

    def undo(self) -> bool:
        return self.pool.undo()

    def reset(self) -> None:
        self.pool.reset()
        self.history.clear()
        self.context.clear()


class SessionManager:
    """按 session_id 隔离的多会话注册表（并发安全）。"""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> Session:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = Session(session_id)
            return self._sessions[session_id]

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def __len__(self) -> int:
        return len(self._sessions)
