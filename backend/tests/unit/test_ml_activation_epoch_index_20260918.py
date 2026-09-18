"""P2-13 回归：ML 特征引擎的 K 线时间索引必须落在真实日期，不能是 1970。

事故背景（轮66 审计 P2-13）
-------------------------
`backend/services/ml/activation_service.py` 的 `_load_klines_df()` 直接执行
`pd.to_datetime(df["timestamp"], utc=True)`，而 `crypto_klines.timestamp` 是
int4 **epoch 秒**（实测 BTC/15m 末行 `1789739100`）。pandas 对**整数**默认按
**纳秒**解释，于是全部 K 线被解成 1970-01-01，并随
`data/factor_matrices/{SYMBOL}_{TF}.csv` 落盘 —— 磁盘上那份 1970 索引的 CSV
就是这条 bug 的物证。仓内其它 3 处转换器（`data_center.py:199` 用 `unit="s"`、
`technical_indicators.py:239` 用 `unit="ms"`、`midlong_walk_forward_hook.py:97`
用 `unit="s"`）都显式带了单位，只此一处漏。

本测试锁三件事：
1. 数值列（int64 / float64，float64 是因为 `dropna` 后会出现 NaN）按 epoch **秒**解；
2. 字符串 / 已是 tz-aware datetime 的输入仍然可用（同一函数要吃得下多种上游形状）；
3. 端到端：喂真实形状的 K 线行，索引年份必须是 2020 之后，不是 1970。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.ml.activation_service import (  # noqa: E402
    _epoch_like_to_utc,
    _load_klines_df,
)

# 实测值：kline_service._query_klines_from_db("BTC", "15m", ...) 末行 timestamp
_REAL_EPOCH = 1789739100
_REAL_EXPECT = pd.Timestamp("2026-09-18 13:45:00", tz="UTC")


def test_int_epoch_is_read_as_seconds_not_nanoseconds():
    """核心断言：int 列按秒解。旧写法会解出 1970-01-01 00:00:01.789739100。"""
    out = _epoch_like_to_utc(pd.Series([_REAL_EPOCH]))
    assert out.iloc[0] == _REAL_EXPECT
    assert out.iloc[0].year == 2026, "整数按纳秒解会落到 1970"


def test_float_epoch_with_nan_still_read_as_seconds():
    """float64 + NaN 是 `dropna` / `errors="coerce"` 之后的常见形状，不能回退成纳秒。"""
    out = _epoch_like_to_utc(pd.Series([float("nan"), float(_REAL_EPOCH)]))
    assert pd.isna(out.iloc[0])
    assert out.iloc[1] == _REAL_EXPECT


def test_string_and_aware_datetime_inputs_survive():
    """字符串（`get_klines_from_db` 的 `datetime` 列）与 tz-aware 输入都要能吃。"""
    from_str = _epoch_like_to_utc(pd.Series(["2026-09-18T13:45:00"]))
    assert from_str.iloc[0] == _REAL_EXPECT
    from_dt = _epoch_like_to_utc(pd.Series(pd.to_datetime(["2026-09-18T13:45:00Z"])))
    assert from_dt.iloc[0] == _REAL_EXPECT


def test_output_is_tz_aware_utc():
    """下游 `data_center` / 因子层统一按 UTC 对齐，索引必须带时区。"""
    out = _epoch_like_to_utc(pd.Series([_REAL_EPOCH]))
    assert str(out.iloc[0].tz) == "UTC"


def test_load_klines_df_index_year_is_not_1970(monkeypatch):
    """端到端：真实形状的 K 线行喂进 `_load_klines_df`，索引年份必须 ≥ 2020。"""
    rows = [
        {
            "timestamp": _REAL_EPOCH - 900 * i,
            "datetime": "2026-09-18T13:45:00",
            "open": 79000.0 + i,
            "high": 80000.0 + i,
            "low": 78000.0 + i,
            "close": 79500.0 + i,
            "volume": 100.0 + i,
        }
        for i in range(80)
    ]

    class _Fake:
        @staticmethod
        def get_klines_from_db(*_a, **_k):
            return rows

    import backend.services.kline_data_service as kds

    monkeypatch.setattr(kds, "kline_service", _Fake(), raising=True)

    df = _load_klines_df("BTC")
    assert df is not None and len(df) > 0
    assert df.index.is_monotonic_increasing
    assert str(df.index.tz) == "UTC"
    assert df.index[0].year >= 2020, f"索引仍是 {df.index[0]} —— 单位又漏了"
    assert df.index[-1] == _REAL_EXPECT


def test_source_has_no_unitless_to_datetime_on_kline_time_columns():
    """源码级守卫：`_load_klines_df` 里不得再出现缺 `unit=` 的裸 `pd.to_datetime`。

    注释先剥掉，避免匹配到本文件/源码里引用的旧写法说明（本项目既有约定）。
    """
    src = (ROOT / "backend" / "services" / "ml" / "activation_service.py").read_text(
        encoding="utf-8"
    )
    body = src.split("def _load_klines_df(", 1)[1].split("\ndef ", 1)[0]
    stripped = "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("#")
    )
    assert "pd.to_datetime" not in stripped, (
        "时间列必须经 _epoch_like_to_utc()，不要在此处直接 pd.to_datetime"
    )
    assert "_epoch_like_to_utc" in stripped


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
