"""Exchange-level data profile for the data center."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from backend.database.connection import MarketSessionLocal
from backend.services.market_data_ingest_queue import market_data_ingest_queue
from backend.services.market_data_metrics import market_data_metrics
from backend.services.raw_market_event_store import raw_market_event_store
from backend.services.market_data_shadow_compare import market_data_shadow_compare

logger = logging.getLogger(__name__)

# crypto_klines ≈ 6800万行 / 38GB：禁止热路径做 COUNT(*) 全表聚合。
# SnapshotScheduler 每 30s 调一次 get_profiles()，全表扫描会把磁盘 IO 打满 → 后端假死。
_PROFILE_CACHE_TTL_SEC = 600.0
_KLINE_QUERY_TIMEOUT_MS = 2500


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


class ExchangeDataProfileService:
    """Build read-only profiles for each exchange's data coverage."""

    def __init__(self) -> None:
        self._cache: dict[str, Any] | None = None
        self._cache_at: float = 0.0

    def get_profiles(self, *, force_refresh: bool = False) -> dict[str, Any]:
        now = time.time()
        if (
            not force_refresh
            and self._cache is not None
            and (now - self._cache_at) < _PROFILE_CACHE_TTL_SEC
        ):
            # 队列/指标仍取实时；重统计用缓存
            cached = dict(self._cache)
            cached["queue"] = market_data_ingest_queue.status()
            cached["metrics"] = market_data_metrics.snapshot()
            cached["cache_age_seconds"] = round(now - self._cache_at, 1)
            cached["cached"] = True
            return cached

        raw_market_event_store.ensure_table()
        now_ts = _now_ts()
        kline_rows: list[Any] = []
        raw_rows: list[Any] = []

        with MarketSessionLocal() as db:
            # 硬超时：宁可退回近似值，也不能堵死 market DB
            try:
                db.execute(text(f"SET LOCAL statement_timeout = '{_KLINE_QUERY_TIMEOUT_MS}ms'"))
            except Exception:
                pass

            try:
                # 两段式轻量统计，禁止 COUNT(DISTINCT) 扫全表：
                # 1) 每所最新/最旧时间（走 timestamp 索引，通常 <1s）
                # 2) 最近 72h 行数（窗口过滤，可超时降级）
                latest_rows = db.execute(text("""
                    SELECT exchange,
                           MAX(timestamp) AS latest_ts,
                           MIN(timestamp) AS earliest_ts
                    FROM crypto_klines
                    GROUP BY exchange
                """)).mappings().all()
                cutoff = int(time.time()) - 72 * 3600
                try:
                    count_rows = db.execute(text("""
                        SELECT exchange, COUNT(*) AS records
                        FROM crypto_klines
                        WHERE timestamp >= :cutoff
                        GROUP BY exchange
                    """), {"cutoff": cutoff}).mappings().all()
                    count_map = {r["exchange"]: int(r["records"] or 0) for r in count_rows}
                except Exception as ce:
                    logger.warning("[ExchangeDataProfile] 72h COUNT 跳过: %s", ce)
                    try:
                        db.rollback()
                        db.execute(text(f"SET LOCAL statement_timeout = '{_KLINE_QUERY_TIMEOUT_MS}ms'"))
                    except Exception:
                        pass
                    count_map = {}
                kline_rows = [
                    {
                        "exchange": r["exchange"],
                        "records": count_map.get(r["exchange"], 0),
                        "symbols": 0,
                        "periods": 0,
                        "latest_ts": r["latest_ts"],
                        "earliest_ts": r["earliest_ts"],
                        "approximate": r["exchange"] not in count_map,
                    }
                    for r in latest_rows
                ]
                kline_rows.sort(key=lambda x: int(x.get("records") or 0), reverse=True)
            except Exception as e:
                logger.warning(
                    "[ExchangeDataProfile] kline 轻量统计失败，改用近似: %s", e
                )
                try:
                    db.rollback()
                    db.execute(text(f"SET LOCAL statement_timeout = '{_KLINE_QUERY_TIMEOUT_MS}ms'"))
                    approx = db.execute(text("""
                        SELECT COALESCE(c.reltuples, 0)::bigint AS approx_rows,
                               pg_size_pretty(pg_total_relation_size(c.oid)) AS table_size
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public' AND c.relname = 'crypto_klines'
                    """)).mappings().first()
                    exchanges = db.execute(text("""
                        SELECT DISTINCT exchange FROM symbol_catalog
                        WHERE exchange IS NOT NULL
                        ORDER BY exchange
                    """)).fetchall()
                    approx_rows = int((approx or {}).get("approx_rows") or 0)
                    n_ex = max(len(exchanges), 1)
                    kline_rows = [
                        {
                            "exchange": r[0],
                            "records": max(approx_rows // n_ex, 0),
                            "symbols": 0,
                            "periods": 0,
                            "latest_ts": None,
                            "earliest_ts": None,
                            "approximate": True,
                            "table_size": (approx or {}).get("table_size"),
                        }
                        for r in exchanges
                    ]
                except Exception as e2:
                    logger.warning("[ExchangeDataProfile] 近似统计也失败: %s", e2)
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    kline_rows = []

            try:
                db.execute(text(f"SET LOCAL statement_timeout = '{_KLINE_QUERY_TIMEOUT_MS}ms'"))
                # event_ts 以秒为主；失败则整段跳过，不拖垮热路径
                cutoff = int(time.time()) - 72 * 3600
                raw_rows = db.execute(text("""
                    SELECT exchange,
                           COUNT(*) AS raw_events,
                           COUNT(DISTINCT canonical_symbol) AS raw_symbols,
                           MAX(event_ts) AS latest_raw_ts
                    FROM raw_market_events
                    WHERE event_ts >= :cutoff
                    GROUP BY exchange
                """), {"cutoff": cutoff}).mappings().all()
            except Exception as e:
                logger.warning("[ExchangeDataProfile] raw_market_events 聚合跳过: %s", e)
                try:
                    db.rollback()
                except Exception:
                    pass
                raw_rows = []

        raw_by_exchange = {row["exchange"]: row for row in raw_rows}
        profiles = []
        for row in kline_rows:
            exchange = row["exchange"]
            latest_ts = int(row["latest_ts"] or 0)
            raw = raw_by_exchange.get(exchange, {})
            latest_raw_ts = int(raw.get("latest_raw_ts") or 0)
            # raw 可能是秒或毫秒
            if latest_raw_ts > 10_000_000_000:
                latest_raw_ts = latest_raw_ts // 1000
            freshness_seconds = now_ts - latest_ts if latest_ts else None
            raw_freshness_seconds = now_ts - latest_raw_ts if latest_raw_ts else None
            profiles.append({
                "exchange": exchange,
                "records": int(row["records"] or 0),
                "symbols": int(row["symbols"] or 0),
                "periods": int(row["periods"] or 0),
                "earliest_ts": int(row["earliest_ts"] or 0) or None,
                "latest_ts": latest_ts or None,
                "freshness_seconds": freshness_seconds,
                "raw_events": int(raw.get("raw_events") or 0),
                "raw_symbols": int(raw.get("raw_symbols") or 0),
                "latest_raw_ts": latest_raw_ts or None,
                "raw_freshness_seconds": raw_freshness_seconds,
                "status": self._status(freshness_seconds, int(row["records"] or 0)),
                "shadow_compare": self._shadow_compare_summary(exchange),
                "window": "72h",
                "approximate": bool(row.get("approximate")),
            })

        known_exchanges = {p["exchange"] for p in profiles}
        for exchange, raw in raw_by_exchange.items():
            if exchange in known_exchanges:
                continue
            latest_raw_ts = int(raw.get("latest_raw_ts") or 0)
            if latest_raw_ts > 10_000_000_000:
                latest_raw_ts = latest_raw_ts // 1000
            profiles.append({
                "exchange": exchange,
                "records": 0,
                "symbols": 0,
                "periods": 0,
                "earliest_ts": None,
                "latest_ts": None,
                "freshness_seconds": None,
                "raw_events": int(raw.get("raw_events") or 0),
                "raw_symbols": int(raw.get("raw_symbols") or 0),
                "latest_raw_ts": latest_raw_ts or None,
                "raw_freshness_seconds": now_ts - latest_raw_ts if latest_raw_ts else None,
                "status": "raw_only",
                "shadow_compare": self._shadow_compare_summary(exchange),
                "window": "72h",
            })

        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "profiles": profiles,
            "queue": market_data_ingest_queue.status(),
            "metrics": market_data_metrics.snapshot(),
            "raw_summary": raw_market_event_store.summary(limit=20),
            "cached": False,
            "cache_ttl_seconds": _PROFILE_CACHE_TTL_SEC,
        }
        self._cache = result
        self._cache_at = time.time()
        return result

    @staticmethod
    def _status(freshness_seconds: int | None, records: int) -> str:
        if records <= 0:
            return "no_data"
        if freshness_seconds is None:
            return "unknown"
        if freshness_seconds <= 300:
            return "healthy"
        if freshness_seconds <= 3600:
            return "lagging"
        return "stale"

    @staticmethod
    def _shadow_compare_summary(exchange: str) -> dict[str, Any]:
        checks = []
        for symbol, timeframe in (("BTC", "1m"), ("BTC", "5m")):
            result = market_data_shadow_compare.compare_klines(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                limit=50,
            )
            checks.append({
                "symbol": symbol,
                "timeframe": timeframe,
                "status": result.get("status"),
                "compared": result.get("compared", 0),
                "matched": result.get("matched", 0),
                "match_rate": result.get("match_rate"),
                "mismatch_count": len(result.get("mismatches") or []),
            })

        valid_rates = [
            item["match_rate"]
            for item in checks
            if item.get("match_rate") is not None
        ]
        return {
            "checks": checks,
            "overall_match_rate": round(sum(valid_rates) / len(valid_rates), 6) if valid_rates else None,
        }


exchange_data_profile_service = ExchangeDataProfileService()
