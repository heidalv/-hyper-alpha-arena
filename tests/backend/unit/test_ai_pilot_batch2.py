"""
AI 主驾改造 第二批回归测试（行为改变类）

改动 3: action 不偷换 —— AI buy + 已有同向仓 → 保留 buy（非 pyramid），除非子仓上限
改动 4: TP/SL AI 优先 —— AI 的 SL ≥ 硬下限时直接采纳
改动 5: 杠杆 AI 优先 —— _calc_leverage 用 AI 值，仅硬上限约束
"""
import pytest
from unittest.mock import MagicMock


pytestmark = pytest.mark.unit


# ────────────── 改动 4: TP/SL AI 优先 ──────────────

def test_calc_tp_sl_uses_ai_sl_above_hard_floor():
    """AI 的 SL 距离 ≥ 硬下限(min_sl_pct)时，直接采纳 AI 的值。"""
    from backend.services.position_memory_manager import PositionMemoryManager, MemoryInsight

    mgr = PositionMemoryManager()
    memory = MemoryInsight(
        symbol_win_rate=0.5, symbol_trade_count=10,
        best_leverage_for_symbol=10, recommended_size_adj=1.0,
    )
    price = 100.0
    # swing tier 的 min_sl_pct=0.945 → 硬下限 SL 价 = 94.5（距离 5.5%）
    # AI 给 SL=92（距离 8%，比硬下限宽）→ 应直接用 92
    tp, sl = mgr._calc_tp_sl(
        side="buy", price=price, leverage=10, volatility_pct=0.02,
        raw_tp=112.0, raw_sl=92.0, memory=memory, tier="swing",
    )
    assert sl == 92.0, f"AI SL=92(距离8%≥硬下限5.5%)应直接采纳, 实际={sl}"


def test_calc_tp_sl_lifts_ai_sl_below_hard_floor():
    """AI 的 SL 距离 < 硬下限时，提升到硬下限（防爆仓，硬安全网）。"""
    from backend.services.position_memory_manager import PositionMemoryManager, MemoryInsight

    mgr = PositionMemoryManager()
    memory = MemoryInsight(
        symbol_win_rate=0.5, symbol_trade_count=10,
        best_leverage_for_symbol=10, recommended_size_adj=1.0,
    )
    price = 100.0
    # AI 给 SL=99（距离 1%，比硬下限窄）→ 提升到硬下限
    # swing 的 min_sl_pct=0.955 → 硬下限 SL 价 = 95.5（距离 4.5%）
    tp, sl = mgr._calc_tp_sl(
        side="buy", price=price, leverage=10, volatility_pct=0.02,
        raw_tp=105.0, raw_sl=99.0, memory=memory, tier="swing",
    )
    _hard_floor_sl = price * 0.955  # 95.5
    assert abs(sl - _hard_floor_sl) < 0.01, (
        f"AI SL=99(距离1%<硬下限4.5%)应提升到硬下限{_hard_floor_sl}, 实际={sl}"
    )


def test_calc_tp_sl_ai_sl_narrower_than_system_but_above_floor_still_used():
    """AI 的 SL 比系统基准窄，但 ≥ 硬下限 → 仍用 AI 的值（不再强制替换成系统值）。"""
    from backend.services.position_memory_manager import PositionMemoryManager, MemoryInsight

    mgr = PositionMemoryManager()
    memory = MemoryInsight(
        symbol_win_rate=0.5, symbol_trade_count=10,
        best_leverage_for_symbol=10, recommended_size_adj=1.0,
    )
    price = 100.0
    # 系统 SL 基准：swing sl_base = max(0.045, 0.02*3.0)=0.06 → 系统 SL=94
    # swing 硬下限 min_sl_pct=0.955 → 硬下限 SL=95.5
    # AI 给 SL=95.5（恰=硬下限）→ 应保留
    tp, sl = mgr._calc_tp_sl(
        side="buy", price=price, leverage=10, volatility_pct=0.02,
        raw_tp=110.0, raw_sl=95.5, memory=memory, tier="swing",
    )
    assert abs(sl - 95.5) < 0.01, f"AI SL=95.5(恰=硬下限)应保留, 实际={sl}"


# ────────────── 改动 5: 杠杆 AI 优先 ──────────────

def test_calc_leverage_uses_ai_value_within_hard_caps():
    """AI 给的杠杆在 [5, leverage_cap] 内时直接用，不再按波动率/置信度改写。"""
    from backend.services.position_memory_manager import PositionMemoryManager, MemoryInsight

    mgr = PositionMemoryManager()
    memory = MemoryInsight(
        symbol_win_rate=0.5, symbol_trade_count=50,
        best_leverage_for_symbol=5, recommended_size_adj=1.0,
    )
    # AI 给 12x，极端波动(10%)，极低置信度(10%)
    # 旧逻辑：vol>8%→cap10, conf<15%→cap8 → 最终 8x
    # 新逻辑：AI 主驾，leverage_cap=15(nature cap swing) → 12x
    lev = mgr._calc_leverage(
        raw_leverage=12, confidence=0.10, volatility_pct=0.10,
        leverage_cap=15, memory=memory,
    )
    assert lev == 12, f"AI给12x在[5,15]内应直接用, 实际={lev}"


def test_calc_leverage_hard_caps_still_enforced():
    """硬上下限 [5,20] 和 leverage_cap 仍然强制。"""
    from backend.services.position_memory_manager import PositionMemoryManager, MemoryInsight

    mgr = PositionMemoryManager()
    memory = MemoryInsight(
        symbol_win_rate=0.5, symbol_trade_count=10,
        best_leverage_for_symbol=10, recommended_size_adj=1.0,
    )
    # AI 给 50x → 硬上限 20
    lev = mgr._calc_leverage(
        raw_leverage=50, confidence=0.9, volatility_pct=0.01,
        leverage_cap=20, memory=memory,
    )
    assert lev == 20, f"AI给50x应被硬上限20夹住, 实际={lev}"

    # AI 给 2x → 硬下限 5
    lev2 = mgr._calc_leverage(
        raw_leverage=2, confidence=0.9, volatility_pct=0.01,
        leverage_cap=15, memory=memory,
    )
    assert lev2 == 5, f"AI给2x应被硬下限5抬到5, 实际={lev2}"

    # leverage_cap=0 (frozen) → 0
    lev3 = mgr._calc_leverage(
        raw_leverage=15, confidence=0.9, volatility_pct=0.01,
        leverage_cap=0, memory=memory,
    )
    assert lev3 == 0, f"frozen(leverage_cap=0)应返回0, 实际={lev3}"


def test_calc_leverage_no_memory_driven_cap():
    """记忆不再压杠杆（历史低杠杆不锁死当前）。"""
    from backend.services.position_memory_manager import PositionMemoryManager, MemoryInsight

    mgr = PositionMemoryManager()
    # 历史最佳杠杆只有 5x，30+ 笔样本 —— 旧逻辑会 cap 到 5+5=10
    memory = MemoryInsight(
        symbol_win_rate=0.5, symbol_trade_count=50,
        best_leverage_for_symbol=5, recommended_size_adj=1.0,
    )
    lev = mgr._calc_leverage(
        raw_leverage=15, confidence=0.8, volatility_pct=0.02,
        leverage_cap=20, memory=memory,
    )
    # AI 主驾：记忆不再改写，15x 在 [5,20] 内直接用
    assert lev == 15, f"记忆best=5不应压AI的15x, 实际={lev}"
