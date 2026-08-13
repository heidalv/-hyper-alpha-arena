"""scalp 5m 历史数据回填入口（M1）。

候选币 = scalp_signal_log 近 60 天活跃币 + 主流通币。
默认交易所 = SCALP_KLINE_EXCHANGE 或 asterdex（与实时热路径一致）。

用法（仓库根目录）：
    python scripts/backfill_scalp_5m.py --dry-run                # 只报告覆盖
    python scripts/backfill_scalp_5m.py --days 60 --limit 10     # 回填前 10 个币
    python scripts/backfill_scalp_5m.py --days 90 --exchange hyperliquid
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parents[1]


def _default_exchange() -> str:
    return (os.getenv("SCALP_KLINE_EXCHANGE", "") or "asterdex").strip().lower()


async def _run(symbols, days, exchange):
    from backend.services.kline_history_sync import KlineHistorySync, SyncStatus
    sync = KlineHistorySync()
    started = await sync.start_sync(
        symbols=symbols,
        periods=["5m"],
        days=days,
        exchange=exchange,
    )
    if "error" in started:
        return started
    deadline = time.time() + 3600
    while sync.progress.status == SyncStatus.RUNNING and time.time() < deadline:
        await asyncio.sleep(5)
    return {
        "start": started,
        "final": sync.get_progress(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="scalp 5m 回填")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--exchange", default=None)
    ap.add_argument("--limit", type=int, default=0, help="0=全部候选币")
    ap.add_argument("--symbols", default="", help="逗号分隔覆盖候选币")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    exchange = (args.exchange or _default_exchange()).lower()
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        from backend.services.scalp.scalp_data_quality import _active_symbols
        symbols = _active_symbols(days=60)
    if args.limit > 0:
        symbols = symbols[: args.limit]

    if args.dry_run:
        from backend.services.scalp.scalp_data_quality import run_scalp_data_quality
        rep = run_scalp_data_quality(days=args.days, exchange=exchange)
        bad = [
            {"symbol": s, "completeness_pct": d["completeness_pct"],
             "max_gap_min": d["max_gap_min"]}
            for s, d in rep["symbols"].items()
            if d["completeness_pct"] < 99.0
        ]
        bad.sort(key=lambda x: x["completeness_pct"])
        print("候选币:", len(rep["symbols"]), "| 达标(<99%%):", len(bad))
        print("最差 15 个:")
        for b in bad[:15]:
            print(" ", b)
        return 0

    print("开始回填: symbols=%d days=%d exchange=%s" % (len(symbols), args.days, exchange))
    result = asyncio.run(_run(symbols, args.days, exchange))
    out_dir = ROOT / "reports" / "scalp_backfill"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ("scalp_backfill_%s.json" % datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M"))
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": args.days,
        "exchange": exchange,
        "symbols": symbols,
        "result": result,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("报告已保存:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
