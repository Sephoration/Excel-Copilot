"""生成示例销售报告 Excel，用于测试 Excel Co-Pilot。

用法：
    uv run python scripts/generate_sample_data.py [输出路径]

默认输出到桌面 `销售报告_示例.xlsx`。生成 500 行销售订单，
刻意混入少量空值、离群值，便于体验自动 EDA 的异常预检。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

CITIES = ["上海", "北京", "广州", "深圳", "杭州", "成都", "南京", "武汉"]
PRODUCTS = ["笔记本电脑", "显示器", "机械键盘", "无线鼠标", "USB 扩展坞", "网络摄像头"]
PRODUCT_PRICE = {
    "笔记本电脑": (4500, 9000),
    "显示器": (900, 2600),
    "机械键盘": (250, 900),
    "无线鼠标": (60, 200),
    "USB 扩展坞": (120, 400),
    "网络摄像头": (150, 600),
}


def build_frame(rows: int = 500, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=rows, freq="D")
    order_ids = [f"SO{20240000 + i}" for i in range(1, rows + 1)]
    products = rng.choice(PRODUCTS, size=rows)
    cities = rng.choice(CITIES, size=rows)
    quantities = rng.integers(1, 60, size=rows)
    prices = np.array([rng.uniform(*PRODUCT_PRICE[p]) for p in products]).round(2)
    sales = (quantities * prices).round(2)
    cost_rate = np.clip(rng.normal(0.65, 0.05), 0.5, 0.85)
    costs = (sales * cost_rate).round(2)
    profits = (sales - costs).round(2)

    df = pd.DataFrame(
        {
            "订单号": order_ids,
            "日期": dates.strftime("%Y-%m-%d"),
            "城市": cities,
            "产品": products,
            "数量": quantities,
            "单价": prices,
            "销售额": sales,
            "成本": costs,
            "利润": profits,
        }
    )

    # 刻意制造少量脏数据，便于体验 EDA 异常预检
    df.loc[3, "城市"] = np.nan  # 空值
    df.loc[17, "利润"] = None  # 空值
    df.loc[42, "数量"] = 9999  # 离群值（Z-score>3）
    df.loc[88, "单价"] = -1  # 异常负数
    return df


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if out is None:
        desktop = Path.home() / "Desktop"
        if not desktop.exists():  # OneDrive 桌面等特殊情况
            desktop = Path.home() / "OneDrive" / "Desktop"
        out = desktop / "销售报告_示例.xlsx"

    df = build_frame()
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(out, index=False, sheet_name="销售订单")
    print(f"已生成 {len(df)} 行示例销售报告 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
