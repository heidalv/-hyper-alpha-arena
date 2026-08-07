# backend/tests/unit/test_leverage_authority.py
import pytest

def test_leverage_cap_by_tier():
    from backend.services.leverage_authority import resolve_leverage
    # long tier 限 12,short/mid 限 20(单一权威)
    assert resolve_leverage(tier="long", requested=20.0) == 12
    assert resolve_leverage(tier="short", requested=10.0) == 10.0
    assert resolve_leverage(tier="mid", requested=25.0) == 20  # 钳到 cap

def test_consecutive_loss_lowers_cap():
    from backend.services.leverage_authority import resolve_leverage
    # 连亏时 mental_state 下调 cap,respect 之(不重置)
    assert resolve_leverage(tier="long", requested=12.0, mental_cap=5) == 5

def test_no_tier_floors_at_1():
    from backend.services.leverage_authority import resolve_leverage
    assert resolve_leverage(tier=None, requested=0.0) == 1.0

def test_mental_cap_cannot_exceed_tier_cap():
    """mental_cap 即便很高,也不能超过 tier cap。"""
    from backend.services.leverage_authority import resolve_leverage
    # long cap=12,mental_cap=15 → 仍受 tier cap 限,且 requested=12
    assert resolve_leverage(tier="long", requested=12.0, mental_cap=15) == 12
