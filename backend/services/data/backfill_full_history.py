"""
全历史数据回填任务（BTC/ETH 上市日起，1d/4h/1h/5m）。

目标：把现有 KlineHistorySync（默认 365 天）改造成从上市日起的全历史回填。
    回填完成后，HistoryDataLoader 可读全历史供因子回测。

用法（在 backend 目录）：
    python -m backend.services.data.backfill_full_history --symbols BTC,ETH --periods 1d,4h,1h,5m
    python -m backend.services.data.backfill_full_history --dry-run   # 仅报告覆盖现状
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

from backend.services.data.history_loader import LISTING_DATES, HistoryDataLoader

logger = logging.getLogger(__name__)

# 回填目标周期（用户确认：1d/4h/1h/5m）
TARGET_PERIODS = ["1d", "4h", "1h", "5m"]
# DB 里 symbol 存的是 BTC/ETH（非 BTC-PERP）；KlineHistorySync 按此查
TARGET_SYMBOLS = ["BTC", "ETH"]
TARGET_EXCHANGE = "hyperliquid"  # 全历史数据主源（DB 里 hyperliquid 有最多历史）


def base_asset(symbol: str) -> str:
    """BTC-PERP -> BTC。"""
    return symbol.split("-")[0].split("/")[0].upper()


async def backfill_all(symbols=None, periods=None, days=None, dry_run=False) -> dict:
    """
    全历史回填。

    days: None = 从上市日起（全历史）；数字 = 近 N 天。
    dry_run: 仅报告现状不回填。
    """
    symbols = symbols or TARGET_SYMBOLS
    periods = periods or TARGET_PERIODS
    report = {"checked": [], "backfilled": [], "errors": []}

    loader = HistoryDataLoader()
    for sym in symbols:
        for period in periods:
            cov = loader.coverage(sym, period, TARGET_EXCHANGE)
            base = base_asset(sym)
            listing = LISTING_DATES.get(base, "2019-01-01")
            entry = {
                "symbol": sym, "period": period, "exchange": TARGET_EXCHANGE,
                "listing_date": listing,
                "current_count": cov.count,
                "current_first": datetime.fromtimestamp(cov.first_ts, tz=timezone.utc).isoformat() if cov.first_ts else None,
                "current_last": datetime.fromtimestamp(cov.last_ts, tz=timezone.utc).isoformat() if cov.last_ts else None,
                "completeness_pct": round(cov.completeness_pct, 1),
                "gaps": cov.gaps,
            }
            report["checked"].append(entry)
            ready = loader.is_full_history_ready(sym, period, min_years=2.0)

            if dry_run:
                status = "READY" if ready else "NEEDS_BACKFILL"
                print(f"[{status}] {sym} {period}: {cov.count} 根, "
                      f"完整度 {cov.completeness_pct:.1f}%, 缺口 {cov.gaps}, "
                      f"上市日 {listing}")
                continue

            if ready:
                logger.info(f"{sym} {period} 已全历史就绪，跳过")
                continue

            # 调用现有 KlineHistorySync 回填
            try:
                from backend.services.kline_history_sync import KlineHistorySync
                sync = KlineHistorySync()
                if days is None:
                    # 全历史：从上市日算天数
                    listing_dt = datetime.fromisoformat(listing).replace(tzinfo=timezone.utc)
                    days = max(30, int((datetime.now(timezone.utc) - listing_dt).days))
                result = await sync.start_sync(
                    symbols=[sym], periods=[period], days=days,
                )
                entry["sync_result"] = result
                report["backfilled"].append(entry)
                logger.info(f"{sym} {period} 回填启动: {result}")
            except Exception as e:
                entry["error"] = str(e)[:200]
                report["errors"].append(entry)
                logger.error(f"{sym} {period} 回填失败: {e}")

    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="BTC/ETH 全历史数据回填")
    ap.add_argument("--symbols", default="BTC-PERP,ETH-PERP", help="逗号分隔")
    ap.add_argument("--periods", default="1d,4h,1h,5m", help="逗号分隔")
    ap.add_argument("--days", type=int, default=None, help="None=全历史(上市日起)")
    ap.add_argument("--dry-run", action="store_true", help="仅报告覆盖现状")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    periods = [p.strip() for p in args.periods.split(",")]

    if args.dry_run:
        asyncio.run(backfill_all(symbols, periods, dry_run=True))
    else:
        report = asyncio.run(backfill_all(symbols, periods, days=args.days))
        print(f"\n回填报告: 检查 {len(report['checked'])}, "
              f"回填 {len(report['backfilled'])}, 错误 {len(report['errors'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
