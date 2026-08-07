# backend/tests/unit/test_tp_sl_authority.py
import pytest

def test_tier_to_nature_canonical():
    from backend.services.tp_sl_authority import TIER_TO_NATURE
    # 统一映射:消除 paper_trading_engine vs position_memory_manager 分歧
    assert TIER_TO_NATURE["short"] == "scalp"
    assert TIER_TO_NATURE["mid"] == "swing"
    assert TIER_TO_NATURE["long"] == "trend_follow"

def test_tp_sl_for_scalp_single_value():
    """scalp 的 TP/SL 只有一个权威值(不再 6 个)。"""
    from backend.services.tp_sl_authority import resolve_tp_sl_pct
    tp, sl = resolve_tp_sl_pct(tier="short")
    assert 0 < sl < 0.05
    assert tp > sl  # RR > 1

def test_tier_to_nature_consistent_across_modules():
    """两处旧映射应等于权威映射。"""
    from backend.services.tp_sl_authority import TIER_TO_NATURE
    from backend.services.paper_trading_engine import _TIER_TO_NATURE as pte_map
    from backend.services.position_memory_manager import _TIER_TO_NATURE as pmm_map
    assert pte_map == TIER_TO_NATURE
    assert pmm_map == TIER_TO_NATURE

def test_resolve_tp_sl_each_tier():
    from backend.services.tp_sl_authority import resolve_tp_sl_pct
    for tier in ("short", "mid", "long"):
        tp, sl = resolve_tp_sl_pct(tier=tier)
        assert 0 < sl and tp > sl

def test_sub_position_manager_mapping_unified_to_authority():
    """第4处 tier→nature 映射(sub_position_manager)已统一到权威。"""
    from backend.services.tp_sl_authority import TIER_TO_NATURE as _auth
    from backend.services.sub_position_manager import TIER_TO_NATURE as _spm
    assert _spm == _auth  # short→scalp, long→trend_follow 一致
