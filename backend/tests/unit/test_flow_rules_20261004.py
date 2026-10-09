# -*- coding: utf-8 -*-
"""主动流规则：离场阶梯、挂单打分、杠杆、样本外开门、DSH 白名单。"""
from pathlib import Path

from backend.services.market_maker.core import InventoryBook
from backend.services.market_maker.flow_rules import (
    FLOW_FEATURES,
    FLOW_SAFE_PARAMS,
    bucket_tradable,
    choose_exit,
    decile_table,
    disaster_stop_bp,
    exchange_leverage,
    gate_decision,
    situation_decision,
    maker_roundtrip_bp,
    notional_cap_usd,
    ofi_ratio,
    playbook_for_prompt,
    purged_splits,
    quantile_edges,
    should_rollback_flow,
    taker_chase_allowed,
    taker_edge_bp,
)
from backend.services.market_maker.runner import PlannedFill, SymbolState
from backend.services.market_maker.self_tuner import parse_proposal


def test_ofi_is_notional_ratio():
    assert ofi_ratio(30, 10) == 0.5
    assert ofi_ratio(0, 0) == 0.0
    assert "ofi_60s" in FLOW_FEATURES
    assert "ofi_60n" not in FLOW_FEATURES


def test_exit_ladder_maker_unless_disaster():
    assert choose_exit(
        qty=1, entry_px=100, bid=0, ask=0, now_ts=100, opened_ts=1,
        max_hold_sec=90, mu=2, vol_300s_bp=10, regime="R1", book_stale=False,
    ) == "no_book"
    assert choose_exit(
        qty=1, entry_px=100, bid=99.95, ask=100.05, now_ts=100, opened_ts=0,
        max_hold_sec=90, mu=2, vol_300s_bp=10, regime="R1", book_stale=False,
    ) == "maker_time"
    assert choose_exit(
        qty=1, entry_px=100, bid=99, ask=101, now_ts=200, opened_ts=10,
        max_hold_sec=90, mu=2, vol_300s_bp=10, regime="R4", book_stale=False,
    ) == "taker_risk"
    assert choose_exit(
        qty=1, entry_px=100, bid=99.98, ask=100.02, now_ts=200, opened_ts=100,
        max_hold_sec=300, mu=-0.1, vol_300s_bp=10, regime="R1", book_stale=False,
    ) == "maker_edge"
    # 浮亏还没到灾难止损时不吃单。100 -> 买一 99.5 大约 -50bp 再扣 4bp，波动止损上限 40。
    assert choose_exit(
        qty=1, entry_px=100, bid=99.5, ask=100.1, now_ts=50, opened_ts=40,
        max_hold_sec=90, mu=1, vol_300s_bp=8, regime="R1", book_stale=False,
    ) == "taker_stop"


def test_fee_inversion_blocks_taker():
    assert maker_roundtrip_bp(100, 100.1, 1) > 0
    assert taker_edge_bp(6, 2) < 0
    assert taker_chase_allowed(3) is False
    assert taker_chase_allowed(5) is True
    assert disaster_stop_bp(3) >= 15
    assert disaster_stop_bp(30) == 40


def test_notional_and_leverage_ignore_compound_ratio():
    # 止损 40bp，6 笔同向：每笔名义受当日 2% 约束，小于单笔 0.5% 那一档。
    one = notional_cap_usd(10_000, 40, 1)
    six = notional_cap_usd(10_000, 40, 6)
    assert one == 10_000 * 0.005 / 0.004
    assert six < one
    assert six == 10_000 * 0.02 / (6 * 0.004)
    lev = exchange_leverage(40, mmr=0.01)
    assert lev == int(1 / (0.012 + 0.01))
    assert "compound_ratio" not in FLOW_SAFE_PARAMS


