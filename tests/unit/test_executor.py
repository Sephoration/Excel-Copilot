from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from excel_co_pilot.core.safe_executor import (
    SafeExecutionError,
    SafeExecutor,
    UnsafeCodeError,
)


def _ns() -> dict:
    df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
    return {"df": df, "pd": pd, "np": np}


def test_execute_simple():
    result = SafeExecutor().execute("result = df['a'].sum()", _ns())
    assert result == 6


def test_execute_dataframe():
    result = SafeExecutor().execute("result = df[df['a'] > 1]", _ns())
    assert isinstance(result, pd.DataFrame)
    assert result.shape[0] == 2


def test_block_import():
    with pytest.raises(UnsafeCodeError):
        SafeExecutor().execute("import os\nresult = 1", _ns())


def test_block_dangerous_name():
    with pytest.raises(UnsafeCodeError):
        SafeExecutor().execute("result = __import__('os')", _ns())


def test_block_unknown_name():
    with pytest.raises(UnsafeCodeError):
        SafeExecutor().execute("result = os.system('ls')", _ns())


def test_block_file_read():
    with pytest.raises(UnsafeCodeError):
        SafeExecutor().execute("result = pd.read_csv('C:/secret.txt')", _ns())


def test_block_file_write():
    with pytest.raises(UnsafeCodeError):
        SafeExecutor().execute("result = df.to_csv('C:/leak.csv')", _ns())


def test_allow_to_string():
    # 内存内转换不应被文件读写拦截误伤
    result = SafeExecutor().execute("result = df.to_string()", _ns())
    assert "a" in str(result)


def test_timeout():
    ex = SafeExecutor(timeout=0.1)
    # 有限但耗时的循环：超过 0.1s 超时被拦截，同时后台线程也会在约 0.5s 内自行结束
    with pytest.raises(SafeExecutionError):
        ex.execute("result = 0\nfor i in range(10**7):\n    result = result + i", _ns())
