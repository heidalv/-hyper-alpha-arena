"""sizing_overlay — 仓位官折扣层（U5 纯计算层，2026-08-25）。

统一计划 V2 §7：在 L6（fractional Kelly f=0.25 / vol targeting / 回撤调控）之上叠加
认知折扣。本模块为**纯函数计算层**（无 IO、可单测）；热路径接线（multi_symbol_kelly /
midlong tranche / scalp size）待下一批谨慎接入，开关 SIZING_OVERLAY_ENABLED 控制。

公式：final = kelly_share × 共识折扣 × 信用分因子 × regime适配 × 相关性惩罚 × 波动率缩放
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def _f(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def committee_hint_to_mult(budget_hint: str) -> float:
    """委员会决策卡 budget_hint → 仓位乘子（影子期只记录；接线后生效）。"""
    return {
        "increase": 1.2, "keep": 1.0, "reduce": 0.5, "pause": 0.0,
    }.get(str(budget_hint or "keep").strip().lower(), 1.0)


def consensus_discount(consensus_confidence: Optional[float]) -> float:
    """共识度折扣：分歧越大下注越小（共识<0.5 减半，[0,1] 线性）。"""
    c = _f(consensus_confidence, 0.5)
    if c >= 0.5:
        return 1.0
    return 0.5 + c  # c=0.3 → 0.8；c=0 → 0.5


def credit_factor(credit: Optional[float]) -> float:
    """决策者历史校准 → [0.7, 1.0]（信用分 0=shadow → 0.7 下限只作参考，实际由仲裁层拒单）。"""
    c = _f(credit, 1.0)
    return max(0.7, min(1.0, c))


def regime_mult(regime: str, vol_ratio: Optional[float] = None) -> float:
    """regime 适配：高波动/转换中收缩总风险预算。"""
    r = str(regime or "ranging").lower()
    base = {
        "trending_up": 1.0, "trending_down": 0.7, "ranging": 0.8,
        "high_vol": 0.5, "transition": 0.5,
    }.get(r, 0.8)
    vr = _f(vol_ratio, 1.0)
    if vr > 1.5:
        base *= 0.7
    return round(base, 4)


def correlation_penalty(n_same_direction: int, cap: int = 3) -> float:
    """同方向持仓集中 → 整体降杠杆（每超一个降 0.15，地板 0.5）。"""
    n = max(0, int(n_same_direction or 0))
    return max(0.5, 1.0 - 0.15 * max(0, n - cap))


def vol_target_scale(realized_vol_pct: Optional[float], target_vol_pct: float = 30.0) -> float:
    """波动率目标缩放：已实现波动 > 目标 → 缩仓（线性，地板 0.4，上限 1.2）。"""
    rv = _f(realized_vol_pct, 0.0)
    if rv <= 0 or target_vol_pct <= 0:
        return 1.0
    return round(max(0.4, min(1.2, target_vol_pct / rv)), 4)


def compute_final_multiplier(
    *,
    kelly_share: float,
    consensus_confidence: Optional[float] = None,
    credit: Optional[float] = None,
    regime: str = "ranging",
    vol_ratio: Optional[float] = None,
    n_same_direction: int = 0,
    realized_vol_pct: Optional[float] = None,
    target_vol_pct: float = 30.0,
    budget_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """V2 §7 组合：返回 {final, breakdown}。所有乘子独立计算、可审计。"""
    k = _f(kelly_share, 0.0)
    d1 = consensus_discount(consensus_confidence)
    d2 = credit_factor(credit)
    d3 = regime_mult(regime, vol_ratio)
    d4 = correlation_penalty(n_same_direction)
    d5 = vol_target_scale(realized_vol_pct, target_vol_pct)
    d6 = committee_hint_to_mult(budget_hint or "keep")
    final = k * d1 * d2 * d3 * d4 * d5 * d6
    return {
        "final": round(final, 6),
        "breakdown": {
            "kelly_share": round(k, 6),
            "consensus_discount": d1,
            "credit_factor": d2,
            "regime_mult": d3,
            "correlation_penalty": d4,
            "vol_target_scale": d5,
            "committee_hint_mult": d6,
        },
    }
