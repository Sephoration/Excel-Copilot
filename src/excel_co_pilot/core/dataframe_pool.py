from __future__ import annotations

from copy import deepcopy

import pandas as pd

from .data_profiler import DataProfiler

MAX_SNAPSHOTS = 20


class DataFramePool:
    """会话级数据池：文件 -> df_1, df_2, ... 以及执行结果写回。

    同时维护最多 MAX_SNAPSHOTS 步的历史快照，支持 undo / reset 操作回滚。
    """

    def __init__(self, profiler: DataProfiler | None = None) -> None:
        self._frames: dict[str, pd.DataFrame] = {}
        self._file_names: dict[str, str] = {}
        self._snapshots: list[tuple[dict[str, pd.DataFrame], dict[str, str]]] = []
        self._profiler = profiler or DataProfiler()

    # -- 快照 / 回滚 ------------------------------------------------------

    def _snapshot(self) -> None:
        frames = {k: deepcopy(v) for k, v in self._frames.items()}
        self._snapshots.append((frames, dict(self._file_names)))
        if len(self._snapshots) > MAX_SNAPSHOTS:
            self._snapshots.pop(0)

    def can_undo(self) -> bool:
        return bool(self._snapshots)

    def undo(self) -> bool:
        """回退到上一步状态。无快照时返回 False。"""
        if not self._snapshots:
            return False
        frames, names = self._snapshots.pop()
        self._frames = frames
        self._file_names = names
        return True

    def reset(self) -> None:
        """清空所有数据并保留一次可撤销状态。"""
        if self._frames:
            self._snapshot()
        self._frames = {}
        self._file_names = {}

    # -- 数据管理 ---------------------------------------------------------

    def add(self, original_name: str, df: pd.DataFrame) -> str:
        self._snapshot()
        alias = f"df_{len(self._frames) + 1}"
        self._frames[alias] = df
        self._file_names[alias] = original_name
        return alias

    def update_result(self, df: pd.DataFrame) -> str:
        """把一次执行产生的 DataFrame 结果写回池中，供后续多步操作引用。

        结果始终以别名 `result` 保存，并同时作为默认 `df` 指向的对象。
        """
        self._snapshot()
        self._frames["result"] = df
        self._file_names["result"] = "执行结果"
        return "result"

    def get(self, alias: str) -> pd.DataFrame | None:
        return self._frames.get(alias)

    def aliases(self) -> list[str]:
        return list(self._frames)

    def describe(self) -> str:
        return ", ".join(f"{a}={n}" for a, n in self._file_names.items())

    def to_namespace(self) -> dict:
        """构造安全执行器可用的命名空间。

        始终注入 df（指向最近一次结果，否则 df_1）、df_1..df_n、result。
        """
        ns: dict = {}
        for alias, df in self._frames.items():
            ns[alias] = df
        ns["df"] = self.default_frame
        return ns

    @property
    def default_frame(self) -> pd.DataFrame | None:
        """最近一次结果优先，其次回到第一个数据表。"""
        if "result" in self._frames:
            return self._frames["result"]
        if self._frames:
            return self._frames["df_1"]
        return None

    def default_frame_alias(self) -> str | None:
        """返回默认数据表对应的别名，供 UI 选中展示。"""
        if "result" in self._frames:
            return "result"
        if self._frames:
            return "df_1"
        return None

    def summary_text(self) -> str:
        if not self._frames:
            return "（尚未加载任何数据）"
        blocks = []
        for alias, df in self._frames.items():
            label = f"{alias} ({self._file_names[alias]})"
            blocks.append(self._profiler.summary_text(df, label))
            blocks.append(self._profiler.sample_text(df, label, n=3))
        return "\n\n".join(blocks)

    def to_dict(self) -> dict[str, pd.DataFrame]:
        return dict(self._frames)
