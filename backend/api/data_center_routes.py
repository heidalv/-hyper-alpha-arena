"""全市场数据中心总览 API（2026-08-16 新增）。

一次返回「真实数据」的完整体检：
- 全市场（分所 × 周期）：现有 bar 数 / 币数 / 最早-最新时间 / 新鲜度；
- 核心币深度 vs 回填目标：缺失多少天/根；
- 采集器运行状态：P0/P1/P1-Watch/回填 心跳（kline_sync_heartbeat 真实落库）；
- 回填配置与模式（分时段批式：预算/休息）；
- 入库及时性：各周期最新 bar 距今秒数（是否「采集即入库」的直接证明）。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

# [perf 2026-08-18] 直连 :9100 的 opener 复用（见 _dc_components）。
_DC_OPENER: Any = None

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ops", tags=["ops"])

_PERIOD_SECONDS: Dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800, "1M": 2592000,
}
_PERIOD_ORDER = ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"]

# 核心观察币 = 短线扫描宇宙（与 KLINE_FRESHNESS_SYMBOLS 同源）+ 常规核心
_DEFAULT_UNIVERSE = [
    "BTC", "ETH", "SOL", "BNB", "ASTER", "XPL", "VIRTUAL",
    "AAVE", "AEON", "APT", "AR", "BTW", "HOME", "HUMA", "ZEC",
    "JTO", "UNI", "XRP",
]

# 大聚合结果缓存：crypto_klines 全表 GROUP BY 实测 5min+（表极大），
# 改为后台线程单飞计算 + 600s TTL。缓存未就绪时页面显示「统计预热中」，
# 其余（核心币深度/心跳/回填/DC）实时返回。
_AGG_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None, "running": False, "error": None}
_AGG_LOCK = threading.Lock()
# 全表扫描每次 ~5min，太频繁会拖慢整库 IO → 每小时刷新一次（监控页口径够用）
_AGG_TTL = 3600.0


def _fresh_stale_limit_sec(period: str) -> float:
    """与 data_center.is_fresh 同口径：stale ≤ period*2 + 60s 视为新鲜。"""
    return float(_PERIOD_SECONDS.get(period, 3600)) * 2.0 + 60.0


def _compute_aggregates() -> None:
    """后台线程：跑全表 GROUP BY（可能 5min+），结果写缓存。"""
    rows: List[Dict[str, Any]] = []
    err: Optional[str] = None
    try:
        from sqlalchemy import text as _sa_text

        from backend.database.connection import MarketSessionLocal
        with MarketSessionLocal() as db:
            db.execute(_sa_text("SET LOCAL statement_timeout = '600000'"))
            rows = db.execute(_sa_text(
                """
                SELECT exchange, period,
                       COUNT(*) AS bars,
                       COUNT(DISTINCT symbol) AS symbols,
                       MIN(timestamp) AS oldest_ts,
                       MAX(timestamp) AS newest_ts
                FROM crypto_klines
                GROUP BY exchange, period
                """
            )).mappings().all()
            rows = [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        err = str(exc)[:200]
        logger.warning("[DataCenterOverview] 全表聚合失败: %s", exc)
    with _AGG_LOCK:
        _AGG_CACHE["data"] = rows
        _AGG_CACHE["error"] = err
        _AGG_CACHE["ts"] = time.time()
        _AGG_CACHE["running"] = False


def _ensure_aggregates() -> None:
    """无缓存或过期时触发后台单飞刷新（不阻塞请求）。"""
    with _AGG_LOCK:
        if _AGG_CACHE["running"]:
            return
        if _AGG_CACHE["data"] is not None and time.time() - _AGG_CACHE["ts"] < _AGG_TTL:
            return
        _AGG_CACHE["running"] = True
    threading.Thread(target=_compute_aggregates, name="dc-overview-agg", daemon=True).start()


def _load_aggregates() -> List[Dict[str, Any]]:
    """取缓存聚合（可能为空=预热中）。"""
    with _AGG_LOCK:
        return list(_AGG_CACHE["data"] or [])


def _universe_freshness(exchange: str, symbols: List[str]) -> Dict[str, Any]:
    """核心币 × 周期：bar 数/最新时间/新鲜度（索引限定，快）。"""
    out: Dict[str, Any] = {"exchange": exchange, "symbols": []}
    try:
        from sqlalchemy import text as _sa_text

        from backend.database.connection import MarketSessionLocal
        with MarketSessionLocal() as db:
            rows = db.execute(_sa_text(
                """
                SELECT symbol, period, COUNT(*) AS bars, MAX(timestamp) AS newest_ts
                FROM crypto_klines
                WHERE exchange = :ex AND symbol = ANY(:syms)
                GROUP BY symbol, period
                """
            ), {"ex": exchange, "syms": list(symbols)}).mappings().all()
        by_sym: Dict[str, Dict[str, dict]] = {}
        for r in rows:
            by_sym.setdefault(str(r["symbol"]).upper(), {})[str(r["period"])] = {
                "bars": int(r["bars"]), "newest_ts": int(r["newest_ts"]),
            }
        now = int(time.time())
        for sym in symbols:
            periods = {}
            for p in _PERIOD_ORDER:
                cell = by_sym.get(sym, {}).get(p)
                if not cell:
                    periods[p] = {"bars": 0, "newest_ts": None, "stale_sec": None, "fresh": False}
                    continue
                stale = max(0, now - cell["newest_ts"])
                periods[p] = {
                    "bars": cell["bars"],
                    "newest_ts": cell["newest_ts"],
                    "stale_sec": stale,
                    "fresh": float(stale) <= _fresh_stale_limit_sec(p),
                }
            out["symbols"].append({"symbol": sym, "periods": periods})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[DataCenterOverview] universe freshness 失败: %s", exc)
    return out


def _heartbeats() -> List[Dict[str, Any]]:
    try:
        from sqlalchemy import text as _sa_text

        from backend.database.connection import MarketSessionLocal
        with MarketSessionLocal() as db:
            rows = db.execute(_sa_text(
                """
                SELECT exchange, period, pool, symbols_ok, symbols_fail,
                       meta_json, updated_at
                FROM kline_sync_heartbeat
                ORDER BY updated_at DESC
                LIMIT 200
                """
            )).mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[DataCenterOverview] heartbeat 读取失败: %s", exc)
        return []


def _dc_components() -> Dict[str, Any]:
    """数据中心进程组件状态（尽力而为，2s 超时）。"""
    import json as _json
    import urllib.request as _ur

    try:
        # 绕过进程内 HTTP(S)_PROXY（.env 注入的 127.0.0.1:1080），localhost 直连
        # [perf 2026-08-18] opener 复用，避免每次请求重载 Windows 证书库。
        global _DC_OPENER
        if _DC_OPENER is None:
            _DC_OPENER = _ur.build_opener(_ur.ProxyHandler({}))
        with _DC_OPENER.open("http://127.0.0.1:9100/health", timeout=2) as resp:
            data = _json.loads(resp.read().decode("utf-8", "replace"))
            return {
                "ok": bool(data.get("ok")),
                "uptime_sec": data.get("uptime_sec"),
                "components": data.get("components") or {},
            }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:120], "components": {}}


def _backfill_config() -> Dict[str, Any]:
    import os
    return {
        "enabled": os.getenv("KLINE_DEPTH_BACKFILL_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on"),
        "mode": os.getenv("KLINE_DEPTH_BACKFILL_SYMBOLS", "").strip() or "hot",
        "symbol_limit": int(os.getenv("KLINE_DEPTH_BACKFILL_SYMBOL_LIMIT", "60") or 60),
        "cold_enabled": os.getenv("KLINE_DEPTH_BACKFILL_COLD_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on"),
        "cold_limit": int(os.getenv("KLINE_DEPTH_BACKFILL_COLD_LIMIT", "20") or 20),
        "round_max_sec": int(os.getenv("KLINE_DEPTH_BACKFILL_ROUND_MAX_SEC", "900") or 900),
        "idle_sec": int(os.getenv("KLINE_DEPTH_BACKFILL_IDLE_SEC", "1800") or 1800),
    }


@router.get("/data-center-overview")
def data_center_overview(
    exchange: Optional[str] = Query(None, description="只返回该交易所（asterdex/binance/...）"),
) -> Dict[str, Any]:
    """全市场数据中心总览（真实数据）。"""
    t0 = time.time()
    from backend.services.kline_history_sync import _depth_targets

    targets = _depth_targets()
    universe = _DEFAULT_UNIVERSE
    active_ex = "asterdex"

    _ensure_aggregates()
    aggs = _load_aggregates()
    with _AGG_LOCK:
        agg_warming = _AGG_CACHE["data"] is None
        agg_error = _AGG_CACHE.get("error")
    by_key = {(str(r.get("exchange")), str(r.get("period"))): r for r in aggs}
    if exchange:
        aggs = [r for r in aggs if str(r.get("exchange")) == exchange]

    now = int(time.time())
    periods_out: List[Dict[str, Any]] = []
    for r in aggs:
        p = str(r.get("period"))
        p_sec = _PERIOD_SECONDS.get(p, 3600)
        newest = int(r.get("newest_ts") or 0)
        stale = max(0, now - newest) if newest else None
        periods_out.append({
            "exchange": r.get("exchange"),
            "period": p,
            "symbols": int(r.get("symbols") or 0),
            "bars": int(r.get("bars") or 0),
            "days": round(float(r.get("bars") or 0) / max(int(r.get("symbols") or 1), 1) / (86400.0 / p_sec), 1),
            "oldest_ts": r.get("oldest_ts"),
            "newest_ts": newest or None,
            "stale_sec": stale,
            "fresh": stale is not None and float(stale) <= _fresh_stale_limit_sec(p),
        })

    # 核心币深度 vs 目标（仅 active 所）
    universe_fresh = _universe_freshness(active_ex, universe)
    depth_rows: List[Dict[str, Any]] = []
    for s in universe_fresh.get("symbols") or []:
        for p in _PERIOD_ORDER:
            cell = (s.get("periods") or {}).get(p) or {}
            target_days = int(targets.get(p, 0))
            bpd = 86400.0 / max(_PERIOD_SECONDS.get(p, 3600), 1)
            target_bars = int(target_days * bpd) if target_days else None
            bars = int(cell.get("bars") or 0)
            days = round(bars / bpd, 1) if bars else 0.0
            missing_bars = max(0, (target_bars or 0) - bars) if target_bars else None
            missing_days = round(missing_bars / bpd, 1) if missing_bars is not None else None
            depth_rows.append({
                "symbol": s["symbol"], "period": p,
                "bars": bars, "days": days,
                "target_bars": target_bars, "target_days": target_days,
                "missing_bars": missing_bars, "missing_days": missing_days,
                "stale_sec": cell.get("stale_sec"), "fresh": bool(cell.get("fresh")),
            })

    hbs = _heartbeats()
    collector_rows: List[Dict[str, Any]] = []
    seen_pools = set()
    for h in hbs:
        key = (h.get("exchange"), h.get("period"), h.get("pool"))
        if key in seen_pools:
            continue
        seen_pools.add(key)
        collector_rows.append({
            "exchange": h.get("exchange"),
            "period": h.get("period"),
            "pool": h.get("pool"),
            "symbols_ok": int(h.get("symbols_ok") or 0),
            "symbols_fail": int(h.get("symbols_fail") or 0),
            "updated_at": str(h.get("updated_at"))[:19],
        })

    return {
        "generated_at": int(t0),
        "active_exchange": active_ex,
        "depth_targets": targets,
        "aggregate_warming": bool(agg_warming),
        "aggregate_error": agg_error,
        "periods": sorted(periods_out, key=lambda x: (_PERIOD_ORDER.index(x["period"]) if x["period"] in _PERIOD_ORDER else 99, x["exchange"])),
        "universe_depth": depth_rows,
        "collectors": collector_rows,
        "backfill": _backfill_config(),
        "data_center": _dc_components(),
        "elapsed_ms": int((time.time() - t0) * 1000),
    }
