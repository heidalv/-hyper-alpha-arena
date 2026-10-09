# -*- coding: utf-8 -*-
"""方向卡：5 分钟重判，只停更差的一边。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.direction_card import (  # noqa: E402
    clamp_direction_proposal, direction_rows_from_legs, judge_direction,
    summarize_direction,
)
from backend.services.market_maker import runner as _mm  # noqa: E402
from backend.services.market_maker.core import (  # noqa: E402
    InventoryBook, LaneRiskLimits, Position, QuoteParams,
)


def test_aster_sell_blocked_uni_kept():
    card = judge_direction([
        {"symbol": "ASTER", "side": "buy", "n": 24, "price_bp": -0.45},
        {"symbol": "ASTER", "side": "sell", "n": 23, "price_bp": -4.43},
        {"symbol": "UNI", "side": "buy", "n": 22, "price_bp": -0.18},
        {"symbol": "UNI", "side": "sell", "n": 15, "price_bp": 6.36},
        {"symbol": "SOL", "side": "buy", "n": 2, "price_bp": -16.0},
        {"symbol": "SOL", "side": "sell", "n": 2, "price_bp": 0.0},
    ])
    assert card == {"ASTER": "sell"}


def test_never_blocks_both_sides():
    card = judge_direction([
        {"symbol": "ETH", "side": "buy", "n": 20, "price_bp": -3.0},
        {"symbol": "ETH", "side": "sell", "n": 20, "price_bp": -6.0},
    ])
    assert card == {"ETH": "sell"}


def _tick(symbol="ASTER", qty=0.0, block=None):
    st = _mm.SymbolState(symbol=symbol)
    st.mid_hist = [100.0] * 21
    book = InventoryBook()
    if qty:
        book.positions[symbol] = Position(
            qty=qty, avg_px=100.0, avg_mid=100.0, opened_ts=1_000_000.0, last_ts=1_000_000.0,
        )
        st.qty = qty
    lim = LaneRiskLimits(
        trend_pause_bp=0.0, ofi_confirm_threshold=0.0, ofi_block_threshold=0.0,
        ofi_require_threshold=0.0, trend_add_block_bp=0.0, trend_add_block_q=0.0,
        inv_add_block_ratio=0.0, sudden_move_bp=0.0, be_mult=0.0, jump_pause_bp=0.0,
        markout_window_n=0, book_slot_min_bp=0.0, max_net_directional_ratio=0.15,
    )
    dec, _ = _mm.plan_tick(
        state=st, mid=100.0, seg_low=99.9, seg_high=100.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=1000.0, fill_notional=100.0, half_spread=0.005,
        params=QuoteParams(), limits=lim, book=book, block_add_side=block,
    )
    return dec


def test_block_sell_add_keeps_reduce_and_buy():
    flat = _tick(block="sell")
    assert flat.bid > 0 and flat.ask == 0
    long = _tick(qty=1.0, block="sell")
    assert long.ask > 0


def test_summary_mentions_hold_and_skip():
    text = summarize_direction([
        {"symbol": "ASTER", "side": "buy", "n": 24, "price_bp": -0.45},
        {"symbol": "ASTER", "side": "sell", "n": 23, "price_bp": -4.43},
        {"symbol": "UNI", "side": "buy", "n": 10, "price_bp": -0.1},
        {"symbol": "UNI", "side": "sell", "n": 10, "price_bp": 1.0},
    ], {"ASTER": "sell"})
    assert "ASTER" in text and "停卖出加仓" in text
    assert "样本不够" in text


def test_clamp_drops_thin_side():
    rows = [
        {"symbol": "SOL", "side": "buy", "n": 2, "price_bp": -16.0},
        {"symbol": "ASTER", "side": "sell", "n": 20, "price_bp": -4.0},
    ]
    card = clamp_direction_proposal({"SOL": "buy", "ASTER": "sell", "UNI": "both"}, rows)
    assert card == {"ASTER": "sell"}


# ── [h626 确定性版] 开仓腿 markout 聚合 ──────────────────────────────

def test_direction_rows_markout_aggregation():
    legs = [
        {"symbol": "ASTER", "side": "buy", "notional": 100.0, "mid_px": 100.0},
        {"symbol": "ASTER", "side": "buy", "notional": 100.0, "mid_px": 100.0},
        {"symbol": "ASTER", "side": "sell", "notional": 100.0, "mid_px": 100.0},
    ]
    mids = {"ASTER": 100.5}
    rows = direction_rows_from_legs(legs, mids)
    by = {(r["symbol"], r["side"]): r for r in rows}
    # 买腿:mid 100→100.5 = +0.5% = +50bp(顺向为正)
    assert by[("ASTER", "buy")]["n"] == 2
    assert abs(by[("ASTER", "buy")]["price_bp"] - 50.0) < 1e-9
    # 卖腿:sgn=-1 ⇒ -50bp(价格上行对空头是逆向)
    assert abs(by[("ASTER", "sell")]["price_bp"] + 50.0) < 1e-9


def test_direction_rows_skips_missing_mid():
    legs = [{"symbol": "SOL", "side": "buy", "notional": 10.0, "mid_px": 1.0}]
    assert direction_rows_from_legs(legs, {}) == []


def test_direction_rows_skips_bad_side_and_notional():
    legs = [
        {"symbol": "ETH", "side": "hold", "notional": 100.0, "mid_px": 100.0},
        {"symbol": "ETH", "side": "buy", "notional": 0.0, "mid_px": 100.0},
        {"symbol": "ETH", "side": "sell", "notional": 100.0, "mid_px": 0.0},
    ]
    assert direction_rows_from_legs(legs, {"ETH": 100.0}) == []


def test_judge_on_markout_rows_blocks_worse_side_only():
    # ASTER 卖边被逆向选择(-4.4bp vs 买边 -0.5bp) ⇒ 只停卖;UNI 卖边在赚 ⇒ 不动
    rows = [
        {"symbol": "ASTER", "side": "buy", "n": 24, "price_bp": -0.45},
        {"symbol": "ASTER", "side": "sell", "n": 23, "price_bp": -4.43},
        {"symbol": "UNI", "side": "buy", "n": 22, "price_bp": -0.18},
        {"symbol": "UNI", "side": "sell", "n": 15, "price_bp": 6.36},
    ]
    assert judge_direction(rows) == {"ASTER": "sell"}


def test_judge_min_n_by_adaptive_override():
    # [h652 §9.3 候选] 逐币 min_n:ASTER 抬高到 30 ⇒ 卖边 23 笔不足 30,不得停;
    # UNI 维持 15 ⇒ 卖边 15 笔 +6.36 非更差,不停。两者皆不停 ⇒ 空卡。
    rows = [
        {"symbol": "ASTER", "side": "buy", "n": 24, "price_bp": -0.45},
        {"symbol": "ASTER", "side": "sell", "n": 23, "price_bp": -4.43},
        {"symbol": "UNI", "side": "buy", "n": 22, "price_bp": -0.18},
        {"symbol": "UNI", "side": "sell", "n": 15, "price_bp": 6.36},
    ]
    assert judge_direction(rows, min_n_by={"ASTER": 30}) == {}


def test_judge_never_blocks_both_and_thin_side():
    # SOL 只有 2 笔:样本不足,不得停任何一边
    rows = [
        {"symbol": "SOL", "side": "buy", "n": 2, "price_bp": -16.0},
        {"symbol": "SOL", "side": "sell", "n": 2, "price_bp": 0.0},
        {"symbol": "ETH", "side": "buy", "n": 20, "price_bp": -3.0},
        {"symbol": "ETH", "side": "sell", "n": 20, "price_bp": -6.0},
    ]
    assert judge_direction(rows) == {"ETH": "sell"}


def test_direction_rows_single_side_no_keyerror():
    """[h665e 回归] 某币只有买单样本时不得 KeyError('sell')
    (新币/清淡币常态,曾把整张方向卡炸掉→前端回退"已停")。"""
    from backend.services.market_maker.direction_card import direction_rows_from_legs

    legs = [
        {"symbol": "UNI", "side": "buy", "mid_px": 9.0, "notional": 50.0},
        {"symbol": "BNB", "side": "buy", "mid_px": 770.0, "notional": 50.0},
        {"symbol": "BNB", "side": "sell", "mid_px": 770.0, "notional": 50.0},
    ]
    rows = direction_rows_from_legs(legs, {"UNI": 9.5, "BNB": 771.0})
    sides = {(r["symbol"], r["side"]) for r in rows}
    assert ("UNI", "buy") in sides
    assert ("UNI", "sell") not in sides      # 缺失边只是缺席,不炸
    assert ("BNB", "buy") in sides and ("BNB", "sell") in sides
    # judge_direction 也不炸,并正确给出"样本不够"
    from backend.services.market_maker.direction_card import judge_direction
    card = judge_direction(rows, min_n=15, worse_bp=-2.0, gap_bp=2.0)
    assert card == {}
