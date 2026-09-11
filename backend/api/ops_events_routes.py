# -*- coding: utf-8 -*-
"""事件总线 / 事件数据新鲜度 运维接口（/api/ops/market-events*、/api/ops/data-freshness，v3 p0-event-data）。

  GET /api/ops/market-events                 查询事件：types=a,b  symbol=BTC  hours=24  min_severity=3  limit=200
  GET /api/ops/market-events/counts          近 N 小时每类事件数量
  GET /api/ops/market-events/risk-windows    当前仍在生效的事件避险窗口（RiskEngine 消费的同一读取器）
  GET /api/ops/market-events/status          各采集器最近一轮摘要 + 流状态 + 每源最新事件
  GET /api/ops/data-freshness                四类新数据（funding 全币池 / 逐笔清算 / 持仓结构 / 公告）落库新鲜度
  GET /api/ops/announcements                 最近公告（exchange / ann_type 过滤）
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ops", tags=["ops-events"])


def _split(csv: Optional[str]) -> Optional[List[str]]:
    if not csv:
        return None
    parts = [p.strip() for p in csv.split(",") if p.strip()]
    return parts or None


@router.get("/market-events")
def market_events(types: Optional[str] = None, symbol: Optional[str] = None, hours: float = Query(24.0, ge=0.01, le=24 * 365),
                  min_severity: Optional[int] = Query(None, ge=1, le=5), limit: int = Query(200, ge=1, le=5000)) -> Dict[str, Any]:
    from backend.services.events import market_events_store as mes
    rows = mes.recent(hours=hours, event_types=_split(types), symbol=symbol, min_severity=min_severity, limit=limit)
    return {"count": len(rows), "hours": hours, "events": rows, "known_types": list(mes.ALL_EVENT_TYPES)}


@router.get("/market-events/counts")
def market_events_counts(hours: float = Query(24.0, ge=0.01, le=24 * 365)) -> Dict[str, Any]:
    from backend.services.events import market_events_store as mes
    return {"hours": hours, "counts": mes.counts(hours=hours), "by_source": mes.latest_by_source()}


@router.get("/market-events/risk-windows")
def market_events_risk_windows(symbol: Optional[str] = None, lookback_hours: float = 24.0,
                               min_severity: int = Query(3, ge=1, le=5)) -> Dict[str, Any]:
    from backend.services.events import market_events_store as mes
    rows = mes.active_risk_windows(symbol, lookback_hours=lookback_hours, min_severity=min_severity)
    return {"symbol": symbol, "active": len(rows), "windows": rows}


@router.get("/market-events/status")
def market_events_status() -> Dict[str, Any]:
    from backend.services.events import market_events_store as mes
    from backend.services.events.collectors import collectors_status
    try:
        status = collectors_status()
    except Exception as exc:
        logger.warning("[ops/events] collectors_status 失败: %s", exc)
        status = {"error": str(exc)[:200]}
    return {"collectors": status, "counts_24h": mes.counts(24.0), "by_source": mes.latest_by_source()}


@router.get("/announcements")
def announcements(exchange: Optional[str] = None, ann_type: Optional[str] = None, hours: float = 24 * 7,
                  limit: int = Query(100, ge=1, le=1000)) -> Dict[str, Any]:
    from sqlalchemy import text
    from backend.database.connection import MarketSessionLocal
    from backend.services.events.exchange_announcements import ensure_schema
    ensure_schema()
    since = int((time.time() - hours * 3600) * 1000)
    conds = ["published_at_ms >= :since"]
    params: Dict[str, Any] = {"since": since, "limit": limit}
    if exchange:
        conds.append("exchange = :ex")
        params["ex"] = exchange.lower()
    if ann_type:
        conds.append("ann_type = :t")
        params["t"] = ann_type
    db = MarketSessionLocal()
    try:
        res = db.execute(text(
            "SELECT id, exchange, ann_type, title, url, symbols, published_at_ms, effective_at_ms, severity, direction, created_at "
            f"FROM exchange_announcements WHERE {' AND '.join(conds)} ORDER BY published_at_ms DESC LIMIT :limit"
        ), params)
        cols = list(res.keys())
        rows = [dict(zip(cols, r)) for r in res.fetchall()]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)[:200]) from exc
    finally:
        db.close()
    for r in rows:
        if r.get("created_at") is not None:
            r["created_at"] = r["created_at"].isoformat()
    return {"count": len(rows), "announcements": rows}


@router.get("/data-freshness")
def data_freshness() -> Dict[str, Any]:
    """Phase 0 验收口径：四类新数据持续入库 ≥ 7 天 —— 这里给每类的最新时间、覆盖币数、近 24h 行数。"""
    from sqlalchemy import text
    from backend.database.connection import MarketSessionLocal
    from backend.services.events import market_events_store as mes
    from backend.services.events.exchange_announcements import ensure_schema as _ann_schema
    from backend.services.events.liquidation_stream import ensure_schema as _liq_schema
    from backend.services.events.position_structure import ensure_schema as _pos_schema
    from backend.services.events.collectors import collectors_status
    _ann_schema(); _liq_schema(); _pos_schema()

    def stream_status() -> Dict[str, Any]:
        # 流跑在采集进程（默认 dc）：经状态文件读取，而不是本进程的空壳状态
        cs = collectors_status()
        st = dict(cs.get("liquidation_stream") or {})
        st["status_source"] = cs.get("status_source")
        st["status_age_sec"] = cs.get("status_age_sec")
        return st

    now_ms = int(time.time() * 1000)
    day_ago = now_ms - 86400_000
    out: Dict[str, Any] = {"as_of_ms": now_ms}
    db = MarketSessionLocal()
    try:
        def _one(sql: str, **params: Any):
            try:
                return db.execute(text(sql), params).fetchone()
            except Exception as exc:
                db.rollback()
                return ("ERR", str(exc)[:120])

        def _all(sql: str, **params: Any):
            try:
                return db.execute(text(sql), params).fetchall()
            except Exception as exc:
                db.rollback()
                return [("ERR", str(exc)[:120])]

        # 1) funding 全币池：每场所最新时刻 + 近 1h 覆盖币数 + 近 24h 行数
        rows = _all(
            "SELECT exchange, MAX(timestamp), COUNT(DISTINCT symbol) FILTER (WHERE timestamp >= :h1), "
            "COUNT(*) FILTER (WHERE timestamp >= :d1) FROM perp_funding WHERE timestamp >= :d1 GROUP BY exchange ORDER BY 1",
            h1=now_ms - 3600_000, d1=day_ago,
        )
        out["perp_funding"] = [
            {"exchange": r[0], "latest_ts_ms": int(r[1]), "age_sec": round((now_ms - int(r[1])) / 1000, 1),
             "symbols_1h": int(r[2]), "rows_24h": int(r[3])} if r[0] != "ERR" else {"error": r[1]}
            for r in rows
        ]
        settled = _one(
            "SELECT COUNT(DISTINCT symbol), MIN(timestamp), MAX(timestamp), COUNT(*) FROM perp_funding "
            "WHERE exchange = 'binance' AND (timestamp / 1000) % 28800 < 120"
        )
        if settled and settled[0] != "ERR":
            out["perp_funding_settled_binance"] = {
                "symbols": int(settled[0] or 0), "oldest_ts_ms": (int(settled[1]) if settled[1] else None),
                "latest_ts_ms": (int(settled[2]) if settled[2] else None), "rows": int(settled[3] or 0),
            }
        # 2) 逐笔清算
        liq = _one("SELECT MAX(ts_ms), COUNT(*) FILTER (WHERE ts_ms >= :d1), COUNT(DISTINCT symbol) FILTER (WHERE ts_ms >= :d1), "
                   "COALESCE(SUM(notional_usd) FILTER (WHERE ts_ms >= :d1), 0) FROM liquidation_ticks", d1=day_ago)
        out["liquidation_ticks"] = (
            {"latest_ts_ms": (int(liq[0]) if liq[0] else None),
             "age_sec": (round((now_ms - int(liq[0])) / 1000, 1) if liq[0] else None),
             "rows_24h": int(liq[1] or 0), "symbols_24h": int(liq[2] or 0), "notional_24h_usd": float(liq[3] or 0),
             "stream": stream_status()} if liq and liq[0] != "ERR" else {"error": liq[1] if liq else "?"}
        )
        liq_ex = _all("SELECT exchange, MAX(ts_ms), COUNT(*) FILTER (WHERE ts_ms >= :d1), "
                      "COALESCE(SUM(notional_usd) FILTER (WHERE ts_ms >= :d1), 0) FROM liquidation_ticks GROUP BY exchange ORDER BY 1",
                      d1=day_ago)
        out["liquidation_ticks_by_exchange"] = [
            {"exchange": r[0], "latest_ts_ms": int(r[1]), "age_sec": round((now_ms - int(r[1])) / 1000, 1),
             "rows_24h": int(r[2] or 0), "notional_24h_usd": float(r[3] or 0)} if r[0] != "ERR" else {"error": r[1]}
            for r in liq_ex
        ]
        liq_h = _all("SELECT source, MAX(ts_ms), COUNT(*) FILTER (WHERE ts_ms >= :d1) FROM liquidation_events GROUP BY source", d1=day_ago)
        out["liquidation_events_hourly"] = [
            {"source": r[0], "latest_ts_ms": int(r[1]), "rows_24h": int(r[2])} if r[0] != "ERR" else {"error": r[1]} for r in liq_h
        ]
        # 3) 持仓结构
        pos = _one("SELECT MAX(ts_ms), COUNT(DISTINCT symbol) FILTER (WHERE ts_ms >= :d1), COUNT(*) FILTER (WHERE ts_ms >= :d1), "
                   "MIN(ts_ms) FROM position_structure", d1=day_ago)
        out["position_structure"] = (
            {"latest_ts_ms": (int(pos[0]) if pos[0] else None),
             "age_sec": (round((now_ms - int(pos[0])) / 1000, 1) if pos[0] else None),
             "symbols_24h": int(pos[1] or 0), "rows_24h": int(pos[2] or 0), "oldest_ts_ms": (int(pos[3]) if pos[3] else None)}
            if pos and pos[0] != "ERR" else {"error": pos[1] if pos else "?"}
        )
        # 4) 公告
        ann = _all("SELECT exchange, MAX(published_at_ms), COUNT(*) FILTER (WHERE created_at >= now() - interval '7 days'), COUNT(*) "
                   "FROM exchange_announcements GROUP BY exchange ORDER BY 1")
        out["exchange_announcements"] = [
            {"exchange": r[0], "latest_published_ms": int(r[1]), "new_7d": int(r[2]), "total": int(r[3])} if r[0] != "ERR" else {"error": r[1]}
            for r in ann
        ]
    finally:
        db.close()
    out["market_events_by_source"] = mes.latest_by_source()
    out["market_events_counts_24h"] = mes.counts(24.0)
    return out
