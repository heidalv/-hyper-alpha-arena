# -*- coding: utf-8 -*-
"""[2026-09-09 第十八轮] 主脑 market 层纳入 1h 派生读数（回答「中线分析是否要加 1h K 线」）。

## 调查结论

- **复核路径**（`qual_layer`）早就有 1h：`[1h] RSI/MACD/EMA/ADX`、`[1h K线×30]`、
  `build_full_deep_context(15m/1h/4h/1d)`；
- **主脑决策路径**（`context_pack.build_market_layer`）此前只有 **1d(230) + 4h(80)**，
  一行 1h 都没有——而近三轮全部有效修复（learned 准入 / 位置闸 / regime 门）
  用的正是 1h 派生特征（chg24 / pos24）；
- 本轮把 1h 派生读数补进主脑 market 层：`ret_1h_pct` / `ret_24h_pct` /
  `pos24_pct` / `range_24h_high|low` / `rsi14_1h` / `atr14_1h_pct` / `ema_trend_1h`。

## 契约

1. 1h K 线 ≥25 根 → 上述字段全部出现，口径与闸门一致
   （`ret_24h_pct = close[-1]/close[-25]-1`，`pos24_pct` 用最近 24 根高低沿）；
2. 1h 数据不足 → 不写这些键（不报错、不影响 1d/4h 字段）；
3. 方向/MTF 逻辑不加 1h（1h 尺度方向无边际，§2/§15 已证伪）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.analysis import context_pack as cp  # noqa: E402


def _kl(closes, highs=None, lows=None):
    out = []
    for i, c in enumerate(closes):
        out.append({
            "timestamp": 1_700_000_000 + i * 3600,
            "open": c, "high": (highs[i] if highs else c * 1.001),
            "low": (lows[i] if lows else c * 0.999), "close": c, "volume": 100.0,
        })
    return out


def _install(monkeypatch, series):
    def _fake(symbol, tf, count):
        return series.get((symbol, tf), [])

    monkeypatch.setattr(cp, "_klines", _fake)
    cp._MARKET_LAYER_CACHE.clear()


def test_market_layer_includes_1h_derived_readouts(monkeypatch):
    closes_1h = [100.0] * 24 + [104.0]  # 25 根 → ret_24h = +4%
    highs = [c + 1 for c in closes_1h]
    lows = [c - 1 for c in closes_1h]
    _install(monkeypatch, {
        ("BTC", "1d"): _kl([100.0 + i for i in range(230)]),
        ("BTC", "4h"): _kl([100.0 + i * 0.1 for i in range(80)]),
        ("BTC", "1h"): _kl(closes_1h, highs, lows),
    })
    out = cp.build_market_layer(["BTC"], [])
    d = out["symbols"]["BTC"]
    assert d["ret_24h_pct"] == 4.0, d
    assert d["ret_1h_pct"] == 4.0  # 末两根 100 → 104
    # 区间 [99,105] → (104-99)/6 ≈ 83.3%
    assert d["pos24_pct"] is not None and 80 <= d["pos24_pct"] <= 85, d
    assert d["range_24h_high"] == 105.0 and d["range_24h_low"] == 99.0
    assert d["rsi14_1h"] is not None
    assert d["atr14_1h_pct"] is not None
    assert d["ema_trend_1h"] == "bullish"


def test_market_layer_skips_1h_fields_when_insufficient(monkeypatch):
    _install(monkeypatch, {
        ("ETH", "1d"): _kl([100.0 + i for i in range(230)]),
        ("ETH", "4h"): _kl([100.0 + i * 0.1 for i in range(80)]),
        ("ETH", "1h"): _kl([100.0] * 10),  # 不足 25 根
    })
    out = cp.build_market_layer(["ETH"], [])
    d = out["symbols"]["ETH"]
    assert "ret_24h_pct" not in d
    assert "pos24_pct" not in d
    # 1d/4h 字段照常
    assert d["ret_1d_pct"] is not None


def test_market_layer_1h_fetch_failure_does_not_break(monkeypatch):
    _install(monkeypatch, {
        ("SOL", "1d"): _kl([100.0 + i for i in range(230)]),
        ("SOL", "4h"): _kl([100.0 + i * 0.1 for i in range(80)]),
        ("SOL", "1h"): [],  # 拉取失败
    })
    out = cp.build_market_layer(["SOL"], [])
    d = out["symbols"]["SOL"]
    assert "ret_24h_pct" not in d
    assert d["last"] is not None
