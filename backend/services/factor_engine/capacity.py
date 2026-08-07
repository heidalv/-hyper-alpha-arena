"""
alpha 容量模型（P1.6，方案 §P1.6 / 对标 WorldQuant capacity）。

目标：给每个因子/策略输出 capacity_usd —— Sharpe 不退化阈值下的最大 AUM。
crypto 容量 = 盘口深度 × ADV × 滑点曲线。

为什么一等公民（方案诊断）：
    WorldQuant 把 capacity 作为部署硬门。短线因子在回测里好看，但实际下单
    超过盘口深度就自我冲击，Sharpe 崩。capacity 是"这个信号能吃多少钱"的答案。

模型（简化但可校准）：
    - 线性市场冲击：impact(q) = lambda * sqrt(q / ADV)，lambda 由历史 L2 估。
    - 给定目标 Sharpe 衰减容忍 delta_sr，求最大单笔下单 q*：
        交易成本 c(q) ≈ lambda * sqrt(q/ADV) * q  (round-trip)
        当 c(q)/预期收益 = delta_sr / sqrt(N) 时 q 即容量边界。
    - capacity_usd ≈ q* * price * 可同时持仓品种数。

接入（方案 P1.6）：
    FactorLifecycle ORTHO→PAPER 加 capacity > min_capacity_usd 硬门（已在 P1.3 实现）。
    PortfolioConstruction 仓位 ≤ capacity。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CapacityModelConfig:
    """容量模型参数。"""
    # 线性冲击系数 lambda（每百万美元交易量造成的 bps 价格冲击）。
    # crypto 主流币 ~5-20 bps per 1% ADV；可由历史 L2 校准。
    lambda_bps: float = 10.0
    # Sharpe 衰减容忍（允许多少 Sharpe 被交易成本吃掉）
    sharpe_decay_tolerance: float = 0.3
    # 回合交易次数（持仓期内预期换手）
    round_trips: int = 1
    # 同时持仓品种数（分散降低单品种冲击）
    num_positions: int = 10
    # 安全系数（保守，避免高估容量）
    safety_factor: float = 0.5


def estimate_lambda_from_l2(
    l2_depths: np.ndarray,
    adv_usd: np.ndarray,
) -> float:
    """
    从历史 L2 盘口估算冲击系数 lambda。

    参数：
        l2_depths: 每个样本时刻 top-N 档累计深度（USD）。
        adv_usd: 对应时刻的日均成交额（USD）。
    返回：
        lambda_bps —— 每 1% ADV 交易量的 bps 冲击。
    """
    l2 = np.asarray(l2_depths, dtype=float)
    adv = np.asarray(adv_usd, dtype=float)
    valid = np.isfinite(l2) & np.isfinite(adv) & (adv > 0) & (l2 > 0)
    if valid.sum() < 5:
        return CapacityModelConfig().lambda_bps
    # 深度越深（相对 ADV），冲击越小。lambda ≈ k / (depth/ADV)
    ratio = l2[valid] / adv[valid]
    # 1% ADV 的冲击 ≈ lambda_bps；用倒数关系近似
    # depth 撑住 1% ADV 时冲击约 1 bps，线性外推
    typical_ratio = float(np.median(ratio))
    if typical_ratio < 1e-6:
        return CapacityModelConfig().lambda_bps * 10  # 极薄盘
    # lambda 与 depth/ADV 反比；校准点：ratio=0.01 → ~5bps
    lambda_bps = 5.0 * (0.01 / typical_ratio)
    return float(np.clip(lambda_bps, 1.0, 200.0))


def compute_capacity(
    adv_usd: float,
    expected_return_per_trade: float,
    base_sharpe: float,
    trades_per_year: int = 252,
    config: CapacityModelConfig | None = None,
    lambda_bps: float | None = None,
) -> float:
    """
    计算单品种容量（USD）。

    参数：
        adv_usd: 该品种日均成交额。
        expected_return_per_trade: 单笔预期收益率（小数，如 0.002 = 20bps）。
        base_sharpe: 无成本 Sharpe（回测）。
        trades_per_year: 年化交易次数。
        config: 模型参数。
        lambda_bps: 冲击系数（None 用 config 默认或外部校准值）。
    返回：
        capacity_usd —— Sharpe 衰减不超过容忍的最大持仓 USD。
    """
    cfg = config or CapacityModelConfig()
    lam = lambda_bps if lambda_bps is not None else cfg.lambda_bps

    # 市场冲击（bps）= lambda * sqrt(q / ADV)，q = 单笔下单 USD
    # round-trip 成本（bps）= 2 * lambda * sqrt(q / ADV)
    # 转 USD 成本 = round_trip_bps/1e4 * q
    # 当成本吃掉的 Sharpe = sharpe_decay_tolerance * base_sharpe 时达容量边界
    #
    # Sharpe 衰减 ≈ 成本 / (预期收益 * sqrt(N))
    # 求解 q* 使：round_trip_cost / (expected_return * trades) ≈ decay_tol * base_sharpe / sqrt(trades)
    if base_sharpe <= 0 or expected_return_per_trade <= 0 or adv_usd <= 0:
        return 0.0

    # 简化求解：q* 满足
    #   lam * sqrt(q/ADV) / 1e4 ≈ decay_tol * expected_return
    # => sqrt(q/ADV) ≈ decay_tol * expected_return * 1e4 / lam
    # => q ≈ ADV * (decay_tol * expected_return * 1e4 / lam)^2
    sqrt_ratio = cfg.sharpe_decay_tolerance * expected_return_per_trade * 1e4 / lam
    sqrt_ratio = max(0.0, min(sqrt_ratio, 1.0))  # 不能超过 ADV 的 100%
    q_star = adv_usd * sqrt_ratio ** 2

    # 容量 = 单品种 q* × 安全系数 × 回合数倒数
    capacity = q_star * cfg.safety_factor / max(1, cfg.round_trips)
    return float(capacity)


def portfolio_capacity(
    single_capacities: list[float],
    config: CapacityModelConfig | None = None,
) -> float:
    """
    组合容量（多品种分散）。

    简化：组合容量 ≈ sum(单品种容量) （完全独立假设，保守可乘相关调整系数）。
    """
    cfg = config or CapacityModelConfig()
    total = sum(max(0.0, c) for c in single_capacities)
    return float(total * cfg.safety_factor)