def test_gate_fail_closed_and_oos_not_win_rate():
    assert gate_decision(None, "BTC", 100)["allow"] is False
    assert gate_decision({"ts": 1}, "BTC", 5000)["allow"] is False
    doc = {"ts": 100, "gates": {"BTC": {
        "allow": True, "side": "buy", "mu": 2.0, "max_hold_sec": 90,
        "oos": {"mean_y": -1.0, "n_eff": 80, "win_rate": 0.9},
    }}}
    assert gate_decision(doc, "BTC", 200)["allow"] is False
    doc["gates"]["BTC"]["oos"]["mean_y"] = 1.2
    doc["gates"]["BTC"]["oos"]["n_eff"] = 10
    assert gate_decision(doc, "BTC", 200)["allow"] is False
    doc["gates"]["BTC"]["oos"]["n_eff"] = 40
    opened = gate_decision(doc, "BTC", 200)
    assert opened["allow"] is True and opened["side"] == "buy"
    assert bucket_tradable(-0.2, 100, 0.95) is False
    assert bucket_tradable(0.4, 30, 0.4) is True


def _situation(mean, n=40, med=1.2, recent=1.1, ahead_i=0, spread_i=1):
    return {
        "n": n, "mean_y": mean, "median_y": med, "recent_mean_y": recent,
        "ahead_i": ahead_i, "spread_i": spread_i,
    }


def test_situation_asks_this_tick_only():
    assert situation_decision(None, "BTC", 100, 10, 10, 4)["reason"] == "no_situation_file"
    doc = {"ts": 100, "coins": {"BTC": {
        "sell": [_situation(2.0, ahead_i=0, spread_i=1)],
        "buy": [_situation(-3.0, ahead_i=0, spread_i=1)],
    }}}
    # 价差 4bp 落在第 2 档（2 到 8），卖一前面不到 30 美元是第 1 档。
    opened = situation_decision(doc, "BTC", 200, ahead_bid_usd=10, ahead_ask_usd=10, spread_bp=4)
    assert opened["allow"] is True and opened["side"] == "sell"
    # 卖一前面变成 150 美元，这一档没有记录，这一拍不做。币没有被关掉。
    skipped = situation_decision(doc, "BTC", 200, ahead_bid_usd=10, ahead_ask_usd=150, spread_bp=4)
    assert skipped["allow"] is False and skipped["reason"] == "situation_not_this_tick"
    # 最典型的一笔不赚，这一档也不做。
    doc["coins"]["BTC"]["sell"][0]["median_y"] = -0.2
    weak = situation_decision(doc, "BTC", 200, 10, 10, 4)
    assert weak["allow"] is False
    # 最近一小段略亏，但整档平均和最典型的一笔都赚：这一拍可以做。
    doc["coins"]["BTC"]["sell"][0]["median_y"] = 2.7
    doc["coins"]["BTC"]["sell"][0]["mean_y"] = 4.8
    doc["coins"]["BTC"]["sell"][0]["recent_mean_y"] = -1.6
    faded = situation_decision(doc, "BTC", 200, 10, 10, 4)
    assert faded["allow"] is True and faded["side"] == "sell"


def test_purged_split_and_deciles():
    parts = purged_splits(200, embargo=6)
    assert parts is not None
    train, cal, test = parts
    assert train.stop <= cal.start
    assert cal.stop + 6 <= test.start
    assert set(train).isdisjoint(test)
    pred = [i / 10 for i in range(30)]
    y = [1.0 if i >= 20 else -0.2 for i in range(30)]
    edges = quantile_edges(pred[:20])
    table = decile_table(pred[20:], y[20:], edges, 15, 90)
    assert any(row["tradable"] for row in table) or all(row["n_eff"] < 30 for row in table)
    assert all(row["win_rate"] is None or row["mean_y"] is not None for row in table if row["n"])


def test_rollback_if_more_fills_but_worse_y():
    assert should_rollback_flow(
        {"mean_y": 2, "n": 40, "taker_fee_share": 0.1, "fill_rate": 0.5, "liquidations": 0},
        {"mean_y": 1, "n": 80, "taker_fee_share": 0.1, "fill_rate": 0.5, "liquidations": 0},
    )
    assert not should_rollback_flow(
        {"mean_y": 2, "n": 40, "taker_fee_share": 0.1, "fill_rate": 0.5, "liquidations": 0},
        {"mean_y": 1.9, "n": 40, "taker_fee_share": 0.1, "fill_rate": 0.5, "liquidations": 0},
    )


