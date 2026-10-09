# -*- coding: utf-8 -*-
"""§5.4 障碍出场（barrier_ladder）单元测试 2026-09-29。"""
import pytest

from backend.services.exit import barrier_ladder as bl


def _spec(**kw):
    d = dict(k=1.0, sl_min_pct=0.8, sl_max_pct=8.0, tp1_r=1.0, tp2_r=2.0,
             tp1_frac=0.50, tp2_frac=0.30, chand_c=2.0, tau_h=24.0)
    d.update(kw)
    return bl.BarrierSpec(**d)


def test_enabled_default_false(monkeypatch):
    monkeypatch.delenv("EXIT_POLICY_MID_BARRIER_LADDER", raising=False)
    assert bl.enabled() is False
    monkeypatch.setenv("EXIT_POLICY_MID_BARRIER_LADDER", "true")
    assert bl.enabled() is True


def test_initial_sl_long_and_clamp():
    s = _spec(k=1.0, sl_min_pct=0.8, sl_max_pct=8.0)
    # ATR = 2% → d = 2%
    assert bl.initial_sl(100.0, 2.0, s, "long") == pytest.approx(98.0)
    # 下限 0.8%
    assert bl.initial_sl(100.0, 0.2, s, "long") == pytest.approx(99.2)
    # 上限 8%
    assert bl.initial_sl(100.0, 20.0, s, "long") == pytest.approx(92.0)
    # short 镜像
    assert bl.initial_sl(100.0, 2.0, s, "short") == pytest.approx(102.0)


def test_sl_close():
    s = _spec()
    st = bl.BarrierState(stage=0, qty=1.0, sl=98.0)
    r = bl.step(s, st, "long", 100.0, 2.0, 97.9, 3600)
    assert r.closed and r.reason == "sl"
    assert r.fills[0].kind == "sl" and r.fills[0].px == pytest.approx(98.0)
    assert r.state.qty == pytest.approx(0.0)


def test_tp1_lock_breakeven():
    s = _spec()  # d = 2% → tp1 = 102
    st = bl.BarrierState(stage=0, qty=1.0, sl=98.0)
    r = bl.step(s, st, "long", 100.0, 2.0, 102.5, 3600)
    assert not r.closed and r.reason == "tp1"
    f = r.fills[0]
    assert f.kind == "tp1" and f.px == pytest.approx(102.0) and f.frac == pytest.approx(0.5)
    assert r.state.stage == 1 and r.state.qty == pytest.approx(0.5)
    assert r.state.sl == pytest.approx(100.0)  # 锁本


def test_tp2_partial_then_chand():
    s = _spec()  # tp1=102, tp2=104, chand = peak − 2×ATR(2.0)=peak−4
    st = bl.BarrierState(stage=1, qty=0.5, sl=100.0)
    r = bl.step(s, st, "long", 100.0, 2.0, 104.5, 7200)
    assert not r.closed and r.reason == "tp2"
    assert r.fills[0].kind == "tp2" and r.fills[0].frac == pytest.approx(0.3)
    assert r.state.stage == 2 and r.state.qty == pytest.approx(0.2)
    assert r.state.trail_peak == pytest.approx(104.5)
    # 价格继续涨到 106 再回落到 101.5：trail = 106−4 = 102 > 101.5 → chand 平
    st2 = r.state
    r2 = bl.step(s, st2, "long", 100.0, 2.0, 106.0, 10800)
    assert not r2.closed
    r3 = bl.step(s, st2, "long", 100.0, 2.0, 101.5, 14400)
    assert r3.closed and r3.reason == "chand"
    assert r3.fills[0].px == pytest.approx(102.0)


def test_time_stop_only_stage0():
    s = _spec()
    st = bl.BarrierState(stage=0, qty=1.0, sl=98.0)
    r = bl.step(s, st, "long", 100.0, 2.0, 100.5, 25 * 3600)
    assert r.closed and r.reason == "time"
    assert r.fills[0].kind == "time" and r.fills[0].px == pytest.approx(100.5)
    # 阶段1 无时间止损
    st1 = bl.BarrierState(stage=1, qty=0.5, sl=100.0)
    r1 = bl.step(s, st1, "long", 100.0, 2.0, 101.0, 40 * 3600)
    assert not r1.closed


def test_short_mirror():
    s = _spec()
    st = bl.BarrierState(stage=0, qty=1.0, sl=102.0)
    r = bl.step(s, st, "short", 100.0, 2.0, 102.5, 3600)
    assert r.closed and r.reason == "sl"
    st2 = bl.BarrierState(stage=0, qty=1.0, sl=102.0)
    r2 = bl.step(s, st2, "short", 100.0, 2.0, 97.5, 3600)
    assert not r2.closed and r2.reason == "tp1"
    assert r2.fills[0].px == pytest.approx(98.0)


def test_state_roundtrip_and_store():
    st = bl.BarrierState(stage=2, qty=0.2, sl=104.0, trail_peak=106.0)
    es = bl.store_state({"exit_policy": {}}, st)
    assert es["barrier_ladder"]["stage"] == 2
    back = bl.state_from_exit_state(es)
    assert back.stage == 2 and back.qty == pytest.approx(0.2)
    assert back.sl == pytest.approx(104.0)
    # 字符串形态
    import json
    back2 = bl.state_from_exit_state(json.dumps(es))
    assert back2.trail_peak == pytest.approx(106.0)
    # 无状态 → 初始
    back3 = bl.state_from_exit_state(None)
    assert back3.stage == 0 and back3.qty == 1.0
