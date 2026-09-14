# -*- coding: utf-8 -*-
"""[F92] 对账器契约：既不能误报，也必须能发现+修复真分叉。

现场：一次巡检报出 3 个币种分叉，差额正好 ±1 条腿（±$300）——不是真分叉，而是
「先读运行态、后读账本」期间又跑了一个 tick（成交已写账本、运行态快照还是上一
tick），下一轮自愈。巡检误报会毁掉可信度 ⇒ 账本裁到 `min(updated_ts)` 快照时刻。

本文件不写业务数据：检测/修复用 monkeypatch 覆盖 IO；只有最后一项是**只读**的
实盘稳定性检查（对账器自己不得误报）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import reconcile as rc  # noqa: E402


def test_detects_divergence_and_flags_not_ok(monkeypatch):
    """运行态有仓、账本没有（或差额超容差）⇒ ok=False 且逐币给出来源两账数字。"""
    monkeypatch.setattr(rc, "runtime_positions", lambda lane: {
        "BTC": {"qty": 3.0, "avg_px": 100.0, "updated_ts": None},
        "ETH": {"qty": 0.0, "avg_px": 0.0, "updated_ts": None},
    })
    monkeypatch.setattr(rc, "latest_marks", lambda syms, venue: {"BTC": 100.0, "ETH": 100.0})
    monkeypatch.setattr(rc, "lane_meta", lambda lane: {"venue": "asterdex", "symbols": ["BTC", "ETH"]})
    monkeypatch.setattr(rc.lane_ledger, "open_positions",
                        lambda **kw: [{"symbol": "BTC", "qty": 2.0}])
    r = rc.compare_lane_books(lane_id="test_lane")
    assert r["ok"] is False and r["checked"] == 2
    m = {x["symbol"]: x for x in r["mismatches"]}
    assert set(m) == {"BTC"}, "只有 BTC 分叉（ETH 两账都空）"
    assert m["BTC"]["runtime_qty"] == 3.0 and m["BTC"]["ledger_qty"] == 2.0
    assert m["BTC"]["diff_qty"] == pytest.approx(-1.0)
    assert m["BTC"]["diff_usd"] == pytest.approx(-100.0)


def test_consistent_books_are_ok(monkeypatch):
    monkeypatch.setattr(rc, "runtime_positions", lambda lane: {
        "BTC": {"qty": 3.0, "avg_px": 100.0, "updated_ts": None}})
    monkeypatch.setattr(rc, "latest_marks", lambda syms, venue: {"BTC": 100.0})
    monkeypatch.setattr(rc, "lane_meta", lambda lane: {"venue": "asterdex", "symbols": ["BTC"]})
    monkeypatch.setattr(rc.lane_ledger, "open_positions",
                        lambda **kw: [{"symbol": "BTC", "qty": 3.0}])
    r = rc.compare_lane_books(lane_id="test_lane")
    assert r["ok"] is True and r["mismatches"] == []


def test_float_noise_within_tolerance_is_not_a_mismatch(monkeypatch):
    """1e-6 相对容差内的浮点噪声不得报警（否则每轮都是假分叉）。"""
    monkeypatch.setattr(rc, "runtime_positions", lambda lane: {
        "BTC": {"qty": 3.0, "avg_px": 100.0, "updated_ts": None}})
    monkeypatch.setattr(rc, "latest_marks", lambda syms, venue: {"BTC": 100.0})
    monkeypatch.setattr(rc, "lane_meta", lambda lane: {"venue": "asterdex", "symbols": ["BTC"]})
    monkeypatch.setattr(rc.lane_ledger, "open_positions",
                        lambda **kw: [{"symbol": "BTC", "qty": 3.0 + 1e-9}])
    assert rc.compare_lane_books(lane_id="test_lane")["ok"] is True


def test_adjustment_row_is_zero_net_and_correct_direction(monkeypatch):
    """修复行必须：方向正确、数量=差额、且 fill_px == mid_px（六维净额恒为 0）。"""
    calls = []

    def _fake_record_fill(**kw):
        calls.append(kw)
        return True

    monkeypatch.setattr(rc.lane_ledger, "record_fill", _fake_record_fill)
    n = rc.apply_adjustments(lane_id="test_lane", mismatches=[
        {"symbol": "BTC", "diff_qty": -1.5, "mark_px": 100.0, "runtime_qty": 3.0, "ledger_qty": 1.5},
        {"symbol": "ETH", "diff_qty": 2.0, "mark_px": 50.0, "runtime_qty": -2.0, "ledger_qty": 0.0},
    ])
    assert n == 2
    btc, eth = calls
    # 账本比运行态多空 1.5（diff = ledger - runtime = -1.5）⇒ 买入补回
    assert btc["symbol"] == "BTC" and btc["side"] == "buy" and btc["qty"] == 1.5
    assert btc["fill_px"] == btc["mid_px"] == 100.0 and btc["fee_rate"] == 0.0
    assert eth["side"] == "sell" and eth["qty"] == 2.0
    assert btc["meta"]["source"] == "reconcile", "修复行必须可审计"


def test_adjustment_skips_symbols_without_mark(monkeypatch):
    """无行情价的币种不得硬造价格（跳过并留人工确认）。"""
    monkeypatch.setattr(rc.lane_ledger, "record_fill", lambda **kw: True)
    n = rc.apply_adjustments(lane_id="test_lane", mismatches=[
        {"symbol": "BTC", "diff_qty": 1.0, "mark_px": 0.0}])
    assert n == 0


def test_open_positions_supports_until_cutoff():
    """账本重建必须支持 `until` 上界（对账快照一致性的基础）。"""
    import inspect
    sig = inspect.signature(rc.lane_ledger.open_positions)
    assert "until" in sig.parameters, "open_positions 缺少 until 上界"
    src = inspect.getsource(rc.lane_ledger.open_positions)
    assert "ts <= CAST(:until AS timestamptz)" in src


def test_live_reconcile_is_stable_readonly():
    """实盘只读稳定性：连续 4 次对账不得出现分叉（竞态误报的直接回归）。

    实盘车道不存在时跳过（不在 CI 强制依赖线上数据）。
    """
    import time
    try:
        r0 = rc.compare_lane_books(lane_id="mm_asterdex")
    except Exception as e:
        pytest.skip(f"实盘车道不可用: {e}")
    if not r0.get("checked"):
        pytest.skip("实盘车道无运行态数据")
    for _ in range(3):
        time.sleep(2.0)
        r = rc.compare_lane_books(lane_id="mm_asterdex")
        assert r["ok"], f"对账分叉（疑似竞态误报）: {r['mismatches']}"
