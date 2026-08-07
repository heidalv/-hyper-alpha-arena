"""
Dynamic Leverage Calculator — 市场自适应 + 本金感知杠杆计算。

替换固定 LEVERAGE_CAP_BY_TIER (short=20x/mid=12x/long=6x) 为统一的动态杠杆，
所有 tier 使用相同杠杆，根据实时市场状况在 [min, max] 区间自适应调整。

因子（市场因子权重和归一到 1.0；本金因子单独走 lev 乘数通道）:
  1. 波动率 (ATR ratio vs 中位数) — 权重 35%
  2. 资金费率 (绝对值)             — 权重 25%
  3. 市场状态 (trending/ranging/crash) — 权重 20%
  4. 账户回撤                        — 权重 20%
  5. 本金档位 (equity 绝对额)        — 杠杆乘数 mult ∈ [0.5, 1.5]
     小本金 → mult>1（放大杠杆复利）；大本金 → mult<1（压低杠杆保本）。
     公式: mult = clamp((equity_ref / equity)^0.4, 0.5, 1.5)，基准 equity_ref=5000U。

输出: [DYNAMIC_LEVERAGE_MIN, DYNAMIC_LEVERAGE_MAX]，默认 [5, 20]
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def calculate_dynamic_leverage(
    db: Session,
    symbol: str,
    account_id: int,
    *,
    current_price: Optional[float] = None,
) -> float:
    """计算当前市场条件下应该使用的统一杠杆。

    返回 float，范围为 [DYNAMIC_LEVERAGE_MIN, DYNAMIC_LEVERAGE_MAX]。
    如果任何数据源不可用，使用偏保守的默认值。
    """
    from backend.config.settings import (
        DYNAMIC_LEVERAGE_ENABLED,
        DYNAMIC_LEVERAGE_MIN,
        DYNAMIC_LEVERAGE_MAX,
        DYNAMIC_LEVERAGE_VOLATILITY_WEIGHT,
        DYNAMIC_LEVERAGE_FUNDING_WEIGHT,
        DYNAMIC_LEVERAGE_REGIME_WEIGHT,
        DYNAMIC_LEVERAGE_DRAWDOWN_WEIGHT,
        DYNAMIC_LEVERAGE_EQUITY_REF,
    )

    if not DYNAMIC_LEVERAGE_ENABLED:
        return DYNAMIC_LEVERAGE_MIN  # fallback to conservative min

    lev_min = max(1.0, float(DYNAMIC_LEVERAGE_MIN))
    lev_max = max(lev_min, float(DYNAMIC_LEVERAGE_MAX))

    risk_score = 0.0
    total_weight = 0.0

    # ── 因子 1: 波动率 (ATR ratio) ──
    vol_score, vol_weight = _calc_volatility_risk(symbol)
    risk_score += vol_score * vol_weight
    total_weight += vol_weight
    logger.debug(f"[DynLev] {symbol} volatility: score={vol_score:.2f} weight={vol_weight:.2f}")

    # ── 因子 2: 资金费率 ──
    try:
        from backend.services.exchange_config import get_exchange_for_account
        _fund_exchange = get_exchange_for_account(account_id)
    except Exception:
        _fund_exchange = None
    fund_score, fund_weight = _calc_funding_risk(symbol, _fund_exchange)
    risk_score += fund_score * fund_weight
    total_weight += fund_weight
    logger.debug(f"[DynLev] {symbol} funding: score={fund_score:.2f} weight={fund_weight:.2f}")

    # ── 因子 3: 市场状态 ──
    regime_score, regime_weight = _calc_regime_risk(symbol)
    risk_score += regime_score * regime_weight
    total_weight += regime_weight
    logger.debug(f"[DynLev] {symbol} regime: score={regime_score:.2f} weight={regime_weight:.2f}")

    # ── 因子 4: 账户回撤 ──
    dd_score, dd_weight = _calc_drawdown_risk(db, account_id)
    risk_score += dd_score * dd_weight
    total_weight += dd_weight
    logger.debug(f"[DynLev] acct={account_id} drawdown: score={dd_score:.2f} weight={dd_weight:.2f}")

    # Normalize risk score to [0, 1]
    if total_weight > 0:
        risk_score = risk_score / total_weight
    risk_score = max(0.0, min(1.0, risk_score))

    # Map to leverage: risk=0 → lev_max, risk=1 → lev_min
    leverage = lev_max - risk_score * (lev_max - lev_min)

    # ── 本金档位 boost：小本金→放大，大本金→缩小（双向） ──
    # 不走 risk_score 通道（单向只能压），改成对已映射 leverage 乘系数。
    # mult=1.0 时不变；>1.0 小本金激进；<1.0 大本金保本。
    eq_mult, eq_equity = _calc_equity_mult(db, account_id)
    leverage = leverage * eq_mult
    leverage = max(1, min(int(lev_max), int(round(leverage))))

    logger.info(
        f"[DynLev] {symbol} acct={account_id} eq=${eq_equity:.0f}(×{eq_mult:.2f}) "
        f"risk={risk_score:.2f} → leverage={leverage}x "
        f"(range=[{lev_min}, {lev_max}])"
    )
    return leverage


# ════════════════════════════════════════════════════════════════════
#  因子计算
# ════════════════════════════════════════════════════════════════════

def _calc_volatility_risk(symbol: str) -> tuple[float, float]:
    """波动率风险评分: 0=低波动(安全), 1=高波动(危险)。

    使用 vol_band 作为代理 —— 已由 risk_band_resolver 维护。
    """
    from backend.config.settings import DYNAMIC_LEVERAGE_VOLATILITY_WEIGHT
    try:
        from backend.services.risk_band_resolver import get_vol_band
        band = get_vol_band(symbol)
        # 映射 vol band → risk score
        band_score = {
            "low":    0.10,  # 低波动 → 接近 max (20x)
            "mid":    0.40,  # 中等   → ~14x
            "high":   0.75,  # 高波动 → ~8x
            "x-high": 0.95,  # 极高   → 接近 min (5x)
        }
        score = band_score.get(band, 0.50)
        return score, float(DYNAMIC_LEVERAGE_VOLATILITY_WEIGHT)
    except Exception as e:
        logger.debug(f"[DynLev] volatility lookup failed for {symbol}: {e}")
        return 0.50, 0.0  # neutral, zero weight


def _get_latest_funding_rate(symbol: str, exchange: str) -> Optional[float]:
    """????? perp_funding ??????????12h ????????

    perp_funding ???????????hyperliquid ? market_flow ???
    binance/bybit/okx/gateio/asterdex ? multi_venue_funding_collector ???
    ?????funding-matrix??????????????
    """
    import time as _time
    try:
        from sqlalchemy import text as _sa_text
        from backend.database.connection import MarketSessionLocal
        now_ms = int(_time.time() * 1000)
        cutoff = now_ms - 12 * 3600 * 1000
        with MarketSessionLocal() as mdb:
            row = mdb.execute(_sa_text(
                "SELECT funding_rate FROM perp_funding "
                "WHERE exchange=:ex AND symbol=:sym AND timestamp >= :cut "
                "ORDER BY timestamp DESC LIMIT 1"
            ), {"ex": exchange, "sym": str(symbol).upper(), "cut": cutoff}).first()
        if row:
            return float(row[0])
    except Exception as exc:
        logger.debug(f"[DynLev] perp_funding ???? {symbol}@{exchange}: {exc}")
    return None


def _calc_funding_risk(symbol: str, exchange: Optional[str] = None) -> tuple[float, float]:
    """???????????????? perp_funding???????? HL ?????"""
    from backend.config.settings import DYNAMIC_LEVERAGE_FUNDING_WEIGHT
    try:
        if not exchange:
            from backend.services.exchange_config import get_active_exchange
            exchange = get_active_exchange()
        ex = (exchange or "").strip().lower()
        if ex == "aster":
            ex = "asterdex"
        rate = _get_latest_funding_rate(symbol, ex)
        if rate is None:
            logger.debug(f"[DynLev] {symbol}@{ex} ???????????? 0")
            return 0.30, 0.0
        fund_rate = abs(float(rate))
        if fund_rate >= 0.0010:    # >=0.10% ??
            score = 0.95
        elif fund_rate >= 0.0005:  # >=0.05% ?
            score = 0.70
        elif fund_rate >= 0.0002:  # >=0.02% ??
            score = 0.35
        elif fund_rate >= 0.0001:  # >=0.01% ??
            score = 0.15
        else:
            score = 0.05
        return score, float(DYNAMIC_LEVERAGE_FUNDING_WEIGHT)
    except Exception as exc:
        logger.debug(f"[DynLev] funding lookup failed for {symbol}@{exchange}: {exc}")
    return 0.30, 0.0


def _calc_regime_risk(symbol: str) -> tuple[float, float]:
    """市场状态风险: crash > ranging > trending。"""
    from backend.config.settings import DYNAMIC_LEVERAGE_REGIME_WEIGHT
    try:
        from backend.services.market_regime_service import get_market_regime
        regime_type, _ = get_market_regime(symbol)
        regime_score = {
            "trending":     0.15,  # 趋势市 → 杠杆可以偏高
            "ranging":      0.55,  # 震荡市 → 中等杠杆
            "volatile":     0.75,  # 高波动 → 偏低
            "breakout":     0.50,  # 突破   → 中等
            "crash":        0.95,  # 崩溃   → 最低杠杆
            "accumulation": 0.35,  # 吸筹   → 偏高
            "distribution": 0.65,  # 派发   → 偏低
            "unknown":      0.50,
        }
        score = regime_score.get(regime_type, 0.50)
        return score, float(DYNAMIC_LEVERAGE_REGIME_WEIGHT)
    except Exception as e:
        logger.debug(f"[DynLev] regime lookup failed for {symbol}: {e}")
    return 0.50, 0.0  # neutral, zero weight


def _calc_drawdown_risk(db: Session, account_id: int) -> tuple[float, float]:
    """账户回撤风险: 回撤越大 → 杠杆越低。"""
    from backend.config.settings import DYNAMIC_LEVERAGE_DRAWDOWN_WEIGHT
    try:
        from backend.database.models import PaperBalance
        bal = db.query(PaperBalance).filter(
            PaperBalance.account_id == account_id
        ).first()
        if bal:
            equity = float(bal.total_equity or bal.available_balance or 0)
            initial = float(bal.initial_balance or equity or 1)
            if initial > 0:
                dd_pct = 1.0 - (equity / initial)
                dd_pct = max(0.0, min(1.0, dd_pct))
                # 映射 drawdown → risk score
                if dd_pct >= 0.30:
                    score = 0.95  # -30%+ → 强制最低杠杆
                elif dd_pct >= 0.20:
                    score = 0.75
                elif dd_pct >= 0.10:
                    score = 0.50
                elif dd_pct >= 0.05:
                    score = 0.25
                else:
                    score = 0.05  # 盈利中
                return score, float(DYNAMIC_LEVERAGE_DRAWDOWN_WEIGHT)
    except Exception as e:
        logger.debug(f"[DynLev] drawdown lookup failed for acct={account_id}: {e}")
    return 0.20, 0.0  # neutral, zero weight


def _calc_equity_mult(db: Session, account_id: int) -> tuple[float, float]:
    """本金档位杠杆乘数：小本金→>1（放大杠杆），大本金→<1（缩小杠杆保本）。

    双向连续映射（不走 risk_score 通道，因为那条单向只能压低）:
        mult = clamp((equity_ref / equity) ^ 0.4, 0.5, 1.5)

    基准 equity_ref=5000U（默认）:
        equity=500U    → mult ≈ 1.50（钳顶，激进复利）
        equity=1000U   → mult ≈ 1.32
        equity=2000U   → mult ≈ 1.32
        equity=5000U   → mult = 1.00（基准，中性）
        equity=10000U  → mult ≈ 0.76
        equity=20000U  → mult ≈ 0.57
        equity=50000U  → mult ≈ 0.50（钳底，保本）

    最终 leverage 仍受 DYNAMIC_LEVERAGE_MAX 硬钳制（默认 20）。
    数据源与 _calc_drawdown_risk 一致（PaperBalance.total_equity）。

    Returns:
        (mult, equity): mult 为乘数，equity 为查到的本金（用于日志，查不到时为 0）
    """
    from backend.config.settings import DYNAMIC_LEVERAGE_EQUITY_REF
    try:
        from backend.database.models import PaperBalance
        bal = db.query(PaperBalance).filter(
            PaperBalance.account_id == account_id
        ).first()
        if bal:
            equity = float(bal.total_equity or bal.available_balance or 0)
            if equity <= 0:
                # 无余额数据：中性（不放大也不缩小）
                return 1.0, 0.0
            equity_ref = max(1.0, float(DYNAMIC_LEVERAGE_EQUITY_REF))
            # 双向映射: 小本金 mult>1; 大本金 mult<1
            mult = (equity_ref / equity) ** 0.4
            mult = max(0.5, min(1.5, mult))
            return mult, equity
    except Exception as e:
        logger.debug(f"[DynLev] equity mult lookup failed for acct={account_id}: {e}")
    return 1.0, 0.0  # neutral on failure
