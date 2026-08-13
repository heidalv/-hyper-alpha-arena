"""交易对快速因子策略选择器（AI 选币后 10 分钟内产出候选）。

对单个交易对扫描：
  period ∈ {5m, 1h, 4h} × factor_set ∈ {hybrid, meanrev, breakout} × threshold ∈ {0.3,0.5,0.7}
复用 kline_factor_backtest，应用硬门禁：
  n>=100；PF>=1.0；前后半段 PF>=0.95；t>1.0 → pass
  PF>=1.0 且前后半段 PF>=0.95 但 t<=1.0 → promising

结果写 pair_strategy_candidates（不自动上线；启用必须人工/审批）。

用法：python scripts/pair_strategy_selector.py --symbol BTC
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parents[1]
EXCHANGE = "asterdex"

PERIOD_CONFIGS = {
    "5m": {"days": 60, "sl": 0.005, "tp": 0.01, "max_hold": 24, "cooldown": 6, "warmup": 120},
    "1h": {"days": 180, "sl": 0.01, "tp": 0.02, "max_hold": 12, "cooldown": 3, "warmup": 120},
    "4h": {"days": 365, "sl": 0.02, "tp": 0.04, "max_hold": 6, "cooldown": 2, "warmup": 120},
}
FACTOR_SETS = ["hybrid", "meanrev", "breakout"]
THRESHOLDS = [0.3, 0.5, 0.7]


def _ensure_table() -> None:
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    from sqlalchemy import text

    with system_identity():
        with SessionLocal() as db:
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS pair_strategy_candidates ("
                " id BIGSERIAL PRIMARY KEY,"
                " symbol VARCHAR(32) NOT NULL,"
                " period VARCHAR(8) NOT NULL,"
                " factor_set VARCHAR(16) NOT NULL,"
                " params_json JSONB NOT NULL,"
                " metrics_json JSONB NOT NULL,"
                " gate_verdict VARCHAR(24) NOT NULL,"
                " generated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            ))
            db.commit()


def _verdict(metrics: Dict[str, Any]) -> str:
    total = metrics.get("total") or {}
    older = metrics.get("older_half") or {}
    newer = metrics.get("newer_half") or {}
    n = int(total.get("n", 0) or 0)
    pf = total.get("profit_factor")
    t = total.get("t_stat")
    older_pf = older.get("profit_factor")
    newer_pf = newer.get("profit_factor")
    if (
        n >= 100 and pf is not None and pf >= 1.0
        and older_pf is not None and older_pf >= 0.95
        and newer_pf is not None and newer_pf >= 0.95
    ):
        return "pass" if (t is not None and t > 1.0) else "promising"
    return "fail"


def main() -> int:
    ap = argparse.ArgumentParser(description="交易对快速策略选择器")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--periods", default="5m,1h,4h")
    ap.add_argument("--ensure-data", action="store_true", help="数据不足自动回填")
    args = ap.parse_args()
    symbol = args.symbol.upper()
    periods = [p.strip() for p in args.periods.split(",") if p.strip()]

    from backend.services.scalp.pair_selector import run_pair_selector

    report = run_pair_selector(
        symbol, periods=periods, ensure_data_first=args.ensure_data,
    )
    out_dir = ROOT / "reports" / "scalp_pair_selector"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ("pair_selector_%s_%s.json" % (symbol, datetime.now(timezone.utc).strftime("%Y-%m-%d")))
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("候选扫描完成: symbol=%s pass=%d" % (symbol, report.get("n_pass", 0)))
    for c in report["candidates"]:
        if c["verdict"] in ("pass", "promising"):
            print(" ", c["period"], c["factor_set"], c["params"]["threshold"], c["verdict"],
                  "PF=", c["metrics"].get("profit_factor"), "t=", c["metrics"].get("t_stat"))
    print("报告已保存:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
