import json

import pytest


@pytest.mark.unit
def test_get_paper_position_health_returns_exit_state(db_session):
    from backend.api.paper_trading_routes import get_paper_position_health
    from backend.database.models import PaperPosition

    pos = PaperPosition(
        account_id=1,
        symbol="BTC",
        side="long",
        size=1.0,
        entry_price=100.0,
        mark_price=110.0,
        leverage=5.0,
        margin=20.0,
        unrealized_pnl=10.0,
        liquidation_price=50.0,
        status="open",
        trade_nature="trend_follow",
        health_score=72.0,
        health_regime="strong_trend",
        peak_unrealized_pnl=12.0,
        peak_pnl_pct=0.12,
        exit_state_json=json.dumps({"nature_staged_tp": {"triggered_stages": [0]}}),
    )
    db_session.add(pos)
    db_session.flush()

    result = get_paper_position_health(pos.id, db=db_session)

    assert result["position_id"] == pos.id
    assert result["health_score"] == 72.0
    assert result["peak_pnl_pct"] == 12.0
    assert result["exit_state"]["nature_staged_tp"]["triggered_stages"] == [0]
