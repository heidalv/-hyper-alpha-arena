"""从本地 alpha_arena.db 导出 crypto_klines 成分析脚本所需 CSV — Stage C1 交付物（离线路径）。

背景:
    当前环境无法直连 Binance（TLS 被重置），但系统本身已长期采集了 7 个交易币种
    (ASTER / BNB / BTC / ETH / SOL / VIRTUAL / XPL) 的多周期 K 线并落到 crypto_klines 表。
    此脚本把这些数据导出成 analyze_symbol_statistics.py 能直接吃的 CSV，保持相同字段名，
    不做任何汇总、不改业务表。

用法:
    python scripts/export_local_klines.py \
        --db data/alpha_arena.db \
        --symbols ASTER,BNB,BTC,ETH,SOL,VIRTUAL,XPL \
        --intervals 15m,1h,4h \
        --output-dir data/market_klines

字段说明:
    输出列与 analyze_symbol_statistics.py 期望完全一致：
      open_time_ms, open, high, low, close, volume,
      close_time_ms, quote_asset_volume, trades_count,
      taker_buy_base_volume, taker_buy_quote_volume
    其中 DB 里没有的列（如 quote_asset_volume / taker_* / trades_count）用空值填充，
    分析脚本只读 OHLCV + open_time_ms，空值列无影响。
"""
from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
import sys
from pathlib import Path


logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("export_local_klines")

FIELDNAMES = [
    "open_time_ms", "open", "high", "low", "close", "volume",
    "close_time_ms", "quote_asset_volume", "trades_count",
    "taker_buy_base_volume", "taker_buy_quote_volume",
]

INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000, "8h": 28_800_000,
    "12h": 43_200_000, "1d": 86_400_000,
}


def export_one(conn: sqlite3.Connection, symbol: str, interval: str, out_path: Path) -> int:
    sql = (
        "SELECT timestamp, open_price, high_price, low_price, close_price, volume "
        "FROM crypto_klines WHERE symbol = ? AND period = ? ORDER BY timestamp ASC"
    )
    rows = conn.execute(sql, (symbol, interval)).fetchall()
    if not rows:
        logger.warning(f"{symbol} {interval}: 本地无数据，跳过")
        return 0

    step_ms = INTERVAL_MS.get(interval, 3_600_000)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(FIELDNAMES)
        for ts, o, h, l, c, v in rows:
            try:
                open_ms = int(ts) * 1000
            except Exception:
                continue
            close_ms = open_ms + step_ms - 1
            w.writerow([open_ms, o, h, l, c, v, close_ms, "", "", "", ""])
    logger.info(f"{symbol} {interval}: 导出 {len(rows)} 根 → {out_path}")
    return len(rows)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="从本地 SQLite 导出 K 线 CSV（给统计脚本吃）")
    p.add_argument("--db", default="data/alpha_arena.db")
    p.add_argument("--symbols", required=True, help="逗号分隔，如 BTC,ETH,SOL")
    p.add_argument("--intervals", default="15m,1h,4h", help="逗号分隔")
    p.add_argument("--output-dir", default="data/market_klines")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    db = Path(args.db)
    if not db.exists():
        logger.error(f"DB 不存在: {db}")
        return 1

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    intervals = [s.strip() for s in args.intervals.split(",") if s.strip()]

    conn = sqlite3.connect(str(db))
    try:
        out_root = Path(args.output_dir)
        total = 0
        for sym in symbols:
            for iv in intervals:
                out = out_root / sym / f"{iv}.csv"
                total += export_one(conn, sym, iv, out)
        logger.info(f"总计导出 {total} 根 K 线 → {out_root}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
