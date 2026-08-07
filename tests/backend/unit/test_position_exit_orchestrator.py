import pytest


def _make_balance(db_session, account_id=1):
    from backend.database.models import PaperBalance

    bal = PaperBalance(
        account_id=account_id,
        initial_balance=10000.0,
        available_balance=9000.0,
        frozen_margin=1000.0,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
        total_equity=10000.0,
        total_fee_paid=0.0,
    )
    db_session.add(bal)
    db_session.flush()
    return bal


def _make_position(db_session, *, account_id=1, price=108.0, size=10.0, nature="trend_follow"):
    from backend.database.models import PaperPosition

    pos = PaperPosition(
        account_id=account_id,
        symbol="BTC",
        side="long",
        size=size,
        entry_price=100.0,
        mark_price=price,
        leverage=5.0,
        margin=200.0,
        unrealized_pnl=(price - 100.0) * size,
        liquidation_price=50.0,
        status="open",
        trade_nature=nature,
        timeframe_tier="long",
        strategy_id="s1",
    )
    db_session.add(pos)
    db_session.flush()
    return pos


@pytest.mark.unit
class TestPositionExitOrchestrator:
    def test_staged_reduce_executes_and_persists_exit_state(self, db_session, monkeypatch):
        from backend.services.position_exit_orchestrator import position_exit_orchestrator
        from backend.services.paper_trading_engine import paper_engine

        _make_balance(db_session, account_id=1)
        pos = _make_position(db_session, price=108.0)
        monkeypatch.setattr(paper_engine, "_get_current_price", lambda symbol, exchange=None: 108.0)

        changed = position_exit_orchestrator.evaluate_and_execute(
            db=db_session,
            account_id=1,
            positions=[{
                "id": pos.id,
                "symbol": pos.symbol,
                "side": pos.side,
                "size": pos.size,
                "entry_price": pos.entry_price,
                "mark_price": pos.mark_price,
                "trade_nature": pos.trade_nature,
                "strategy_id": pos.strategy_id,
            }],
            market_summary={"BTC": {"volatility_value": 0.01}},
        )

        db_session.refresh(pos)
        assert changed == 1
        assert pos.status == "open"
        assert pos.size < 10.0
        assert pos.exit_state_json

    def test_exit_state_preserves_trend_adjustment(self, db_session, monkeypatch):
        import json
        from backend.services.position_exit_orchestrator import position_exit_orchestrator
        from backend.services.paper_trading_engine import paper_engine

        _make_balance(db_session, account_id=3)
        pos = _make_position(db_session, account_id=3, price=108.0)
        pos.exit_state_json = json.dumps({
            "trend_adjustment": {
                "trailing_atr_mult": 2.5,
                "staged_tp_adjust": "raise",
            },
            "nature_staged_tp": {
                "triggered_stages": [],
                "peak_pnl_pct": 0.0,
                "trailing_active": False,
                "trailing_sl_price": None,
                "entry_price": 100.0,
            },
        })
        db_session.flush()
        monkeypatch.setattr(paper_engine, "_get_current_price", lambda symbol, exchange=None: 108.0)

        position_exit_orchestrator.evaluate_and_execute(
            db=db_session,
            account_id=3,
            positions=[{
                "id": pos.id,
                "symbol": pos.symbol,
                "side": pos.side,
                "size": pos.size,
                "entry_price": pos.entry_price,
                "mark_price": pos.mark_price,
                "trade_nature": pos.trade_nature,
                "strategy_id": pos.strategy_id,
            }],
            market_summary={"BTC": {"volatility_value": 0.01}},
        )

        db_session.refresh(pos)
        saved = json.loads(pos.exit_state_json)
        assert saved.get("trend_adjustment", {}).get("trailing_atr_mult") == 2.5
        assert saved.get("trend_adjustment", {}).get("staged_tp_adjust") == "raise"
        assert "nature_staged_tp" in saved

    def test_trailing_update_after_all_stages(self, db_session, monkeypatch):
        import json
        from backend.services.position_exit_orchestrator import position_exit_orchestrator
        from backend.services.paper_trading_engine import paper_engine

        _make_balance(db_session, account_id=2)
        pos = _make_position(db_session, account_id=2, price=126.0)
        pos.exit_state_json = json.dumps({
            "nature_staged_tp": {
                "triggered_stages": [0, 1, 2],
                "peak_pnl_pct": 0.25,
                "trailing_active": False,
                "trailing_sl_price": None,
                "entry_price": 100.0,
            }
        })
        db_session.flush()
        monkeypatch.setattr(paper_engine, "_get_current_price", lambda symbol, exchange=None: 126.0)

        changed = position_exit_orchestrator.evaluate_and_execute(
            db=db_session,
            account_id=2,
            positions=[{
                "id": pos.id,
                "symbol": pos.symbol,
                "side": pos.side,
                "size": pos.size,
                "entry_price": pos.entry_price,
                "mark_price": pos.mark_price,
                "trade_nature": pos.trade_nature,
                "strategy_id": pos.strategy_id,
            }],
            market_summary={"BTC": {"volatility_value": 0.01}},
        )

        db_session.refresh(pos)
        assert changed == 0
        assert pos.sl_price is not None
        assert "trailing_active" in pos.exit_state_json
