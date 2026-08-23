"""MR regime 突变实时守卫 — 单元测试（2026-08-23）。

覆盖：平盘/正常波动放行、后 24 根波动率 4 倍爆发拦截、K 线不足 fail-open、
无 klines fail-open、列缺失 fail-open。
"""
import numpy as np
import pandas as pd

from backend.services.scalp.mr_regime_breakout_guard import mr_breakout_danger


def _klines_from_returns(returns, base=100.0, wick=0.0003, seed=0):
    """由收益率序列构造 OHLC 随机游走 K 线（close 几何游走，high/low 加小影线）。"""
    rng = np.random.default_rng(seed)
    close = base * np.exp(np.cumsum(np.asarray(returns, dtype=float)))
    prev_close = np.concatenate([[base], close[:-1]])
    high = np.maximum(prev_close, close) * (1.0 + np.abs(rng.normal(0.0, wick, len(close))))
    low = np.minimum(prev_close, close) * (1.0 - np.abs(rng.normal(0.0, wick, len(close))))
    return pd.DataFrame(
        {"open": prev_close, "high": high, "low": low, "close": close}
    )


def test_flat_normal_walk_returns_false():
    """平盘/正常波动（60 根小幅随机游走）→ (False, "")。"""
    rng = np.random.default_rng(7)
    rets = rng.normal(0.0, 0.0004, 60)  # 0.04% 步长的小幅随机游走
    df = _klines_from_returns(rets, seed=11)
    ok, reason = mr_breakout_danger({"klines": df})
    assert ok is False
    assert reason == ""


def test_vol_breakout_last_24_returns_true():
    """后 24 根波动率比前 24 根高 4 倍 → (True, 含数字的原因)。"""
    rng = np.random.default_rng(42)
    rets = rng.normal(0.0, 0.0005, 60)
    rets[-24:] = rng.normal(0.0, 0.0005 * 4.0, 24)  # 后 24 根波动放大 4 倍
    df = _klines_from_returns(rets, seed=13)
    ok, reason = mr_breakout_danger({"klines": df})
    assert ok is True
    assert isinstance(reason, str) and reason
    assert any(ch.isdigit() for ch in reason)  # 原因必须含数字


def test_insufficient_klines_fails_open():
    """K 线不足 60 根 → (False, "") 且不抛异常。"""
    df = _klines_from_returns(np.zeros(30), seed=3)  # 仅 30 根
    ok, reason = mr_breakout_danger({"klines": df})
    assert ok is False
    assert reason == ""


def test_missing_klines_fails_open():
    """market_data 无 klines → (False, "") 且不抛异常。"""
    ok, reason = mr_breakout_danger({})
    assert ok is False
    assert reason == ""
    ok2, reason2 = mr_breakout_danger({"price": 100.0})
    assert ok2 is False
    assert reason2 == ""


def test_missing_columns_fails_open():
    """列缺失 → (False, "") 且不抛异常。"""
    df = pd.DataFrame({"close": np.linspace(100.0, 101.0, 60)})
    ok, reason = mr_breakout_danger({"klines": df})
    assert ok is False
    assert reason == ""
