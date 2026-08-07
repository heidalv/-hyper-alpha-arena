#!/usr/bin/env python3
"""
Market data baseline probe.

This script measures the current market-data stack before the high-throughput
architecture is introduced. Defaults are read-only and safe for a running dev
environment.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_PERIODS = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL", "BNB"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def timed(label: str, fn: Callable[[], Any]) -> dict[str, Any]:
    start = time.perf_counter()
    ok = True
    error = None
    value: Any = None
    try:
        value = fn()
    except Exception as exc:  # noqa: BLE001 - baseline should capture failures
        ok = False
        error = f"{type(exc).__name__}: {exc}"
    elapsed_ms = (time.perf_counter() - start) * 1000
    return {
        "label": label,
        "ok": ok,
        "elapsed_ms": round(elapsed_ms, 2),
        "error": error,
        "value": value,
    }


def http_get_json(base_url: str, path: str, timeout: float) -> Any:
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read().decode("utf-8")
    return json.loads(payload)


def compact_api_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if "klines" in value:
        klines = value.get("klines") or []
        return {
            "count": len(klines),
            "first_ts": klines[0].get("timestamp") if klines else None,
            "last_ts": klines[-1].get("timestamp") if klines else None,
            "last_close": klines[-1].get("close") if klines else None,
        }
    if "periods" in value:
        periods = value.get("periods") or {}
        return {
            "overall_status": value.get("overall_status"),
            "periods": {
                p: {
                    "status": h.get("status"),
                    "records": h.get("record_count"),
                    "coverage_pct": h.get("coverage_pct"),
                    "freshness_seconds": h.get("freshness_seconds"),
                    "gap_count": h.get("gap_count"),
                }
                for p, h in periods.items()
            },
        }
    if "data" in value and "total_records" in value:
        return {
            "exchange": value.get("exchange"),
            "total_records": value.get("total_records"),
            "rows": len(value.get("data") or []),
        }
    if "sub_tasks" in value:
        return {
            "status": value.get("status"),
            "exchange": value.get("exchange"),
            "total_tasks": value.get("total_tasks"),
            "completed_tasks": value.get("completed_tasks"),
            "failed_tasks": value.get("failed_tasks"),
            "overall_progress": value.get("overall_progress"),
        }
    if "metrics" in value and "total_count" in value:
        return {
            "total_count": value.get("total_count"),
            "total_failed": value.get("total_failed"),
            "overall_success_rate": value.get("overall_success_rate"),
            "metric_names": sorted((value.get("metrics") or {}).keys()),
        }
    return value


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((pct / 100) * (len(ordered) - 1))))
    return round(ordered[index], 2)


def summarize_latencies(samples: list[dict[str, Any]]) -> dict[str, Any]:
    values = [s["elapsed_ms"] for s in samples if s.get("ok")]
    return {
        "count": len(samples),
        "ok": sum(1 for s in samples if s.get("ok")),
        "failed": sum(1 for s in samples if not s.get("ok")),
        "avg_ms": round(statistics.mean(values), 2) if values else None,
        "p50_ms": percentile(values, 50),
        "p95_ms": percentile(values, 95),
        "max_ms": round(max(values), 2) if values else None,
    }


def measure_api(args: argparse.Namespace) -> dict[str, Any]:
    paths: list[tuple[str, str]] = [
        ("history_progress", "/api/klines/history-sync/progress"),
        ("data_summary", "/api/klines/history-sync/data-summary"),
        ("market_data_metrics", "/api/klines/metrics"),
    ]

    for symbol in args.symbols:
        paths.append((f"health:{symbol}", f"/api/klines/health/{symbol}"))
        for period in args.periods:
            paths.append(
                (
                    f"kline:{symbol}:{period}",
                    f"/api/market/kline-with-indicators/{symbol}?market={args.market}&period={period}&count={args.kline_count}",
                )
            )

    samples = []
    for label, path in paths:
        result = timed(
            label,
            lambda path=path: http_get_json(args.base_url, path, args.http_timeout),
        )
        result["path"] = path
        result["value"] = compact_api_value(result.get("value"))
        samples.append(result)

    return {
        "summary": summarize_latencies(samples),
        "samples": samples,
    }


def measure_db(args: argparse.Namespace) -> dict[str, Any]:
    from sqlalchemy import bindparam
    from sqlalchemy import text

    from backend.database.connection import MarketSessionLocal

    queries: list[tuple[str, str, dict[str, Any]]] = [
        (
            "crypto_klines_total",
            "SELECT COUNT(*) AS c FROM crypto_klines",
            {},
        ),
        (
            "crypto_klines_by_exchange",
            """
            SELECT exchange, COUNT(*) AS c
            FROM crypto_klines
            GROUP BY exchange
            ORDER BY c DESC
            """,
            {},
        ),
        (
            "crypto_klines_recent_by_symbol_period",
            """
            SELECT exchange, symbol, period, COUNT(*) AS c, MAX(timestamp) AS latest_ts
            FROM crypto_klines
            WHERE symbol IN :symbols
            GROUP BY exchange, symbol, period
            ORDER BY exchange, symbol, period
            """,
            {"symbols": tuple(args.symbols)},
        ),
    ]

    samples = []
    with MarketSessionLocal() as db:
        for label, sql, params in queries:
            def _run(sql=sql, params=params):
                stmt = text(sql)
                if "symbols" in params:
                    stmt = stmt.bindparams(bindparam("symbols", expanding=True))
                    params = {**params, "symbols": list(params["symbols"])}
                rows = db.execute(stmt, params).mappings().all()
                return [dict(r) for r in rows[:100]]

            result = timed(label, _run)
            samples.append(result)

    return {
        "summary": summarize_latencies(samples),
        "samples": samples,
    }


def measure_unified_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    from backend.services.unified_data_pool import unified_data_pool

    samples = [
        timed(
            "unified_pool_get_snapshot",
            lambda: _compact_snapshot(unified_data_pool.get_snapshot(max_age=args.snapshot_max_age)),
        )
    ]

    if args.include_snapshot_capture:
        samples.append(
            timed(
                "unified_pool_capture_light_snapshot",
                lambda: _compact_snapshot(
                    unified_data_pool.capture_snapshot(
                        symbols=args.symbols,
                        account_id=None,
                        environment=args.market,
                        include_klines=args.include_snapshot_klines,
                        include_strategy=False,
                        light_mode=True,
                    )
                ),
            )
        )

    return {
        "summary": summarize_latencies(samples),
        "samples": samples,
    }


def _compact_snapshot(snapshot: Any) -> Any:
    if snapshot is None:
        return None
    return {
        "snapshot_id": getattr(snapshot, "snapshot_id", None),
        "timestamp_iso": getattr(snapshot, "timestamp_iso", None),
        "markets": len(getattr(snapshot, "markets", {}) or {}),
        "klines": len(getattr(snapshot, "klines", {}) or {}),
        "indicators": len(getattr(snapshot, "indicators", {}) or {}),
        "data_completeness": getattr(snapshot, "data_completeness", None),
    }


def measure_write_probe(args: argparse.Namespace) -> dict[str, Any]:
    if not args.include_write_probe:
        return {
            "skipped": True,
            "reason": "Pass --include-write-probe to run an isolated temp-table write probe.",
        }

    from sqlalchemy import text

    from backend.database.connection import MarketSessionLocal

    rows = [{"id": i, "payload": f"baseline-{i}", "created_at": int(time.time())} for i in range(args.write_probe_rows)]

    with MarketSessionLocal() as db:
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS market_data_baseline_probe (
                    id INTEGER PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
        )
        db.commit()

        result = timed(
            f"write_probe_insert_{args.write_probe_rows}_rows",
            lambda: _insert_probe_rows(db, rows),
        )

        if args.cleanup_write_probe:
            db.execute(text("DELETE FROM market_data_baseline_probe"))
            db.commit()

    elapsed = result["elapsed_ms"]
    rows_per_second = round(args.write_probe_rows / (elapsed / 1000), 2) if result["ok"] and elapsed else None
    result["rows"] = args.write_probe_rows
    result["rows_per_second"] = rows_per_second
    return result


def _insert_probe_rows(db: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
    from sqlalchemy import text

    db.execute(
        text(
            """
            INSERT OR REPLACE INTO market_data_baseline_probe (id, payload, created_at)
            VALUES (:id, :payload, :created_at)
            """
        ),
        rows,
    )
    db.commit()
    return {"inserted": len(rows)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Measure market-data baseline latency and capacity.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--market", default="hyperliquid")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS), type=lambda v: [s.strip().upper() for s in v.split(",") if s.strip()])
    parser.add_argument("--periods", default=",".join(DEFAULT_PERIODS), type=lambda v: [p.strip() for p in v.split(",") if p.strip()])
    parser.add_argument("--kline-count", type=int, default=100)
    parser.add_argument("--http-timeout", type=float, default=15)
    parser.add_argument("--snapshot-max-age", type=float, default=120)
    parser.add_argument("--include-snapshot-capture", action="store_true")
    parser.add_argument("--include-snapshot-klines", action="store_true")
    parser.add_argument("--include-write-probe", action="store_true")
    parser.add_argument("--write-probe-rows", type=int, default=1000)
    parser.add_argument("--cleanup-write-probe", action="store_true", default=True)
    parser.add_argument("--output", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = {
        "generated_at": now_iso(),
        "base_url": args.base_url,
        "market": args.market,
        "symbols": args.symbols,
        "periods": args.periods,
        "api": measure_api(args),
        "db": measure_db(args),
        "unified_snapshot": measure_unified_snapshot(args),
        "write_probe": measure_write_probe(args),
    }

    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
