"""纸盘 scalp 成本审计（M0）。

对比 paper_orders 实际成交价与 5m K 线收盘参考价，估算：
- 滑点 bps（按方向符号化：买贵为正、卖低为正）
- 手续费 bps
- P50/P90/P95/MAX 分位数，供回测悲观成本参数（P95×1.5）

用法（仓库根目录）：
    python scripts/audit_scalp_cost.py
    python scripts/audit_scalp_cost.py --days 60
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("audit_scalp_cost")

ROOT = Path(__file__).resolve().parents[1]


def _quantile(vals: List[float], q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, int(q * len(s))))
    return s[idx]


def _load_kline_close(symbols: List[str], start_dt: datetime, end_dt: datetime) -> Dict[str, Dict[int, float]]:
    from backend.database.connection import MarketSessionLocal
    from backend.core.tenant import system_identity

    out: Dict[str, Dict[int, float]] = defaultdict(dict)
    if not symbols:
        return out
    placeholders = ",".join(":sym_%d" % i for i in range(len(symbols)))
    params = {"start": int(start_dt.timestamp()), "end": int(end_dt.timestamp())}
    params.update({"sym_%d" % i: s for i, s in enumerate(symbols)})
    with system_identity():
        with MarketSessionLocal() as db:
            rows = db.execute(
                text(
                    "SELECT symbol, timestamp, close_price FROM crypto_klines "
                    "WHERE period = '5m' AND symbol IN (%s) "
                    "AND timestamp >= :start AND timestamp <= :end "
                    "ORDER BY timestamp"
                    % placeholders
                ),
                params,
            ).mappings().all()
    for r in rows:
        try:
            out[str(r["symbol"])][int(r["timestamp"])] = float(r["close_price"])
        except Exception:
            continue
    return out


def audit(days: int = 30) -> dict:
    from backend.database.connection import SessionLocal
    from backend.core.tenant import system_identity

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)
    slippage_bps: List[float] = []
    slippage_abs_bps: List[float] = []
    fee_bps: List[float] = []
    total_fee = 0.0
    total_notional = 0.0
    matched = 0
    unmatched = 0
    n_anomalies = 0

    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(
                text(
                    "SELECT symbol, side, quantity, filled_price, fee, filled_at "
                    "FROM paper_orders "
                    "WHERE trade_nature = 'scalp' AND status = 'filled' "
                    "AND filled_price IS NOT NULL AND filled_at IS NOT NULL "
                    "AND created_at >= :start AND created_at <= :end"
                ),
                {"start": start_dt, "end": end_dt},
            ).mappings().all()

    symbols = sorted({str(r["symbol"]) for r in rows})
    closes = _load_kline_close(symbols, start_dt - timedelta(days=1), end_dt)

    for r in rows:
        symbol = str(r["symbol"])
        side = str(r["side"] or "").lower()
        try:
            filled_price = float(r["filled_price"])
            quantity = float(r["quantity"])
            fee = float(r["fee"] or 0.0)
            filled_at = r["filled_at"]
            if filled_at is None:
                continue
            # 参考价取成交前一根已收盘 5m K 线（避免用未来收盘价虚高滑点）
            bucket = int(filled_at.timestamp() / 300) * 300 - 300
        except Exception:
            unmatched += 1
            continue
        ref = closes.get(symbol, {}).get(bucket)
        if ref is None:
            unmatched += 1
            continue
        if ref <= 0 or filled_price <= 0 or quantity <= 0:
            unmatched += 1
            continue
        notional = quantity * filled_price
        total_notional += notional
        total_fee += fee
        if notional > 0:
            fee_bps.append(fee / notional * 1e4)
        matched += 1
        if side in ("buy", "long"):
            slip = (filled_price / ref - 1.0) * 1e4
        elif side in ("sell", "short"):
            slip = (ref / filled_price - 1.0) * 1e4
        else:
            slip = 0.0
        if not math.isfinite(slip) or abs(slip) > 200.0:
            # 价差超过 2%：视为脏数据/换币/口径错位，不计入成本分位数
            n_anomalies += 1
            continue
        slippage_bps.append(slip)
        slippage_abs_bps.append(abs(slip))

    def _p95x15(vals: List[float]) -> float:
        if not vals:
            return 0.0
        return round(_quantile(vals, 0.95) * 1.5, 4)

    report = {
        "generated_at": end_dt.isoformat(),
        "days": days,
        "n_orders": len(rows),
        "n_matched": matched,
        "n_unmatched": unmatched,
        "n_anomalies": n_anomalies,
        "total_notional_usd": round(total_notional, 2),
        "total_fee_usd": round(total_fee, 4),
        "fee_bps": {
            "p50": round(_quantile(fee_bps, 0.50), 4),
            "p90": round(_quantile(fee_bps, 0.90), 4),
            "p95": round(_quantile(fee_bps, 0.95), 4),
            "max": round(max(fee_bps), 4) if fee_bps else 0.0,
            "mean": round(sum(fee_bps) / len(fee_bps), 4) if fee_bps else 0.0,
        },
        "slippage_bps": {
            "p50": round(_quantile(slippage_bps, 0.50), 4),
            "p90": round(_quantile(slippage_bps, 0.90), 4),
            "p95": round(_quantile(slippage_bps, 0.95), 4),
            "max": round(max(slippage_bps), 4) if slippage_bps else 0.0,
            "mean": round(sum(slippage_bps) / len(slippage_bps), 4) if slippage_bps else 0.0,
            "abs_p95": round(_quantile(slippage_abs_bps, 0.95), 4),
            "abs_mean": round(sum(slippage_abs_bps) / len(slippage_abs_bps), 4) if slippage_abs_bps else 0.0,
        },
        "pessimistic_params_for_backtest": {
            "slippage_bps_p95x15": _p95x15(slippage_abs_bps),
            "fee_bps_p95": round(_quantile(fee_bps, 0.95), 4),
        },
    }
    out_dir = ROOT / "reports" / "scalp_cost"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ("scalp_cost_audit_%s.json" % end_dt.strftime("%Y-%m-%d"))
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("成本审计完成: %s", path)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="scalp 纸盘成本审计")
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    rep = audit(days=args.days)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