def test_playbook_drops_market_making_laws():
    book = playbook_for_prompt({"laws": [
        {"law": "捕获不够就亏", "era": "mm"},
        {"law": "旧定律没有标记"},
        {"law": "往返 y 为正才留", "era": "flow", "n": 40},
    ]})
    assert len(book["laws"]) == 1
    assert book["laws"][0]["era"] == "flow"


def test_flow_proposal_rejects_spread():
    raw = '{"action":"adjust","param":"spread_mult","old":1,"new":1.2,"rollback":1,' \
          '"verdict_metric":"fills_per_hour","reason":"更多成交"}'
    assert parse_proposal(raw, FLOW_SAFE_PARAMS) is None
    ok = '{"action":"adjust","param":"entry_margin_bp","old":1,"new":1.5,"rollback":1,' \
         '"verdict_metric":"roundtrip_y","reason":"安全垫"}'
    prop = parse_proposal(ok, FLOW_SAFE_PARAMS)
    assert prop and prop["param"] == "entry_margin_bp"
    assert prop["verdict_metric"] == "roundtrip_y"


def test_oos_conditional_uses_only_hits_in_the_later_part(monkeypatch):
    # [2026-10-09] 本用例锁定旧单边流契约；ping-pong 默认开启后钉住回滚路径。
    monkeypatch.setenv("MM_PINGPONG", "0")
    # [2026-10-09] 同时钉住"挂队尾"（MM_ENTRY_AGGRESSIVE 的 2026-10-08 默认值
    # 会插进价差，本用例写于那之前，锁定的是队尾进场契约）。
    monkeypatch.setenv("MM_ENTRY_AGGRESSIVE", "0")
    from backend.services.market_maker.flow_rules import oos_conditional_mean
    values = [-5.0] * 10 + [2.0] * 10
    hit = [True] * 20
    mean, n = oos_conditional_mean(values, hit, split=10, embargo=0)
    assert n == 10 and abs(mean - 2.0) < 1e-9
    hit[-1] = False
    mean, n = oos_conditional_mean(values, hit, split=10, embargo=0)
    assert n == 9
    from backend.services.market_maker.flow_rules import pool_positive_deciles
    table = [
        {"decile": i, "n": 40, "mean_y": 1.5 if i >= 5 else -0.4,
         "win_rate": 0.6, "n_eff": 6.0, "tradable": False}
        for i in range(10)
    ]
    pooled = pool_positive_deciles(table, 15, 90)
    assert pooled is not None and pooled["tradable"] is True
    assert pooled["n_eff"] >= 30
    from backend.services.market_maker.active_flow import active_flow_decision
    state = SymbolState(symbol="BTC")
    book = InventoryBook()
    dec = type("D", (), {})()
    dec.bid = dec.ask = 0.0
    dec.fills = []
    dec.skip = ""
    dec.exit_path = ""
    dec.action = ""
    dec.regime = ""
    root = Path(__file__).resolve().parents[2] / "data" / "_flow_test_rt"
    # 父目录用临时文件名避免污染正式日志：roundtrip_root 是项目根，测试用临时目录对象。
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    active_flow_decision(
        state=state, mid=100, ofi=0.8, trend_bp=10, bb=99.9, ba=100.1,
        now_ts=1_000, fill_notional=9_999, taker_fee_bp=4, sl_bp=40, tp_bp=60,
        max_hold_sec=90, flow_thresh=0.15, local_book=book, dec=dec,
        PlannedFill=PlannedFill, maker_fee_bp=0, allow_entry=True,
        model_side="buy", model_mu=2.5, regime="R1", equity=10_000,
        vol_300s_bp=8, seg_low=0, seg_high=0, seg_sell=0, seg_buy=0,
        roundtrip_root=tmp,
    )
    assert dec.fills == []
    assert dec.bid > 0 and dec.ask == 0
    assert state.qty == 0
    # 下一拍价格打到挂单才成交，手续费为 0。
    active_flow_decision(
        state=state, mid=100, ofi=0.2, trend_bp=1, bb=99.9, ba=100.1,
        now_ts=1_010, fill_notional=9_999, taker_fee_bp=4, sl_bp=40, tp_bp=60,
        max_hold_sec=90, flow_thresh=0.15, local_book=book, dec=dec,
        PlannedFill=PlannedFill, maker_fee_bp=0, allow_entry=True,
        model_side="buy", model_mu=2.5, regime="R1", equity=10_000,
        vol_300s_bp=8, seg_low=dec.bid, seg_high=dec.bid, seg_sell=5, seg_buy=0,
        roundtrip_root=tmp,
    )
    assert state.qty > 0
    assert dec.fills[-1].fee_usd == 0.0
    assert dec.exit_path == "flow_entry_maker"
    # [整顿轮·T1 2026-10-05 更新预期] 危险形态有仓：**必须走挂单，不得吃单**。
    #
    # 旧断言（吃单离场）已被实测推翻：近 24h `taker_stop` 一条路径 −47.2bp/腿、
    # 合计 −$266，是当时唯一的大失血点；而走挂单的强平腿 +19.91bp/腿。
    # 新契约：R4/R5 或盘口过期时，离场**只许挂单**（0 费）；
    #         挂单未成交时库存会多留若干拍，由两条兜底保证不卡死：
    #           ① `_EXIT_TAKER_AFTER_SEC`（默认 1800s）超时硬吃单
    #           ② `max_one_side_seconds` / `timeout_hard_taker_sec`
    # 回滚：`MM_RISK_EXIT_MAKER_ONLY=0` 恢复旧的"直接吃单"行为。
    #
    # 断言守"性质"而非"标签"：本拍不许有付费腿，且库存必须能在有限拍内出清。
    _taker_legs = [f for f in dec.fills if float(getattr(f, "fee_usd", 0.0) or 0.0) < 0]
    assert _taker_legs == [], (
        f"危险形态下禁止吃单离场，却出现 {len(_taker_legs)} 条付费腿")

    # 后续若干拍里有人持续打到我们的挂单 ⇒ 库存必须被出清（不得卡死）。
    # 这也验证"挂单抢平"不会退化成"永不成交"。
    for _k in range(6):
        if abs(state.qty) < 1e-9:
            break
        _q_ask = float(state.quote_ask or 0.0)
        _q_bid = float(state.quote_bid or 0.0)
        active_flow_decision(
            state=state, mid=100, ofi=0, trend_bp=0, bb=99.9, ba=100.1,
            now_ts=1_030 + _k * 10, fill_notional=0, taker_fee_bp=4,
            sl_bp=40, tp_bp=60, max_hold_sec=90, flow_thresh=0.15,
            local_book=book, dec=dec, PlannedFill=PlannedFill,
            maker_fee_bp=0, allow_entry=False, model_side=None, model_mu=None,
            regime="R4", equity=10_000, vol_300s_bp=8,
            # 双向都打到我们的挂单价 ⇒ 无论挂在哪一侧都会被成交
            seg_low=(_q_bid or 99.9), seg_high=(_q_ask or 100.1),
            seg_sell=5, seg_buy=5, roundtrip_root=tmp,
        )
    assert abs(state.qty) < 1e-9, (
        "危险形态下库存必须能在有限拍内出清（不得卡死）")
    _taker_legs = [f for f in dec.fills if float(getattr(f, "fee_usd", 0.0) or 0.0) < 0]
    assert _taker_legs == [], "全程不得出现付费腿"


