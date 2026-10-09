# -*- coding: utf-8 -*-
"""[2026-10-09 进化重挂] ping-pong 学习链测试：桶门、选币、参数进化、记账语境。"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from backend.services.market_maker import pp_situation
from backend.services.market_maker.flow_universe import (
    pp_breakeven, pp_membership, select_pp_slots,
)
from backend.services.market_maker.runner import PlannedFill, SymbolState, TickDecision
from backend.services.market_maker import pingpong as PP
from backend.services.market_maker.core import InventoryBook, Position

TMP = Path(tempfile.mkdtemp(prefix="pp_evo_test_"))


# ══ 桶门纯函数 ══════════════════════════════════════════════════════

def test_bucket_key_and_breakeven():
    assert pp_situation.bucket_key(1.0, 10.0) == "s0_a0"
    assert pp_situation.bucket_key(5.0, 100.0) == "s1_a2"
    assert pp_situation.bucket_key(20.0, 500.0) == "s3_a3"
    assert pp_situation.breakeven_win_rate(39.0, -28.0) == pytest.approx(28 / 67)
    assert pp_situation.breakeven_win_rate(0.0, -10.0) == 1.0


def test_bucket_negative_insufficient_fails_open():
    row = {"n": 5, "win_rate": 0.2, "avg_win_bp": 5.0, "avg_loss_bp": -30.0}
    assert pp_situation.bucket_negative(row, min_n=20) is False, "样本不足不得拦截"
    assert pp_situation.bucket_negative(None, min_n=20) is False


def test_bucket_negative_blocks_on_evidence():
    bad = {"n": 40, "win_rate": 0.3, "avg_win_bp": 10.0, "avg_loss_bp": -25.0}
    assert pp_situation.bucket_negative(bad, min_n=20) is True
    good = {"n": 40, "win_rate": 0.62, "avg_win_bp": 39.0, "avg_loss_bp": -28.0}
    assert pp_situation.bucket_negative(good, min_n=20) is False


def test_side_blocked_doc_flow():
    doc = {"ts": 2000.0, "coins": {"BTC": {
        "buy": {"s0_a0": {"n": 40, "win_rate": 0.3, "avg_win_bp": 10.0,
                          "avg_loss_bp": -25.0}},
    }}}
    assert pp_situation.side_blocked(doc, "BTC", 2100.0, 1.0, 10.0, "buy", min_n=20)
    assert not pp_situation.side_blocked(doc, "BTC", 2100.0, 1.0, 10.0, "sell")
    assert not pp_situation.side_blocked(None, "BTC", 2100.0, 1.0, 10.0, "buy")
    assert not pp_situation.side_blocked(doc, "BTC", 99999.0, 1.0, 10.0, "buy"), "过期表 fail-open"


# ══ 选币（rt_bp 口径）══════════════════════════════════════════════

def test_pp_membership():
    good = {"n": 40, "win_rate": 0.62, "avg_win_bp": 39.0, "avg_loss_bp": -28.0,
            "avg_bp": 13.0}
    bad = {"n": 40, "win_rate": 0.3, "avg_win_bp": 10.0, "avg_loss_bp": -25.0,
           "avg_bp": -12.0}
    assert pp_membership("BTC", good, 1000.0, {}) == "eligible"
    assert pp_membership("BTC", bad, 1000.0, {}) == "drop"
    assert pp_membership("BTC", {"n": 5, "win_rate": 0.8, "avg_win_bp": 5.0,
                                 "avg_loss_bp": -5.0, "avg_bp": 0.0},
                         1000.0, {}) == "watch"
    assert pp_membership("BTC", None, 1000.0, {}) == "watch"


def test_select_pp_slots_drops_loser_keeps_winner():
    doc = {"ts": 1000.0, "coins": {
        "BTC": {"n": 40, "win_rate": 0.62, "avg_win_bp": 39.0, "avg_loss_bp": -28.0,
                "avg_bp": 13.0},
        "ETH": {"n": 40, "win_rate": 0.3, "avg_win_bp": 10.0, "avg_loss_bp": -25.0,
                "avg_bp": -12.0},
    }}
    kept, new_in = select_pp_slots(["BTC", "ETH"], doc, ["BTC", "ETH"], {}, 1000.0)
    assert "BTC" in kept and "ETH" not in kept


# ══ 记账语境 + 桶门 + 学习旋钮 ══════════════════════════════════════

def _call(state, *, bid, ask, now_ts, book=None, dec=None, mid=None, rt_dir=None,
          seg_low=0.0, seg_high=0.0, seg_sell=0.0, seg_buy=0.0,
          bid_qty=0.0, ask_qty=0.0, vol_at_bid=0.0, vol_at_ask=0.0,
          equity=10_000.0, vol_300s_bp=5.0, **over):
    book = book if book is not None else InventoryBook()
    dec = dec if dec is not None else TickDecision(symbol=state.symbol)
    if mid is None:
        mid = ((bid + ask) / 2.0) if (bid > 0 and ask > 0) else 100.0
    kw = dict(
        state=state, mid=mid, bb=bid, ba=ask, now_ts=now_ts,
        taker_fee_bp=4.0, local_book=book, dec=dec, PlannedFill=PlannedFill,
        regime="R1", equity=equity, vol_300s_bp=vol_300s_bp,
        seg_low=seg_low, seg_high=seg_high, seg_sell=seg_sell, seg_buy=seg_buy,
        bid_qty=bid_qty, ask_qty=ask_qty, vol_at_bid=vol_at_bid,
        vol_at_ask=vol_at_ask, roundtrip_root=rt_dir or TMP,
        stop_floor_bp=15.0, stop_cap_bp=40.0, loss_frac=0.005, same_side_n=1,
    )
    kw.update(over)
    PP.pingpong_decision(**kw)
    return dec, book, state


def _open_long(state, book, rt_dir):
    _call(state, bid=100.0, ask=100.1, now_ts=1000.0, book=book, rt_dir=rt_dir,
          bid_qty=10, ask_qty=8)
    _call(state, bid=100.0, ask=100.1, now_ts=1010.0, book=book, rt_dir=rt_dir,
          bid_qty=10, ask_qty=8, seg_low=99.9, seg_high=99.9, seg_sell=5,
          seg_buy=0, vol_at_bid=5)
    dec3, book, st = _call(state, bid=100.0, ask=100.1, now_ts=1020.0, book=book,
                           rt_dir=rt_dir, bid_qty=10, ask_qty=8,
                           seg_low=99.9, seg_high=99.9, seg_sell=8, seg_buy=0,
                           vol_at_bid=8)
    return dec3, book, st


def test_roundtrip_records_entry_context(monkeypatch):
    rt_dir = Path(tempfile.mkdtemp(prefix="pp_rt_"))
    st = SymbolState(symbol="BTC")
    book = InventoryBook()
    _open_long(st, book, rt_dir)
    assert st.qty > 0
    assert st.pp_entry_ctx.get("side") == "buy"
    assert st.pp_entry_ctx.get("spread_bp") > 0
    # 平仓 ⇒ 往返账写入语境字段
    _call(st, bid=100.0, ask=100.1, now_ts=1030.0, book=book, rt_dir=rt_dir,
          bid_qty=10, ask_qty=8, seg_low=100.0, seg_high=100.2,
          seg_sell=0, seg_buy=5, vol_at_ask=5)
    assert st.qty == 0
    rows = [json.loads(x) for x in (rt_dir / "data" / "flow_roundtrip_log.jsonl")
            .read_text(encoding="utf-8").splitlines() if x.strip()]
    pp_rows = [r for r in rows if r.get("strategy") == "PP"]
    assert len(pp_rows) == 1
    row = pp_rows[0]
    assert row["entry_side"] == "buy"
    assert row["entry_spread_bp"] > 0
    assert row["entry_front_usd"] == pytest.approx(10.0 * 100.0)


def test_bucket_gate_blocks_negative_side_only(monkeypatch):
    # bid 前档 10 BTC @100 = $1000 ⇒ a3；spread (100.1−100)/100.05 ≈ 10bp ⇒ s2
    doc = {"ts": 2000.0, "coins": {"BTC": {
        "buy": {"s2_a3": {"n": 40, "win_rate": 0.3, "avg_win_bp": 10.0,
                          "avg_loss_bp": -25.0}},
    }}}
    st = SymbolState(symbol="BTC")
    dec, book, st = _call(st, bid=100.0, ask=100.1, now_ts=2100.0,
                          bid_qty=10, ask_qty=0.08, pp_sit_doc=doc)
    assert dec.bid == 0.0, "负桶一侧必须休息"
    assert dec.ask > 0, "另一侧照挂"
    assert dec.skip == "pp_bucket_block(buy)"


def test_bucket_gate_fails_open(monkeypatch):
    # 证据不足的桶不拦
    doc = {"ts": 2000.0, "coins": {"BTC": {
        "buy": {"s2_a3": {"n": 5, "win_rate": 0.3, "avg_win_bp": 10.0,
                          "avg_loss_bp": -25.0}},
    }}}
    dec, _, _ = _call(SymbolState(symbol="BTC"), bid=100.0, ask=100.1,
                      now_ts=2100.0, bid_qty=10, ask_qty=0.08, pp_sit_doc=doc)
    assert dec.bid > 0 and dec.ask > 0, "样本不足必须照挂"
    # 没有文档也照挂
    dec2, _, _ = _call(SymbolState(symbol="BTC"), bid=100.0, ask=100.1,
                       now_ts=2100.0, bid_qty=10, ask_qty=0.08)
    assert dec2.bid > 0 and dec2.ask > 0


def test_learned_exit_ticks_take_effect(monkeypatch):
    st = SymbolState(symbol="BTC", qty=50.0, avg_px=100.0,
                     opened_ts=900.0, opened_ts_true=900.0)
    book = InventoryBook()
    book.positions["BTC"] = Position(qty=50.0, avg_px=100.0, avg_mid=100.0,
                                     opened_ts=900.0, last_ts=900.0)
    dec, _, _ = _call(st, bid=100.0, ask=100.1, now_ts=1000.0, book=book,
                      pp_exit_ticks=2.0)
    assert dec.ask == pytest.approx(100.3), "学习值 2 档 ⇒ 卖一 + 2 tick"


def test_learned_rest_sec_used_when_env_unset(monkeypatch):
    monkeypatch.delenv("MM_PP_REST_SEC", raising=False)
    st = SymbolState(symbol="BTC")
    book = InventoryBook()
    _open_long(st, book, TMP)
    _call(st, bid=100.0, ask=100.1, now_ts=1030.0, book=book,
          bid_qty=10, ask_qty=8, seg_low=100.0, seg_high=100.2,
          seg_sell=0, seg_buy=5, vol_at_ask=5, pp_rest_sec=7.0)
    assert st.pp_rest_until == pytest.approx(1030.0 + 7.0)


# ══ 参数进化（桶门阈值反事实走查）════════════════════════════════

def _rows_for_evo() -> list:
    """两个桶：好桶(60%胜、赚>亏) 与 坏桶(30%胜、亏>赚)。"""
    rows = []
    for i in range(40):
        rows.append({"strategy": "PP", "y_bp": 12.0 if i % 2 == 0 else -6.0,
                     "symbol": "BTC", "entry_side": "buy",
                     "entry_spread_bp": 1.0, "entry_front_usd": 10.0, "ts": 1000 + i})
    for i in range(40):
        rows.append({"strategy": "PP", "y_bp": 6.0 if i % 3 == 0 else -20.0,
                     "symbol": "BTC", "entry_side": "buy",
                     "entry_spread_bp": 5.0, "entry_front_usd": 500.0, "ts": 1000 + i})
    return rows


def test_pp_evolution_counterfactual(monkeypatch):
    from backend.services.market_maker import evolution as evo
    rows = _rows_for_evo()
    monkeypatch.setattr(evo, "_load_pp_roundtrips", lambda: rows)
    monkeypatch.setattr(evo, "evolve_enabled", lambda: False)
    r = evo.pp_evolution_round()
    assert r["ok"]
    # 在位 min_n=20：坏桶 40 样本被拦 ⇒ 保留 ≈40 行，均 bp > 0
    assert r["incumbent"]["n"] == pytest.approx(40)
    assert r["incumbent"]["avg_bp"] > 0
    # 候选 10 与在位同效果（还是拦坏桶）⇒ 改进不足不算 deploy；100 同上
    cands = {c["min_n"]: c for c in r["candidates"]}
    assert 10.0 in cands and 40.0 in cands and 100.0 in cands
    assert r["decision"] in ("deploy", "keep")
    assert r["applied"] is False, "未开总开关不得落地"
