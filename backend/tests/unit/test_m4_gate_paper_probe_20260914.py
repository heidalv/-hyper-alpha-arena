# -*- coding: utf-8 -*-
"""[M4 2026-09-14] 位置闸 paper 缩仓 + learned 窄带 paper 探针契约测试。

背景：paper 是数据工厂（用户定调「模拟盘=收集交易数据」），位置闸/learned 窄带
的统计依据全部来自旧策略样本。paper 下命中否决 → 缩仓×0.25 放行（记录），
live 硬 veto/hold 不变。回滚开关全部提供。

契约：
- 位置闸：paper_mode=true 且命中高位追多 → 放行 + detail.paper_shrink_mult=0.25；
  paper_mode=false → 仍 veto（旧行为）；开关关闭 → 仍 veto。
- learned 窄带（circuit gate）：loss_locks_disabled(paper) + 命中 learned 否决 →
  (True, "paper_probe×0.25: ...")；开关关 → hold；regime 方向性硬拦不变。
"""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

LG_MOD = "backend.services.full_auto.midlong_location_gate"
CG_MOD = "backend.services.full_auto.midlong_circuit_gate"


# ── 位置闸 paper 缩仓 ──

def _lg_fresh(monkeypatch, **env):
    for k, v in {
        "MIDLONG_LOCATION_GATE_ENABLED": "true",
        "MIDLONG_LOCATION_MAX_PCT_LONG": "60",
        "MIDLONG_LOCATION_MIN_PCT_SHORT": "40",
        "MIDLONG_LOCATION_MAX_ADVERSE_24H_PCT": "5",
        "MIDLONG_LOCATION_REGIMES": "ranging,unknown",
        "MIDLONG_LOCATION_TIERS": "mid,long",
        "MIDLONG_LOCATION_PAPER_SHRINK_ENABLED": "true",
        "MIDLONG_LOCATION_PAPER_SHRINK_MULT": "0.25",
    }.items():
        monkeypatch.setenv(k, str(env.get(k, v)))
    mod = importlib.import_module(LG_MOD)
    return importlib.reload(mod)


def _ms(price, highs, lows, chg24=None):
    blk = {"price": price, "indicators_1h": {"highs": highs, "lows": lows}}
    if chg24 is not None:
        blk["price_change_24h_pct"] = chg24
    return {"BTC": blk}


def test_location_paper_shrink_allows_high_long(monkeypatch):
    mod = _lg_fresh(monkeypatch)
    ok, reason, detail = mod.location_gate_check(
        "BTC", "buy", tier="mid", regime="ranging",
        market_summary=_ms(108.0, [110.0] * 24, [100.0] * 24),
        paper_mode=True,
    )
    assert ok is True
    assert "paper 缩仓" in reason
    assert detail["paper_shrink_mult"] == pytest.approx(0.25)
    assert "location_gate_veto" in detail["paper_shrink_veto_reason"]


def test_location_live_still_vetoes(monkeypatch):
    mod = _lg_fresh(monkeypatch)
    ok, reason, _ = mod.location_gate_check(
        "BTC", "buy", tier="mid", regime="ranging",
        market_summary=_ms(108.0, [110.0] * 24, [100.0] * 24),
        paper_mode=False,
    )
    assert not ok and "location_gate_veto" in reason


def test_location_paper_shrink_rollback_flag(monkeypatch):
    monkeypatch.setenv("MIDLONG_LOCATION_PAPER_SHRINK_ENABLED", "false")
    mod = _lg_fresh(monkeypatch, MIDLONG_LOCATION_PAPER_SHRINK_ENABLED="false")
    ok, reason, _ = mod.location_gate_check(
        "BTC", "buy", tier="mid", regime="ranging",
        market_summary=_ms(108.0, [110.0] * 24, [100.0] * 24),
        paper_mode=True,
    )
    assert not ok and "location_gate_veto" in reason


# ── learned 窄带 paper 探针（circuit gate） ──

