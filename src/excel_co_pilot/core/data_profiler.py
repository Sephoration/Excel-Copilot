from __future__ import annotations

import warnings

import numpy as np
import pandas as pd


class DataProfiler:
    """Automatic EDA: summary + anomaly pre-check."""

    def profile(self, df: pd.DataFrame) -> dict:
        cols = list(df.columns)
        col_info = []
        for col in cols:
            series = df[col]
            non_null = series.dropna()
            info = {
                "name": str(col),
                "dtype": str(series.dtype),
                "null_count": int(series.isna().sum()),
                "null_rate": round(float(series.isna().mean()), 4),
                "unique_count": int(series.nunique(dropna=True)),
                "inferred_type": self._infer_type(series, non_null),
            }
            numeric = pd.to_numeric(non_null, errors="coerce").dropna()
            if len(numeric):
                info["min"] = self._round(numeric.min())
                info["max"] = self._round(numeric.max())
                info["mean"] = self._round(numeric.mean())
                info["median"] = self._round(numeric.median())
            col_info.append(info)
        issues = self._anomaly_check(df)
        return {
            "rows": int(df.shape[0]),
            "cols": int(df.shape[1]),
            "columns": col_info,
            "issues": issues,
        }

    @staticmethod
    def _round(value: object) -> object:
        if isinstance(value, (int, np.integer)):
            return int(value)
        if isinstance(value, (float, np.floating)):
            return round(float(value), 4)
        return str(value)

    @staticmethod
    def _infer_type(series: pd.Series, non_null: pd.Series) -> str:
        if non_null.empty:
            return "空列"
        if (
            pd.api.types.is_numeric_dtype(series)
            or pd.to_numeric(non_null, errors="coerce").notna().mean() > 0.9
        ):
            return "数字"
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                parsed = pd.to_datetime(non_null, errors="coerce")
            if parsed.notna().mean() > 0.9:
                return "日期"
        except Exception:
            pass
        return "文本"

    @staticmethod
    def _anomaly_check(df: pd.DataFrame) -> list[str]:
        issues: list[str] = []
        for col in df.columns:
            name = str(col)
            if any(ch.isspace() for ch in name) or not name.replace("_", "").isalnum():
                issues.append(f"列名 '{name}' 含空格或特殊字符")
            non_null = df[col].dropna()
            numeric = pd.to_numeric(non_null, errors="coerce").dropna()
            if 0 < len(numeric) < len(non_null):
                issues.append(f"列 '{name}' 数字中混入文本值")
            if len(numeric) > 50:
                z = (numeric - numeric.mean()) / (numeric.std() or 1)
                outliers = int((z.abs() > 3).sum())
                if outliers:
                    issues.append(f"列 '{name}' 有 {outliers} 个离群值 (Z-score>3)")
            if pd.api.types.is_datetime64_any_dtype(non_null.dtype):
                try:
                    inferred = pd.to_datetime(non_null, format="mixed").dropna()
                    if inferred.notna().mean() < 0.95:
                        issues.append(f"列 '{name}' 日期格式不统一")
                except Exception:
                    pass
        return issues

    def summary_text(self, df: pd.DataFrame, label: str = "") -> str:
        prof = self.profile(df)
        prefix = f"[{label}] " if label else ""
        lines = [f"{prefix}数据: {prof['rows']} 行 x {prof['cols']} 列"]
        for col in prof["columns"]:
            stats = (
                f", min={col.get('min')}, max={col.get('max')}, mean={col.get('mean')}"
                if "mean" in col
                else ""
            )
            lines.append(
                f"  - {col['name']} ({col['inferred_type']}, {col['dtype']}): "
                f"空值率={col['null_rate']:.1%}, 唯一值={col['unique_count']}{stats}"
            )
        if prof["issues"]:
            lines.append("异常预检:")
            lines.extend(f"  ! {issue}" for issue in prof["issues"])
        return "\n".join(lines)

    def sample_text(self, df: pd.DataFrame, label: str = "", n: int = 3) -> str:
        head = df.head(n)
        if head.empty:
            return f"[{label}] 空表" if label else "空表"
        return head.to_string(index=False)
