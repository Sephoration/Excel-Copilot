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
from ..config import save_env, settings
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
        if persist:
            save_env(
                {
                    "DEEPSEEK_API_KEY": api_key,
                    "LLM_BASE_URL": self._llm_config["base_url"],
                    "LLM_MODEL": self._llm_config["model"],
                }
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

    # -- 回滚 -------------------------------------------------------------

    def undo(self) -> bool:
        return self.pool.undo()

    def reset(self) -> None:
        self.pool.reset()
        self.history.clear()


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
