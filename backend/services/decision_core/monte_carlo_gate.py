"""Monte Carlo 轻量预检 — 开仓前估算 tail loss，超阈值缩仓（Cripton 简化版）。"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_PATHS = 1000
DEFAULT_HORIZON_BARS = 48  # ~2d on 1h bars
TAIL_LOSS_PCT_THRESHOLD = 0.12  # 12% 权益 tail → 缩仓


@dataclass
class MonteCarloResult:
    tail_loss_pct: float
    median_return_pct: float
    size_multiplier: float
    detail: str = ""


def _vol_from_market(market_data: dict) -> float:
    if not isinstance(market_data, dict):
        return 0.015
    vol = float(market_data.get("volatility_pct") or market_data.get("volatility_value") or 0)
    if vol > 1.0:
        vol /= 100.0
    if vol <= 0:
        atr_pct = float(market_data.get("atr_pct") or 0)
        if atr_pct > 0:
            vol = atr_pct / 100.0 if atr_pct > 1 else atr_pct
    return max(0.005, min(0.08, vol or 0.015))


def estimate_tail_risk(
    *,
    market_data: Optional[dict],
    sl_pct: float,
    side: str = "buy",
    paths: int = DEFAULT_PATHS,
    horizon: int = DEFAULT_HORIZON_BARS,
) -> MonteCarloResult:
    """GBM 随机路径估算 worst-path loss（相对 SL 距离）。"""
    sl = max(0.01, float(sl_pct or 0.04))
    vol = _vol_from_market(market_data or {})
    dt = 1.0 / 24.0  # 1h bar as day fraction
    mu = 0.0
    is_long = (side or "buy").lower() in ("buy", "long")

    worst = 0.0
    returns: list[float] = []
    n = max(100, min(5000, int(paths or DEFAULT_PATHS)))
    h = max(8, min(200, int(horizon or DEFAULT_HORIZON_BARS)))

    for _ in range(n):
        price = 1.0
        path_min = 0.0
        path_max = 0.0
        for _ in range(h):
            z = random.gauss(0, 1)
            price *= math.exp((mu - 0.5 * vol * vol) * dt + vol * math.sqrt(dt) * z)
            path_min = min(path_min, price - 1.0)
            path_max = max(path_max, price - 1.0)
        ret = path_max if is_long else -path_min
        returns.append(ret)
        adverse = -path_min if is_long else path_max
        worst = max(worst, adverse)

    returns.sort()
    median = returns[len(returns) // 2] if returns else 0.0
    tail_loss = worst  # max adverse move in paths

    sm = 1.0
    if tail_loss > sl * 2.5:
        sm = 0.25
    elif tail_loss > sl * 1.8:
        sm = 0.5
    elif tail_loss > sl * 1.3:
        sm = 0.75

    if tail_loss > TAIL_LOSS_PCT_THRESHOLD:
        sm = min(sm, 0.5)

    return MonteCarloResult(
        tail_loss_pct=round(tail_loss, 4),
        median_return_pct=round(median, 4),
        size_multiplier=sm,
        detail=f"paths={n} vol={vol:.3f} tail={tail_loss:.2%} sl={sl:.2%}",
    )
