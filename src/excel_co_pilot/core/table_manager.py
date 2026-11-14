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
from .data_cleaning import clean_frame
from .data_loader import DataLoader

_EXCEL_SUFFIXES = (".xlsx", ".xlsm")
_MAX_JOIN_ROWS = 1_000_000


def _estimate_join_rows(
    left: pd.DataFrame,
    right: pd.DataFrame,
    keys1: list[str],
    keys2: list[str],
    join_type: str,
) -> int:
    """按连接键计数，计算 pandas 连接结果行数，不展开笛卡尔积。"""
    internal_keys = [f"_key_{i}" for i in range(len(keys1))]

    def counts(frame: pd.DataFrame, keys: list[str], name: str) -> pd.DataFrame:
        key_frame = frame.loc[:, keys].copy()
        key_frame.columns = internal_keys
        result = key_frame.groupby(
            internal_keys, dropna=False, observed=True, sort=False
        ).size().reset_index(name=name)
        # 全为空的一组可能被推断成 float；统一为对象避免计数表因推断差异无法合并。
        for key in internal_keys:
            result[key] = result[key].astype(object)
        return result

    left_counts = counts(left, keys1, "_left_count")
    right_counts = counts(right, keys2, "_right_count")
    # 相同缺失键的匹配行为与 pd.merge 一致；计数表每键只有一行。
    grouped = pd.merge(left_counts, right_counts, on=internal_keys, how="outer")
    total = 0
    rows = grouped[["_left_count", "_right_count"]].itertuples(index=False, name=None)
    for left_count, right_count in rows:
        lcount = 0 if pd.isna(left_count) else int(left_count)
        rcount = 0 if pd.isna(right_count) else int(right_count)
        if lcount and rcount:
            total += lcount * rcount
        elif lcount and join_type in ("left", "outer"):
            total += lcount
        elif rcount and join_type in ("right", "outer"):
            total += rcount
    return total


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
        self._revisions: dict[str, int] = {}
        self._undo_frames: dict[str, pd.DataFrame] = {}

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
        """添加一张新表（CSV / XLSX / XLSM），自动设为活跃表。

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
        self._revisions[table_id] = 0
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
        return DataLoader().load(path)

    @staticmethod
    def _load_excel(path: Path, sheet_name: str | None) -> tuple[pd.DataFrame, str]:
        with pd.ExcelFile(path) as workbook:
            all_sheets = workbook.sheet_names
            if not all_sheets:
                raise ValueError("Excel 文件中没有工作表")
            if sheet_name is None:
                sheet_name = all_sheets[0]
            elif sheet_name not in all_sheets:
                raise ValueError(f"工作表 '{sheet_name}' 不存在，可用工作表: {all_sheets}")
            return pd.read_excel(workbook, sheet_name=sheet_name), sheet_name

    def remove_table(self, table_id: str) -> bool:
        """删除指定表。"""
        if table_id not in self._frames:
            return False
        del self._frames[table_id]
        del self._table_infos[table_id]
        self._revisions.pop(table_id, None)
        self._undo_frames.pop(table_id, None)
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

    def get_table_df(self, table_id: str) -> pd.DataFrame:
        """Return an independent copy of a registered table for export."""
        if table_id not in self._frames:
            raise ValueError("指定的表不存在")
        return self._frames[table_id].copy(deep=True)

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

    def _cleaning_target(self, table_id: str | None) -> tuple[str, pd.DataFrame]:
        selected = table_id or self._active_table_id
        if not selected or selected not in self._frames:
            raise ValueError("请选择一张已加载的表")
        return selected, self._frames[selected]

    @staticmethod
    def _cleaning_result(before: pd.DataFrame, after: pd.DataFrame, revision: int, sample_rows: int = 5) -> dict[str, Any]:
        if not isinstance(sample_rows, int) or isinstance(sample_rows, bool) or not 1 <= sample_rows <= 20:
            raise ValueError("预览行数必须在 1 到 20 之间")

        def rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
            head = frame.head(sample_rows)
            preview = head.astype(object).where(pd.notna(head), None)
            def cell(value: Any) -> Any:
                if hasattr(value, "isoformat"):
                    return value.isoformat()
                if hasattr(value, "item"):
                    return value.item()
                return value
            return [
                {str(key): cell(value) for key, value in row.items()}
                for row in preview.to_dict(orient="records")
            ]

        return {
            "revision": revision,
            "changed": not before.equals(after),
            "before_rows": len(before),
            "after_rows": len(after),
            "before": rows(before),
            "after": rows(after),
        }

    def preview_cleaning(self, steps: list[dict[str, Any]], table_id: str | None = None, sample_rows: int = 5) -> dict[str, Any]:
        """Preview an in-memory change without changing the current table or source file."""
        selected, before = self._cleaning_target(table_id)
        after = clean_frame(before, steps)
        return self._cleaning_result(before, after, self._revisions[selected], sample_rows)

    def apply_cleaning(self, steps: list[dict[str, Any]], table_id: str | None = None, expected_revision: int | None = None) -> dict[str, Any]:
        """Apply validated steps to the in-memory table; retain one undo snapshot."""
        selected, before = self._cleaning_target(table_id)
        if expected_revision is not None and expected_revision != self._revisions[selected]:
            raise ValueError("表格已变化，请重新预览后再应用")
        after = clean_frame(before, steps)
        next_revision = self._revisions[selected] + 1
        result = self._cleaning_result(before, after, next_revision)
        if result["changed"]:
            self._undo_frames[selected] = before.copy(deep=True)
            self._frames[selected] = after
            self._revisions[selected] = next_revision
            self._table_infos[selected].total_rows = len(after)
            self._table_infos[selected].total_columns = len(after.columns)
        else:
            result["revision"] = self._revisions[selected]
        return result

    def undo_cleaning(self, table_id: str | None = None) -> dict[str, Any]:
        """Undo the most recent applied cleaning operation for this table."""
        selected, before = self._cleaning_target(table_id)
        previous = self._undo_frames.get(selected)
        if previous is None:
            raise ValueError("当前表没有可撤销的清洗操作")
        next_revision = self._revisions[selected] + 1
        result = self._cleaning_result(before, previous, next_revision)
        self._frames[selected] = previous
        self._undo_frames.pop(selected, None)
        self._revisions[selected] = next_revision
        self._table_infos[selected].total_rows = len(previous)
        self._table_infos[selected].total_columns = len(previous.columns)
        return result

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
        if len(set(keys1)) != len(keys1) or len(set(keys2)) != len(keys2):
            raise ValueError("连接字段不能重复")
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

        estimated_rows = _estimate_join_rows(df1, df2, keys1, keys2, join_type)
        if estimated_rows > _MAX_JOIN_ROWS:
            raise ValueError(
                f"预计连接结果有 {estimated_rows:,} 行，超过 {_MAX_JOIN_ROWS:,} 行限制；"
                "请检查重复连接键或缩小输入数据范围"
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
        self._revisions[table_id] = 0
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