def test_queue_must_be_eaten_before_fill(monkeypatch):
    # [2026-10-09] 本用例锁定旧单边流契约；ping-pong 默认开启后钉住回滚路径，
    # 并钉住"挂队尾"（本用例写于 MM_ENTRY_AGGRESSIVE 默认开启之前）。
    monkeypatch.setenv("MM_PINGPONG", "0")
    monkeypatch.setenv("MM_ENTRY_AGGRESSIVE", "0")
    from backend.services.market_maker.active_flow import active_flow_decision
    import tempfile
    state = SymbolState(symbol="PLAY")
    book = InventoryBook()
    dec = type("D", (), {})()
    dec.bid = dec.ask = 0.0
    dec.fills = []
    dec.skip = ""
    dec.exit_path = ""
    dec.action = ""
    dec.regime = ""
    tmp = Path(tempfile.mkdtemp())
    common = dict(
        mid=100, ofi=0.0, trend_bp=0.0, bb=99.9, ba=100.1,
        fill_notional=0, taker_fee_bp=4, sl_bp=40, tp_bp=60,
        max_hold_sec=1, flow_thresh=0.15, local_book=book, dec=dec,
        PlannedFill=PlannedFill, maker_fee_bp=0, allow_entry=True,
        model_side="buy", model_mu=2.0, regime="R1", equity=10_000,
        vol_300s_bp=8, roundtrip_root=tmp, bid_qty=10, ask_qty=1,
        probe_notional_usd=0,
    )
    active_flow_decision(state=state, now_ts=1_000, seg_low=0, seg_high=0,
                         seg_sell=0, seg_buy=0, vol_at_bid=0, **common)
    assert state.qty == 0 and state.flow_queue_ahead == 10
    active_flow_decision(state=state, now_ts=1_010, seg_low=99.9, seg_high=99.9,
                         seg_sell=3, seg_buy=0, vol_at_bid=3, **common)
    assert state.qty == 0 and state.flow_queue_cum == 3
    active_flow_decision(state=state, now_ts=1_020, seg_low=99.9, seg_high=99.9,
                         seg_sell=8, seg_buy=0, vol_at_bid=8, **common)
    assert state.qty > 0
    assert dec.exit_path == "flow_entry_maker"


