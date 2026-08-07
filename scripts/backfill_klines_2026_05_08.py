"""
K 线历史数据批量回补脚本（深挖第 3 轮 衍生 / 2026-05-08）

V2: 修复 8 并发触发 429 的问题。改为：
  - 单 symbol × 单 period 串行
  - 直接调底层 ccxt + 自带 backoff & 429 重试
  - period 之间休 1 秒，batch 之间休 1 秒

回补深度：
    1m → 7d, 3m → 14d, 5m → 30d, 15m → 60d, 30m → 90d, 1h → 180d,
    4h → 365d, 1d → 730d
"""
from __future__ import annotations

import asyncio
import time
import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from sqlalchemy import text
from backend.database.connection import SessionLocal

logging.getLogger("ccxt").setLevel(logging.WARNING)

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "alpha_arena.db"

CORE_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "VIRTUAL", "ASTER", "XPL", "WIF"]

PERIOD_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400,
}
PLAN: List[Tuple[str, int]] = [
    ("1d",  730),
    ("4h",  365),
    ("1h",  180),
    ("30m", 90),
    ("15m", 60),
    ("5m",  30),
    ("3m",  14),
    ("1m",  7),
]

BATCH_LIMIT = 1000        # Hyperliquid 单次 5000 上限，1000 更稳
BATCH_SLEEP = 1.0         # 每次 batch 后睡眠（秒）
RETRY_429 = 4             # 429 时最多重试次数
RETRY_BASE_SLEEP = 5      # 429 重试 backoff 基数


def _db_count(symbol: str, period: str) -> int:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute(
            "SELECT COUNT(*) FROM crypto_klines WHERE symbol=? AND period=?",
            (symbol, period),
        )
        return c.fetchone()[0]
    finally:
        conn.close()


def _insert_batch(exchange: str, symbol: str, period: str, klines: List[Dict]) -> int:
    """与 ORM 模型/现有 collector 完全一致的列：
    market='CRYPTO', environment='mainnet'（按 schema 默认值）。
    无 updated_at 列；created_at 走 server default。
    """
    if not klines:
        return 0
    inserted = 0
    sql = text("""
        INSERT OR IGNORE INTO crypto_klines
        (exchange, symbol, market, environment, timestamp, period, datetime_str,
         open_price, high_price, low_price, close_price, volume)
        VALUES (:exchange, :symbol, 'CRYPTO', 'mainnet', :timestamp, :period, :datetime_str,
                :open, :high, :low, :close, :volume)
    """)
    with SessionLocal() as db:
        for k in klines:
            try:
                ts = int(k["timestamp"])
                dt_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
                res = db.execute(sql, {
                    "exchange": exchange, "symbol": symbol,
                    "timestamp": ts, "period": period, "datetime_str": dt_str,
                    "open": k["open"], "high": k["high"],
                    "low": k["low"], "close": k["close"], "volume": k["volume"],
                })
                if res.rowcount:
                    inserted += 1
            except Exception as ie:
                # 第一次迭代失败时打印诊断
                if inserted == 0 and len(klines) <= 5:
                    print(f"      insert error sample: {ie}")
        db.commit()
    return inserted


def _fetch_with_retry(ex, ccxt_symbol: str, period: str, since_ms: int, limit: int) -> List:
    """带 429 backoff 的同步拉取"""
    last_err = None
    for attempt in range(RETRY_429 + 1):
        try:
            return ex.fetch_ohlcv(ccxt_symbol, period, since=since_ms, limit=limit) or []
        except Exception as e:
            msg = str(e)
            last_err = e
            if "429" in msg or "Too Many" in msg:
                wait = RETRY_BASE_SLEEP * (2 ** attempt)
                print(f"      429 限速，{wait}s 后重试 (#{attempt + 1}/{RETRY_429})")
                time.sleep(wait)
                continue
            return []
    raise last_err if last_err else RuntimeError("unknown error")


async def backfill_one(symbol: str, period: str, days: int) -> Dict:
    """串行回补单个 symbol+period"""
    from backend.services.hyperliquid_market_data import get_default_hyperliquid_client
    client = get_default_hyperliquid_client()
    ex = client.exchange
    if hasattr(client, "normalize_symbol"):
        ccxt_symbol = client.normalize_symbol(symbol)
    else:
        ccxt_symbol = f"{symbol.upper()}/USDC:USDC"

    period_sec = PERIOD_SECONDS[period]
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    before = _db_count(symbol, period)
    expected = int((end_time - start_time).total_seconds() / period_sec)
    if before >= expected * 0.95:
        return {"period": period, "days": days, "before": before,
                "after": before, "added": 0, "status": "skipped"}

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
            klines = [{
                "timestamp": int(c[0] / 1000),
                "open": float(c[1]), "high": float(c[2]),
                "low": float(c[3]), "close": float(c[4]),
                "volume": float(c[5]),
            } for c in ohlcv]
            ins = await asyncio.to_thread(_insert_batch, "hyperliquid", symbol, period, klines)
            added_total += ins
        cur = cur_end
        await asyncio.sleep(BATCH_SLEEP)

    after = _db_count(symbol, period)
    return {
        "period": period, "days": days, "before": before, "after": after,
        "added": after - before, "status": "completed",
        "failed_batches": failed_batches,
    }


async def main():
    print("=" * 78)
    print("K 线批量回补 V2 — 串行 + 429 backoff")
    print("=" * 78)

    t0 = time.time()
    total_added = 0
    summary: List[Dict] = []

    for sym in CORE_SYMBOLS:
        print(f"\n▶ {sym}")
        for period, days in PLAN:
            r = await backfill_one(sym, period, days)
            total_added += r["added"]
            flag = ("✅" if r["status"] == "skipped" or r["added"] >= 0 else "❌")
            extra = ""
            if r.get("failed_batches"):
                extra = f"  ⚠️ {r['failed_batches']} batch failed"
            print(f"  {flag} {sym:<8} {period:<3} ({days:>3}d)  "
                  f"{r['before']:>5} → {r['after']:>5}  "
                  f"(+{r['added']:>5})  {r['status']}{extra}")
            summary.append({"symbol": sym, **r})

    print()
    print("=" * 78)
    print(" 总览")
    print("=" * 78)
    by_sym: Dict[str, int] = {}
    for s in summary:
        by_sym[s["symbol"]] = by_sym.get(s["symbol"], 0) + s["added"]
    for sym, n in by_sym.items():
        print(f"  {sym:<8}  +{n:>6} 根")
    print()
    print(f"  ⏱ 总耗时: {time.time() - t0:.1f}s")
    print(f"  ➕ 总新增: {total_added} 根")
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(main())
