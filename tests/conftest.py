from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    path = tmp_path / "orders.csv"
    pd.DataFrame(
        {"id": [1, 2, 3], "name": ["a", "b", "c"], "value": [10, 20, 30]}
    ).to_csv(path, index=False)
    return path
