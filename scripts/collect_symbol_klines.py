"""K 线数据采集脚本 — Stage C1 交付物。

用途:
    从 Binance 公开 klines 端点拉取指定币种 / 周期 / 时长的历史 K 线，
    只写入 data/market_klines/*.csv，不碰业务数据库。

用法示例:
    python scripts/collect_symbol_klines.py \
        --symbols BTC,ETH,SOL,BNB,ASTER,XPL,VIRTUAL,DOGE \
        --intervals 15m,1h,4h \
        --days 90

说明:
    Binance 单次 klines 限制 1000 根，脚本分页拉取到 --days 覆盖为止。
    公开端点无需 API key，但请求过密会被限流；脚本已加 0.2s 间隔。
    原始 Binance OHLCV 字段全量落地，便于后续重算不同窗口统计。
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import time
from pathlib import Path
from typing import Iterable

import requests

BINANCE_KLINES_ENDPOINT = "https://api.binance.com/api/v3/klines"

INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000, "8h": 28_800_000,
    "12h": 43_200_000, "1d": 86_400_000, "3d": 259_200_000, "1w": 604_800_000,
}

FIELDNAMES = [
    "open_time_ms", "open", "high", "low", "close", "volume",
    "close_time_ms", "quote_asset_volume", "trades_count",
    "taker_buy_base_volume", "taker_buy_quote_volume",
]

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("collect_klines")


def normalize_symbol(raw: str) -> str:
    """把用户输入的 'BTC' / 'btc' / 'BTCUSDT' 都归一为 Binance 现货交易对 'BTCUSDT'."""
    s = raw.strip().upper()
    if not s:
        raise ValueError("empty symbol")
    if s.endswith("USDT"):
        return s
    return f"{s}USDT"


def fetch_klines_page(
    symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 1000,
) -> list[list]:
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": limit,
    }
    resp = requests.get(BINANCE_KLINES_ENDPOINT, params=params, timeout=15)
    if resp.status_code == 451:
        logger.error("Binance 公开端点在当前地区被拒绝（HTTP 451）。请改用代理或在可访问地区运行。")
        sys.exit(1)
    resp.raise_for_status()
    return resp.json()


def fetch_full_range(
    symbol: str, interval: str, start_ms: int, end_ms: int, request_sleep: float = 0.2,
) -> Iterable[list]:
    step = INTERVAL_MS[interval] * 1000
    cursor = start_ms
    total = 0
    while cursor < end_ms:
        chunk_end = min(cursor + step, end_ms)
        page = fetch_klines_page(symbol, interval, cursor, chunk_end)
        if not page:
            logger.warning(f"{symbol} {interval} @ {cursor}: 空页返回，终止该分段")
            break
        for row in page:
            yield row
            total += 1
        cursor = int(page[-1][6]) + 1
        time.sleep(request_sleep)
    logger.info(f"{symbol} {interval}: 共 {total} 根")


def write_csv(out_path: Path, rows: Iterable[list]) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(FIELDNAMES)
        for row in rows:
            w.writerow(row[:11])
            written += 1
    return written


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="采集 Binance K 线到 CSV")
    p.add_argument("--symbols", required=True,
                   help="逗号分隔，例如 BTC,ETH,SOL")
    p.add_argument("--intervals", default="15m,1h,4h",
                   help="逗号分隔，默认 15m,1h,4h")
    p.add_argument("--days", type=int, default=90,
                   help="向前回溯天数，默认 90")
    p.add_argument("--output-dir", default="data/market_klines",
                   help="输出根目录（相对工作目录），默认 data/market_klines")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    symbols = [normalize_symbol(s) for s in args.symbols.split(",") if s.strip()]
    intervals = [s.strip() for s in args.intervals.split(",") if s.strip()]

    for iv in intervals:
        if iv not in INTERVAL_MS:
            logger.error(f"不支持的周期: {iv}。支持: {','.join(INTERVAL_MS.keys())}")
            return 2

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - args.days * 86_400_000
    out_root = Path(args.output_dir)

    for sym in symbols:
        for iv in intervals:
            out_path = out_root / sym / f"{iv}.csv"
            logger.info(f"→ {sym} {iv}  拉取 {args.days} 天 ({out_path})")
            try:
                rows = list(fetch_full_range(sym, iv, start_ms, end_ms))
            except requests.HTTPError as e:
                logger.error(f"{sym} {iv} HTTP 错误: {e}")
                continue
            except Exception as e:
                logger.error(f"{sym} {iv} 异常: {e}")
                continue
            written = write_csv(out_path, rows)
            logger.info(f"  ✓ 写入 {written} 根到 {out_path}")

    logger.info("全部完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
