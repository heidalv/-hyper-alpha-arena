# -*- coding: utf-8 -*-
"""[h657] Q 速控纯函数测试。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import qspeed as Q  # noqa: E402


def test_compute_q_bounds_and_weights():
    # 全好分量 ⇒ Q=1.0
    q = Q.compute_q(spread_ok_share=1.0, fill_rate_per_h=Q.FILL_BASE_PER_H,
                    capture_bp=Q.CAPTURE_GOOD_BP, adv_bp=0.0)
    assert q == pytest.approx(1.0, abs=1e-9)
    # 全坏 ⇒ 0
    q0 = Q.compute_q(spread_ok_share=0.0, fill_rate_per_h=0.0,
                     capture_bp=-5.0, adv_bp=10.0)
    assert q0 == 0.0
    # 毒性罚分能把 Q 压到 0
    q1 = Q.compute_q(spread_ok_share=1.0, fill_rate_per_h=Q.FILL_BASE_PER_H,
                     capture_bp=Q.CAPTURE_GOOD_BP, adv_bp=0.0, toxic=1.0)
    assert q1 == 0.0


def test_compute_q_tail_storm_cap():
    # [h662] 平均分量全好,但近 30 分钟止损腿 ≥2 ⇒ Q 强制压到 0.39 以下(触发停加仓)
    q = Q.compute_q(spread_ok_share=1.0, fill_rate_per_h=Q.FILL_BASE_PER_H,
                    capture_bp=Q.CAPTURE_GOOD_BP, adv_bp=0.0, stop_rate_30m=2.0)
    assert q <= Q.STOP_STORM_Q_CAP
    assert q < Q.Q_HALF
    # 止损腿 1 条 ⇒ 不触发钳
    q2 = Q.compute_q(spread_ok_share=1.0, fill_rate_per_h=Q.FILL_BASE_PER_H,
                     capture_bp=Q.CAPTURE_GOOD_BP, adv_bp=0.0, stop_rate_30m=1.0)
    assert q2 == pytest.approx(1.0, abs=1e-9)


def test_speed_action_bands():
    assert Q.speed_action(0.9)["action"] == "full"
    assert Q.speed_action(0.9)["mult"] == 1.0
    assert Q.speed_action(0.5)["action"] == "half"
    assert Q.speed_action(0.5)["mult"] == 0.5
    assert Q.speed_action(0.3)["action"] == "pause"
    assert Q.speed_action(0.3)["mult"] == 0.0


def test_speed_action_hysteresis_dwell():
    p = Q.speed_action(0.3, None, now_min=100.0)
    assert p["action"] == "pause" and p["paused_since"] == 100.0
    p2 = Q.speed_action(0.9, p, now_min=104.0)
    assert p2["action"] == "pause"
    p3 = Q.speed_action(0.45, p, now_min=106.0)
    assert p3["action"] == "pause"
    p4 = Q.speed_action(0.6, p, now_min=105.0 + Q.DWELL_MIN)
    assert p4["action"] in ("half", "full")
    assert p4["paused_since"] is None


def test_plan_tick_q_size_mult_zero_pauses_adds():
    """[h662 修复锁定] q_size_mult=0.0 必须真的停加仓(回归:falsy-0 曾把它吞成 1.0)。"""
    from backend.services.market_maker import runner as _mm
    from backend.services.market_maker.core import (  # noqa: F401
        InventoryBook, LaneRiskLimits, QuoteParams,
    )

    st = _mm.SymbolState(symbol="BTC")
    st.mid_hist = [100.0] * 21
    book = InventoryBook()
    lim = LaneRiskLimits(trend_pause_bp=0.0, ofi_confirm_threshold=0.0,
                         ofi_block_threshold=0.0, ofi_require_threshold=0.0,
                         trend_add_block_bp=0.0, trend_add_block_q=0.0,
                         inv_add_block_ratio=0.0, sudden_move_bp=0.0, be_mult=0.0,
                         jump_pause_bp=0.0, markout_window_n=0, book_slot_min_bp=0.0,
                         max_net_directional_ratio=0.15, q_speed_gate=0.0)
    dec, _ = _mm.plan_tick(
        state=st, mid=100.0, seg_low=99.9, seg_high=100.1,
        seg_taker_sell=5.0, seg_taker_buy=5.0, now_ts=1_000_030.0,
        equity=1000.0, fill_notional=100.0, half_spread=0.005,
        params=QuoteParams(), limits=lim, book=book, block_add_side=None,
        q_size_mult=0.0,
    )
    assert dec.fills == [], "q_size_mult=0 必须停加仓(零成交判定)"
    dec2, _ = _mm.plan_tick(
        state=st, mid=100.0, seg_low=99.9, seg_high=100.1,
        seg_taker_sell=5.0, seg_taker_buy=5.0, now_ts=1_000_030.0,
        equity=1000.0, fill_notional=100.0, half_spread=0.005,
        params=QuoteParams(), limits=lim, book=book, block_add_side=None,
        q_size_mult=1.0,
    )
    assert dec2.fills, "q_size_mult=1.0 应恢复成交判定"


def test_q_decayed_coins(tmp_path, monkeypatch):
    monkeypatch.setattr(Q, "HISTORY", tmp_path / "q.jsonl")
    now = time.time()
    lines = [
        {"ts": now - 200 * 60, "symbol": "BAD", "mult": 0.0, "q": 0.3},
        {"ts": now - 120 * 60, "symbol": "BAD", "mult": 0.0, "q": 0.2},
        {"ts": now - 10 * 60, "symbol": "OK", "mult": 0.0, "q": 0.3},
        {"ts": now - 5 * 60, "symbol": "GOOD", "mult": 1.0, "q": 0.8},
        {"ts": now - 1 * 60, "symbol": "BAD", "mult": 1.0, "q": 0.6},
    ]
    (tmp_path / "q.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    assert Q.q_decayed_coins(min_minutes=180.0) == []


def test_q_decayed_coins_sustained(tmp_path, monkeypatch):
    monkeypatch.setattr(Q, "HISTORY", tmp_path / "q.jsonl")
    now = time.time()
    lines = [
        {"ts": now - 300 * 60, "symbol": "BAD", "mult": 0.0, "q": 0.3},
        {"ts": now - 60 * 60, "symbol": "BAD", "mult": 0.0, "q": 0.2},
    ]
    (tmp_path / "q.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    assert Q.q_decayed_coins(min_minutes=180.0) == ["BAD"]
