"""trend_cycle_archive — 结构化趋势记忆的归档与读取（设计 V2 §4.5）。

替代长线路径的 RAG 文本检索：每轮趋势归档为结构化 TrendCycle 记录，
下一轮趋势开仓时读同币种×同 L1 结构的历史 R 分布做仓位校准/退出耐心参考。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def archive_trend_cycle(
    db: Session,
    *,
    account_id: int,
    symbol: str,
    direction: str,
    start_ts: datetime,
    end_ts: Optional[datetime],
    l1_score_at_entry: Optional[int],
    entry_timing_score: Optional[float],
    batches: Optional[List[Dict[str, Any]]],
    total_r: Optional[float],
    peak_r: Optional[float],
    exit_reason: str,
    hold_days: Optional[float],
) -> Optional[int]:
    """归档一轮趋势。幂等性由调用方（平仓 hook）保证单次调用。"""
    try:
        from backend.database.models import TrendCycle

        rec = TrendCycle(
            account_id=int(account_id),
            symbol=str(symbol or "").upper(),
            direction=str(direction or "long").lower(),
            start_ts=start_ts,
            end_ts=end_ts,
            l1_score_at_entry=l1_score_at_entry,
            entry_timing_score=entry_timing_score,
            batches=batches,
            total_r=total_r,
            peak_r=peak_r,
            exit_reason=str(exit_reason or "")[:100],
            hold_days=hold_days,
        )
        db.add(rec)
        db.flush()
        return int(rec.id)
    except Exception as e:
        logger.warning("[TrendCycle] 归档失败 %s: %s", symbol, e)
        return None


def get_similar_cycles(
    db: Session,
    *,
    symbol: str,
    direction: str = "long",
    l1_score_min: int = 3,
    limit: int = 30,
) -> List[Dict[str, Any]]:
    """读同币种×同方向的近期趋势归档（供仓位校准/退出耐心参考）。

    返回按 start_ts 倒序的轻量 dict 列表：total_r / peak_r / exit_reason / hold_days。
    """
    try:
        from backend.database.models import TrendCycle

        rows = (
            db.query(TrendCycle)
            .filter(
                TrendCycle.symbol == str(symbol or "").upper(),
                TrendCycle.direction == str(direction or "long").lower(),
                TrendCycle.l1_score_at_entry >= int(l1_score_min),
            )
            .order_by(TrendCycle.start_ts.desc())
            .limit(int(limit))
            .all()
        )
        out = []
        for r in rows:
            out.append({
                "symbol": r.symbol,
                "start_ts": r.start_ts.isoformat() if r.start_ts else None,
                "total_r": r.total_r,
                "peak_r": r.peak_r,
                "exit_reason": r.exit_reason,
                "hold_days": r.hold_days,
                "batches_n": len(r.batches) if isinstance(r.batches, list) else 0,
            })
        return out
    except Exception as e:
        logger.warning("[TrendCycle] 读取历史失败 %s: %s", symbol, e)
        return []


def r_distribution(cycles: List[Dict[str, Any]]) -> Dict[str, float]:
    """从历史 cycle 列表计算 R 分布摘要（均值/胜率/中位持有天数）。"""
    rs = [c.get("total_r") for c in cycles if c.get("total_r") is not None]
    holds = [c.get("hold_days") for c in cycles if c.get("hold_days") is not None]
    if not rs:
        return {"n": 0, "mean_r": 0.0, "win_rate": 0.0, "median_hold_d": 0.0}
    rs_sorted = sorted(rs)
    n = len(rs_sorted)
    import statistics
    return {
        "n": n,
        "mean_r": round(statistics.fmean(rs_sorted), 3),
        "win_rate": round(sum(1 for x in rs_sorted if x > 0) / n, 3),
        "median_hold_d": round(statistics.median(holds), 1) if holds else 0.0,
    }