def test_time_exit_stays_maker_so_fee_stays_zero(monkeypatch):
    """到点只挂平仓单，不吃单。吃单要付 4bp，小利润会被手续费倒挂吃掉。"""
    # [2026-10-09] 本用例锁定旧单边流契约；ping-pong 默认开启后钉住回滚路径。
    monkeypatch.setenv("MM_PINGPONG", "0")
    monkeypatch.setenv("MM_ENTRY_AGGRESSIVE", "0")
    from backend.services.market_maker.active_flow import active_flow_decision
    from backend.services.market_maker.core import Position
    import tempfile

    # [h883] 名义抬到 (高于  最小下单额),避免被灰尘清扫分支拦截
    state = SymbolState(symbol="BTC", qty=0.1, avg_px=100.0, opened_ts=100.0)
    book = InventoryBook()
    book.positions["BTC"] = Position(qty=0.1, avg_px=100.0, avg_mid=100.0,
                                     opened_ts=100.0, last_ts=100.0)
    dec = type("D", (), {})()
    dec.bid = dec.ask = 0.0
    dec.fills = []
    dec.skip = ""
    dec.exit_path = ""
    dec.action = ""
    dec.regime = ""
    active_flow_decision(
        state=state, mid=100, ofi=0, trend_bp=0, bb=99.9, ba=100.1,
        now_ts=100.0 + 301, fill_notional=0, taker_fee_bp=4, sl_bp=40, tp_bp=60,
        max_hold_sec=1, flow_thresh=0.15, local_book=book, dec=dec,
        PlannedFill=PlannedFill, maker_fee_bp=0, allow_entry=False,
        model_side=None, model_mu=None, regime="R1", equity=10_000,
        vol_300s_bp=8, roundtrip_root=Path(tempfile.mkdtemp()),
    )
    assert abs(state.qty) > 0
    assert dec.fills == []
    assert dec.ask > 0
    assert dec.exit_path != "hold_expired"

