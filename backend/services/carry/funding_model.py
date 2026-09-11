# -*- coding: utf-8 -*-
"""[F63] 资金费 carry 经济模型（纯函数，可单测、无 IO）。

设计依据：《复合策略与交易系统全面改造设计_V2》§1.3 L2 ——
「现货-永续真 carry」需要两条腿：现货多头 + 永续空头（或反向）。价格风险对冲后，
残差收益 = **资金费收入 − 一次进出的成本 − 基差错**。

关键经济事实（本模型要回答的）：
  资金费按 8h 结算，单期通常只有 0.1–3bp；而一次建仓+平仓要付 taker 往返 8bp
  （Aster 4bp×2）。因此 carry **不是**「每期赚一点」，而是
  「**持有足够多个周期**，用累计资金费覆盖一次性成本」——盈亏平衡持有期
  = 往返成本 / 单期资金费。这个数字决定了策略是否可行，也是本模型的核心输出。

口径：
  - `funding_bp`：单期（8h）资金费，正=收（做空永续且费率为正时收取）；
  - `round_trip_cost_bp`：一次进出成本（两条腿都要付，默认按永续 taker 往返 8bp）；
  - `basis_drift_bp`：持有期内基差（永续−现货）的净漂移，作为风险项扣除。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

PERIODS_PER_DAY = 3.0          # 默认 8h 结算 → 每天 3 期
PERIODS_PER_YEAR = 365.0 * PERIODS_PER_DAY

# 各场地资金费**结算周期**（小时）。口径必须分开，否则年化会差 8 倍：
#   - Aster / Binance / Bybit / OKX / Gate：8h（每 3 期/天）
#   - Hyperliquid：1h（24 期/天）
# 实测依据：`perp_funding` 是每 ~5 分钟采样的**预测费率**（asterdex/binance/bybit
# 约 110 条/天、hyperliquid 约 2500 条/天），采样密度不等于结算频率。
PERIOD_HOURS_BY_VENUE = {
    "asterdex": 8.0,
    "binance": 8.0,
    "bybit": 8.0,
    "okx": 8.0,
    "gateio": 8.0,
    "hyperliquid": 1.0,
}
DEFAULT_PERIOD_HOURS = 8.0


def period_hours(venue: str) -> float:
    return float(PERIOD_HOURS_BY_VENUE.get(str(venue or "").lower(), DEFAULT_PERIOD_HOURS))


def periods_per_day(venue: str) -> float:
    return 24.0 / period_hours(venue)


def periods_per_year(venue: str) -> float:
    return 365.0 * periods_per_day(venue)

# 默认成本：永续腿 taker 往返 8bp（Aster 4bp×2）。现货腿若无现货通道，
# 用现货 taker 近似（主流所 10bp 往返）——两项都要算进去才算「真成本」。
DEFAULT_PERP_ROUND_TRIP_BP = 8.0
DEFAULT_SPOT_ROUND_TRIP_BP = 10.0


def round_trip_cost_bp(
    *,
    perp_taker_bp: float = 4.0,
    spot_taker_bp: float = 5.0,
    perp_legs: int = 2,
    spot_legs: int = 2,
) -> float:
    """两条腿各进各出一次的总成本（bp，相对单边名义）。"""
    return max(0.0, float(perp_taker_bp)) * max(0, int(perp_legs)) + \
        max(0.0, float(spot_taker_bp)) * max(0, int(spot_legs))


def breakeven_periods(avg_funding_bp: float, cost_bp: float) -> Optional[float]:
    """覆盖一次进出成本所需的资金费期数。

    返回 None 表示**永远不划算**（单期资金费 ≤ 0）。
    """
    f = float(avg_funding_bp or 0.0)
    if f <= 0:
        return None
    return max(0.0, float(cost_bp)) / f


def net_carry_bp(
    *,
    avg_funding_bp: float,
    periods: float,
    cost_bp: float,
    basis_drift_bp: float = 0.0,
) -> float:
    """持有 `periods` 个资金费周期后的净收益（bp）。"""
    return (float(avg_funding_bp) * float(periods)
            - float(cost_bp)
            - float(basis_drift_bp))


def annualized_pct(funding_bp_per_period: float, periods_per_year: float = PERIODS_PER_YEAR) -> float:
    """把单期资金费折成简单年化（%）。不做复利——carry 的仓位是固定的。"""
    return float(funding_bp_per_period) * float(periods_per_year) / 100.0


def carry_metrics(
    *,
    avg_funding_bp: float,
    cost_bp: float,
    hold_days: float,
    basis_drift_bp: float = 0.0,
    venue: str = "",
    periods_per_day_override: Optional[float] = None,
) -> Dict[str, Any]:
    """给定持有天数，算出净收益与年化（周期口径按场地区分）。"""
    ppd = (float(periods_per_day_override) if periods_per_day_override
           else periods_per_day(venue))
    periods = float(hold_days) * ppd
    net = net_carry_bp(avg_funding_bp=avg_funding_bp, periods=periods,
                       cost_bp=cost_bp, basis_drift_bp=basis_drift_bp)
    be = breakeven_periods(avg_funding_bp, cost_bp)
    return {
        "hold_days": float(hold_days),
        "periods": round(periods, 2),
        "periods_per_day": ppd,
        "net_bp": round(net, 4),
        "net_usd_per_1k": round(net / 1e4 * 1000.0, 4),
        "breakeven_periods": None if be is None else round(be, 2),
        "breakeven_days": None if be is None else round(be / ppd, 2),
        "annualized_pct": round(annualized_pct(avg_funding_bp, 365.0 * ppd), 4),
        "profitable": net > 0,
    }


def decide_carry(
    *,
    avg_funding_bp: float,
    cost_bp: float,
    planned_hold_days: float,
    min_net_bp: float = 0.0,
    min_hold_days: float = 0.0,
    basis_drift_bp: float = 0.0,
    venue: str = "",
) -> Dict[str, Any]:
    """机会裁决：净收益 > 阈值且持有期足够长才可执行。

    **fail-closed**：缺 `avg_funding_bp`（None/0）或持有期不足以覆盖成本 → 不可执行。
    """
    if avg_funding_bp is None:
        return {"executable": False, "reason": "缺资金费数据（未验证）", "net_bp": 0.0}
    m = carry_metrics(avg_funding_bp=avg_funding_bp, cost_bp=cost_bp,
                      hold_days=planned_hold_days, basis_drift_bp=basis_drift_bp,
                      venue=venue)
    if float(avg_funding_bp) <= 0:
        return {**m, "executable": False,
                "reason": f"当期资金费 {avg_funding_bp:+.3f}bp ≤ 0（收不到钱）"}
    if planned_hold_days < min_hold_days:
        return {**m, "executable": False,
                "reason": f"计划持有 {planned_hold_days:.1f} 天 < 下限 {min_hold_days:.1f} 天"}
    be_days = m["breakeven_days"]
    if be_days is not None and planned_hold_days < be_days:
        return {**m, "executable": False,
                "reason": f"需持有 ≥{be_days:.1f} 天才能覆盖 {cost_bp:.1f}bp 成本"}
    if m["net_bp"] <= min_net_bp:
        return {**m, "executable": False,
                "reason": f"净收益 {m['net_bp']:.3f}bp ≤ 阈值 {min_net_bp:.3f}bp"}
    return {**m, "executable": True, "reason": ""}


def funding_stats(rates_bp: List[float]) -> Dict[str, Any]:
    """一段资金费序列的统计量（用于机会表与回测）。"""
    xs = [float(x) for x in rates_bp if x is not None]
    if not xs:
        return {"n": 0, "mean_bp": 0.0, "median_bp": 0.0, "positive_ratio": 0.0,
                "std_bp": 0.0, "t": 0.0}
    n = len(xs)
    mean = sum(xs) / n
    srt = sorted(xs)
    median = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2.0
    var = sum((x - mean) ** 2 for x in xs) / (n - 1) if n > 1 else 0.0
    std = var ** 0.5
    t = mean / (std / (n ** 0.5)) if std > 0 and n > 1 else 0.0
    return {
        "n": n, "mean_bp": round(mean, 5), "median_bp": round(median, 5),
        "std_bp": round(std, 5), "t": round(t, 3),
        "positive_ratio": round(sum(1 for x in xs if x > 0) / n, 4),
        "min_bp": round(min(xs), 5), "max_bp": round(max(xs), 5),
    }
