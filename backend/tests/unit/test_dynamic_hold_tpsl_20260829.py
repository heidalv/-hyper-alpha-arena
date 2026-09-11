# -*- coding: utf-8 -*-
"""短线持仓三阶段 / 行情翻脸 / min_hold 旁路 / 实盘同步。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _ctx(**kwargs):
    from backend.services.exit.exit_types import PositionContext
    defaults = dict(
        position_id=11, symbol="ETH", tier="short", side="long",
        entry_price=100.0, current_price=100.5, quantity=1.0,
        unrealized_pnl_pct=0.5, peak_pnl_pct=0.5, hold_seconds=120,
        atr_pct=2.0, sl_price=98.8, tp_price=102.0,
    )
    defaults.update(kwargs)
    return PositionContext(**defaults)


def test_short_atr_stages_reachable():
    from backend.services.exit.tier_exit_strategies import ShortTierExit
    d = ShortTierExit().evaluate(
        _ctx(unrealized_pnl_pct=1.0, peak_pnl_pct=1.0, atr_pct=1.0),
        breakeven_active=True,
    )
    assert d is not None
    assert d.action == "reduce"
    assert d.qty_ratio == pytest.approx(0.25)
    assert "TP#1" in d.reason


def test_fast_cut_uses_percent_not_fraction(monkeypatch):
    """fast_cut 的浮盈口径是百分数（0.3 = 0.3%），不是小数。

    [2026-09-02 P2.1 更新] 两处与首版不同，均为有意的行为变更：
    1. 触发窗口默认已由 15min 放宽到 30min（.env SCALP_EXIT_FAST_CUT_MIN=30，
       依据见 tier_exit_strategies 内注释），故用例显式钉住窗口，测的是"口径"
       而非"当前窗口值"；
    2. 新增"确实走坏"条件（unrealized <= -0.2%）—— 微盈微亏震荡中还没发育的
       仓位不再被砍。故这里给明确浮亏，保持验证 fast_cut 本身能触发。
    """
    from backend.services.exit.tier_exit_strategies import ShortTierExit
    monkeypatch.setenv("SCALP_EXIT_FAST_CUT_MIN", "15")
    d = ShortTierExit().evaluate(
        _ctx(hold_seconds=16 * 60, unrealized_pnl_pct=-0.25, peak_pnl_pct=0.2),
    )
    assert d is not None
    assert d.action == "close"
    assert d.source == "time_decay"
    assert "fast_cut" in d.reason


def test_regime_flip_extreme_locks_to_cost():
    from backend.services.exit.tier_exit_strategies import ShortTierExit
    d = ShortTierExit().evaluate(
        _ctx(regime="extreme", unrealized_pnl_pct=0.8, peak_pnl_pct=0.8, sl_price=98.5),
    )
    assert d is not None
    assert d.action == "tighten_sl"
    assert d.source == "regime_flip"
    assert d.new_sl_price == pytest.approx(100.02, abs=0.01)


def test_regime_flip_ranging_tightens_trail():
    from backend.services.exit.tier_exit_strategies import ShortTierExit
    d = ShortTierExit().evaluate(
        _ctx(regime="ranging", unrealized_pnl_pct=1.2, peak_pnl_pct=1.5, sl_price=99.0),
        breakeven_active=True,
    )
    assert d is not None
    assert d.action == "tighten_sl"
    assert d.source == "regime_flip"
    assert d.new_sl_price > 99.0


def test_min_hold_allows_short_breakeven():
    from backend.services.exit.exit_types import ExitRequest
    from backend.services.exit.unified_exit_state_machine import exit_state_machine

    exit_state_machine.reset_position(21)
    req = ExitRequest(
        position_id=21, symbol="ETH", tier="short",
        source="hold_review", proposed_action="hold", urgency="NORMAL",
    )
    d = exit_state_machine.submit(req, _ctx(position_id=21, hold_seconds=90))
    assert d.action == "tighten_sl"
    assert d.source == "breakeven"


def test_min_hold_still_blocks_ai_master_close():
    from backend.services.exit.exit_types import ExitRequest
    from backend.services.exit.unified_exit_state_machine import exit_state_machine

    exit_state_machine.reset_position(22)
    req = ExitRequest(
        position_id=22, symbol="ETH", tier="short",
        source="master_close", proposed_action="close", urgency="NORMAL",
    )
    d = exit_state_machine.submit(req, _ctx(position_id=22, hold_seconds=90))
    assert d.action == "hold"
    assert "保护期" in d.reason


def test_min_hold_bypass_can_rollback(monkeypatch):
    import backend.config.settings as settings_mod
    from backend.services.exit.exit_types import ExitRequest
    from backend.services.exit.unified_exit_state_machine import exit_state_machine

    monkeypatch.setattr(settings_mod, "SCALP_DYNAMIC_HOLD_TPSL", False)
    exit_state_machine.reset_position(23)
    req = ExitRequest(
        position_id=23, symbol="ETH", tier="short",
        source="hold_review", proposed_action="hold", urgency="NORMAL",
    )
    d = exit_state_machine.submit(req, _ctx(position_id=23, hold_seconds=90))
    assert d.action == "hold"
    assert "保护期" in d.reason


def test_live_sync_skips_paper_account():
    from backend.services.exchange import live_tpsl_sync as sync

    sync.reset_sync_state_for_tests()
    account = SimpleNamespace(id=7, trading_mode="paper", selected_exchange="binance", user_id=1)
    pos = SimpleNamespace(
        account_id=7, symbol="BTC", side="long",
        tp_price=110.0, sl_price=95.0, size=1.0,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = account
    out = sync.maybe_sync_live_tpsl(db, pos, force=True)
    assert out.get("skipped") is True
    assert out.get("reason") == "paper"


def test_live_sync_calls_ccxt_replace_when_dirty():
    from backend.services.exchange import live_tpsl_sync as sync

    sync.reset_sync_state_for_tests()
    account = SimpleNamespace(
        id=8, trading_mode="live", selected_exchange="binance",
        user_id=1, binance_market_type="usdt_m",
    )
    pos = SimpleNamespace(
        account_id=8, symbol="ETH", side="long",
        tp_price=2200.0, sl_price=1900.0, size=0.5,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = account
    async def _awaitable(*_a, **_k):
        return {"ok": True, "via": "ccxt"}

    client = SimpleNamespace(replace_tpsl_orders=_awaitable)

    with patch.object(sync, "_resolve_client", return_value=(client, "binance")):
        out = sync.maybe_sync_live_tpsl(db, pos, force=True)
    assert out.get("ok") is True
    # 第二次相同价格应跳过
    out2 = sync.maybe_sync_live_tpsl(db, pos, force=True)
    assert out2.get("reason") == "unchanged"


def test_live_sync_hl_uses_update_tpsl():
    from backend.services.exchange import live_tpsl_sync as sync

    sync.reset_sync_state_for_tests()
    account = SimpleNamespace(id=9, trading_mode="live", selected_exchange="hyperliquid", user_id=1)
    pos = SimpleNamespace(
        account_id=9, symbol="BTC", side="long",
        tp_price=70000.0, sl_price=65000.0, size=0.01,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = account
    inner = MagicMock()
    inner.update_tpsl.return_value = {"success": True, "sl_updated": True}
    client = SimpleNamespace(_client=inner)
    with patch.object(sync, "_resolve_client", return_value=(client, "hyperliquid")):
        out = sync.maybe_sync_live_tpsl(db, pos, force=True)
    assert out.get("ok") is True
    inner.update_tpsl.assert_called_once()
    kwargs = inner.update_tpsl.call_args
    assert kwargs.args[1] == "BTC"
    assert kwargs.kwargs["new_sl_price"] == 65000.0


def test_ccxt_replace_tpsl_cancels_and_places():
    from backend.services.exchange.ccxt_base_adapter import CcxtBaseAdapter

    class _FakeEx:
        def __init__(self):
            self.cancelled = []
            self.created = []

        async def fetch_open_orders(self, _sym):
            return [{
                "id": "old-sl",
                "type": "STOP_MARKET",
                "stopPrice": 99.0,
                "reduce_only": True,
                "info": {"reduceOnly": "true", "type": "STOP_MARKET", "stopPrice": 99.0},
            }]

        async def cancel_order(self, oid, sym):
            self.cancelled.append((oid, sym))

        async def create_order(self, *args, **kwargs):
            self.created.append((args, kwargs))
            return {"id": "new-sl"}

    adapter = CcxtBaseAdapter.__new__(CcxtBaseAdapter)
    adapter._exchange = _FakeEx()
    adapter._ccxt_id = "bybit"
    adapter._dual_side_cache = (0.0, False)

    out = asyncio.run(adapter.replace_tpsl_orders(
        "ETH", side="long", quantity=1.0, sl_price=98.0, tp_price=None,
    ))
    assert out["ok"] is True
    assert adapter._exchange.cancelled == [("old-sl", "ETH/USDT:USDT")]
    assert adapter._exchange.created
    args = adapter._exchange.created[0][0]
    assert args[1] == "STOP_MARKET"
    assert args[2] == "sell"
