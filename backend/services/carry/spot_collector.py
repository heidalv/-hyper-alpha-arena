# -*- coding: utf-8 -*-
"""[F64] 现货行情采集器 —— L2（现货-永续 carry）落地的**硬前提**。

为什么必须新建：
  实测确认库里**没有现货数据**——`crypto_klines.market` 恒为 `'CRYPTO'`（无 spot 标识），
  `crypto_prices` 为空表。没有现货价就无法计算基差，也无法验证对冲腿的真实成本。
  设计文档 §1.3 把「新增现货数据 + 现货下单通道」列为 L2 的前置条件，本模块补前半截。

数据源：交易所公共 REST（默认 Binance spot `/api/v3/klines`，无需 API key）。
落表：`market_spot_klines`（唯一键 exchange+symbol+interval+ts_ms，幂等 upsert）。

**未完成的部分（诚实声明）**：现货**下单通道**仍未建，因此 carry 机会在前端
依然标记 `executable=false`；本模块只解决「有现货价可算基差」。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_EXCHANGE = "binance_spot"
BINANCE_SPOT_BASE = "https://api.binance.com"
DEFAULT_SYMBOLS = ["BTC", "ETH", "BNB", "XRP", "SOL", "DOGE",
                   "ADA", "LINK", "UNI", "AVAX", "LTC", "DOT",
                   "ATOM", "NEAR", "APT", "ARB", "OP", "SUI"]
DEFAULT_INTERVAL = "5m"

# Binance spot 的 symbol 写法
def spot_pair(symbol: str, quote: str = "USDT") -> str:
    return f"{str(symbol).upper()}{quote}"


def ensure_table() -> None:
    """建现货 K 线表（幂等）。"""
    try:
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import MarketSessionLocal

        with system_identity():
            with MarketSessionLocal() as db:
                db.execute(text(
                    "CREATE TABLE IF NOT EXISTS market_spot_klines ("
                    " id BIGSERIAL PRIMARY KEY,"
                    " exchange VARCHAR(32) NOT NULL,"
                    " symbol VARCHAR(32) NOT NULL,"
                    " interval VARCHAR(8) NOT NULL,"
                    " ts_ms BIGINT NOT NULL,"
                    " open_price DOUBLE PRECISION,"
                    " high_price DOUBLE PRECISION,"
                    " low_price DOUBLE PRECISION,"
                    " close_price DOUBLE PRECISION,"
                    " volume DOUBLE PRECISION,"
                    " quote_volume DOUBLE PRECISION,"
                    " trades INTEGER,"
                    " created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
                ))
                db.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_spot_klines_key"
                    " ON market_spot_klines (exchange, symbol, interval, ts_ms)"
                ))
                db.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_spot_klines_symbol_ts"
                    " ON market_spot_klines (symbol, ts_ms DESC)"
                ))
                db.commit()
    except Exception as e:
        logger.warning("[F64] ensure_table 失败: %s", e)


def fetch_klines(
    symbol: str,
    *,
    interval: str = DEFAULT_INTERVAL,
    limit: int = 500,
    base_url: str = BINANCE_SPOT_BASE,
    timeout: float = 15.0,
    session: Any = None,
) -> List[Dict[str, Any]]:
    """拉取现货 K 线（可注入 session 以便单测，不真正联网）。"""
    import requests

    url = f"{base_url.rstrip('/')}/api/v3/klines"
    params = {"symbol": spot_pair(symbol), "interval": interval,
              "limit": int(max(1, min(limit, 1000)))}
    sess = session or requests
    resp = sess.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    raw = resp.json()
    out: List[Dict[str, Any]] = []
    for r in raw:
        out.append({
            "ts_ms": int(r[0]),
            "open": float(r[1]), "high": float(r[2]),
            "low": float(r[3]), "close": float(r[4]),
            "volume": float(r[5]), "quote_volume": float(r[7]),
            "trades": int(r[8]) if len(r) > 8 else 0,
        })
    return out


def upsert_klines(exchange: str, symbol: str, interval: str,
                  rows: List[Dict[str, Any]]) -> int:
    """幂等写入（同一 (exchange,symbol,interval,ts) 覆盖）。返回写入行数。"""
    if not rows:
        return 0
    ensure_table()
    try:
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import MarketSessionLocal

        with system_identity():
            with MarketSessionLocal() as db:
                for r in rows:
                    db.execute(text(
                        "INSERT INTO market_spot_klines (exchange, symbol, interval,"
                        " ts_ms, open_price, high_price, low_price, close_price,"
                        " volume, quote_volume, trades) VALUES"
                        " (:e, :s, :i, :ts, :o, :h, :l, :c, :v, :qv, :n)"
                        " ON CONFLICT (exchange, symbol, interval, ts_ms) DO UPDATE SET"
                        " open_price=EXCLUDED.open_price, high_price=EXCLUDED.high_price,"
                        " low_price=EXCLUDED.low_price, close_price=EXCLUDED.close_price,"
                        " volume=EXCLUDED.volume, quote_volume=EXCLUDED.quote_volume,"
                        " trades=EXCLUDED.trades"
                    ), {"e": exchange, "s": str(symbol).upper(), "i": interval,
                        "ts": int(r["ts_ms"]), "o": r["open"], "h": r["high"],
                        "l": r["low"], "c": r["close"], "v": r["volume"],
                        "qv": r["quote_volume"], "n": r["trades"]})
                db.commit()
        return len(rows)
    except Exception as e:
        logger.warning("[F64] upsert_klines(%s) 失败: %s", symbol, e)
        return 0


def fetch_klines_range(
    symbol: str,
    *,
    interval: str = DEFAULT_INTERVAL,
    start_ms: int,
    end_ms: int,
    base_url: str = BINANCE_SPOT_BASE,
    timeout: float = 20.0,
    session: Any = None,
    max_pages: int = 60,
) -> List[Dict[str, Any]]:
    """分页拉取 [start_ms, end_ms) 的现货 K 线（单次最多 1000 根，自动翻页）。

    回填历史用：`fetch_klines` 只取最近 N 根，做对冲腿回测远远不够。
    """
    import requests

    sess = session or requests
    out: List[Dict[str, Any]] = []
    cursor = int(start_ms)
    for _ in range(max(1, int(max_pages))):
        resp = sess.get(
            f"{base_url.rstrip('/')}/api/v3/klines",
            params={"symbol": spot_pair(symbol), "interval": interval,
                    "startTime": cursor, "endTime": int(end_ms), "limit": 1000},
            timeout=timeout,
        )
        resp.raise_for_status()
        raw = resp.json()
        if not raw:
            break
        for r in raw:
            out.append({
                "ts_ms": int(r[0]),
                "open": float(r[1]), "high": float(r[2]),
                "low": float(r[3]), "close": float(r[4]),
                "volume": float(r[5]), "quote_volume": float(r[7]),
                "trades": int(r[8]) if len(r) > 8 else 0,
            })
        if len(raw) < 1000:
            break
        cursor = int(raw[-1][0]) + 1
        if cursor >= int(end_ms):
            break
    return out


def backfill(
    symbols: Optional[List[str]] = None,
    *,
    exchange: str = DEFAULT_EXCHANGE,
    interval: str = DEFAULT_INTERVAL,
    days: float = 30.0,
    session: Any = None,
    sleep_sec: float = 0.25,
) -> Dict[str, Any]:
    """回填最近 `days` 天的现货 K 线（分页）。"""
    syms = [s.upper() for s in (symbols or DEFAULT_SYMBOLS)]
    end_ms = int(time.time() * 1000)
    start_ms = int(end_ms - float(days) * 86400_000)
    result: Dict[str, Any] = {"exchange": exchange, "interval": interval,
                              "days": days, "symbols": {}, "written": 0, "errors": {}}
    for s in syms:
        try:
            rows = fetch_klines_range(s, interval=interval, start_ms=start_ms,
                                      end_ms=end_ms, session=session)
            n = upsert_klines(exchange, s, interval, rows)
            result["symbols"][s] = {"fetched": len(rows), "written": n,
                                    "first_ts_ms": rows[0]["ts_ms"] if rows else None,
                                    "last_ts_ms": rows[-1]["ts_ms"] if rows else None}
            result["written"] += n
        except Exception as e:
            result["errors"][s] = str(e)[:200]
            logger.warning("[F64] 回填 %s 现货失败: %s", s, e)
        if sleep_sec:
            time.sleep(sleep_sec)
    return result


def collect(
    symbols: Optional[List[str]] = None,
    *,
    exchange: str = DEFAULT_EXCHANGE,
    interval: str = DEFAULT_INTERVAL,
    limit: int = 500,
    sleep_sec: float = 0.2,
    session: Any = None,
) -> Dict[str, Any]:
    """采集一批标的的现货 K 线。返回逐标的写入统计。"""
    syms = [s.upper() for s in (symbols or DEFAULT_SYMBOLS)]
    result: Dict[str, Any] = {"exchange": exchange, "interval": interval,
                              "symbols": {}, "written": 0, "errors": {}}
    for s in syms:
        try:
            rows = fetch_klines(s, interval=interval, limit=limit, session=session)
            n = upsert_klines(exchange, s, interval, rows)
            result["symbols"][s] = {"fetched": len(rows), "written": n,
                                    "last_ts_ms": rows[-1]["ts_ms"] if rows else None,
                                    "last_close": rows[-1]["close"] if rows else None}
            result["written"] += n
        except Exception as e:
            result["errors"][s] = str(e)[:200]
            logger.warning("[F64] 采集 %s 现货失败: %s", s, e)
        if sleep_sec:
            time.sleep(sleep_sec)
    return result


def latest_spot_price(symbol: str, *, exchange: str = DEFAULT_EXCHANGE,
                      max_age_sec: float = 600.0) -> Optional[Dict[str, Any]]:
    """最新现货价（过期返回 None —— 不用旧价冒充现价）。"""
    try:
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import MarketSessionLocal

        with system_identity():
            with MarketSessionLocal() as db:
                row = db.execute(text(
                    "SELECT ts_ms, close_price, volume FROM market_spot_klines"
                    " WHERE exchange=:e AND symbol=:s ORDER BY ts_ms DESC LIMIT 1"
                ), {"e": exchange, "s": str(symbol).upper()}).mappings().first()
        if not row:
            return None
        age = time.time() - int(row["ts_ms"]) / 1000.0
        if max_age_sec > 0 and age > max_age_sec:
            return None
        return {"symbol": str(symbol).upper(), "ts_ms": int(row["ts_ms"]),
                "price": float(row["close_price"]), "age_sec": round(age, 1)}
    except Exception as e:
        logger.warning("[F64] latest_spot_price 失败: %s", e)
        return None


def latest_perp_mark(symbol: str, venue: str = "asterdex",
                     *, max_age_sec: float = 600.0) -> Optional[Dict[str, Any]]:
    """最新永续价。优先用**新鲜盘口中间价**，退化到 `perp_funding.mark_price`。

    实测：`perp_funding.mark_price` 几乎全为 NULL（只有 hyperliquid 有值，
    binance 仅 776/11047 条），因此不能只靠它；盘口中间价是更可靠的来源。
    """
    try:
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import MarketSessionLocal

        with system_identity():
            with MarketSessionLocal() as db:
                row = db.execute(text(
                    "SELECT timestamp, (best_bid+best_ask)/2.0 AS mid"
                    " FROM market_orderbook_snapshots"
                    " WHERE exchange=:e AND symbol=:s AND best_bid>0 AND best_ask>best_bid"
                    " ORDER BY timestamp DESC LIMIT 1"
                ), {"e": venue, "s": str(symbol).upper()}).mappings().first()
                if row:
                    age = time.time() - int(row["timestamp"]) / 1000.0
                    if max_age_sec <= 0 or age <= max_age_sec:
                        return {"symbol": str(symbol).upper(), "ts_ms": int(row["timestamp"]),
                                "mark_px": float(row["mid"]), "source": "orderbook_mid",
                                "age_sec": round(age, 1)}
                row = db.execute(text(
                    "SELECT timestamp, mark_price FROM perp_funding"
                    " WHERE exchange=:e AND symbol=:s AND mark_price > 0"
                    " ORDER BY timestamp DESC LIMIT 1"
                ), {"e": venue, "s": str(symbol).upper()}).mappings().first()
        if not row:
            return None
        age = time.time() - int(row["timestamp"]) / 1000.0
        if max_age_sec > 0 and age > max_age_sec:
            return None
        return {"symbol": str(symbol).upper(), "ts_ms": int(row["timestamp"]),
                "mark_px": float(row["mark_price"]), "source": "perp_funding.mark_price",
                "age_sec": round(age, 1)}
    except Exception as e:
        logger.warning("[F64] latest_perp_mark 失败: %s", e)
        return None


def basis_bp(symbol: str, venue: str = "asterdex",
             *, max_age_sec: float = 600.0) -> Optional[Dict[str, Any]]:
    """基差 = (永续标记价 − 现货价) / 现货价 × 1e4（bp）。

    两边都必须新鲜，否则返回 None（不用过期数据算基差）。
    """
    spot = latest_spot_price(symbol, max_age_sec=max_age_sec)
    perp = latest_perp_mark(symbol, venue)
    if not spot or not perp or spot["price"] <= 0:
        return None
    return {
        "symbol": str(symbol).upper(), "venue": venue,
        "spot_px": spot["price"], "perp_mark_px": perp["mark_px"],
        "basis_bp": round((perp["mark_px"] - spot["price"]) / spot["price"] * 1e4, 4),
        "spot_age_sec": spot["age_sec"],
    }


def collect_task(symbols: Optional[List[str]] = None, *,
                 interval: str = DEFAULT_INTERVAL, limit: int = 500) -> Dict[str, Any]:
    """调度器任务入口：采集现货 K 线（默认 6 币 5m）。"""
    return collect(symbols, interval=interval, limit=limit)


def register_collector(*, interval_minutes: int = 30) -> bool:
    """把现货采集注册到全局调度器（`SPOT_COLLECT_ENABLED=0` 可关闭）。"""
    import os

    if os.getenv("SPOT_COLLECT_ENABLED", "1").strip().lower() in ("0", "false", "no"):
        logger.info("[F64] SPOT_COLLECT_ENABLED=0，跳过现货采集注册")
        return False
    try:
        from backend.services.scheduler import task_scheduler

        task_scheduler.start()
        task_scheduler.add_interval_task(
            collect_task, int(interval_minutes) * 60, "spot_klines_collect", 1, None,
        )
        logger.info("[F64] 现货采集已注册：每 %s 分钟", interval_minutes)
        return True
    except Exception as e:
        logger.warning("[F64] 现货采集注册失败: %s", e)
        return False
