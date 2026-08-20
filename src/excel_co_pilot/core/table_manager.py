"""多表管理器：注册 / 切换 / 连接多个表格文件（移植自 ExcelMind）。

会话级实例（Session.tables），通过 contextvar 暴露给数据分析工具使用，
保证多会话（多用户）之间互不干扰。
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import settings
from ..utils.encoding import detect_encoding

_EXCEL_SUFFIXES = (".xlsx", ".xls", ".xlsm")


@dataclass
class TableInfo:
    """表的元信息"""

    id: str
    filename: str
    file_path: str
    sheet_name: str
    total_rows: int
    total_columns: int
    loaded_at: datetime = field(default_factory=datetime.now)
    is_joined: bool = False
    source_tables: list[str] = field(default_factory=list)


class TableManager:
    """多表管理器：持有多个 DataFrame，支持切换与连接。"""

    def __init__(self) -> None:
        self._frames: dict[str, pd.DataFrame] = {}
        self._table_infos: dict[str, TableInfo] = {}
        self._active_table_id: str | None = None

    @property
    def is_loaded(self) -> bool:
        """是否已有任何表。"""
        return bool(self._frames)

    @property
    def active_table_id(self) -> str | None:
        return self._active_table_id

    def add_table(
        self, file_path: str | Path, sheet_name: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        """添加一张新表（CSV / XLSX / XLS / XLSM），自动设为活跃表。

        Returns:
            (表ID, 结构信息)
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        if path.suffix.lower() not in (".csv", *_EXCEL_SUFFIXES):
            raise ValueError(f"不支持的文件格式: {path.suffix}")

        if path.suffix.lower() == ".csv":
            df, sheet = self._load_csv(path), "csv"
        else:
            df, sheet = self._load_excel(path, sheet_name)

        structure = _structure(df, file_path, sheet)
        table_id = str(uuid.uuid4())[:8]
        self._frames[table_id] = df
        self._table_infos[table_id] = TableInfo(
            id=table_id,
            filename=path.name,
            file_path=str(file_path),
            sheet_name=sheet,
            total_rows=structure["total_rows"],
            total_columns=structure["total_columns"],
        )
        self._active_table_id = table_id
        return table_id, structure

    @staticmethod
    def _load_csv(path: Path) -> pd.DataFrame:
        return pd.read_csv(path, encoding=detect_encoding(path))

    @staticmethod
    def _load_excel(path: Path, sheet_name: str | None) -> tuple[pd.DataFrame, str]:
        all_sheets = pd.ExcelFile(path).sheet_names
        if sheet_name is None:
            sheet_name = all_sheets[0]
        elif sheet_name not in all_sheets:
            raise ValueError(f"工作表 '{sheet_name}' 不存在，可用工作表: {all_sheets}")
        return pd.read_excel(path, sheet_name=sheet_name), sheet_name

    def remove_table(self, table_id: str) -> bool:
        """删除指定表。"""
        if table_id not in self._frames:
            return False
        del self._frames[table_id]
        del self._table_infos[table_id]
        if self._active_table_id == table_id:
            self._active_table_id = next(iter(self._frames)) if self._frames else None
        return True

    def get_table_info(self, table_id: str) -> TableInfo | None:
        return self._table_infos.get(table_id)

    def get_active_table_info(self) -> TableInfo | None:
        return (
            self._table_infos.get(self._active_table_id)
            if self._active_table_id
            else None
        )

    def get_active_df(self) -> pd.DataFrame:
        """当前活跃表的 DataFrame（工具读取的数据源）。"""
        if not self._active_table_id or self._active_table_id not in self._frames:
            raise ValueError("未加载任何表格")
        return self._frames[self._active_table_id]

    def set_active_table(self, table_id: str) -> bool:
        if table_id in self._frames:
            self._active_table_id = table_id
            return True
        return False

    def list_tables(self) -> list[dict[str, Any]]:
        """所有表的信息列表（用于 UI 展示）。"""
        result: list[dict[str, Any]] = []
        for table_id, info in self._table_infos.items():
            result.append(
                {
                    "id": info.id,
                    "filename": info.filename,
                    "sheet_name": info.sheet_name,
                    "total_rows": info.total_rows,
                    "total_columns": info.total_columns,
                    "loaded_at": info.loaded_at.isoformat(),
                    "is_active": table_id == self._active_table_id,
                    "is_joined": info.is_joined,
                    "source_tables": info.source_tables,
                }
            )
        return result

    def get_table_columns(self, table_id: str) -> list[str]:
        frame = self._frames.get(table_id)
        return list(frame.columns) if frame is not None else []

    def join_tables(
        self,
        table1_id: str,
        table2_id: str,
        keys1: list[str],
        keys2: list[str],
        join_type: str = "inner",
        new_name: str = "连接表",
    ) -> tuple[str, dict[str, Any]]:
        """连接两张表（支持多字段 / 复合键连接）。"""
        df1, df2 = self._frames.get(table1_id), self._frames.get(table2_id)
        if df1 is None or df2 is None:
            raise ValueError("指定的表不存在")
        info1, info2 = self._table_infos[table1_id], self._table_infos[table2_id]

        if len(keys1) != len(keys2):
            raise ValueError("两表的连接字段数量必须一致")
        if not keys1:
            raise ValueError("至少需要指定一个连接字段")
        for key in keys1:
            if key not in df1.columns:
                raise ValueError(f"表1中不存在字段: {key}")
        for key in keys2:
            if key not in df2.columns:
                raise ValueError(f"表2中不存在字段: {key}")
        if join_type not in ("inner", "left", "right", "outer"):
            raise ValueError(
                f"不支持的连接类型: {join_type}，可选: inner/left/right/outer"
            )

        merged = pd.merge(
            df1,
            df2,
            left_on=keys1,
            right_on=keys2,
            how=join_type,
            suffixes=("_表1", "_表2"),
        )

        table_id = str(uuid.uuid4())[:8]
        self._frames[table_id] = merged
        self._table_infos[table_id] = TableInfo(
            id=table_id,
            filename=f"🔗 {new_name}",
            file_path=f"[连接表] {new_name}",
            sheet_name="merged",
            total_rows=len(merged),
            total_columns=len(merged.columns),
            is_joined=True,
            source_tables=[info1.filename, info2.filename],
        )
        self._active_table_id = table_id
        return table_id, _structure(merged, f"[连接表] {new_name}", "merged")

    # ---- 供 Agent / UI 使用的摘要 ----

    def get_active_summary(self) -> str:
        """当前活跃表的 Markdown 摘要（注入 Agent 提示词 / 联表建议）。"""
        info = self.get_active_table_info()
        df = self._get_df(self._active_table_id)
        if info is None or df is None:
            return "（尚未加载任何表格）"
        return _table_summary(info, df, settings.excel_preview_rows)

    def get_table_summary(self, table_id: str) -> str:
        """指定表的 Markdown 摘要（供智能联表分析）。"""
        info, df = self._table_infos.get(table_id), self._frames.get(table_id)
        if info is None or df is None:
            return "（表不存在）"
        return _table_summary(info, df, settings.excel_preview_rows)

    def _get_df(self, table_id: str | None) -> pd.DataFrame | None:
        return self._frames.get(table_id) if table_id else None


