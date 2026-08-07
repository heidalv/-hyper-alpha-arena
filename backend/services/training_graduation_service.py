"""训练期策略自动毕业 / 降级扫描。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _has_validated_proposal(db, strategy_id: str) -> bool:
    from backend.database.models import OpenCodeEvolutionProposalDB

    rows = (
        db.query(OpenCodeEvolutionProposalDB)
        .filter(OpenCodeEvolutionProposalDB.status == "paper_validated")
        .all()
    )
    for row in rows:
        try:
            after = json.loads(row.after_json or "{}")
            if after.get("verdict") == "degraded":
                continue
        except Exception:
            pass
        return True
    return len(rows) > 0


def _p0_health_ok() -> bool:
    try:
        from backend.services.health_snapshot_service import build_health_snapshot

        snap = build_health_snapshot(timeout_sec=3.0)
        return bool(snap.get("overall_ok", True))
    except Exception:
        return True


def scan_graduation(db) -> Dict[str, Any]:
    from backend.database.models import AIStrategy, StrategyMemory, FullAutoSession
    from backend.database.connection import sqlite_write_commit
    from backend.services.training_phase_service import (
        set_graduation_status,
        enqueue_graduation,
        is_active,
    )
    from backend.services.training_audit import log_training_event, write_graduation_report

    if not is_active():
        return {"skipped": "training_inactive"}

    session = db.query(FullAutoSession).filter(FullAutoSession.status == "running").first()
    if not session:
        return {"skipped": "no_running_session"}

    graduated = 0
    rejected = 0
    candidates = 0
    acct = session.paper_account_id or session.account_id
    strats = db.query(AIStrategy).filter(
        AIStrategy.account_id == acct,
        AIStrategy.status.in_(["active", "paused"]),
    ).all()

    for strat in strats:
        mem = db.query(StrategyMemory).filter(
            StrategyMemory.strategy_id == strat.strategy_id
        ).first()
        if not mem:
            continue
        sid = strat.strategy_id
        trades = int(mem.total_trades or 0)
        wr = float(mem.win_rate or 0)
        dd = float(mem.max_drawdown or 0)

        if trades >= 15 and wr >= 0.42 and dd <= 0.20:
            set_graduation_status(sid, "candidate", trades=trades, wr=wr, dd=dd)
            candidates += 1

        if (
            trades >= 20
            and wr >= 0.45
            and dd <= 0.18
            and _has_validated_proposal(db, sid)
            and _p0_health_ok()
        ):
            set_graduation_status(sid, "graduated", trades=trades, wr=wr, dd=dd)
            enqueue_graduation(sid)
            genome = dict(strat.genome or {})
            tags = list(genome.get("tags") or [])
            if "golden_frozen" not in tags:
                tags.append("golden_frozen")
            genome["tags"] = tags
            genome["graduation_status"] = "graduated"
            genome["graduated_at"] = datetime.now(timezone.utc).isoformat()
            strat.genome = genome
            strat.learning_enabled = False
            report_path = write_graduation_report(sid, {
                "strategy_id": sid,
                "trades": trades,
                "win_rate": wr,
                "max_drawdown": dd,
                "graduated_at": genome["graduated_at"],
            })
            log_training_event("graduated", strategy_id=sid, report=report_path)
            graduated += 1
            continue

        if trades >= 20 and wr < 0.35 and dd > 0.25:
            set_graduation_status(sid, "rejected", trades=trades, wr=wr, dd=dd)
            genome = dict(strat.genome or {})
            tags = [t for t in (genome.get("tags") or []) if t != "golden_frozen"]
            genome["tags"] = tags
            genome["graduation_status"] = "rejected"
            strat.genome = genome
            log_training_event("graduation_rejected", strategy_id=sid, wr=wr, dd=dd)
            rejected += 1

    if graduated or rejected:
        sqlite_write_commit(db)
    return {"graduated": graduated, "rejected": rejected, "candidates": candidates}
