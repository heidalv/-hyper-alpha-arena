"""多场所资金费率矩阵扫描器（Phase 1 数据现代化核心）。

2026-07-06 新增：
    把机会发现从"HL+Aster 两家、单腿裸方向"升级为"多场所资金费率矩阵 + 最优
    delta-neutral 多空腿组合"，对齐 2026 年主流 Orbit/ProFunding 聚合器范式。

核心思想（delta-neutral 资金费率套利 = 刷积分的最佳载体）：
    永续合约里"多头付资金费给空头"（funding_rate > 0 时）。因此：
      - 在资金费率**最低/最负**的场所开**多**（长腿）——付得最少甚至倒收；
      - 在资金费率**最高/最正**的场所开等额**空**（空腿）——收得最多；
    两腿方向相反、名义相等 → delta 中性、无方向风险，净赚两场所的**资金费价差**。
    若长腿落在有积分的 DEX 上，则额外白拿积分（bonus）。

本模块是**纯计算 + 数据装配**，不下单、不依赖网络：
    - scan_funding_matrix(): 传入 {exchange: {symbol: funding_rate}} 即产出矩阵与组合；
    - 费率/积分状态从 program_registry（离线权威源）读取；
    - 无实时资金费率输入时返回空结果（由 Phase 4 历史回放或实时快照喂数据）。

所有产出均为"扣成本后"口径：给出毛资金费 APR、手续费拖累、保本持有天数、
指定持有期的净 EV，宁可保守。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 各场所资金费结算周期（小时）。多数 CEX/DEX 为 8h，部分 DEX（HL/Aster/Backpack）为 1h。
DEFAULT_FUNDING_INTERVAL_HOURS: Dict[str, float] = {
    "binance": 8.0,
    "okx": 8.0,
    "bybit": 8.0,
    "gateio": 8.0,
    "hyperliquid": 1.0,
    "asterdex": 1.0,
    "backpack": 1.0,
    "paradex": 8.0,
    "lighter": 1.0,
    "pacifica": 1.0,
    "extended": 8.0,
}

DAYS_PER_YEAR = 365.0


@dataclass
class VenueFunding:
    """某场所某 symbol 的资金费率 + 费率 + 积分状态快照。"""

    exchange: str
    symbol: str
    funding_rate: float             # 每结算周期的资金费率（小数，正=多头付费）
    funding_interval_hours: float = 8.0
    maker_rate: float = 0.0002
    taker_rate: float = 0.0005
    points_active: bool = False     # 该场所是否有 active 积分项目
    program_id: Optional[str] = None
    program_status: str = "unknown"

    @property
    def funding_per_day(self) -> float:
        """折算到"每日"的资金费率（多头视角，正=每日付出）。"""
        if self.funding_interval_hours <= 0:
            return 0.0
        return self.funding_rate * (24.0 / self.funding_interval_hours)


@dataclass
class DeltaNeutralCombo:
    """某 symbol 的最优 delta-neutral 多空腿组合（扣成本口径）。"""

    symbol: str
    long_exchange: str              # 长腿（资金费最低/最负处开多）
    short_exchange: str             # 空腿（资金费最高/最正处开空）
    long_funding_per_day: float
    short_funding_per_day: float
    # 净资金费收益（每日，占单腿名义的比例）= 空腿收 - 长腿付
    net_funding_per_day: float
    gross_funding_apr: float        # 年化毛资金费价差
    # 手续费拖累：两腿各一次开+平（round-trip），占名义比例（一次性）
    fee_drag: float
    breakeven_days: Optional[float]  # 保本持有天数（净资金费>0 时才有意义）
    net_apr_at_horizon: float        # 指定持有期(horizon_days)的净年化（已摊销手续费）
    horizon_days: float
    points_long_leg: bool           # 长腿是否落在 active 积分 DEX（bonus）
    points_program_id: Optional[str] = None
    notes: str = ""

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "long_exchange": self.long_exchange,
            "short_exchange": self.short_exchange,
            "long_funding_per_day": round(self.long_funding_per_day, 6),
            "short_funding_per_day": round(self.short_funding_per_day, 6),
            "net_funding_per_day": round(self.net_funding_per_day, 6),
            "gross_funding_apr": round(self.gross_funding_apr, 4),
            "fee_drag": round(self.fee_drag, 6),
            "breakeven_days": round(self.breakeven_days, 2) if self.breakeven_days else None,
            "net_apr_at_horizon": round(self.net_apr_at_horizon, 4),
            "horizon_days": self.horizon_days,
            "points_long_leg": self.points_long_leg,
            "points_program_id": self.points_program_id,
            "notes": self.notes,
        }


def _venue_meta(exchange: str) -> Dict:
    """从 program_registry 取某场所的离线费率 + 积分状态。"""
    try:
        from backend.services.rebate_arb import program_registry as pr

        fees = pr.get_offline_incentive(exchange)
        active = [p for p in pr.all_programs() if p.exchange == exchange and p.is_active()]
        prog = active[0] if active else None
        return {
            "maker_rate": fees.get("maker_rate", 0.0002),
            "taker_rate": fees.get("taker_rate", 0.0005),
            "points_active": prog is not None,
            "program_id": prog.program_id if prog else None,
            "program_status": prog.status if prog else "unknown",
        }
    except Exception:
        return {
            "maker_rate": 0.0002,
            "taker_rate": 0.0005,
            "points_active": False,
            "program_id": None,
            "program_status": "unknown",
        }


def build_venue_funding(
    funding_rates: Dict[str, Dict[str, float]],
    interval_hours: Optional[Dict[str, float]] = None,
) -> Dict[str, List[VenueFunding]]:
    """把 {exchange: {symbol: funding_rate}} 装配为 {symbol: [VenueFunding,...]}。"""
    interval_hours = interval_hours or DEFAULT_FUNDING_INTERVAL_HOURS
    by_symbol: Dict[str, List[VenueFunding]] = {}
    for exchange, sym_rates in (funding_rates or {}).items():
        meta = _venue_meta(exchange)
        interval = interval_hours.get(exchange, DEFAULT_FUNDING_INTERVAL_HOURS.get(exchange, 8.0))
        for symbol, rate in (sym_rates or {}).items():
            if rate is None:
                continue
            vf = VenueFunding(
                exchange=exchange,
                symbol=symbol,
                funding_rate=float(rate),
                funding_interval_hours=interval,
                maker_rate=meta["maker_rate"],
                taker_rate=meta["taker_rate"],
                points_active=meta["points_active"],
                program_id=meta["program_id"],
                program_status=meta["program_status"],
            )
            by_symbol.setdefault(symbol, []).append(vf)
    return by_symbol


def _best_combo_for_symbol(
    venues: List[VenueFunding],
    horizon_days: float,
    use_taker: bool,
    prefer_points_long: bool,
) -> Optional[DeltaNeutralCombo]:
    """给定某 symbol 在各场所的资金费，找最优 delta-neutral 多空腿组合。"""
    if len(venues) < 2:
        return None

    # 长腿：资金费/日最低（付得最少或倒收）；空腿：资金费/日最高（收得最多）。
    by_day_sorted = sorted(venues, key=lambda v: v.funding_per_day)
    long_leg = by_day_sorted[0]
    short_leg = by_day_sorted[-1]

    # 若倾向让长腿落在积分场所：在"资金费不过分劣于最优"的前提下优先选 active 积分场所。
    if prefer_points_long:
        points_longs = [v for v in venues if v.points_active and v is not short_leg]
        if points_longs:
            cand = min(points_longs, key=lambda v: v.funding_per_day)
            # 仅当额外资金费成本可接受（比最优长腿每日多付 < 0.05%）才切换
            if cand.funding_per_day - long_leg.funding_per_day <= 0.0005:
                long_leg = cand

    if long_leg.exchange == short_leg.exchange:
        return None

    net_funding_per_day = short_leg.funding_per_day - long_leg.funding_per_day
    gross_apr = net_funding_per_day * DAYS_PER_YEAR

    # 手续费拖累：每腿开+平各一次 round-trip = 2 × fee_rate；两腿相加。
    long_fee = long_leg.taker_rate if use_taker else long_leg.maker_rate
    short_fee = short_leg.taker_rate if use_taker else short_leg.maker_rate
    fee_drag = 2.0 * long_fee + 2.0 * short_fee

    breakeven_days = (fee_drag / net_funding_per_day) if net_funding_per_day > 1e-9 else None

    # 指定持有期净年化：净资金费累计 - 一次性手续费，再年化。
    horizon = max(horizon_days, 1e-6)
    net_pnl_frac = net_funding_per_day * horizon - fee_drag
    net_apr_at_horizon = net_pnl_frac * (DAYS_PER_YEAR / horizon)

    points_long = bool(long_leg.points_active)
    notes = ""
    if points_long:
        notes = f"长腿 {long_leg.exchange} 有 active 积分项目({long_leg.program_id})，可叠加积分 bonus"
    elif short_leg.points_active:
        notes = f"空腿 {short_leg.exchange} 有积分但空单积分权重通常低于多单"

    return DeltaNeutralCombo(
        symbol=long_leg.symbol,
        long_exchange=long_leg.exchange,
        short_exchange=short_leg.exchange,
        long_funding_per_day=long_leg.funding_per_day,
        short_funding_per_day=short_leg.funding_per_day,
        net_funding_per_day=net_funding_per_day,
        gross_funding_apr=gross_apr,
        fee_drag=fee_drag,
        breakeven_days=breakeven_days,
        net_apr_at_horizon=net_apr_at_horizon,
        horizon_days=horizon_days,
        points_long_leg=points_long,
        points_program_id=long_leg.program_id if points_long else None,
        notes=notes,
    )


def scan_funding_matrix(
    funding_rates: Dict[str, Dict[str, float]],
    *,
    interval_hours: Optional[Dict[str, float]] = None,
    horizon_days: float = 7.0,
    use_taker: bool = True,
    prefer_points_long: bool = True,
    min_net_apr: float = 0.0,
) -> List[DeltaNeutralCombo]:
    """扫描多场所资金费率矩阵，产出每个 symbol 的最优 delta-neutral 组合。

    Args:
        funding_rates: {exchange: {symbol: funding_rate(每结算周期小数)}}
        interval_hours: {exchange: 结算周期小时数}，缺省用内置表
        horizon_days: 假设持有天数（用于摊销一次性手续费算净 APR）
        use_taker: True=用 taker 费保守估计成本；False=乐观用 maker
        prefer_points_long: True=在成本可接受时让长腿优先落在 active 积分场所
        min_net_apr: 只返回净年化 >= 此阈值的组合

    Returns:
        按"指定持有期净年化"降序排列的组合列表（可为空）。
    """
    by_symbol = build_venue_funding(funding_rates, interval_hours)
    combos: List[DeltaNeutralCombo] = []
    for symbol, venues in by_symbol.items():
        try:
            combo = _best_combo_for_symbol(
                venues, horizon_days, use_taker, prefer_points_long
            )
            if combo is not None and combo.net_apr_at_horizon >= min_net_apr:
                combos.append(combo)
        except Exception as exc:
            logger.debug("[FundingMatrix] %s 组合计算失败: %s", symbol, exc)
    combos.sort(key=lambda c: c.net_apr_at_horizon, reverse=True)
    return combos
