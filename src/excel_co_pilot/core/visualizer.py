"""Plotly 可视化引擎：图表类型推荐 + 交互式 HTML 生成。

设计目标：
- 根据数据特征自动推荐图表类型（趋势→折线、占比→饼、分布→直方图）。
- 生成可缩放、可悬停查看数值的交互式 HTML。
- 所有渲染在本地内存完成，数据不出域。
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# 支持自然语言指令映射的图表类型别名，方便 LLM 输出归一化。
_KIND_ALIASES = {
    "line": "line",
    "折线": "line",
    "折线图": "line",
    "趋势": "line",
    "bar": "bar",
    "柱状": "bar",
    "柱状图": "bar",
    "条形": "bar",
    "条形图": "bar",
    "pie": "pie",
    "饼": "pie",
    "饼图": "pie",
    "占比": "pie",
    "histogram": "histogram",
    "hist": "histogram",
    "直方": "histogram",
    "直方图": "histogram",
    "分布": "histogram",
    "scatter": "scatter",
    "散点": "scatter",
    "散点图": "scatter",
    "box": "box",
    "箱线": "box",
    "箱线图": "box",
    "area": "area",
    "面积": "area",
    "面积图": "area",
}


class Visualizer:
    """把 DataFrame 渲染成 Plotly 交互式 HTML 的引擎。"""

    def normalize_kind(self, kind: str) -> str:
        """把自然语言/LLM 输出的图表类型归一化为 Plotly 可用类型。"""
        return _KIND_ALIASES.get(str(kind).strip().lower(), str(kind).strip().lower())

    def recommend(self, df: pd.DataFrame) -> str:
        """根据列类型启发式推荐图表类型。"""
        cols = list(df.columns)
        if not cols:
            return "bar"
        numeric = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
        categorical = [c for c in cols if not pd.api.types.is_numeric_dtype(df[c])]

        if len(cols) >= 2 and categorical and numeric:
            return "bar"  # 分类 x 数值 → 柱状图
        if len(numeric) >= 2:
            # 两列数值且第一列单调递增 → 视为趋势
            first = df[numeric[0]].dropna()
            if len(first) > 1 and first.is_monotonic_increasing:
                return "line"
            return "scatter"
        if len(numeric) == 1:
            if df[numeric[0]].nunique(dropna=True) < 20:
                return "pie"
            return "histogram"
        return "bar"

    def build(
        self,
        df: pd.DataFrame,
        kind: str = "bar",
        x: str | None = None,
        y: str | None = None,
        title: str = "",
        color: str | None = None,
    ) -> go.Figure:
        """构建 Plotly figure。参数可缺省，会自动推断合理的 x/y。"""
        kind = self.normalize_kind(kind)
        if df.empty:
            return go.Figure().update_layout(title=title or "（空数据，无图可画）")

        cols = list(df.columns)

        # 推断 x / y 列
        if x is None and y is None:
            x, y = self._infer_axes(df, kind)
        elif x is None and y is not None:
            x = df.index.name or None
        elif y is None and x is not None:
            y = self._first_numeric_col(df)

        try:
            if kind == "pie":
                values = y or self._first_numeric_col(df)
                names = x or cols[0]
                return px.pie(df, names=names, values=values, title=title)
            if kind == "histogram":
                col = x or self._first_numeric_col(df) or cols[0]
                return px.histogram(df, x=col, title=title, nbins=30)
            if kind == "scatter":
                return px.scatter(df, x=x, y=y, title=title, color=color)
            if kind == "line":
                return px.line(df, x=x, y=y, title=title, color=color)
            if kind == "area":
                return px.area(df, x=x, y=y, title=title, color=color)
            if kind == "box":
                return px.box(df, x=x, y=y, title=title, color=color)
            # 默认 bar
            return px.bar(df, x=x, y=y, title=title, color=color)
        except ValueError, KeyError:
            # 推断失败时退回到最简单的柱状图
            return px.bar(df, x=cols[0], y=self._first_numeric_col(df), title=title)

    def render(self, fig: go.Figure, full_html: bool = True) -> str:
        """把 figure 渲染成 HTML 字符串。

        full_html=True 返回可独立嵌入 iframe 的完整文档；
        full_html=False 仅返回 <div> 片段。
        """
        if full_html:
            return fig.to_html(
                full_html=True,
                include_plotlyjs="cdn",
                config={"displaylogo": False},
            )
        return fig.to_html(
            full_html=False,
            include_plotlyjs=False,
            config={"displaylogo": False},
        )

    # -- 内部辅助 ---------------------------------------------------------

    @staticmethod
    def _numeric_cols(df: pd.DataFrame) -> list[str]:
        return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]

    def _first_numeric_col(self, df: pd.DataFrame) -> str | None:
        cols = self._numeric_cols(df)
        return cols[0] if cols else None

    def _infer_axes(self, df: pd.DataFrame, kind: str) -> tuple[str | None, str | None]:
        """为柱状/折线等需要 x/y 的图推断坐标列。"""
        numeric = self._numeric_cols(df)
        categorical = [c for c in df.columns if c not in numeric]
        if categorical and numeric:
            return categorical[0], numeric[0]
        if len(numeric) >= 2:
            return numeric[0], numeric[1]
        if len(numeric) == 1:
            return df.index.name, numeric[0]
        return None, None


def figure_to_html(fig: go.Figure, full_html: bool = True) -> str:
    """便捷函数：直接由 figure 得到 HTML 字符串。"""
    return Visualizer().render(fig, full_html=full_html)
