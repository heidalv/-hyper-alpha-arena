"""诚实积分估值（FDV 折现）与扣成本净 EV（Phase 1）。

2026-07-06 新增：
    此前积分估值靠拍脑袋（如 S3 `points_value = 0.005 * hype * 0.5`），既不透明也易高估。
    本模块把积分价值拆成可解释、可配、可保守的链路：

        单积分价值 = (项目预期 FDV × 空投占比 × 我的份额) / 总积分 × 折现率

    再结合刷积分产生的**手续费/资金费成本**，算出**扣成本后的净 EV**。核心原则：宁可低估。

设计要点：
    - 所有输入都有保守默认；缺数据时给"不可估"而非乐观数字。
    - discount_rate（折现率）默认重折现（0.15）：未 TGE 的积分兑现极不确定，
      要打很深的折扣，避免"纸面富贵"。
    - 输出同时给"乐观/基准/保守"三档，让人看到区间而非单点。
    - 与 funding_rate_matrix 的净资金费 APR 叠加，得到"刷分总净 EV"。

不依赖网络：FDV/占比等参数由 program_registry 或调用方显式传入；无参数即返回 unknown。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# 折现率默认档（越低越保守）。未 TGE / 兑换无承诺的项目应取保守档。
DISCOUNT_CONSERVATIVE = 0.15
DISCOUNT_BASE = 0.35
DISCOUNT_OPTIMISTIC = 0.60


@dataclass
class PointsValuationInput:
    """单个项目积分估值输入（缺失即用保守默认或标记不可估）。"""

    program_id: str
    # 项目预期完全稀释估值（USD）。None = 未知 → 不可估。
    expected_fdv_usd: Optional[float] = None
    # 空投分配给积分持有者的供应占比（0~1）。典型 0.05~0.15。
    airdrop_supply_pct: float = 0.10
    # 全网预计总积分（用于摊薄单积分价值）。None = 未知 → 不可估。
    total_points_estimate: Optional[float] = None
    # 我方在观察期内预计能累积的积分数。
    my_points_estimate: float = 0.0
    # 折现率（覆盖默认）。
    discount_rate: Optional[float] = None


@dataclass
class PointsValuation:
    """积分估值结果（三档 + 净 EV 口径）。"""

    program_id: str
    estimable: bool
    per_point_value_usd: float = 0.0        # 基准档单积分价值
    my_points_value_conservative: float = 0.0
    my_points_value_base: float = 0.0
    my_points_value_optimistic: float = 0.0
    discount_rate: float = DISCOUNT_CONSERVATIVE
    notes: str = ""

    def to_dict(self) -> Dict:
        return {
            "program_id": self.program_id,
            "estimable": self.estimable,
            "per_point_value_usd": round(self.per_point_value_usd, 8),
            "my_points_value_conservative": round(self.my_points_value_conservative, 4),
            "my_points_value_base": round(self.my_points_value_base, 4),
            "my_points_value_optimistic": round(self.my_points_value_optimistic, 4),
            "discount_rate": self.discount_rate,
            "notes": self.notes,
        }


def value_points(inp: PointsValuationInput) -> PointsValuation:
    """按 FDV 折现法诚实估值某项目积分。数据不足时返回 estimable=False。"""
    if (
        inp.expected_fdv_usd is None
        or inp.total_points_estimate is None
        or inp.total_points_estimate <= 0
        or inp.expected_fdv_usd <= 0
    ):
        return PointsValuation(
            program_id=inp.program_id,
            estimable=False,
            notes="缺少 FDV 或总积分估计 → 不可估（拒绝乐观拍脑袋）",
        )

    airdrop_pct = max(0.0, min(1.0, inp.airdrop_supply_pct))
    airdrop_pool_usd = inp.expected_fdv_usd * airdrop_pct
    raw_per_point = airdrop_pool_usd / inp.total_points_estimate

    base_discount = (
        inp.discount_rate if inp.discount_rate is not None else DISCOUNT_BASE
    )
    per_point_base = raw_per_point * base_discount

    my_pts = max(0.0, inp.my_points_estimate)
    return PointsValuation(
        program_id=inp.program_id,
        estimable=True,
        per_point_value_usd=per_point_base,
        my_points_value_conservative=my_pts * raw_per_point * DISCOUNT_CONSERVATIVE,
        my_points_value_base=my_pts * per_point_base,
        my_points_value_optimistic=my_pts * raw_per_point * DISCOUNT_OPTIMISTIC,
        discount_rate=base_discount,
        notes=(
            f"FDV={inp.expected_fdv_usd:,.0f} × 空投占比{airdrop_pct:.0%} / 总积分"
            f"{inp.total_points_estimate:,.0f} = 原始单价{raw_per_point:.6g}，"
            f"基准折现{base_discount:.0%}"
        ),
    )


def value_points_for_program(
    program_id: str,
    *,
    notional_usd: float,
    horizon_days: float,
) -> PointsValuation:
    """结合 program_registry 的估值参数 + 名义×天数累积速率，做诚实积分估值。

    project 未填齐 FDV/总积分/累积速率时 → estimable=False（积分价值按 0 计），
    这是有意的诚实降级：拿不到可靠数字就不臆造积分收益。
    """
    try:
        from backend.services.rebate_arb.program_registry import (
            estimate_my_points,
            get_points_valuation_params,
        )
    except Exception as exc:
        logger.debug("[PointsValuation] 无法加载 program_registry: %s", exc)
        return PointsValuation(program_id=program_id, estimable=False, notes="注册表不可用")

    params = get_points_valuation_params(program_id)
    my_points = estimate_my_points(program_id, notional_usd, horizon_days)
    if params is None or my_points is None:
        return PointsValuation(
            program_id=program_id,
            estimable=False,
            notes="项目未填齐 FDV/总积分/累积速率 → 不可估（积分价值按 0，宁可低估）",
        )
    return value_points(
        PointsValuationInput(
            program_id=program_id,
            expected_fdv_usd=params["expected_fdv_usd"],
            airdrop_supply_pct=params["airdrop_supply_pct"],
            total_points_estimate=params["total_points_estimate"],
            my_points_estimate=my_points,
        )
    )


@dataclass
class NetEVResult:
    """刷分组合的扣成本净 EV（资金费净收益 + 积分价值 - 成本）。"""

    gross_funding_pnl_usd: float        # 持有期资金费净收益（可正可负）
    points_value_usd: float             # 折现后积分价值（保守/基准，由调用方选档）
    fee_cost_usd: float                 # 手续费成本
    net_ev_usd: float                   # 净 EV = 资金费净收益 + 积分价值 - 手续费
    notional_usd: float
    horizon_days: float
    net_ev_apr: float                   # 净 EV 年化（占名义比例）
    breakdown: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "gross_funding_pnl_usd": round(self.gross_funding_pnl_usd, 4),
            "points_value_usd": round(self.points_value_usd, 4),
            "fee_cost_usd": round(self.fee_cost_usd, 4),
            "net_ev_usd": round(self.net_ev_usd, 4),
            "notional_usd": round(self.notional_usd, 2),
            "horizon_days": self.horizon_days,
            "net_ev_apr": round(self.net_ev_apr, 4),
            "breakdown": {k: round(v, 6) for k, v in self.breakdown.items()},
        }


def net_ev(
    *,
    notional_usd: float,
    net_funding_per_day: float,
    fee_drag: float,
    horizon_days: float,
    points_value_usd: float = 0.0,
) -> NetEVResult:
    """把资金费净收益、积分价值、手续费成本合成"扣成本净 EV"。

    Args:
        notional_usd: 单腿名义（两腿等额，成本/收益均按此名义计）
        net_funding_per_day: 每日净资金费率（占名义比例，来自 funding_rate_matrix）
        fee_drag: 一次性手续费拖累（占名义比例，两腿开+平）
        horizon_days: 持有天数
        points_value_usd: 折现后积分价值（USD，调用方决定用哪一档）
    """
    notional = max(notional_usd, 0.0)
    horizon = max(horizon_days, 1e-6)

    gross_funding_pnl = net_funding_per_day * horizon * notional
    fee_cost = fee_drag * notional
    net = gross_funding_pnl + points_value_usd - fee_cost
    net_apr = (net / notional) * (365.0 / horizon) if notional > 0 else 0.0

    return NetEVResult(
        gross_funding_pnl_usd=gross_funding_pnl,
        points_value_usd=points_value_usd,
        fee_cost_usd=fee_cost,
        net_ev_usd=net,
        notional_usd=notional,
        horizon_days=horizon_days,
        net_ev_apr=net_apr,
        breakdown={
            "net_funding_per_day": net_funding_per_day,
            "fee_drag": fee_drag,
        },
    )
