"""
执行算法（P3.2，方案 §P3.2 / §2.2.7）。

目标：替代"信号→直接下单"，拆分为子单降低自我冲击。
- TWAP：等时间切片
- POV：实时成交量固定比例参与
- FundingIS：funding-aware Implementation Shortfall（持 perp 有显性 funding 成本）
- SOR：跨 CEX+DEX 最优路由（接口预留）

crypto 特化：FundingIS cost 模型含预测 funding × 预期持仓时间。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ChildOrder:
    """子单（算法拆分产出）。"""
    qty: float
    delay_ms: float          # 相对父单的延迟
    algo_hint: str = "MARKET"  # MARKET / LIMIT
    limit_price: float | None = None


@dataclass
class AlgoConfig:
    """算法配置。"""
    # TWAP
    twap_slices: int = 10
    twap_interval_ms: float = 1000.0
    # POV
    pov_participation: float = 0.05  # 5% 实时成交量
    pov_max_duration_ms: float = 60000.0
    # FundingIS
    funding_threshold_bps: float = 5.0   # funding 高于此倾向提前完成
    expected_hold_ms: float = 3600000.0  # 预期持仓 1h


def twap(parent_qty: float, config: AlgoConfig | None = None) -> list[ChildOrder]:
    """
    TWAP：等量等时间切片。

    返回子单列表（qty + delay）。
    """
    cfg = config or AlgoConfig()
    n = max(1, cfg.twap_slices)
    per_slice = parent_qty / n
    return [
        ChildOrder(qty=per_slice, delay_ms=i * cfg.twap_interval_ms)
        for i in range(n)
    ]


def pov(parent_qty: float, volume_forecast_fn: Callable[[float], float],
        config: AlgoConfig | None = None) -> list[ChildOrder]:
    """
    POV：按实时成交量固定比例参与。

    volume_forecast_fn(elapsed_ms) → 累计成交量预测。
    子单大小 = POV × 该时刻窗口成交量。
    """
    cfg = config or AlgoConfig()
    if parent_qty <= 0:
        return []
    children: list[ChildOrder] = []
    filled = 0.0
    elapsed = 0.0
    step_ms = 1000.0
    while filled < parent_qty - 1e-9 and elapsed < cfg.pov_max_duration_ms:
        vol_in_step = max(0.0, volume_forecast_fn(elapsed + step_ms)
                          - volume_forecast_fn(elapsed))
        child_qty = min(vol_in_step * cfg.pov_participation,
                        parent_qty - filled)
        if child_qty > 0:
            children.append(ChildOrder(qty=child_qty, delay_ms=elapsed))
            filled += child_qty
        elapsed += step_ms
    if filled < parent_qty - 1e-9:
        # 超时未完成，剩余市价
        children.append(ChildOrder(qty=parent_qty - filled, delay_ms=elapsed))
    return children


def funding_is(
    parent_qty: float,
    funding_rate_8h: float,
    config: AlgoConfig | None = None,
) -> tuple[list[ChildOrder], float]:
    """
    Funding-aware Implementation Shortfall。

    持 perp 有显性 funding 成本。funding 高时倾向提前完成（少切片），
    funding 低时倾向分散（多切片，减冲击）。

    返回 (子单列表, 预期 funding 成本 USD 估算)。
    """
    cfg = config or AlgoConfig()
    funding_bps = abs(funding_rate_8h) * 1e4
    # funding 越高，切片越少（尽快完成省 funding）
    if funding_bps > cfg.funding_threshold_bps:
        n_slices = max(1, cfg.twap_slices // 3)
    else:
        n_slices = cfg.twap_slices
    per_slice = parent_qty / n_slices
    interval = cfg.twap_interval_ms * (0.5 if funding_bps > cfg.funding_threshold_bps else 1.0)
    children = [
        ChildOrder(qty=per_slice, delay_ms=i * interval)
        for i in range(n_slices)
    ]
    # 预期 funding 成本（持仓时间内的 funding 支付）
    hold_fraction = cfg.expected_hold_ms / (8 * 3600 * 1000)  # 占 8h 比例
    funding_cost = abs(funding_rate_8h) * hold_fraction
    return children, funding_cost


def sor_route(parent_qty: float, venue_quotes: dict[str, tuple[float, float]],
              config: AlgoConfig | None = None) -> dict[str, float]:
    """
    Smart Order Routing：跨 venue 最优路由。

    venue_quotes: {venue: (price, available_size)}
    返回 {venue: routed_qty}，按价格最优贪心分配。
    """
    remaining = parent_qty
    routing: dict[str, float] = {}
    # 按价格升序（买方视角）贪心
    sorted_venues = sorted(venue_quotes.items(), key=lambda x: x[1][0])
    for venue, (price, avail) in sorted_venues:
        if remaining <= 0:
            break
        take = min(avail, remaining)
        if take > 0:
            routing[venue] = take
            remaining -= take
    return routing
