# -*- coding: utf-8 -*-
"""[F279 2026-09-16] lane_ledger.position_id 契约测试。

背景（为什么必须修）：
  实测 `lane_ledger` 中 lane_id='mm_asterdex' 的 5255 行 **position_id 全为 NULL**。
  后果：开仓腿与平仓腿无法配对 ⇒ **往返级归因做不了**。轮 7/轮 8 的绩效分析
  因此只能停留在"腿"的层面（入场 +6.4bp / 平仓 −9.2bp），看不到一次完整往返，
  也无法验证"被动出场改造"的收益。

本测试锁定四条不变量：
  1. 每次从空仓建仓 → 生成新的 position_id；
  2. 同向加仓 → **沿用**同一 position_id（不能每笔都换）；
  3. 减仓/平仓 → **沿用**建仓时的 position_id（这是配对的关键）；
  4. 回到空仓后再次建仓 → 生成**新的** position_id（不能复用旧的）。
  5. PlannedFill 的 position_id 会一路带到 lane_ledger.record_fill。
"""
from __future__ import annotations

import inspect

import pytest

from backend.services.market_maker.core import InventoryBook, Position


def test_open_creates_new_position_id():
    b = InventoryBook()
    r = b.apply_fill(symbol="BTC", side="buy", qty=0.1, fill_px=100.0,
                     mid_px=100.1, now_ts=1.0)
    assert r["position_id"], "建仓必须生成 position_id"
    assert r["position_id"].startswith("mm:BTC:")


def test_scale_in_reuses_same_position_id():
    b = InventoryBook()
    a = b.apply_fill(symbol="BTC", side="buy", qty=0.1, fill_px=100.0,
                     mid_px=100.1, now_ts=1.0)["position_id"]
    c = b.apply_fill(symbol="BTC", side="buy", qty=0.1, fill_px=101.0,
                     mid_px=101.1, now_ts=2.0)["position_id"]
    assert a == c, "同向加仓必须沿用同一库存周期 id"


def test_partial_close_reuses_same_position_id():
    b = InventoryBook()
    entry = b.apply_fill(symbol="BTC", side="buy", qty=0.3, fill_px=100.0,
                         mid_px=100.1, now_ts=1.0)["position_id"]
    part = b.apply_fill(symbol="BTC", side="sell", qty=0.1, fill_px=101.0,
                        mid_px=100.9, now_ts=2.0)
    assert part["position_id"] == entry, "部分平仓腿必须带上建仓时的 id（否则无法配对）"
    assert abs(b.qty("BTC") - 0.2) < 1e-12


def test_full_close_then_reopen_gets_new_id():
    b = InventoryBook()
    first = b.apply_fill(symbol="BTC", side="buy", qty=0.1, fill_px=100.0,
                         mid_px=100.1, now_ts=1.0)["position_id"]
    close = b.apply_fill(symbol="BTC", side="sell", qty=0.1, fill_px=101.0,
                         mid_px=100.9, now_ts=2.0)
    assert close["position_id"] == first, "平仓腿属于同一周期"
    assert abs(b.qty("BTC")) < 1e-12
    second = b.apply_fill(symbol="BTC", side="sell", qty=0.1, fill_px=102.0,
                          mid_px=101.9, now_ts=3.0)["position_id"]
    assert second != first, "回到空仓后再次建仓必须是新周期"


def test_flip_gets_new_id():
    b = InventoryBook()
    first = b.apply_fill(symbol="BTC", side="buy", qty=0.1, fill_px=100.0,
                         mid_px=100.1, now_ts=1.0)["position_id"]
    flip = b.apply_fill(symbol="BTC", side="sell", qty=0.3, fill_px=101.0,
                        mid_px=100.9, now_ts=2.0)["position_id"]
    assert flip != first, "反手后的剩余仓位是新周期"
    assert b.qty("BTC") < 0


def test_ids_unique_across_symbols_and_books():
    b = InventoryBook()
    a = b.apply_fill(symbol="BTC", side="buy", qty=1, fill_px=10, mid_px=10, now_ts=1)["position_id"]
    c = b.apply_fill(symbol="ETH", side="buy", qty=1, fill_px=10, mid_px=10, now_ts=1)["position_id"]
    assert a != c
    b2 = InventoryBook()
    d = b2.apply_fill(symbol="BTC", side="buy", qty=1, fill_px=10, mid_px=10, now_ts=1)["position_id"]
    assert d == a, "同一 book 内序号确定性（回放可复现）"


def test_externally_injected_position_gets_id():
    """plan_tick 会把 SymbolState 注入成 Position()（无 id）——不能被漏掉。"""
    b = InventoryBook()
    b.positions["BTC"] = Position(qty=0.5, avg_px=100.0, avg_mid=100.0, opened_ts=1.0)
    r = b.apply_fill(symbol="BTC", side="sell", qty=0.5, fill_px=101.0,
                     mid_px=100.9, now_ts=2.0)
    assert r["position_id"], "外部注入的持仓在成交时也必须补发 id"


def test_planned_fill_carries_position_id_and_ledger_receives_it():
    """契约：PlannedFill.position_id 必须被 _record_fills 传给 record_fill。"""
    from backend.services.market_maker import runner as R

    assert "position_id" in R.PlannedFill.__dataclass_fields__
    src = inspect.getsource(R.ShadowRunner._record_fills)
    assert "position_id" in src, "_record_fills 必须把 position_id 写入账本"
    assert "f.position_id" in src


def test_replay_paths_also_pass_position_id():
    """回放路径（F59 replay / portfolio_replay）不能漏，否则与实盘口径不一致。"""
    from backend.services.market_maker import replay as RP

    sig = inspect.signature(RP._record_fill)
    assert "position_id" in sig.parameters, "_record_fill 必须接受 position_id"
    src = inspect.getsource(RP)
    n = src.count("position_id=str(d.get(")
    assert n >= 2, "两处 _record_fill 调用都要传 position_id（实测=%d）" % n


def test_ensure_position_id_backfills_external_position():
    """进程重启后从 SymbolState 恢复的持仓没有 id —— 必须能补发。"""
    b = InventoryBook()
    b.positions["ETH"] = Position(qty=-1.0, avg_px=2000.0, avg_mid=2000.0, opened_ts=5.0)
    pid = b.ensure_position_id("ETH")
    assert pid.startswith("mm:ETH:")
    # 再次调用必须稳定（不能每次都换）
    assert b.ensure_position_id("ETH") == pid
