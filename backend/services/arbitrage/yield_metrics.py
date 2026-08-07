"""
套利机会收益指标 — 统一排序用的 score 计算

避免将一次性价差/基差错误年化，或将已是百分比的 spread 再次 ×365。
"""

from __future__ import annotations


def funding_annual_yield(abs_rate_24h_avg: float) -> float:
    """资金费率年化: |24h均值| × 3次/天 × 365"""
    return abs(abs_rate_24h_avg) * 3 * 365


def cross_exchange_score(spread_pct: float, z_score: float) -> float:
    """
    跨所价差排序分 — 基于预期收敛收益（非年化）

    spread_pct 单位为百分比 (0.5 = 0.5%)
    用 |z| 加权，Z 越大机会越强
    """
    abs_spread = abs(spread_pct) / 100.0  # 转为小数
    z_factor = min(abs(z_score) / 3.0, 1.0)
    return abs_spread * (0.5 + 0.5 * z_factor)


def basis_convergence_score(basis_pct: float) -> float:
    """
    基差收敛排序分 — 一次性收敛，不年化

    basis_pct 为小数 (0.003 = 0.3%)
    """
    return abs(basis_pct)


def normalize_score_for_sort(source: str, opp_data: dict) -> float:
    """按策略类型返回统一排序分数（越大越优先）"""
    if source == "funding_rate":
        return float(opp_data.get("expected_annual_yield", 0))
    if source == "cross_exchange":
        spread = opp_data.get("spread")
        if spread is not None:
            return cross_exchange_score(
                getattr(spread, "spread_pct", 0),
                getattr(spread, "z_score", 0),
            )
        return cross_exchange_score(
            opp_data.get("spread_pct", 0),
            opp_data.get("z_score", 0),
        )
    if source == "basis":
        return basis_convergence_score(opp_data.get("basis_pct", 0))
    return float(opp_data.get("expected_annual_yield", 0))
