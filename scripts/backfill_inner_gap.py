"""按符号+周期回填 K 线（含前缀/后缀缺口与内部空洞）——DepthBackfill 全量轮次
尚未轮到时的定向补充工具。

用法：
  python scripts/backfill_inner_gap.py --symbol SOL --period 1h --days 210 [--exchange asterdex]

复用各所 ccxt 采集器（自动走代理 + 限流桶），幂等插入（ON CONFLICT 去重）。
[2026-08-08] 新增：DepthBackfill 周期顺序调整后（1d/1w 优先），关键币短周期
（如 SOL/1h）可能滞后数小时，此工具可先行定向补齐。
"""
import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.kline_collectors import ExchangeDataSourceFactory
from backend.services.kline_data_service import kline_service


async def _backfill(exchange: str, symbol: str, period: str, days: int) -> None:
    collector = ExchangeDataSourceFactory.get_collector(exchange)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    bars = await collector.fetch_historical_klines(symbol, start, end, period)
    print(f"FETCHED: {len(bars)}")
    if bars:
        await kline_service._insert_kline_data(bars)
        print(f"INSERTED: {len(bars)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="按需回填单币单周期 K 线")
    parser.add_argument("--exchange", default="asterdex")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--period", default="1h")
    parser.add_argument("--days", type=int, default=210)
    args = parser.parse_args()
    asyncio.run(_backfill(args.exchange, args.symbol.upper(), args.period, args.days))


if __name__ == "__main__":
    main()
