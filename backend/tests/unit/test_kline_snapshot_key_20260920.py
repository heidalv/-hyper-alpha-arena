# -*- coding: utf-8 -*-
"""[轮135 2026-09-20] K线深度分析师的**静默失效**根因：快照键 `current` vs 门控键 `close`。

## 实测
K 线取得到（5m=96/15m=96/1h=72/4h=60/1d=30 根），技术快照也算出了 trend
（strong_bearish/bearish/neutral…），但**每个周期的 `close` 都是 None**；
而严格门控的判据是 `close > 0 and trend not in ("neutral",…)`：
```python
close = float(tech.get("close", 0) or 0)     # 快照里根本没这个键 → 0
```
⇒ `has_real=False` ⇒ **恒判 `DATA_MISSING:无真实K线趋势`** ⇒ K线深度分析师 100% 输出中性 0
（BTC/ETH/SOL 实测全中）。这与 轮132 的 `name 'tier'` 同类：**命名不一致导致整段静默降级**。

## 修复
- 快照同时写 `close` 与 `current`（下游两类消费者都不再瞎）；
- 门控同时认 `close` / `current`（任一侧改名都不至于让门控瞎掉）。

## 本文件守什么
1. 快照两个键都存在且一致；
2. 有真实快照（close>0 且 trend≠neutral）时，`_rule_based_analysis` **不得**返回 DATA_MISSING；
3. 真·无数据（close=0 或 trend=neutral）时**必须**返回 DATA_MISSING（不许编方向）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.trading_analysts import KlineAnalyst  # noqa: E402


def _snap(trend="bearish", close=80000.0, key="close"):
    return {"1h": {key: close, "trend": trend, "rsi": 40, "vol_ratio": 1.1}}


def test_snapshot_writes_both_close_and_current():
    """快照必须同时提供 `close` 与 `current`（历史消费者两类都有）。"""
    ka = KlineAnalyst()
    kl = ka._fetch_klines("BTC")
    assert kl, "BTC 取不到 K 线（数据面问题，另查）"
    snap = ka._compute_technical_snapshot("BTC", kl)
    assert snap, "快照为空"
    for tf, tech in snap.items():
        assert "close" in tech, f"{tf} 缺 close（门控会瞎）"
        assert "current" in tech, f"{tf} 缺 current（老消费者会瞎）"
        assert tech["close"] == tech["current"], f"{tf} close/current 不一致"


def test_real_snapshot_does_not_produce_data_missing(monkeypatch):
    monkeypatch.setenv("STRICT_DATA_GATE", "true")
    ka = KlineAnalyst()
    out = ka._rule_based_analysis("BTC", {"1h": {"close": 80000.0, "trend": "bearish",
                                                "rsi": 40, "vol_ratio": 1.1}})
    detail = str((out.get("signal") or {}).get("detail") or "")
    assert "DATA_MISSING" not in detail, f"有真实快照仍判无数据（字段名又对不上了）：{detail}"
    assert (out.get("signal") or {}).get("signal") in ("bullish", "bearish", "neutral")


def test_current_only_snapshot_also_works(monkeypatch):
    """只有 `current`（旧格式）也必须能通过门控 —— 防"改了一侧又瞎另一侧"。"""
    monkeypatch.setenv("STRICT_DATA_GATE", "true")
    ka = KlineAnalyst()
    out = ka._rule_based_analysis("BTC", {"1h": {"current": 80000.0, "trend": "bullish",
                                                 "rsi": 60, "vol_ratio": 1.2}})
    detail = str((out.get("signal") or {}).get("detail") or "")
    assert "DATA_MISSING" not in detail, f"旧格式（仅 current）仍被判无数据：{detail}"


def test_true_missing_still_gated(monkeypatch):
    """真·无数据时**必须**继续 gate（不许因为修 bug 就放开"编方向"）。"""
    monkeypatch.setenv("STRICT_DATA_GATE", "true")
    ka = KlineAnalyst()
    out = ka._rule_based_analysis("BTC", {"1h": {"close": 0, "trend": "neutral"}})
    detail = str((out.get("signal") or {}).get("detail") or "")
    assert "DATA_MISSING" in detail, "无真实数据时必须仍然 gate"


def test_live_kline_deep_is_not_always_neutral():
    """端到端：真实 analyze 后，信号的 detail 不应一律是 DATA_MISSING。

    注意：本断言允许个别周期/币种因数据不足而 gate，但**不允许全部**都是 DATA_MISSING
    （那正是本轮修的静默失效）。
    """
    ka = KlineAnalyst()
    rep = ka.analyze(["BTC", "ETH"])
    sigs = list(getattr(rep, "signals", None) or [])
    assert sigs, "analyze 没有返回任何信号"
    bad = [s for s in sigs if "DATA_MISSING" in str(s.get("detail") or "")]
    assert len(bad) < len(sigs), f"全部信号仍是 DATA_MISSING（修复未生效）：{len(bad)}/{len(sigs)}"
