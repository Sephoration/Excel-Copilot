from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from excel_co_pilot.core.visualizer import Visualizer


def test_recommend_bar():
    df = pd.DataFrame({"city": ["a", "b"], "amount": [1, 2]})
    assert Visualizer().recommend(df) == "bar"


def test_build_returns_figure():
    df = pd.DataFrame({"city": ["a", "b", "c"], "amount": [1, 2, 3]})
    fig = Visualizer().build(df, kind="bar", x="city", y="amount")
    assert isinstance(fig, go.Figure)


def test_build_infers_axes():
    df = pd.DataFrame({"city": ["a", "b"], "amount": [1, 2]})
    fig = Visualizer().build(df)  # 缺省 kind/x/y，应自动推断
    assert isinstance(fig, go.Figure)


def test_render_html():
    df = pd.DataFrame({"city": ["a", "b"], "amount": [1, 2]})
    fig = Visualizer().build(df)
    html = Visualizer().render(fig, full_html=True)
    assert "<html" in html
    assert "plotly" in html


def test_normalize_kind():
    v = Visualizer()
    assert v.normalize_kind("柱状图") == "bar"
    assert v.normalize_kind("line") == "line"
