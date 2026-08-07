#!/usr/bin/env python3
"""
Repair crypto_klines using exchange historical closed candles.

Default mode is dry-run. Pass --apply to update/insert only closed candles.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database.connection import MarketSessionLocal, sqlite_write_commit  # noqa: E402
from backend.database.dialect import dialect  # noqa: E402
from backend.services.hyperliquid_market_data import get_kline_data_from_hyperliquid  # noqa: E402
from backend.services.market_data_adapters.registry import exchange_adapter_registry  # noqa: E402


PERIOD_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def period_seconds(period: str) -> int:
    return PERIOD_SECONDS.get(period, 60)


def closed_candles(
    klines: list[dict[str, Any]],
    period: str,
    settle_periods: int = 3,
    settle_seconds: int = 3600,
) -> list[dict[str, Any]]:
    interval = period_seconds(period)
    current_open_ts = int(time.time() // interval * interval)
    stable_delay = max(max(0, settle_periods) * interval, max(0, settle_seconds))
    stable_before_ts = current_open_ts - stable_delay
    return [k for k in klines if int(k["timestamp"]) < stable_before_ts]


def normalize_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def normalize_kline(kline: dict[str, Any]) -> dict[str, Any] | None:
    timestamp = kline.get("timestamp") or kline.get("time")
    if timestamp is None:
        return None
    ts = int(timestamp)
    if ts > 10_000_000_000:
        ts = ts // 1000
    return {
        "timestamp": ts,
        "open": kline.get("open"),
        "high": kline.get("high"),
        "low": kline.get("low"),
        "close": kline.get("close"),
        "volume": kline.get("volume"),
    }


async def fetch_exchange_klines(exchange: str, symbol: str, period: str, limit: int) -> list[dict[str, Any]]:
    exchange_key = exchange.strip().lower()
    if exchange_key == "hyperliquid":
        raw = await asyncio.to_thread(
            get_kline_data_from_hyperliquid,
            symbol,
            period,
            limit,
            False,
            "mainnet",
        )
    else:
        raw = await exchange_adapter_registry.get_klines(
            exchange_key,
            symbol,
            period,
            limit=limit,
        )
    normalized = [normalize_kline(item) for item in raw or []]
    return [item for item in normalized if item is not None]


def mismatch_fields(old: dict[str, Any] | None, new: dict[str, Any], volume_tolerance: float) -> dict[str, dict[str, Any]]:
    if old is None:
        return {"row": {"old": None, "new": "missing"}}

    mapping = {
        "open": "open_price",
        "high": "high_price",
        "low": "low_price",
        "close": "close_price",
        "volume": "volume",
    }
    mismatches = {}
    for raw_key, old_key in mapping.items():
        new_value = normalize_float(new.get(raw_key))
        old_value = normalize_float(old.get(old_key))
        if new_value is None and old_value is None:
            continue
        tolerance = volume_tolerance if raw_key == "volume" else 1e-9
        if new_value is None or old_value is None or abs(new_value - old_value) > tolerance:
            mismatches[raw_key] = {"old": old_value, "new": new_value}
    return mismatches


def load_existing(exchange: str, symbol: str, period: str, timestamps: list[int]) -> dict[int, dict[str, Any]]:
    if not timestamps:
        return {}
    from sqlalchemy import bindparam

    stmt = text("""
        SELECT timestamp, open_price, high_price, low_price, close_price, volume
        FROM crypto_klines
        WHERE exchange = :exchange
          AND symbol = :symbol
          AND period = :period
          AND timestamp IN :timestamps
    """).bindparams(bindparam("timestamps", expanding=True))
    with MarketSessionLocal() as db:
        rows = db.execute(stmt, {
            "exchange": exchange,
            "symbol": symbol.upper(),
            "period": period,
            "timestamps": timestamps,
        }).mappings().all()
    return {int(row["timestamp"]): dict(row) for row in rows}


def apply_repairs(exchange: str, symbol: str, period: str, repairs: list[dict[str, Any]]) -> dict[str, int]:
    insert_sql = text(dialect.insert_on_conflict_do_nothing(
        "crypto_klines",
        "exchange, symbol, market, timestamp, period, datetime_str, "
        "open_price, high_price, low_price, close_price, volume, environment",
        ":exchange, :symbol, 'CRYPTO', :timestamp, :period, :datetime_str, "
        ":open_price, :high_price, :low_price, :close_price, :volume, 'mainnet'",
        conflict_cols="exchange, symbol, market, period, timestamp, environment",
    ))
    update_sql = text("""
        UPDATE crypto_klines
        SET open_price = :open_price,
            high_price = :high_price,
            low_price = :low_price,
            close_price = :close_price,
            volume = :volume,
            datetime_str = :datetime_str
        WHERE exchange = :exchange
          AND symbol = :symbol
          AND market = 'CRYPTO'
          AND period = :period
          AND timestamp = :timestamp
          AND environment = 'mainnet'
    """)
    inserted = 0
    updated = 0
    with MarketSessionLocal() as db:
        for item in repairs:
            k = item["new"]
            ts = int(k["timestamp"])
            params = {
                "exchange": exchange,
                "symbol": symbol.upper(),
                "timestamp": ts,
                "period": period,
                "datetime_str": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "open_price": k["open"],
                "high_price": k["high"],
                "low_price": k["low"],
                "close_price": k["close"],
                "volume": k["volume"],
            }
            if item["action"] == "insert":
                db.execute(insert_sql, params)
                inserted += 1
            else:
                result = db.execute(update_sql, params)
                updated += int(result.rowcount or 0)
        sqlite_write_commit(db, label="kline_quality_repair.apply")
    return {"inserted": inserted, "updated": updated}


def parse_csv(value: str, fallback: str, upper: bool = False) -> list[str]:
    raw = value or fallback
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return [item.upper() for item in items] if upper else items


def normalize_exchange(exchange: str) -> str:
    return exchange_adapter_registry.normalize_exchange(exchange)


async def run_one_async(args: argparse.Namespace, symbol: str, period: str) -> dict[str, Any]:
    exchange = normalize_exchange(args.exchange)

    try:
        fetched = await asyncio.wait_for(
            fetch_exchange_klines(exchange, symbol, period, args.limit),
            timeout=max(5, int(getattr(args, "fetch_timeout", 30))),
        )
    except Exception as exc:
        return {
            "status": "failed",
            "exchange": exchange,
            "symbol": symbol.upper(),
            "period": period,
            "limit": args.limit,
            "closed_only": args.closed_only,
            "settle_periods": args.settle_periods,
            "settle_seconds": args.settle_seconds,
            "fetched": 0,
            "closed": 0,
            "existing": 0,
            "mismatch_count": 0,
            "apply": args.apply,
            "apply_result": {"inserted": 0, "updated": 0},
            "error": f"{type(exc).__name__}: {exc}",
            "sample_mismatches": [],
        }
    candles = (
        closed_candles(fetched, period, args.settle_periods, args.settle_seconds)
        if args.closed_only
        else fetched
    )
    timestamps = [int(k["timestamp"]) for k in candles]
    existing = load_existing(exchange, symbol, period, timestamps)

    repairs = []
    for k in candles:
        ts = int(k["timestamp"])
        old = existing.get(ts)
        fields = mismatch_fields(old, k, args.volume_tolerance)
        if fields:
            repairs.append({
                "timestamp": ts,
                "action": "insert" if old is None else "update",
                "fields": fields,
                "new": k,
            })

    apply_result = {"inserted": 0, "updated": 0}
    if args.apply and repairs:
        apply_result = apply_repairs(exchange, symbol, period, repairs)

    return {
        "status": "ok" if fetched else "no_data",
        "exchange": exchange,
        "symbol": symbol.upper(),
        "period": period,
        "limit": args.limit,
        "closed_only": args.closed_only,
        "settle_periods": args.settle_periods,
        "settle_seconds": args.settle_seconds,
        "fetched": len(fetched),
        "closed": len(candles),
        "existing": len(existing),
        "mismatch_count": len(repairs),
        "apply": args.apply,
        "apply_result": apply_result,
        "sample_mismatches": [
            {
                "timestamp": r["timestamp"],
                "action": r["action"],
                "fields": r["fields"],
            }
            for r in repairs[:20]
        ],
    }


async def run_async(args: argparse.Namespace) -> dict[str, Any]:
    exchanges = [normalize_exchange(exchange) for exchange in parse_csv(getattr(args, "exchanges", ""), args.exchange)]
    symbols = parse_csv(args.symbols, args.symbol, upper=True)
    periods = parse_csv(args.periods, args.period)

    checks = []
    for exchange in exchanges:
        for symbol in symbols:
            for period in periods:
                scoped_args = argparse.Namespace(**{**vars(args), "exchange": exchange})
                checks.append(await run_one_async(scoped_args, symbol=symbol, period=period))

    if len(checks) == 1:
        return checks[0]

    aggregate_apply = {
        "inserted": sum(item["apply_result"]["inserted"] for item in checks),
        "updated": sum(item["apply_result"]["updated"] for item in checks),
    }
    return {
        "exchanges": [exchange.strip().lower() for exchange in exchanges],
        "symbols": symbols,
        "periods": periods,
        "limit": args.limit,
        "closed_only": args.closed_only,
        "settle_periods": args.settle_periods,
        "settle_seconds": args.settle_seconds,
        "apply": args.apply,
        "aggregate": {
            "checks": len(checks),
            "failed": sum(1 for item in checks if item.get("status") == "failed"),
            "no_data": sum(1 for item in checks if item.get("status") == "no_data"),
            "fetched": sum(item["fetched"] for item in checks),
            "closed": sum(item["closed"] for item in checks),
            "existing": sum(item["existing"] for item in checks),
            "mismatch_count": sum(item["mismatch_count"] for item in checks),
            "apply_result": aggregate_apply,
        },
        "checks": checks,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    async def _run_and_close() -> dict[str, Any]:
        try:
            return await run_async(args)
        finally:
            await exchange_adapter_registry.close_all()

    return asyncio.run(_run_and_close())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repair crypto_klines from exchange historical candles.")
    parser.add_argument("--exchange", default="hyperliquid")
    parser.add_argument("--exchanges", default="", help="Comma-separated exchanges, e.g. hyperliquid,binance")
    parser.add_argument("--symbol", default="BTC")
    parser.add_argument("--period", default="1m")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols, e.g. BTC,ETH,SOL")
    parser.add_argument("--periods", default="", help="Comma-separated periods, e.g. 1m,5m")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--fetch-timeout", type=int, default=30, help="Seconds before one exchange/symbol/period fetch is skipped.")
    parser.add_argument("--volume-tolerance", type=float, default=0.01)
    parser.add_argument("--closed-only", action="store_true", default=True)
    parser.add_argument("--include-open", dest="closed_only", action="store_false")
    parser.add_argument(
        "--settle-periods",
        type=int,
        default=3,
        help="Skip the most recent N closed periods because some exchanges revise just-closed candles.",
    )
    parser.add_argument(
        "--settle-seconds",
        type=int,
        default=3600,
        help="Skip candles newer than this many seconds; takes precedence when larger than settle-periods.",
    )
    parser.add_argument("--apply", action="store_true")
    return parser


def main() -> int:
    report = run(build_parser().parse_args())
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
