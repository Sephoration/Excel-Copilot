from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture
def sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "city": ["上海", "北京", "上海", "广州", "北京"],
            "amount": [100, 200, 150, 80, 250],
            "price": [10.5, 20.0, 15.0, 8.0, 25.0],
        }
    )


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    path = tmp_path / "orders.csv"
    pd.DataFrame(
        {"id": [1, 2, 3], "name": ["a", "b", "c"], "value": [10, 20, 30]}
    ).to_csv(path, index=False)
    return path
