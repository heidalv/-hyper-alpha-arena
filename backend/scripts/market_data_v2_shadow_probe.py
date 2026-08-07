#!/usr/bin/env python3
"""
One-shot market-data v2 shadow probe.

This script enables v2 only inside this process, fetches a small batch of raw
K-line events, compares them with crypto_klines, and optionally captures a
SnapshotStore snapshot. It does not change the running backend process env.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    os.environ["MARKET_DATA_V2_ENABLED"] = "true"

    from backend.services.market_data_ingest_queue import IngestTask, market_data_ingest_queue
    from backend.services.market_data_shadow_compare import market_data_shadow_compare
    from backend.services.raw_market_event_store import raw_market_event_store
    from backend.services.snapshot_producer import snapshot_producer
    from backend.services.snapshot_reader import snapshot_reader

    task = IngestTask(
        exchange=args.exchange,
        symbol=args.symbol,
        timeframe=args.timeframe,
        limit=args.limit,
    )
    try:
        processed = await market_data_ingest_queue.process_task(task)
        ingest_result = {
            "task_id": processed.task_id,
            "status": processed.status,
            "raw_events": processed.raw_events,
            "error": processed.error,
        }
    except Exception as exc:
        ingest_result = {
            "task_id": task.task_id,
            "status": "failed",
            "raw_events": task.raw_events,
            "error": f"{type(exc).__name__}: {exc}",
        }

    raw_summary = raw_market_event_store.summary(limit=10)
    compare = market_data_shadow_compare.compare_klines(
        exchange=args.exchange,
        symbol=args.symbol,
        timeframe=args.timeframe,
        limit=args.compare_limit,
    )

    snapshot_capture = None
    snapshot_status = None
    if args.capture_snapshot:
        snapshot_capture = snapshot_producer.capture(
            symbols=[args.symbol],
            periods=[args.timeframe],
            exchange=args.exchange,
            count=min(args.limit, 50),
            force=True,
        )
        snapshot = snapshot_reader.get_snapshot(max_age=120)
        snapshot_status = {
            "has_snapshot": bool(snapshot),
            "snapshot_id": snapshot.get("snapshot_id") if snapshot else None,
            "kline_groups": len(snapshot.get("klines", {})) if snapshot else 0,
        }

    return {
        "probe": {
            "exchange": args.exchange,
            "symbol": args.symbol,
            "timeframe": args.timeframe,
            "limit": args.limit,
        },
        "ingest": ingest_result,
        "raw_summary": raw_summary,
        "compare": compare,
        "snapshot_capture": snapshot_capture,
        "snapshot_status": snapshot_status,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one market-data v2 shadow probe.")
    parser.add_argument("--exchange", default="hyperliquid")
    parser.add_argument("--symbol", default="BTC")
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--compare-limit", type=int, default=50)
    parser.add_argument("--capture-snapshot", action="store_true")
    parser.add_argument("--output", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = asyncio.run(run_probe(args))
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
