"""V5 决策核心 — 离线回放对比 + 验收指标检查。

用法（项目根目录执行）：
    backend\\.venv\\Scripts\\python.exe scripts\\v5_acceptance_check.py [--days 14]

两部分输出：
1. 离线回放对比：按当前 runtime tier 配额（短线+中长线）模拟频率治理。
2. 验收指标：fee/gross、平均盈亏、日交易对照配额、最大单笔亏损占权益。

说明（2026-08-02）：
  模拟盘日开仓可刻意放大攒样本；日交易验收线读取 scalp_daily_cap+trend_daily_cap，
  不再写死 ≤10 笔。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))


def _tier_caps():
    """读取生效的短线/中长线日配额（与 unified_gate 同源）。"""
    try:
        from backend.services.runtime_tuning_store import get_tuning_int
        from backend.config.settings import SCALP_DAILY_OPEN_CAP, TREND_DAILY_OPEN_CAP

        scalp = max(0, int(get_tuning_int("scalp_daily_cap", SCALP_DAILY_OPEN_CAP)))
        trend = max(0, int(get_tuning_int("trend_daily_cap", TREND_DAILY_OPEN_CAP)))
    except Exception:
        scalp, trend = 150, 15
    return scalp, trend, scalp + trend


def load_orders(days: int):
    from backend.database.connection import SessionLocal
    from backend.database.models import PaperOrder

    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        rows = (
            db.query(PaperOrder)
            .filter(PaperOrder.status == "filled", PaperOrder.created_at >= cutoff)
            .order_by(PaperOrder.created_at.asc())
            .all()
        )
        return [
            {
                "symbol": o.symbol,
                "side": o.side,
                "fee": float(o.fee or 0),
                "pnl": float(o.pnl or 0) if o.pnl is not None else None,
                "close_reason": o.close_reason,
                "trade_nature": o.trade_nature,
                "created_at": o.created_at,
                "account_id": o.account_id,
            }
            for o in rows
        ]
    finally:
        db.close()


def get_equity():
    from backend.database.connection import SessionLocal
    from backend.database.models import PaperBalance

    db = SessionLocal()
    try:
        rows = db.query(PaperBalance).all()
        return sum(float(r.total_equity or 0) for r in rows) or 500000.0
    finally:
        db.close()


def replay_compare(orders, daily_cap=None, symbol_cap=None):
    """按时间重放：开仓单超出频率额度的，连同其对应平仓单一起剔除。"""
    if daily_cap is None or symbol_cap is None:
        _, _, combined = _tier_caps()
        try:
            from backend.config.settings import get_v5_max_symbol_trades_per_day
            symbol_cap = int(get_v5_max_symbol_trades_per_day("paper"))
        except Exception:
            symbol_cap = 12
        daily_cap = int(combined)

    day_opens = defaultdict(int)
    sym_opens = defaultdict(int)
    open_budget = defaultdict(int)

    kept, blocked = [], []
    for o in orders:
        d = o["created_at"].date()
        acc = o["account_id"]
        if o["close_reason"] is None:
            over_daily = day_opens[(d, acc)] >= daily_cap
            over_symbol = sym_opens[(d, acc, o["symbol"])] >= symbol_cap
            if over_daily or over_symbol:
                blocked.append(o)
                continue
            day_opens[(d, acc)] += 1
            sym_opens[(d, acc, o["symbol"])] += 1
            open_budget[(acc, o["symbol"])] += 1
            kept.append(o)
        else:
            if open_budget[(acc, o["symbol"])] > 0:
                open_budget[(acc, o["symbol"])] -= 1
                kept.append(o)
            else:
                blocked.append(o)

    def agg(rows):
        closes = [r for r in rows if r["close_reason"] is not None]
        fees = sum(r["fee"] for r in rows)
        pnl = sum((r["pnl"] or 0) for r in closes)
        return {
            "orders": len(rows),
            "closes": len(closes),
            "gross_pnl": round(pnl, 2),
            "fees": round(fees, 2),
            "net_pnl": round(pnl - fees, 2),
        }

    return {
        "baseline_old_pipeline": agg(orders),
        "v5_frequency_governed": agg(kept),
        "blocked_orders": len(blocked),
        "sim_daily_cap": daily_cap,
        "sim_symbol_cap": symbol_cap,
        "note": "按 runtime 短线+中长线配额合计模拟；V5 实际还叠加盈亏比/费用/regime",
    }


def acceptance(orders, equity):
    closes = [o for o in orders if o["close_reason"] is not None and o["pnl"] is not None]
    if not closes:
        return {"error": "无平仓数据"}

    scalp_cap, trend_cap, combined_cap = _tier_caps()
    fees = sum(o["fee"] for o in orders)
    wins = [o["pnl"] - o["fee"] for o in closes if (o["pnl"] - o["fee"]) > 0]
    losses = [abs(o["pnl"] - o["fee"]) for o in closes if (o["pnl"] - o["fee"]) <= 0]
    gross_win = sum(wins) + fees
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0

    opens_by_day = defaultdict(int)
    for o in orders:
        if o["close_reason"] is None:
            opens_by_day[o["created_at"].date()] += 1
    max_daily = max(opens_by_day.values()) if opens_by_day else 0
    avg_daily = (sum(opens_by_day.values()) / len(opens_by_day)) if opens_by_day else 0

    max_single_loss = max(losses) if losses else 0
    fee_gross = fees / gross_win if gross_win > 0 else None

    checks = {
        "fee_gross_le_10pct": {
            "value": round(fee_gross, 4) if fee_gross is not None else None,
            "target": "≤0.10",
            "pass": fee_gross is not None and fee_gross <= 0.10,
        },
        "avg_loss_le_avg_win": {
            "value": f"avg_win={avg_win:.0f} avg_loss={avg_loss:.0f}",
            "target": "avg_loss ≤ avg_win",
            "pass": avg_loss <= avg_win,
        },
        "daily_trades_within_tier_caps": {
            "value": f"max={max_daily} avg={avg_daily:.1f}",
            "target": f"≤{combined_cap}/day (scalp={scalp_cap}+trend={trend_cap})",
            "pass": max_daily <= combined_cap,
            "informational": True,
        },
        "max_single_loss_le_1_5pct_equity": {
            "value": f"${max_single_loss:.0f} ({max_single_loss / equity:.2%} of ${equity:.0f})",
            "target": "≤1.5%",
            "pass": max_single_loss <= equity * 0.015,
        },
    }
    hard = {
        k: v for k, v in checks.items()
        if isinstance(v, dict) and not v.get("informational")
    }
    checks["QUALITY_PASS"] = all(c["pass"] for c in hard.values())
    checks["ALL_PASS"] = all(
        c["pass"] for c in checks.values() if isinstance(c, dict) and "pass" in c
    )
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()

    scalp_cap, trend_cap, combined = _tier_caps()
    orders = load_orders(args.days)
    equity = get_equity()
    print(f"加载近 {args.days} 天订单: {len(orders)} 条, 当前总权益 ${equity:,.0f}")
    print(f"生效配额: scalp_daily_cap={scalp_cap} trend_daily_cap={trend_cap} 合计≤{combined}/日\n")

    print("══════ 1. 离线回放：旧管线 vs V5 频率治理 ══════")
    print(json.dumps(replay_compare(orders), ensure_ascii=False, indent=2))

    print("\n══════ 2. 验收指标（基于实际成交） ══════")
    print(json.dumps(acceptance(orders, equity), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
