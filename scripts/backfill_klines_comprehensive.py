"""
K 线全面回补 — PostgreSQL alpha_market.crypto_klines

深度策略：
  - 1d / 1w：至少 2 年（730 天）
  - 1m ~ 4h：至少 6 个月（180 天）

用法（项目根目录）：
  backend\\.venv\\Scripts\\python.exe scripts/backfill_klines_comprehensive.py
  backend\\.venv\\Scripts\\python.exe scripts/backfill_klines_comprehensive.py --daily-only
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from sqlalchemy import text

from backend.database.connection import MarketSessionLocal
from backend.database.dialect import dialect
from backend.services.market_data_symbol_config import resolve_configured_symbols

logging.getLogger("ccxt").setLevel(logging.WARNING)

PERIOD_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800,
}

# (period, days)
PLAN_INTRADAY: List[Tuple[str, int]] = [
    ("4h", 180),
    ("1h", 180),
    ("30m", 180),
    ("15m", 180),
    ("5m", 180),
    ("3m", 180),
    ("1m", 180),
]
PLAN_DAILY_PLUS: List[Tuple[str, int]] = [
    ("1w", 730),
    ("1d", 730),
]

BATCH_LIMIT = 1000
BATCH_SLEEP = 0.8
RETRY_429 = 5
RETRY_BASE_SLEEP = 5


def _resolve_symbols() -> List[str]:
    symbols, meta = resolve_configured_symbols("KLINE_REALTIME_SYMBOLS")
    fallback = ["BTC", "ETH", "SOL", "BNB", "ASTER", "VIRTUAL", "XPL", "JTO", "WIF"]
    if symbols:
        merged = list(dict.fromkeys([*(s.upper() for s in symbols), *fallback]))
        return merged
    print(f"[symbols] 配置为空，使用默认: {fallback} (meta={meta})")
    return fallback


def _db_count(symbol: str, period: str, exchange: str = "hyperliquid") -> int:
    with MarketSessionLocal() as db:
        return db.execute(
            text(
                "SELECT COUNT(*) FROM crypto_klines "
                "WHERE exchange=:ex AND symbol=:sym AND period=:period AND environment='mainnet'"
            ),
            {"ex": exchange, "sym": symbol.upper(), "period": period},
        ).scalar() or 0


def _insert_batch(exchange: str, symbol: str, period: str, klines: List[Dict]) -> int:
    if not klines:
        return 0
    inserted = 0
    insert_sql = text(
        dialect.insert_on_conflict_do_nothing(
            "crypto_klines",
            "exchange, symbol, market, timestamp, period, datetime_str, "
            "open_price, high_price, low_price, close_price, volume, "
            "environment, created_at",
            ":exchange, :symbol, 'CRYPTO', :timestamp, :period, :datetime_str, "
            ":open_price, :high_price, :low_price, :close_price, :volume, "
            "'mainnet', CURRENT_TIMESTAMP",
            conflict_cols="exchange, symbol, market, period, timestamp, environment",
        )
    )
    with MarketSessionLocal() as db:
        for k in klines:
            ts = int(k["timestamp"])
            dt_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            res = db.execute(
                insert_sql,
                {
                    "exchange": exchange,
                    "symbol": symbol.upper(),
                    "timestamp": ts,
                    "period": period,
                    "datetime_str": dt_str,
                    "open_price": k["open"],
                    "high_price": k["high"],
                    "low_price": k["low"],
                    "close_price": k["close"],
                    "volume": k["volume"],
                },
            )
            if res.rowcount:
                inserted += 1
        db.commit()
    return inserted


def _fetch_with_retry(ex, ccxt_symbol: str, period: str, since_ms: int, limit: int) -> List:
    last_err = None
    for attempt in range(RETRY_429 + 1):
        try:
            return ex.fetch_ohlcv(ccxt_symbol, period, since=since_ms, limit=limit) or []
        except Exception as e:
            msg = str(e)
            last_err = e
            if "429" in msg or "Too Many" in msg or "RateLimit" in msg:
                wait = RETRY_BASE_SLEEP * (2 ** attempt)
                print(f"      429 限速，{wait}s 后重试 (#{attempt + 1}/{RETRY_429})")
                time.sleep(wait)
                continue
            print(f"      fetch error: {msg[:80]}")
            return []
    if last_err:
        print(f"      fetch failed after retries: {last_err}")
    return []


async def backfill_one(symbol: str, period: str, days: int, exchange: str = "hyperliquid") -> Dict:
    from backend.services.hyperliquid_market_data import get_default_hyperliquid_client

    client = get_default_hyperliquid_client()
    ex = client.exchange
    ccxt_symbol = (
        client.normalize_symbol(symbol)
        if hasattr(client, "normalize_symbol")
        else f"{symbol.upper()}/USDC:USDC"
    )

    period_sec = PERIOD_SECONDS[period]
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    before = _db_count(symbol, period, exchange)
    expected = max(1, int((end_time - start_time).total_seconds() / period_sec))
    # 已有 95% 以上则跳过
    if before >= expected * 0.95:
        return {
            "period": period, "days": days, "before": before,
            "after": before, "added": 0, "expected": expected, "status": "skipped",
        }

    cur = start_time
    added_total = 0
    batch_td = timedelta(seconds=BATCH_LIMIT * period_sec)
    failed_batches = 0

    while cur < end_time:
        cur_end = min(cur + batch_td, end_time)
        since_ms = int(cur.timestamp() * 1000)
        try:
            ohlcv = await asyncio.to_thread(
                _fetch_with_retry, ex, ccxt_symbol, period, since_ms, BATCH_LIMIT
            )
        except Exception as e:
            print(f"      batch {cur:%Y-%m-%d} failed: {str(e)[:60]}")
            failed_batches += 1
            ohlcv = []

        if ohlcv:
            klines = [
                {
                    "timestamp": int(c[0] / 1000),
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                }
                for c in ohlcv
            ]
            ins = await asyncio.to_thread(_insert_batch, exchange, symbol, period, klines)
            added_total += ins
            # 推进游标：最后一根 K 线时间 + 1 周期
            last_ts = klines[-1]["timestamp"]
            cur = datetime.fromtimestamp(last_ts, tz=timezone.utc) + timedelta(seconds=period_sec)
        else:
            cur = cur_end

        await asyncio.sleep(BATCH_SLEEP)

    after = _db_count(symbol, period, exchange)
    return {
        "period": period,
        "days": days,
        "before": before,
        "after": after,
        "added": after - before,
        "expected": expected,
        "status": "completed",
        "failed_batches": failed_batches,
    }


async def run_backfill(*, daily_only: bool = False) -> None:
    symbols = _resolve_symbols()
    plan = list(PLAN_DAILY_PLUS)
    if not daily_only:
        plan.extend(PLAN_INTRADAY)

    print("=" * 78)
    print("K 线全面回补")
    print(f"  币种: {', '.join(symbols)}")
    print(f"  日线及以上: 2年 (1d/1w) | 日内周期: 6个月 (1m~4h)")
    if daily_only:
        print("  模式: --daily-only (仅 1d/1w)")
    print("=" * 78)

    t0 = time.time()
    total_added = 0
    summary: List[Dict] = []

    for sym in symbols:
        print(f"\n>> {sym}")
        for period, days in plan:
            try:
                r = await backfill_one(sym, period, days)
            except Exception as e:
                print(f"  FAIL {sym} {period} error: {e}")
                continue
            total_added += r["added"]
            flag = "OK" if r["status"] == "skipped" or r["added"] >= 0 else "FAIL"
            extra = f"  WARN {r['failed_batches']} batch failed" if r.get("failed_batches") else ""
            print(
                f"  {flag} {sym:<8} {period:<3} ({days:>3}d)  "
                f"{r['before']:>6} → {r['after']:>6}  "
                f"(+{r['added']:>6})  expect~{r.get('expected', '?')}  {r['status']}{extra}"
            )
            summary.append({"symbol": sym, **r})

    print()
    print("=" * 78)
    print(" 总览")
    print("=" * 78)
    by_period: Dict[str, int] = {}
    for s in summary:
        by_period[s["period"]] = by_period.get(s["period"], 0) + s["added"]
    for period in sorted(by_period.keys(), key=lambda p: PERIOD_SECONDS.get(p, 0), reverse=True):
        print(f"  {period:<4}  +{by_period[period]:>8} 根")
    print(f"\n  elapsed: {time.time() - t0:.1f}s")
    print(f"  total added: {total_added} bars")
    print("=" * 78)


def main():
    parser = argparse.ArgumentParser(description="K线全面回补")
    parser.add_argument(
        "--daily-only",
        action="store_true",
        help="仅回补 1d/1w（2年），适合先快速补齐长线",
    )
    args = parser.parse_args()
    asyncio.run(run_backfill(daily_only=args.daily_only))


if __name__ == "__main__":
    main()
