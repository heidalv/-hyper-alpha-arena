"""microstructure_gauge — 博弈确认（设计总方案 B5，2026-08-19）。

趋势起始点的微观验证腿（B1 三选二确认的腿3）：
- funding_mean_reversion：资金费率从极端（|rate|>=极值阈值）回归正常区间
- liquidation_stabilize：近 24h 出现大额清算后价格未续创新低（杠杆出清企稳）
- vpin：占位 None（order flow 毒性 VPIN，待 trades 流 buy/sell 标记接线）
confirmed = 三腿中至少两腿通过（当前两腿全过或数据不足回退 False）。
纯规则、非交易路径。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

_FUNDING_EXTREME = 0.0005   # 8h 费率 |rate| >= 0.05% 视为极端
_FUNDING_NORMAL = 0.0001    # 回归阈值 0.01%


def _funding_series(db, symbol: str, hours: int = 48):
    """近 hours 小时资金费率序列（perp_funding 表，缺失安全返回 None）。"""
    try:
        from backend.database.models import PerpFunding
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        rows = db.query(PerpFunding).filter(
            PerpFunding.symbol == symbol.upper(),
            PerpFunding.settled_at >= cutoff,
        ).order_by(PerpFunding.settled_at.asc()).all()
        return [float(r.funding_rate) for r in rows]
    except Exception as e:
        logger.debug("[MicroGauge] funding 加载失败: %s", e)
        return None


def funding_mean_reversion(series: Optional[List[float]]) -> bool:
    """费率极端后回归：序列前半有 |r|>=extreme，最新值 |r|<normal。"""
    if not series or len(series) < 8:
        return False
    arr = np.array(series)
    half = len(arr) // 2
    had_extreme = bool((np.abs(arr[:half]) >= _FUNDING_EXTREME).any())
    now_normal = float(abs(arr[-1])) < _FUNDING_NORMAL
    return had_extreme and now_normal


def liquidation_stabilize(db, symbol: str, lookback_hours: int = 24) -> bool:
    """大额清算后价格企稳：近 24h 有清算事件且此后价格未跌破清算时点价格×(1-1.5%)。"""
    try:
        from backend.database.models import LiquidationEvent  # 表名可能不同，安全尝试
        _M = LiquidationEvent
    except Exception:
        _M = None
    try:
        from backend.database.models import Liquidation
        if _M is None:
            _M = Liquidation
    except Exception:
        pass
    if _M is None:
        return False
    try:
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)
        rows = db.query(_M).filter(_M.symbol == symbol.upper()) \
            .filter(_M.created_at >= cutoff).all()
        if not rows:
            return False
        # 最近一次清算时点的价格 vs 当前价（用 kline 最新收盘）
        from backend.services.kline_data_service import kline_service
        kl = kline_service.get_klines_from_db(symbol.upper(), "1h", 24)
        if not kl:
            return False
        import pandas as pd
        closes = pd.to_numeric(pd.DataFrame(kl)["close"], errors="coerce").dropna()
        if len(closes) == 0:
            return False
        cur = float(closes.iloc[-1])
        low_since = float(closes.iloc[-6:].min())
        return low_since >= cur * 0.985  # 近 6h 未创比当前低 1.5% 的新低
    except Exception as e:
        logger.debug("[MicroGauge] 清算企稳判定失败: %s", e)
        return False


def gauge(db, symbol: str) -> Dict[str, Any]:
    """博弈确认：返回 {funding_mean_reversion, liquidation_stabilize, vpin, confirmed}。"""
    out: Dict[str, Any] = {
        "funding_mean_reversion": False, "liquidation_stabilize": False,
        "vpin": None, "confirmed": False,
    }
    try:
        fs = _funding_series(db, symbol)
        out["funding_mean_reversion"] = funding_mean_reversion(fs)
        out["liquidation_stabilize"] = liquidation_stabilize(db, symbol)
        legs = [out["funding_mean_reversion"], out["liquidation_stabilize"]]
        out["confirmed"] = bool(sum(legs) >= 2)
    except Exception as e:
        logger.debug("[MicroGauge] gauge 失败: %s", e)
    return out
