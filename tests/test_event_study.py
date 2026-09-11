# -*- coding: utf-8 -*-
"""event_study 纯逻辑单测（不连库）：Wilson CI、路径对齐、签名超额、显著性门、regime 聚合。"""
from __future__ import annotations

from backend.research.event_study import (
    COST_THRESHOLD,
    event_sign,
    hours_axis,
    mean_se_interval,
    optimal_hold,
    path_returns,
    signed_excess,
    study_from_events,
    wilson_interval,
)


def test_wilson_known_values():
    lo, hi = wilson_interval(15, 30)
    assert 0.33 < lo < 0.40
    assert 0.60 < hi < 0.67
    assert wilson_interval(0, 0) == (0.0, 1.0)
    lo0, hi0 = wilson_interval(0, 30)
    assert lo0 == 0.0 and hi0 < 0.12


def test_align_and_returns():
    hours = hours_axis(2, 3)
    assert hours[0] == -2 and hours[-1] == 3 and 0 in hours
    prices = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]  # -2..+3
    rets = path_returns(prices, t0_index=2)
    assert rets is not None
    assert abs(rets[2] - 0.0) < 1e-12
    assert abs(rets[5] - (105 / 102 - 1)) < 1e-12
    assert path_returns([None, 1, None], t0_index=0) is None


def test_signed_excess_btc_vs_alt():
    # alt +10%, btc +4% → excess +6%; bearish sign flips
    assert abs(signed_excess(0.10, 0.04, 1, asset_is_btc=False) - 0.06) < 1e-12
    assert abs(signed_excess(0.10, 0.04, -1, asset_is_btc=False) + 0.06) < 1e-12
    # BTC 自身：不用超额
    assert abs(signed_excess(0.10, 0.04, 1, asset_is_btc=True) - 0.10) < 1e-12


def test_event_sign_direction_overrides_default():
    assert event_sign("announcement.delisting", None) == -1
    assert event_sign("news.high_impact", 0.8) == 1
    assert event_sign("news.high_impact", -0.5) == -1
    assert event_sign("funding.extreme", None) == 0


def test_significance_gate_synthetic():
    # 构造 40 个 BTC 事件：t+4h 全部 +2%，其它小时 0
    hours = hours_axis(2, 6)
    t0 = 1_700_000_000
    hour = 3600
    t0h = (t0 // hour) * hour
    btc = {}
    for h in hours:
        # price path: 100 at t0, 102 at +4h
        px = 100.0
        if h >= 4:
            px = 102.0
        btc[t0h + h * hour] = px
    events = []
    hourly = {"BTC": dict(btc)}
    for i in range(40):
        ts = (t0h + i * 7 * 24 * hour)  # 每周一个，错开 regime 窗
        events.append({
            "id": i, "event_type": "macro.released", "symbol": "BTC",
            "ts_ms": ts * 1000, "severity": 3, "direction": 1.0,
        })
        # copy same shape prices shifted
        series = hourly["BTC"]
        for h in hours:
            series[ts + h * hour] = 100.0 if h < 4 else 102.0
        series[ts - 30 * 24 * hour] = 90.0  # 30d ago lower → trend_up

    rep = study_from_events(
        "macro.released", events, hourly, pre_h=2, post_h=6, min_n=30,
        cost_threshold=COST_THRESHOLD,
    )
    assert rep.n_used == 40
    assert rep.optimal_hold_h == 4
    assert rep.mean_at_opt > 0.015
    assert rep.hit_rate == 1.0
    assert rep.significant is True
    assert "trend_up" in rep.regime_split


def test_below_min_n_not_significant():
    hours = hours_axis(1, 2)
    t0 = 1_700_000_000
    hour = 3600
    t0h = (t0 // hour) * hour
    hourly = {"BTC": {t0h + h * hour: 100.0 + h for h in hours}}
    events = [{"id": 1, "event_type": "whale.large", "symbol": None, "ts_ms": t0h * 1000,
               "severity": 4, "direction": None}]
    rep = study_from_events("whale.large", events, hourly, pre_h=1, post_h=2, min_n=30)
    assert rep.n_used == 1
    assert rep.significant is False
    assert "min_n" in rep.notes