def _cg_fresh(monkeypatch, **env):
    for k, v in {
        "MIDLONG_CIRCUIT_ENABLED": "true",
        "MIDLONG_SHORT_MODE": "regime_gated",
        "MIDLONG_DOWN_SHORT_MODE": "learned",
        "MIDLONG_LONG_MODE": "learned",
        "MIDLONG_LEARNED_PAPER_PROBE": "true",
        "MIDLONG_LEARNED_PAPER_PROBE_MULT": "0.25",
    }.items():
        monkeypatch.setenv(k, str(env.get(k, v)))
    mod = importlib.import_module(CG_MOD)
    return importlib.reload(mod)


def test_learned_probe_paper_long(monkeypatch):
    """paper + learned 多头否决（up 但 chg24 不在 [3,6)）→ 探针放行 ×0.25。"""
    import backend.services.risk_management.loss_lock_policy as llp

    monkeypatch.setattr(llp, "loss_locks_disabled", lambda *a, **k: True, raising=False)
    mod = _cg_fresh(monkeypatch)
    monkeypatch.setattr(mod, "_daily_regime", lambda s: "up", raising=False)
    monkeypatch.setattr(mod, "_tier_in_learned", lambda t: True, raising=False)
    monkeypatch.setattr(mod, "_long_learned_ok", lambda s, r: (False, "chg24=1.0 未达 [3,6)"), raising=False)

    ok, reason = mod.check_midlong_entry(14, "BTC", side="long", tier="mid")
    assert ok is True
    assert reason.startswith("paper_probe×0.25:")
    assert "midlong_long_learned_block" in reason


def test_learned_probe_rollback_flag(monkeypatch):
    import backend.services.risk_management.loss_lock_policy as llp

    monkeypatch.setattr(llp, "loss_locks_disabled", lambda *a, **k: True, raising=False)
    monkeypatch.setenv("MIDLONG_LEARNED_PAPER_PROBE", "false")
    mod = _cg_fresh(monkeypatch, MIDLONG_LEARNED_PAPER_PROBE="false")
    monkeypatch.setattr(mod, "_daily_regime", lambda s: "up", raising=False)
    monkeypatch.setattr(mod, "_tier_in_learned", lambda t: True, raising=False)
    monkeypatch.setattr(mod, "_long_learned_ok", lambda s, r: (False, "chg24=1.0 未达 [3,6)"), raising=False)

    ok, reason = mod.check_midlong_entry(14, "BTC", side="long", tier="mid")
    assert ok is False
    assert "midlong_long_learned_block" in reason


def test_learned_probe_live_unchanged(monkeypatch):
    """live（loss locks 启用）→ learned 否决照旧 hold（探针只属于 paper）。"""
    import backend.services.risk_management.loss_lock_policy as llp

    monkeypatch.setattr(llp, "loss_locks_disabled", lambda *a, **k: False, raising=False)
    mod = _cg_fresh(monkeypatch)
    monkeypatch.setattr(mod, "_daily_regime", lambda s: "up", raising=False)
    monkeypatch.setattr(mod, "_tier_in_learned", lambda t: True, raising=False)
    monkeypatch.setattr(mod, "_long_learned_ok", lambda s, r: (False, "chg24=1.0 未达 [3,6)"), raising=False)

    ok, reason = mod.check_midlong_entry(188, "BTC", side="long", tier="mid")
    assert ok is False
    assert "midlong_long_learned_block" in reason


def test_regime_direction_blocks_not_probed(monkeypatch):
    """regime 方向性硬拦（down 禁多）不参与探针——paper 下依旧 hold。"""
    import backend.services.risk_management.loss_lock_policy as llp

    monkeypatch.setattr(llp, "loss_locks_disabled", lambda *a, **k: True, raising=False)
    mod = _cg_fresh(monkeypatch)
    monkeypatch.setattr(mod, "_daily_regime", lambda s: "down", raising=False)

    ok, reason = mod.check_midlong_entry(14, "BTC", side="long", tier="mid")
    assert ok is False
    assert "midlong_long_regime_block" in reason
    assert not reason.startswith("paper_probe")