# ---- 会话隔离：工具在执行期间读取当前会话的 TableManager ----

_current_manager: ContextVar[TableManager | None] = ContextVar(
    "excel_co_pilot_table_manager", default=None
)


def set_current_manager(manager: TableManager):
    """绑定当前会话的 TableManager（Agent 执行期间调用，返回 token）。"""
    return _current_manager.set(manager)


def reset_current_manager(token) -> None:
    """解除绑定。"""
    _current_manager.reset(token)


def get_current_manager() -> TableManager:
    """当前会话的 TableManager（数据分析工具内部使用）。"""
    manager = _current_manager.get()
    if manager is None:
        raise ValueError("当前没有可用的表格会话")
    return manager


def _structure(df: pd.DataFrame, file_path: str, sheet_name: str) -> dict[str, Any]:
    columns_info = []
    for col in df.columns:
        col_data = df[col]
        columns_info.append(
            {
                "name": str(col),
                "dtype": str(col_data.dtype),
                "non_null_count": int(col_data.count()),
                "null_count": int(col_data.isna().sum()),
            }
        )
    return {
        "file_path": file_path,
        "sheet_name": sheet_name,
        "total_rows": len(df),
        "total_columns": len(df.columns),
        "columns": columns_info,
    }


def _table_summary(info: TableInfo, df: pd.DataFrame, preview_rows: int) -> str:
    """生成表的 Markdown 摘要（结构 + 前 N 行预览）。"""
    structure = _structure(df, info.file_path, info.sheet_name)
    preview = df.head(preview_rows)

    lines = [
        f"📊 **当前表**: {info.filename}",
        f"📑 **工作表**: {structure['sheet_name']}",
        f"📏 **数据规模**: {structure['total_rows']} 行 × {structure['total_columns']} 列",
        "",
        "**列信息**:",
    ]
    for col in structure["columns"]:
        lines.append(
            f"  - `{col['name']}` ({col['dtype']}): {col['non_null_count']} 非空值"
        )
    if preview_rows and not preview.empty:
        lines.append("")
        lines.append(f"**前 {preview_rows} 行数据预览**:")
        headers = list(preview.columns)
        lines.append("| " + " | ".join(str(h) for h in headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for row in preview.to_dict(orient="records"):
            values = [str(row.get(h, ""))[:20] for h in headers]
            lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)
