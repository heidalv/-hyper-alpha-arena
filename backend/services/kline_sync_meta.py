"""
K线同步元数据：symbol_catalog / kline_sync_heartbeat。

阶段1（2026-07-31）：为 P0/P1/P2 采集隔离提供目录与心跳落库。
表在首次写入时 CREATE IF NOT EXISTS，不依赖迁移跑通即可用。
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, List, Optional, Sequence

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

_tables_ready = False
_tables_lock = threading.Lock()


def _ensure_tables() -> None:
    global _tables_ready
    if _tables_ready:
        return
    with _tables_lock:
        if _tables_ready:
            return
        try:
            from backend.database.connection import MarketSessionLocal
            with MarketSessionLocal() as db:
                db.execute(sa_text("""
                    CREATE TABLE IF NOT EXISTS symbol_catalog (
                        id SERIAL PRIMARY KEY,
                        exchange VARCHAR(20) NOT NULL,
                        symbol VARCHAR(32) NOT NULL,
                        status VARCHAR(20) NOT NULL DEFAULT 'trading',
                        contract_type VARCHAR(20) NOT NULL DEFAULT 'perp',
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT uq_symbol_catalog_ex_sym UNIQUE (exchange, symbol)
                    )
                """))
                db.execute(sa_text("""
                    CREATE INDEX IF NOT EXISTS ix_symbol_catalog_exchange
                    ON symbol_catalog (exchange)
                """))
                db.execute(sa_text("""
                    CREATE TABLE IF NOT EXISTS kline_sync_heartbeat (
                        id SERIAL PRIMARY KEY,
                        exchange VARCHAR(20) NOT NULL,
                        period VARCHAR(10) NOT NULL DEFAULT '*',
                        pool VARCHAR(8) NOT NULL DEFAULT 'p0',
                        last_success_at TIMESTAMP NULL,
                        symbols_ok INTEGER NOT NULL DEFAULT 0,
                        symbols_fail INTEGER NOT NULL DEFAULT 0,
                        meta_json TEXT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT uq_kline_sync_hb_ex_period_pool
                            UNIQUE (exchange, period, pool)
                    )
                """))
                db.commit()
            _tables_ready = True
        except Exception as e:
            logger.warning("[KlineSyncMeta] ensure_tables 失败: %s", e)


def upsert_symbol_catalog(
    exchange: str,
    symbols: Sequence[str],
    *,
    status: str = "trading",
    contract_type: str = "perp",
) -> int:
    """批量 upsert 可交易目录。返回写入/更新条数。"""
    _ensure_tables()
    ex = (exchange or "").strip().lower()
    if ex == "aster":
        ex = "asterdex"
    if not ex:
        return 0
    cleaned: List[str] = []
    seen = set()
    from backend.services.symbol_normalizer import is_valid_base_symbol, normalize_symbol

    for s in symbols or []:
        su = normalize_symbol(s)
        if su and is_valid_base_symbol(su) and su not in seen:
            seen.add(su)
            cleaned.append(su)
    if not cleaned:
        return 0
    n = 0
    try:
        from backend.database.connection import MarketSessionLocal
        with MarketSessionLocal() as db:
            for su in cleaned:
                db.execute(sa_text("""
                    INSERT INTO symbol_catalog (exchange, symbol, status, contract_type, updated_at)
                    VALUES (:ex, :sym, :st, :ct, CURRENT_TIMESTAMP)
                    ON CONFLICT (exchange, symbol) DO UPDATE SET
                        status = EXCLUDED.status,
                        contract_type = EXCLUDED.contract_type,
                        updated_at = CURRENT_TIMESTAMP
                """), {"ex": ex, "sym": su, "st": status, "ct": contract_type})
                n += 1
            db.commit()
    except Exception as e:
        logger.warning("[KlineSyncMeta] upsert_symbol_catalog(%s) 失败: %s", ex, e)
        return 0
    return n


def list_catalog_symbols(exchange: str, status: str = "trading") -> List[str]:
    """读 symbol_catalog；空则返回 []。"""
    _ensure_tables()
    ex = (exchange or "").strip().lower()
    if ex == "aster":
        ex = "asterdex"
    try:
        from backend.database.connection import MarketSessionLocal
        with MarketSessionLocal() as db:
            rows = db.execute(sa_text("""
                SELECT symbol FROM symbol_catalog
                WHERE exchange = :ex AND status = :st
                ORDER BY symbol
            """), {"ex": ex, "st": status}).fetchall()
        return [str(r[0]).upper() for r in rows if r and r[0]]
    except Exception as e:
        logger.debug("[KlineSyncMeta] list_catalog_symbols 失败: %s", e)
        return []


def refresh_catalog_from_scanner(exchange: str) -> List[str]:
    """从 MarketScanner 拉全市场可交易对并写入 catalog。"""
    ex = (exchange or "").strip().lower()
    if ex == "aster":
        ex = "asterdex"
    symbols: List[str] = []
    try:
        from backend.services.market_scanner import MarketScanner
        symbols = MarketScanner.get_all_tradable_symbols(ex) or []
    except Exception as e:
        logger.warning("[KlineSyncMeta] scanner 拉目录失败 %s: %s", ex, e)
    if symbols:
        upsert_symbol_catalog(ex, symbols)
        logger.info("[KlineSyncMeta] catalog 刷新 %s: %d symbols", ex, len(symbols))
    return [str(s).upper() for s in symbols]


def record_heartbeat(
    exchange: str,
    *,
    pool: str,
    period: str = "*",
    symbols_ok: int = 0,
    symbols_fail: int = 0,
    meta: Optional[dict[str, Any]] = None,
) -> None:
    """写入/更新采集心跳。"""
    _ensure_tables()
    ex = (exchange or "").strip().lower()
    if ex == "aster":
        ex = "asterdex"
    if not ex:
        return
    pool_l = (pool or "p0").strip().lower()
    period_l = (period or "*").strip()
    meta_json = None
    if meta is not None:
        try:
            meta_json = json.dumps(meta, ensure_ascii=False, default=str)[:4000]
        except Exception:
            meta_json = None
    try:
        from backend.database.connection import MarketSessionLocal
        with MarketSessionLocal() as db:
            db.execute(sa_text("""
                INSERT INTO kline_sync_heartbeat
                    (exchange, period, pool, last_success_at, symbols_ok, symbols_fail, meta_json, updated_at)
                VALUES
                    (:ex, :period, :pool, CURRENT_TIMESTAMP, :ok, :fail, :meta, CURRENT_TIMESTAMP)
                ON CONFLICT (exchange, period, pool) DO UPDATE SET
                    last_success_at = CURRENT_TIMESTAMP,
                    symbols_ok = EXCLUDED.symbols_ok,
                    symbols_fail = EXCLUDED.symbols_fail,
                    meta_json = EXCLUDED.meta_json,
                    updated_at = CURRENT_TIMESTAMP
            """), {
                "ex": ex,
                "period": period_l,
                "pool": pool_l,
                "ok": int(symbols_ok),
                "fail": int(symbols_fail),
                "meta": meta_json,
            })
            db.commit()
    except Exception as e:
        logger.debug("[KlineSyncMeta] record_heartbeat 失败: %s", e)


def get_heartbeats(exchange: Optional[str] = None) -> List[dict[str, Any]]:
    """读心跳列表（运维/验收）。"""
    _ensure_tables()
    try:
        from backend.database.connection import MarketSessionLocal
        with MarketSessionLocal() as db:
            if exchange:
                ex = exchange.strip().lower()
                if ex == "aster":
                    ex = "asterdex"
                rows = db.execute(sa_text("""
                    SELECT exchange, period, pool, last_success_at, symbols_ok, symbols_fail, meta_json
                    FROM kline_sync_heartbeat WHERE exchange = :ex
                    ORDER BY pool, period
                """), {"ex": ex}).fetchall()
            else:
                rows = db.execute(sa_text("""
                    SELECT exchange, period, pool, last_success_at, symbols_ok, symbols_fail, meta_json
                    FROM kline_sync_heartbeat
                    ORDER BY exchange, pool, period
                """)).fetchall()
        out = []
        for r in rows:
            out.append({
                "exchange": r[0],
                "period": r[1],
                "pool": r[2],
                "last_success_at": r[3].isoformat() if r[3] else None,
                "symbols_ok": r[4],
                "symbols_fail": r[5],
                "meta_json": r[6],
            })
        return out
    except Exception as e:
        logger.debug("[KlineSyncMeta] get_heartbeats 失败: %s", e)
        return []


def get_catalog_coverage() -> List[dict[str, Any]]:
    """各所 catalog 规模 + crypto_klines 粗覆盖（运维/磁盘规划）。

    禁止对整表 crypto_klines（约 6800 万行 / 38GB）做 COUNT(*) 全表聚合：
    Snapshot/监控若周期性调用会把磁盘 IO 打满，表现为后端“假死”。
    这里只做 catalog 精确统计 + 表级近似行数/体积。
    """
    _ensure_tables()
    out: List[dict[str, Any]] = []
    try:
        from backend.database.connection import MarketSessionLocal
        with MarketSessionLocal() as db:
            try:
                db.execute(sa_text("SET LOCAL statement_timeout = '2500ms'"))
            except Exception:
                pass
            cat_rows = db.execute(sa_text("""
                SELECT exchange, COUNT(*) FILTER (WHERE status = 'trading') AS trading_n,
                       COUNT(*) AS total_n, MAX(updated_at) AS updated_at
                FROM symbol_catalog GROUP BY exchange ORDER BY exchange
            """)).fetchall()
            approx = db.execute(sa_text("""
                SELECT COALESCE(c.reltuples, 0)::bigint AS approx_rows,
                       pg_size_pretty(pg_total_relation_size(c.oid)) AS table_size
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relname = 'crypto_klines'
            """)).first()
        approx_rows = int(approx[0]) if approx else 0
        table_size = approx[1] if approx else None
        n_ex = max(len(cat_rows), 1)
        for r in cat_rows:
            out.append({
                "exchange": r[0],
                "catalog_trading": r[1],
                "catalog_total": r[2],
                "catalog_updated_at": r[3].isoformat() if r[3] else None,
                "symbols_with_klines": None,  # 全表 DISTINCT 过贵，不再实时算
                "kline_rows": max(approx_rows // n_ex, 0),
                "kline_rows_approx_total": approx_rows,
                "table_size": table_size,
                "approximate": True,
            })
    except Exception as e:
        logger.warning("[KlineSyncMeta] get_catalog_coverage 失败: %s", e)
    return out
