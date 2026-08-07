"""Tests for PaperTradingEngine integration with the unified paper exchange layer."""

from types import SimpleNamespace


def test_resolve_account_exchange_uses_trader_selected_exchange(db_session):
    from backend.database.models import Account
    from backend.services.paper_trading_engine import PaperTradingEngine

    account = Account(
        id=901,
        user_id=1,
        name="OKX Paper Trader",
        selected_exchange="okx",
        trading_mode="paper",
    )
    db_session.add(account)
    db_session.flush()

    engine = PaperTradingEngine()

    assert engine._resolve_account_exchange(db_session, 901) == "okx"


def test_order_exchange_is_locked_even_if_account_exchange_changes(db_session):
    from backend.database.models import Account, PaperOrder
    from backend.services.paper_trading_engine import PaperTradingEngine

    account = Account(
        id=902,
        user_id=1,
        name="Switching Paper Trader",
        selected_exchange="okx",
        trading_mode="paper",
    )
    order = PaperOrder(
        account_id=902,
        exchange="asterdex",
        symbol="BTC",
        side="buy",
        order_type="limit",
        price=100,
        quantity=1,
        leverage=10,
        status="pending",
    )
    db_session.add(account)
    db_session.add(order)
    db_session.flush()
    account.selected_exchange = "binance"
    db_session.flush()

    engine = PaperTradingEngine()

    assert engine._resolve_order_exchange(db_session, order) == "asterdex"


def test_reduce_only_close_uses_exchange_specific_unified_fee_rules():
    from backend.services.paper_trading_engine import PaperTradingEngine

    engine = PaperTradingEngine()
    pos = SimpleNamespace(
        id=1,
        symbol="BTC",
        leverage=10,
        trade_nature="swing",
    )

    _, hyper_fee = engine._simulate_reduce_fill(
        exchange="hyperliquid",
        pos=pos,
        close_side="sell",
        quantity=1,
        current_price=100,
        reason="manual",
        fill_price_override=100,
    )
    _, aster_fee = engine._simulate_reduce_fill(
        exchange="asterdex",
        pos=pos,
        close_side="sell",
        quantity=1,
        current_price=100,
        reason="manual",
        fill_price_override=100,
    )

    assert hyper_fee == 100 * 0.00035
    assert aster_fee == 100 * 0.00005
    assert aster_fee < hyper_fee


def test_recalc_balance_flushes_closed_position_before_query(db_session):
    from backend.database.models import Account, PaperBalance, PaperPosition
    from backend.services.paper_trading_engine import PaperTradingEngine

    db_session.autoflush = False
    account = Account(
        id=903,
        user_id=1,
        name="No Autoflush Paper Trader",
        selected_exchange="asterdex",
        trading_mode="paper",
    )
    balance = PaperBalance(
        account_id=903,
        initial_balance=1000,
        total_equity=1000,
        available_balance=990,
        frozen_margin=10,
        unrealized_pnl=0,
        realized_pnl=0,
        total_fee_paid=0,
    )
    position = PaperPosition(
        account_id=903,
        symbol="BTC",
        side="long",
        size=1,
        entry_price=100,
        mark_price=100,
        leverage=10,
        margin=10,
        liquidation_price=90,
        status="open",
    )
    db_session.add(account)
    db_session.add(balance)
    db_session.add(position)
    db_session.flush()

    engine = PaperTradingEngine()
    engine._recalc_balance(db_session, balance)
    assert balance.frozen_margin == 10

    position.status = "closed"
    engine._recalc_balance(db_session, balance)

    assert balance.frozen_margin == 0
    assert balance.available_balance == 1000


def test_build_entry_price_fallback_for_partial_closes():
    from types import SimpleNamespace
    from backend.services.paper_trading_engine import PaperTradingEngine

    engine = PaperTradingEngine()
    open_order = SimpleNamespace(
        id=1, symbol="XMR", strategy_id="s1", side="buy", close_reason=None,
        status="filled", filled_price=394.90, entry_price=None,
    )
    close_order = SimpleNamespace(
        id=2, symbol="XMR", strategy_id="s1", side="sell", close_reason="breakeven_tp",
        status="filled", filled_price=417.32, entry_price=None,
    )
    fb = engine._build_entry_price_fallback([open_order, close_order])
    assert fb[1] == 394.90
    assert fb[2] == 394.90


def test_normalize_close_reason_sl_with_profit_becomes_breakeven_tp():
    from backend.services.paper_trading_engine import PaperTradingEngine

    assert PaperTradingEngine.normalize_close_reason("sl", 1833.43) == "breakeven_tp"
    assert PaperTradingEngine.normalize_close_reason("sl", 0) == "breakeven_sl"
    assert PaperTradingEngine.normalize_close_reason("sl", -50) == "sl"
    assert PaperTradingEngine.normalize_close_reason("tp", 100) == "tp"
