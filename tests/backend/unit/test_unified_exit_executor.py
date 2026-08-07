"""P1: UnifiedExitExecutor tier 路由与门控."""
from backend.services.master_close_guard import (
    route_exit_tier,
    check_agent_exit_hardfact,
    check_master_close_hardfact,
)
from backend.services.unified_exit_executor import (
    unified_exit_executor,
    ExitExecuteRequest,
)


def test_route_exit_tier():
    assert route_exit_tier("sl") == 0
    assert route_exit_tier("max_hold_timeout") == 0
    assert route_exit_tier("trend_review_close") == 1
    assert route_exit_tier("hold_timeout_review") == 1
    assert route_exit_tier("master_running_close") == 2
    assert route_exit_tier("ai_take_profit") == 2


def test_tier1_agent_small_loss_allowed_for_trend_review():
    r = check_agent_exit_hardfact(
        tier="long",
        action="close",
        entry_price=100.0,
        mark_price=97.0,
        sl_price=90.0,
        unrealized_pnl=-30.0,
        margin=1000.0,
        exit_channel="trend_review_close",
        reason_hint="trend weakening",
    )
    assert r.allow is True


def test_tier2_master_small_loss_blocked():
    r = check_master_close_hardfact(
        tier="mid",
        action="close",
        entry_price=100.0,
        mark_price=99.0,
        sl_price=95.0,
        unrealized_pnl=-10.0,
        margin=1000.0,
        reason_hint="market noise",
    )
    assert r.allow is False


def test_unified_exit_executor_blocks_tier2_small_loss():
    from backend.services.full_auto_trading_service import FullAutoTradingService
    pos = {
        "timeframe_tier": "mid",
        "entry_price": 100.0,
        "mark_price": 99.0,
        "sl_price": 95.0,
        "unrealized_pnl": -10.0,
        "margin": 1000.0,
        "side": "long",
        "opened_at": "2020-01-01T00:00:00+00:00",
    }
    req = ExitExecuteRequest(
        db=None,
        account_id=1,
        symbol="BTC",
        action="close",
        pos=pos,
        exit_channel="master_running_close",
        tier_level=2,
        get_risk_score=lambda _a: 50.0,
        tier_protection=FullAutoTradingService._build_tier_protection(),
    )
    gate = unified_exit_executor.should_block(req)
    assert gate.blocked is True


def test_unified_exit_executor_allows_tier1_trend_review():
    pos = {
        "timeframe_tier": "long",
        "entry_price": 100.0,
        "mark_price": 99.0,
        "sl_price": 90.0,
        "unrealized_pnl": -10.0,
        "margin": 1000.0,
        "side": "long",
        "opened_at": "2020-01-01T00:00:00+00:00",
    }
    from backend.services.full_auto_trading_service import FullAutoTradingService
    req = ExitExecuteRequest(
        db=None,
        account_id=1,
        symbol="BTC",
        action="close",
        pos=pos,
        exit_channel="trend_review_close",
        tier_level=1,
        get_risk_score=lambda _a: 50.0,
        tier_protection=FullAutoTradingService._build_tier_protection(),
    )
    gate = unified_exit_executor.should_block(req)
    assert gate.blocked is False
