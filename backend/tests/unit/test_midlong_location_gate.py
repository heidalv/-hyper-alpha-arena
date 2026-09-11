# -*- coding: utf-8 -*-
"""[2026-09-09 根因修复] 中长线「位置闸」契约测试。

数据依据（`_audit_ml/21_timing.py`，99 笔真实 mid/long 成交 × 1h K 线事件研究）：
  - 入场价在 24h 区间 80-100% 分位：n=33，入场后 24h 均值 -3.89%、胜率 0.152
  - 60-80% 分位：n=13，-3.21%、胜率 0.231
  - 0-20% 分位：n=7，+1.86%、胜率 0.857
  - 24h 已跌 >5% 时做多：n=18，24h 均值 -4.41%、胜率 0.167
契约：
  1. ranging 态 + 区间分位 ≥60% 做多 → 拒；≤40% 做空 → 拒；
  2. 24h 已跌 ≥5% 做多 → 拒；24h 已涨 ≥5% 做空 → 拒；
  3. trend 态 / 非 mid,long tier / 数据缺失 → 放行（fail-open，不制造停摆）；
  4. 关闭开关即完全失效。
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

MODULE = "backend.services.full_auto.midlong_location_gate"


def _fresh(monkeypatch, **env):
    for k, v in {
        "MIDLONG_LOCATION_GATE_ENABLED": "true",
        "MIDLONG_LOCATION_MAX_PCT_LONG": "60",
        "MIDLONG_LOCATION_MIN_PCT_SHORT": "40",
        "MIDLONG_LOCATION_MAX_ADVERSE_24H_PCT": "5",
        "MIDLONG_LOCATION_REGIMES": "ranging,unknown",
        "MIDLONG_LOCATION_TIERS": "mid,long",
    }.items():
        monkeypatch.setenv(k, str(env.get(k, v)))
    mod = importlib.import_module(MODULE)
    return importlib.reload(mod)


def _ms(price, highs, lows, chg24=None):
    blk = {"price": price, "indicators_1h": {"highs": highs, "lows": lows}}
    if chg24 is not None:
        blk["price_change_24h_pct"] = chg24
    return {"BTC": blk}


def test_high_range_long_blocked(monkeypatch):
    mod = _fresh(monkeypatch)
    # 24h 区间 100~110，现价 108 → 分位 80% → 高位追多被拒
    ok, reason, detail = mod.location_gate_check(
        "BTC", "buy", tier="mid", regime="ranging",
        market_summary=_ms(108.0, [110.0] * 24, [100.0] * 24),
    )
    assert not ok and "location_gate_veto" in reason
    assert detail["range_pos_pct"] == 80.0


def test_low_range_short_blocked(monkeypatch):
    mod = _fresh(monkeypatch)
    # 现价 102 → 分位 20% → 低位追空被拒
    ok, reason, _ = mod.location_gate_check(
        "BTC", "sell", tier="mid", regime="ranging",
        market_summary=_ms(102.0, [110.0] * 24, [100.0] * 24),
    )
    assert not ok and "location_gate_veto" in reason


def test_low_range_long_allowed(monkeypatch):
    mod = _fresh(monkeypatch)
    ok, reason, _ = mod.location_gate_check(
        "BTC", "buy", tier="mid", regime="ranging",
        market_summary=_ms(101.0, [110.0] * 24, [100.0] * 24),
    )
    assert ok, reason


def test_knife_catch_blocked(monkeypatch):
    mod = _fresh(monkeypatch)
    # 区间分位合规（50%）但 24h 已跌 6% → 接刀做多被拒
    ok, reason, _ = mod.location_gate_check(
        "BTC", "buy", tier="mid", regime="ranging",
        market_summary=_ms(105.0, [110.0] * 24, [100.0] * 24, chg24=-6.0),
    )
    assert not ok and "接刀" in reason


def test_fractional_change_24h_caliber(monkeypatch):
    mod = _fresh(monkeypatch)
    # 0~1 小数口径的 -0.06 应等同 -6%
    ok, reason, detail = mod.location_gate_check(
        "BTC", "buy", tier="mid", regime="ranging",
        market_summary=_ms(105.0, [110.0] * 24, [100.0] * 24, chg24=-0.06),
    )
    assert not ok and detail["change_24h_pct"] == -6.0


def test_trend_regime_not_limited(monkeypatch):
    mod = _fresh(monkeypatch)
    ok, reason, _ = mod.location_gate_check(
        "BTC", "buy", tier="mid", regime="trend",
        market_summary=_ms(108.0, [110.0] * 24, [100.0] * 24),
    )
    assert ok and "不限制" in reason


def test_short_tier_not_limited(monkeypatch):
    mod = _fresh(monkeypatch)
    ok, _, _ = mod.location_gate_check(
        "BTC", "buy", tier="short", regime="ranging",
        market_summary=_ms(108.0, [110.0] * 24, [100.0] * 24),
    )
    assert ok


def test_missing_data_fail_open(monkeypatch):
    mod = _fresh(monkeypatch)
    # 1h K 线兜底也拿不到数据时 → 必须放行（fail-open，不制造停摆）
    monkeypatch.setattr(mod, "_range_from_klines", lambda _s: (None, None, None))
    ok, reason, _ = mod.location_gate_check(
        "BTC", "buy", tier="mid", regime="ranging", market_summary={},
    )
    assert ok and "fail-open" in reason


def test_disable_switch(monkeypatch):
    mod = _fresh(monkeypatch, MIDLONG_LOCATION_GATE_ENABLED="false")
    ok, reason, _ = mod.location_gate_check(
        "BTC", "buy", tier="mid", regime="ranging",
        market_summary=_ms(108.0, [110.0] * 24, [100.0] * 24),
    )
    assert ok and "未启用" in reason


def test_short_mode_default_regime_gated(monkeypatch):
    """[2026-09-09 二次修订] mid 空头默认 `regime_gated`（日线 regime 门），非机械全停。

    [第十三轮] down-regime 空头默认 flat（`MIDLONG_DOWN_SHORT_MODE=flat`）；
    allowed 时 down 放行。数据依据见 `test_midlong_regime_short_gate.py` 文件头。
    """
    monkeypatch.delenv("MIDLONG_SHORT_MODE", raising=False)
    monkeypatch.setenv("MIDLONG_OPEN_SHORT_ENABLED", "false")
    monkeypatch.setenv("MIDLONG_DOWN_SHORT_MODE", "allowed")  # 本用例验证 down 放行路径
    mod = importlib.import_module("backend.services.full_auto.midlong_circuit_gate")
    importlib.reload(mod)
    assert mod._short_mode() == "regime_gated"
    # down regime → 放行；up regime → 拦
    monkeypatch.setattr(mod, "_daily_regime", lambda sym: "down")
    ok, _ = mod.check_midlong_entry(14, "BTC", side="sell", tier="mid")
    assert ok
    monkeypatch.setattr(mod, "_daily_regime", lambda sym: "up")
    ok, reason = mod.check_midlong_entry(14, "BTC", side="sell", tier="mid")
    assert not ok and "midlong_short_regime_block" in reason
