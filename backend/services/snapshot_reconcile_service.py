"""DecisionSnapshot.executed 与 paper 成交对账。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def reconcile_recent_snapshots(hours: int = 24, limit: int = 500) -> Dict[str, Any]:
    """将 analytics snapshots 与 paper 持仓对齐，回写 executed。"""
    from backend.database.connection import AnalyticsSessionLocal, SessionLocal
    from backend.database.models import DecisionSnapshot, PaperPosition

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    adb = AnalyticsSessionLocal()
    main_db = SessionLocal()
    updated = 0
    checked = 0
    mismatches: List[dict] = []

    try:
        rows = (
            adb.query(DecisionSnapshot)
            .filter(DecisionSnapshot.timestamp >= since)
            .filter(DecisionSnapshot.action.in_(["buy", "sell"]))
            .order_by(DecisionSnapshot.timestamp.desc())
            .limit(limit)
            .all()
        )
        for snap in rows:
            checked += 1
            sym = (snap.symbol or "").upper()
            if not sym:
                continue
            if getattr(snap, "executed", None):
                continue

            pos_open = (
                main_db.query(PaperPosition)
                .filter(PaperPosition.symbol == sym, PaperPosition.status == "open")
                .first()
            )
            matched = bool(pos_open)
            if matched and hasattr(snap, "executed"):
                snap.executed = True
                if hasattr(snap, "execution_channel") and not snap.execution_channel:
                    snap.execution_channel = "paper"
                updated += 1
            elif snap.evaluate_verdict_json and not (snap.evaluate_verdict_json or {}).get("allowed", True):
                pass
            else:
                mismatches.append({
                    "id": snap.id,
                    "symbol": sym,
                    "action": snap.action,
                    "proposal_id": getattr(snap, "proposal_id", None),
                })

        if updated:
            adb.commit()
    except Exception as err:
        logger.warning("[Reconcile] 失败: %s", err)
        try:
            adb.rollback()
        except Exception:
            pass
    finally:
        adb.close()
        main_db.close()

    return {
        "checked": checked,
        "updated_executed": updated,
        "unmatched_open_proposals": len(mismatches),
        "mismatches_sample": mismatches[:20],
    }
