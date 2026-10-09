# -*- coding: utf-8 -*-
"""仓位编号不能在重启后撞车，否则两笔交易会被记成一笔。"""
from backend.services.market_maker.core import InventoryBook
from backend.services.market_maker.runner import SymbolState


def test_restart_does_not_reuse_position_id():
    first = InventoryBook()
    first.apply_fill(symbol="BTC", side="buy", qty=1, fill_px=100,
                     mid_px=100.1, now_ts=1_700_000_000)
    opened = first.positions["BTC"].position_id
    first.apply_fill(symbol="BTC", side="sell", qty=1, fill_px=101,
                     mid_px=100.9, now_ts=1_700_000_030)

    restarted = InventoryBook()
    restarted.apply_fill(symbol="BTC", side="buy", qty=1, fill_px=100,
                         mid_px=100.1, now_ts=1_700_000_100)
    again = restarted.positions["BTC"].position_id

    assert opened == "mm:BTC:1:1700000000"
    assert again == "mm:BTC:1:1700000100"
    assert opened != again


def test_replay_clock_keeps_short_id():
    book = InventoryBook()
    book.apply_fill(symbol="BTC", side="buy", qty=1, fill_px=100,
                    mid_px=100.1, now_ts=1000.0)
    assert book.positions["BTC"].position_id == "mm:BTC:1"


def test_true_open_time_survives_save():
    state = SymbolState(symbol="BTC", opened_ts=50.0, opened_ts_true=1_700_000_000)
    loaded = SymbolState.from_dict(state.to_dict())
    assert loaded.opened_ts_true == 1_700_000_000
    assert loaded.opened_ts == 50.0
