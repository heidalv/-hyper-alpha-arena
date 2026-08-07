"""RebateBacktestRunner — 基于历史 outcome 的轻量回测。"""

from __future__ import annotations

from typing import Any, Dict

from backend.database.connection import SessionLocal
from backend.database.models import RebatePerformanceLogDB, RebateTradeOutcomeDB
from backend.services.rebate_arb.schema import ensure_rebate_schema


class RebateBacktestRunner:
    def run(self, strategy_type: str = "S8", limit: int = 500) -> Dict[str, Any]:
        sid = (strategy_type or "S8").upper()
        ensure_rebate_schema()
        db = SessionLocal()
        try:
            outcomes = (
                db.query(RebateTradeOutcomeDB)
                .filter(RebateTradeOutcomeDB.strategy_type == sid)
                .order_by(RebateTradeOutcomeDB.id.desc())
                .limit(limit)
                .all()
            )
            if not outcomes:
                # Backfill from legacy performance logs for old data.
                logs = (
                    db.query(RebatePerformanceLogDB)
                    .filter(RebatePerformanceLogDB.strategy_type == sid)
                    .order_by(RebatePerformanceLogDB.id.desc())
                    .limit(limit)
                    .all()
                )
                samples = [
                    {
                        "net": float(x.total_pnl or 0) + float(x.total_rebate or 0),
                        "points": float(x.total_points or 0),
                        "hold_hours": float(x.hold_hours or 0),
                    }
                    for x in logs
                ]
            else:
                samples = [
                    {
                        "net": float(x.net_value or 0),
                        "points": float(x.points or 0),
                        "hold_hours": float(x.hold_hours or 0),
                    }
                    for x in outcomes
                ]
        finally:
            db.close()

        count = len(samples)
        wins = sum(1 for x in samples if x["net"] > 0)
        net_value = sum(x["net"] for x in samples)
        avg_hold = sum(x["hold_hours"] for x in samples) / count if count else 0
        win_rate = wins / count if count else 0

        recommendation = "keep"
        if count >= 5 and win_rate < 0.45:
            recommendation = "reduce_size"
        elif count >= 5 and win_rate > 0.65 and net_value > 0:
            recommendation = "increase_size_paper_only"

        return {
            "success": True,
            "strategy_type": sid,
            "sample_count": count,
            "win_rate": win_rate,
            "net_value": net_value,
            "avg_hold_hours": avg_hold,
            "recommendation": recommendation,
            "proposal": {
                "params": {
                    "max_position_multiplier": 0.8 if recommendation == "reduce_size" else 1.1,
                },
                "requires_paper_validation": True,
                "requires_manual_live_confirm": True,
            },
        }


rebate_backtest_runner = RebateBacktestRunner()
