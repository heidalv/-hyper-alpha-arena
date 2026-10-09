# -*- coding: utf-8 -*-
"""P2+P3 入场边际闸（edge_gate）单元测试 2026-09-29。"""
import pytest

from backend.services.full_auto import edge_gate as eg


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.setenv("MIDLONG_EDGE_GATE_ENABLED", "true")
    yield
    monkeypatch.delenv("MIDLONG_EDGE_GATE_ENABLED", raising=False)


def test_enabled(monkeypatch):
    monkeypatch.setenv("MIDLONG_EDGE_GATE_ENABLED", "true")
    assert eg.enabled() is True
    monkeypatch.setenv("MIDLONG_EDGE_GATE_ENABLED", "false")
    assert eg.enabled() is False


def test_tier_guard_bypass(no_env, monkeypatch):
    monkeypatch.setattr(eg, "z_btc_mom", lambda now: None)
    ok, reason = eg.check_edge_entry(14, "BTC", "long", "long")
    assert ok and reason == ""


def test_long_direction_block(no_env, monkeypatch):
    monkeypatch.setenv("MIDLONG_EDGE_DEADZONE_PROBE", "false")  # 本用例锁严格方向闸
    monkeypatch.setattr(eg, "z_btc_mom", lambda now: 0.2)
    ok, reason = eg.check_edge_entry(14, "BTC", "long", "mid", entry_price=60000)
    assert not ok and "dir_block" in reason


def test_long_m_block(no_env, monkeypatch):
    monkeypatch.setattr(eg, "z_btc_mom", lambda now: 0.8)
    monkeypatch.setattr(eg, "funding_z", lambda sym, now: -2.0)
    ok, reason = eg.check_edge_entry(14, "BTC", "long", "mid", entry_price=60000)
    assert not ok and "m_block" in reason


def test_long_ok(no_env, monkeypatch):
    monkeypatch.setattr(eg, "z_btc_mom", lambda now: 1.2)
    monkeypatch.setattr(eg, "funding_z", lambda sym, now: 0.9)
    ok, reason = eg.check_edge_entry(14, "BTC", "long", "mid", entry_price=60000)
    assert ok and "edge_ok" in reason


def test_short_math_assumption(no_env, monkeypatch):
    """空头数学假设：m_short=|z_btc|，funding 不参与（权重 0）。"""
    monkeypatch.setattr(eg, "z_btc_mom", lambda now: -1.0)
    called = {"n": 0}
    def _fz(sym, now):
        called["n"] += 1
        return 0.0
    monkeypatch.setattr(eg, "funding_z", _fz)
    ok, reason = eg.check_edge_entry(14, "BTC", "short", "mid", entry_price=60000)
    assert ok and "short_ok" in reason and "funding权重0" in reason
    assert called["n"] == 0  # funding 未被查询


def test_short_direction_block(no_env, monkeypatch):
    monkeypatch.setenv("MIDLONG_EDGE_DEADZONE_PROBE", "false")  # 本用例锁严格方向闸
    monkeypatch.setattr(eg, "z_btc_mom", lambda now: -0.1)
    ok, reason = eg.check_edge_entry(14, "BTC", "short", "mid", entry_price=60000)
    assert not ok and "dir_block" in reason


def test_price_filter(no_env, monkeypatch):
    monkeypatch.setattr(eg, "z_btc_mom", lambda now: 1.2)
    monkeypatch.setattr(eg, "funding_z", lambda sym, now: 0.9)
    ok, reason = eg.check_edge_entry(14, "XPL", "long", "mid", entry_price=0.5)
    assert not ok and "price_block" in reason


def test_book_guard_same_dir(no_env, monkeypatch):
    monkeypatch.setattr(eg, "_open_same_dir", lambda a, s, t="mid": 4)
    ok, reason = eg.check_book_guard(14, "long", "mid")
    assert not ok and "same_dir_block" in reason


def test_book_guard_day_loss(no_env, monkeypatch):
    monkeypatch.setattr(eg, "_open_same_dir", lambda a, s, t="mid": 0)
    monkeypatch.setattr(eg, "_day_pnl_breach", lambda a, spec: (True, "day_loss_-100"))
    ok, reason = eg.check_book_guard(14, "long", "mid")
    assert not ok and "day_loss_halt" in reason


def test_fail_open_btc_no_data(no_env, monkeypatch):
    monkeypatch.setattr(eg, "z_btc_mom", lambda now: None)
    ok, reason = eg.check_edge_entry(14, "BTC", "long", "mid", entry_price=60000)
    assert ok and "fail-open" in reason


def test_deadzone_probe_paper(no_env, monkeypatch):
    """死区探针：模拟账户 z_btc 死区内按方向符号放行，reason 带 paper_probe×0.25。"""
    monkeypatch.setenv("MIDLONG_EDGE_DEADZONE_PROBE", "true")
    monkeypatch.setattr(eg, "z_btc_mom", lambda now: 0.30)  # 死区上半 → long 探针
    import backend.services.risk_management.loss_lock_policy as _llp
    monkeypatch.setattr(_llp, "loss_locks_disabled", lambda *a, **k: True)
    ok, reason = eg.check_edge_entry(14, "BTC", "long", "mid", entry_price=60000)
    assert ok and "paper_probe×0.25" in reason and "deadzone_probe" in reason
    # 方向相反（死区上半开空）→ 不放行探针
    ok2, _ = eg.check_edge_entry(14, "BTC", "short", "mid", entry_price=60000)
    assert not ok2


def test_deadzone_probe_off_and_live(no_env, monkeypatch):
    monkeypatch.setattr(eg, "z_btc_mom", lambda now: 0.30)
    import backend.services.risk_management.loss_lock_policy as _llp
    # 探针关闭 → 死区仍拦
    monkeypatch.setenv("MIDLONG_EDGE_DEADZONE_PROBE", "false")
    ok, reason = eg.check_edge_entry(14, "BTC", "long", "mid", entry_price=60000)
    assert not ok and "dir_block" in reason
    # 探针开启但 live 账户（亏损锁未禁用）→ 不放行
    monkeypatch.setenv("MIDLONG_EDGE_DEADZONE_PROBE", "true")
    monkeypatch.setattr(_llp, "loss_locks_disabled", lambda *a, **k: False)
    ok2, _ = eg.check_edge_entry(14, "BTC", "long", "mid", entry_price=60000)
    assert not ok2


def test_deadzone_probe_price_filter_still_applies(no_env, monkeypatch):
    monkeypatch.setenv("MIDLONG_EDGE_DEADZONE_PROBE", "true")
    monkeypatch.setattr(eg, "z_btc_mom", lambda now: 0.30)
    import backend.services.risk_management.loss_lock_policy as _llp
    monkeypatch.setattr(_llp, "loss_locks_disabled", lambda *a, **k: True)
    ok, reason = eg.check_edge_entry(14, "XPL", "long", "mid", entry_price=0.5)
    assert not ok and "price_block" in reason
