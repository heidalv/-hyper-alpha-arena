# -*- coding: utf-8 -*-
"""[2026-09-14 F95] 实盘闸门拦截分布（过程可观测）契约。

现场问题：「到底哪道闸门在吃我的成交？」——回放侧有 skip 计数，实盘侧此前只能
手工打一次 tick 看瞬时值，无法回答"过去一小时是谁在拦"。这里把 skip 原因按进程
累计并暴露在 status 里（前端「链路健康」展示），并要求与回放同口径归一化。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402


def test_skip_key_normalizes_arguments():
    """带参数的原因必须归一（否则每个数值都成独立键、统计无意义）。"""
    assert mmrunner.skip_key("net_exposure(1021)") == "net_exposure"
    assert mmrunner.skip_key("symbol_exposure(600)") == "symbol_exposure"
    assert mmrunner.skip_key("ofi_toxic_sell(-0.62)") == "ofi_toxic_sell"
    assert mmrunner.skip_key("stale_data(74.8min>3.0min)") == "stale_data"
    assert mmrunner.skip_key("vol_pause(sigma=1.60)") == "vol_pause"
    assert mmrunner.skip_key("") == ""
    assert mmrunner.skip_key(None) == ""


def test_runner_tracks_skip_and_side_counts():
    """runner 必须持有累计计数器，并在 status 中暴露。"""
    r = mmrunner.ShadowRunner(lane_id="t", venue="x", symbols=["BTC"])
    assert isinstance(r.skip_counts, dict) and r.skip_counts == {}
    assert set(r.side_counts) == {"both", "one", "none"}
    st = r.status()
    assert "skip_counts" in st and "side_counts" in st


def test_tick_updates_counters_source_contract():
    """tick 主循环必须累计 skip/报价侧计数（源码契约，防重构丢失）。"""
    import inspect
    src = inspect.getsource(mmrunning_tick())
    assert "skip_key(dec.skip)" in src
    assert 'side_counts["both"]' in src and 'side_counts["none"]' in src


def mmrunning_tick():
    return mmrunner.ShadowRunner.tick
