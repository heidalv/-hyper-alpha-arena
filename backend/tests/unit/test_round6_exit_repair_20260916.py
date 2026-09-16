# -*- coding: utf-8 -*-
"""[2026-09-16 验收轮6] 亏损修复契约测试：4h 反转离场 / 追高天花板 / 开仓冷却 / ExitPolicy 重标定。

背景：9/14-9/16 两日 23 笔平仓 peak 合计 ~296、giveback ~302.88。三个根因：
  * 出场不对称：TP/trailing 按「价格走 2%+」校准，探针仓峰值只有 0.2~0.5% → 永不触发（A：ExitPolicy 重标定）；
  * 死扛：方向复查要求 4h+1d 双周期同反，且 factor_route 仓跳过一切方向复查 → 4h 反转仍扛到 SL（B：4h 单周期反转离场）；
  * 追高 + 止损后立刻重开同向：83~90% 分位追多、SL 冷却被 0 禁用（C：追高天花板硬否决；D：恢复开仓冷却）。

每个开关都有 env 回滚路径（契约：改配置前先跑本测试）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.full_auto import midlong_position_manager as mpm  # noqa: E402
from backend.services.full_auto import midlong_location_gate as locgate  # noqa: E402
from backend.services import reentry_cooldown as rc  # noqa: E402
from backend.services.exit import exit_policy as xp  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("MIDLONG_MID_4H_REVERSAL_EXIT", "MIDLONG_LOCATION_", "REENTRY_", "EXIT_POLICY_")):
            monkeypatch.delenv(k, raising=False)
    yield


# ─────────────────────────── A: ExitPolicy mid 重标定 ───────────────────────────

def test_mid_exit_policy_round6_env_override(monkeypatch):
    """env 覆盖逐项生效（与 .env 实况一致，防漂移）。"""
    monkeypatch.setenv("EXIT_POLICY_MID_TRAILING_ACTIVATION_PCT", "1.0")
    monkeypatch.setenv("EXIT_POLICY_MID_TRAILING_CALLBACK_PCT", "0.5")
    monkeypatch.setenv("EXIT_POLICY_MID_TP_STAGES", "0.8,1.6,3.0")
    monkeypatch.setenv("EXIT_POLICY_MID_MIN_ROI", "43200:0.5,86400:0.0")
    monkeypatch.setenv("EXIT_POLICY_MID_TIME_LIMIT_SEC", "172800")
    p = xp.ExitPolicy.for_lane("mid")
    assert p.trailing_activation_pct == pytest.approx(1.0)
    assert p.trailing_callback_pct == pytest.approx(0.5)
    assert p.tp_stages == (0.8, 1.6, 3.0)
    assert p.min_roi == ((43200, 0.5), (86400, 0.0))
    assert p.time_limit_sec == 172800


def test_mid_min_roi_decay_closes_dead_hold():
    """12h 内 roi<0.5% 强平、24h 内 roi<0 强平——探针仓不再死扛 7 天。"""
    p = xp.ExitPolicy.for_lane("mid")
    p = xp.ExitPolicy(**{**p.to_dict(), "sl_pct": None})
    snap = lambda cur, el, peak=None: xp.ExitSnapshot(  # noqa: E731
        side="long", entry=100.0, current=cur, elapsed_sec=el,
        peak_roi_pct=(peak if peak is not None else (cur - 100.0) / 100.0 * 100.0),
    )
    assert xp.evaluate(p, snap(100.2, 43200)).is_close  # 12h, roi=+0.2% < 0.5%
    assert xp.evaluate(p, snap(100.6, 43200)).action == "hold"  # 12h, roi=+0.6%
    assert xp.evaluate(p, snap(99.5, 86400)).is_close  # 24h, roi=-0.5% < 0
    # 24h, roi=+1% → 不被 min_roi 强平（trailing 激活会 tighten_sl，但不是 close）
    assert not xp.evaluate(p, snap(101.0, 86400)).is_close


# ─────────────────────────── B: 4h 单周期反转离场 ───────────────────────────

def _ms(symbol, mid_bias, macd4, trend4, long_bias="bullish", macd1=0.5, trend1="bullish"):
    return {
        symbol: {
            "orchestrator": {"mid_bias": mid_bias, "long_bias": long_bias},
            "indicators_4h": {"macd": macd4, "trend": trend4},
            "indicators_1d": {"macd": macd1, "trend": trend1},
        }
    }


def test_4h_reversal_reason_long_mid_dead_hold():
    ms = _ms("ASTER", mid_bias="bearish", macd4=-0.6, trend4="bearish")
    r = mpm._mid_4h_reversal_reason(
        "ASTER", {"symbol": "ASTER", "side": "long"}, ms,
        pos_tier="mid", side="long", hold_hours=3.0, pnl_pct=0.001,
    )
    assert r is not None and "4h=bearish" in r


def test_4h_reversal_no_fire_when_profitable_or_fresh():
    ms = _ms("ASTER", mid_bias="bearish", macd4=-0.6, trend4="bearish")
    # 浮盈 > +0.3% → 交给 trailing/min_roi，不在此砍
    assert mpm._mid_4h_reversal_reason(
        "ASTER", {}, ms, pos_tier="mid", side="long", hold_hours=3.0, pnl_pct=0.005,
    ) is None
    # 持有 < 2h → 给论点兑现窗口
    assert mpm._mid_4h_reversal_reason(
        "ASTER", {}, ms, pos_tier="mid", side="long", hold_hours=1.5, pnl_pct=0.001,
    ) is None
    # 4h 支持 → 不触发
    ms2 = _ms("ASTER", mid_bias="bullish", macd4=0.6, trend4="bullish")
    assert mpm._mid_4h_reversal_reason(
        "ASTER", {}, ms2, pos_tier="mid", side="long", hold_hours=3.0, pnl_pct=0.001,
    ) is None
    # 仅 mid tier；long 车道交给 Chandelier
    assert mpm._mid_4h_reversal_reason(
        "ASTER", {}, ms, pos_tier="long", side="long", hold_hours=3.0, pnl_pct=0.001,
    ) is None


def test_4h_reversal_short_symmetric_and_switch_rollback(monkeypatch):
    ms = _ms("XPL", mid_bias="bullish", macd4=0.6, trend4="bullish")
    r = mpm._mid_4h_reversal_reason(
        "XPL", {"symbol": "XPL", "side": "short"}, ms,
        pos_tier="mid", side="short", hold_hours=4.0, pnl_pct=-0.01,
    )
    assert r is not None and "4h=bullish" in r
    # 开关回滚：settings 声明是 import 期读取 env，运行时以 settings 属性为准
    from backend.config import settings as _s
    monkeypatch.setattr(_s, "MIDLONG_MID_4H_REVERSAL_EXIT", False)
    assert mpm._mid_4h_reversal_reason(
        "XPL", {}, ms, pos_tier="mid", side="short", hold_hours=4.0, pnl_pct=-0.01,
    ) is None


def test_four_h_vote_contract():
    ms = _ms("SOL", mid_bias="bearish", macd4=-0.5, trend4="bearish")
    assert mpm._four_h_vote("SOL", ms) == "bearish"
    assert mpm._four_h_vote("NOPE", ms) == "mixed"  # 无数据 → mixed（不误杀）
    # 多数决：bias 多 + macd 空 + trend 多 → bullish
    ms2 = _ms("SOL", mid_bias="bullish", macd4=-0.5, trend4="bullish")
    assert mpm._four_h_vote("SOL", ms2) == "bullish"


def test_rule_direction_still_dual_tf():
    """双周期复查契约不变：4h 反向但 1d 支持 → hold（避免噪音误杀）。"""
    ms = _ms("BTC", mid_bias="bearish", macd4=-0.6, trend4="bearish",
             long_bias="bullish", macd1=0.6, trend1="bullish")
    d = mpm._rule_direction("BTC", {"side": "long"}, ms)
    assert d["action"] == "hold"
    ms2 = _ms("BTC", mid_bias="bearish", macd4=-0.6, trend4="bearish",
              long_bias="bearish", macd1=-0.6, trend1="bearish")
    assert mpm._rule_direction("BTC", {"side": "long"}, ms2)["action"] == "close"


# ─────────────────────────── C: 追高天花板 ───────────────────────────

def _loc_ms(pos_price, hi, lo):
    return {"ASTER": {"price": pos_price, "range_24h_high": hi, "range_24h_low": lo}}


def test_location_ceiling_hard_veto_extreme_chase():
    # pos = (90-50)/(100-50) = 80% ≥ 70% → paper 也硬否决
    ok, reason, detail = locgate.location_gate_check(
        "ASTER", "buy", tier="mid", regime="ranging",
        market_summary=_loc_ms(90.0, 100.0, 50.0), paper_mode=True,
    )
    assert not ok and "天花板" in reason and detail.get("paper_shrink_ceiling") == 70


def test_location_ceiling_below_still_shrinks():
    # 65% 分位 → 仍缩仓放行（收集样本）
    ok, reason, detail = locgate.location_gate_check(
        "ASTER", "buy", tier="mid", regime="ranging",
        market_summary=_loc_ms(82.5, 100.0, 50.0), paper_mode=True,
    )
    assert ok and detail.get("paper_shrink_mult") == pytest.approx(0.25)


def test_location_ceiling_rollback_zero(monkeypatch):
    monkeypatch.setenv("MIDLONG_LOCATION_PAPER_SHRINK_CEILING", "0")
    ok, _, detail = locgate.location_gate_check(
        "ASTER", "buy", tier="mid", regime="ranging",
        market_summary=_loc_ms(90.0, 100.0, 50.0), paper_mode=True,
    )
    assert ok and detail.get("paper_shrink_mult") == pytest.approx(0.25)


def test_location_live_still_veto_and_defer_preserved(monkeypatch):
    # live（paper_mode=False）行为不变：60% 分位即 veto
    ok, reason, _ = locgate.location_gate_check(
        "ASTER", "buy", tier="mid", regime="ranging",
        market_summary=_loc_ms(90.0, 100.0, 50.0), paper_mode=False,
    )
    assert not ok and "高位追多" in reason
    # 日线 chop 让位 learned 多头闸：位置规则（含天花板）整体跳过
    monkeypatch.setattr(locgate, "_long_gate_authoritative", lambda s, t: True)
    ok2, reason2, _ = locgate.location_gate_check(
        "ASTER", "buy", tier="mid", regime="ranging",
        market_summary=_loc_ms(90.0, 100.0, 50.0), paper_mode=True,
    )
    assert ok2 and "让位" in reason2


# ─────────────────────────── D: 开仓冷却 ───────────────────────────

def test_sl_cooldown_blocks_same_direction_reopen(monkeypatch):
    monkeypatch.setenv("REENTRY_SL_COOLDOWN_SEC_MID", "7200")
    monkeypatch.setenv("REENTRY_LOSS_COOLDOWN_SEC_MID", "3600")
    rc.clear_state(14, "ASTER", "mid")
    rc.record_full_close(14, "ASTER", "long", tier="mid", close_pnl=-5.0, close_reason="sl")
    blocked, reason = rc.reopen_blocked(14, "ASTER", "buy", new_tier="mid")
    assert blocked and "120分钟" in reason  # SL 冷却 7200s = 120min


def test_loss_cooldown_applies_to_soft_close(monkeypatch):
    monkeypatch.setenv("REENTRY_SL_COOLDOWN_SEC_MID", "7200")
    monkeypatch.setenv("REENTRY_LOSS_COOLDOWN_SEC_MID", "3600")
    rc.clear_state(14, "VIRTUAL", "mid")
    rc.record_full_close(14, "VIRTUAL", "long", tier="mid", close_pnl=-5.0,
                         close_reason="thesis_should_close")
    blocked, reason = rc.reopen_blocked(14, "VIRTUAL", "buy", new_tier="mid")
    assert blocked and "60分钟" in reason  # 亏损冷却 3600s = 60min


def test_cooldown_tier_isolation(monkeypatch):
    monkeypatch.setenv("REENTRY_SL_COOLDOWN_SEC_MID", "7200")
    # 内存无 long 桶时 reopen_blocked 会回查 DB 耐久冷却——本测试只验证 tier 隔离语义，
    # 不依赖真实库数据：把耐久回查钉成放行。
    monkeypatch.setattr(rc, "_durable_reopen_blocked", lambda *a, **k: (False, ""))
    rc.clear_state(14, "UNI", "mid")
    rc.record_full_close(14, "UNI", "long", tier="mid", close_pnl=-5.0, close_reason="sl")
    # long 车道不受 mid 冷却影响（tier 隔离，round-3 契约）
    assert rc.reopen_blocked(14, "UNI", "buy", new_tier="long") == (False, "")
    rc.clear_state(14, "UNI", "mid")
