"""5m 数据完整性检查（M1，只读）。

按候选币（scalp_signal_log 近 60 天活跃币 + 主流通币）统计：
- 覆盖天数 / 应有 K 线数 / 实际去重 K 线数
- 完整性 % = 实际 / 应有（按数据实际最早时间戳起算，避免把上市前算缺）
- 最大缺口分钟数

输出：reports/scalp_data/scalp_data_quality_YYYY-MM-DD.json
用法：
    python -m backend.services.scalp.scalp_data_quality --days 60 --exchange asterdex
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]

MAJORS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "SUI"]


def _active_symbols(days: int = 60) -> List[str]:
    from backend.database.connection import SessionLocal
    from backend.core.tenant import system_identity

    start = int((datetime.now(timezone.utc).timestamp()) - days * 86400)
    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(
                text(
                    "SELECT symbol, COUNT(*) AS n FROM scalp_signal_log "
                    "WHERE signal_ts >= :start GROUP BY symbol ORDER BY n DESC"
                ),
                {"start": start},
            ).mappings().all()
    syms = [str(r["symbol"]) for r in rows]
    for m in MAJORS:
        if m not in syms:
            syms.append(m)
    return syms


def run_scalp_data_quality(days: int = 60,
                           exchange: Optional[str] = None) -> Dict[str, Any]:
    from backend.database.connection import MarketSessionLocal
    from backend.core.tenant import system_identity

    symbols = _active_symbols(days)
    now = datetime.now(timezone.utc)
    start_ts = int(now.timestamp()) - days * 86400
    expected_total = days * 288
    report: Dict[str, Any] = {
        "generated_at": now.isoformat(),
        "days": days,
        "period": "5m",
        "exchange": exchange,
        "expected_bars_per_symbol": expected_total,
        "symbols": {},
    }
    ph = ",".join(":s%d" % i for i in range(len(symbols)))
    params = {"start": start_ts}
    params.update({"s%d" % i: s for i, s in enumerate(symbols)})
    ex_clause = ""
    if exchange:
        params["ex"] = exchange
        ex_clause = " AND exchange = :ex"

    with system_identity():
        with MarketSessionLocal() as db:
            rows = db.execute(
                text(
                    "SELECT symbol, COUNT(DISTINCT timestamp) AS n, "
                    "MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts "
                    "FROM crypto_klines "
                    "WHERE period = '5m' AND symbol IN (%s) "
                    "AND timestamp >= :start%s "
                    "GROUP BY symbol" % (ph, ex_clause)
                ),
                params,
            ).mappings().all()
            for r in rows:
                sym = str(r["symbol"])
                n = int(r["n"] or 0)
                first_ts = int(r["first_ts"] or 0)
                last_ts = int(r["last_ts"] or 0)
                span_days = max(1.0, (last_ts - first_ts) / 86400.0)
                expected = int(span_days * 288) + 1
                completeness = min(100.0, n / max(1, expected) * 100.0) if expected else 0.0
                # 最大缺口：按相邻时间戳间隔
                max_gap_min = None
                if n >= 2:
                    ts_rows = db.execute(
                        text(
                            "SELECT timestamp FROM crypto_klines "
                            "WHERE period='5m' AND symbol=:s%s "
                            "AND timestamp >= :start ORDER BY timestamp" % ex_clause
                        ),
                        {"s": sym, "start": start_ts, **({"ex": exchange} if exchange else {})},
                    ).scalars().all()
                    gaps = [
                        int(b) - int(a)
                        for a, b in zip(ts_rows, ts_rows[1:])
                        if int(b) - int(a) > 360
                    ]
                    max_gap_min = max(gaps) // 60 if gaps else 0
                report["symbols"][sym] = {
                    "bars": n,
                    "first_ts": first_ts,
                    "last_ts": last_ts,
                    "span_days": round(span_days, 2),
                    "completeness_pct": round(completeness, 2),
                    "max_gap_min": max_gap_min,
                }

    ok = [s for s, d in report["symbols"].items() if d["completeness_pct"] >= 99.0]
    report["n_symbols"] = len(report["symbols"])
    report["n_complete_ge99"] = len(ok)
    report["complete_ratio"] = round(len(ok) / max(1, len(report["symbols"])), 4)

    out_dir = _ROOT / "reports" / "scalp_data"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ("scalp_data_quality_%s.json" % now.strftime("%Y-%m-%d"))
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("数据完整性报告: %s", path)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="scalp 5m 数据完整性")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--exchange", default=None)
    args = ap.parse_args()
    rep = run_scalp_data_quality(days=args.days, exchange=args.exchange)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
