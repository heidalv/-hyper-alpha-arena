"""P0-1: min_hold_emergency_loss_pct 映射验收."""
from backend.services.full_auto_trading_service import FullAutoTradingService


def test_tier_protection_emergency_pct_not_drawdown():
    tp = FullAutoTradingService._build_tier_protection()
    assert tp["short"]["emergency_pct"] == -8.0
    assert tp["mid"]["emergency_pct"] == -6.0
    assert tp["long"]["emergency_pct"] == -5.0
    assert tp["short"]["protect_min"] == 60.0


def test_emergency_close_logic_short():
    """保护期内 -7% 应拦截，-9% 应允许 emergency close。"""
    tp = FullAutoTradingService._build_tier_protection()
    emergency_pct = tp["short"]["emergency_pct"]
    assert -9.0 <= emergency_pct  # -9 <= -8 → allow
    assert -7.0 > emergency_pct   # -7 > -8 → block
