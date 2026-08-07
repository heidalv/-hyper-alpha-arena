"""Champion 暂停策略自动恢复 — 10min tick。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_COOLDOWN_HOURS = 6


def _strategy_tags(strategy) -> List[str]:
    genome = getattr(strategy, "genome", None) or {}
    if not isinstance(genome, dict):
        return []
    return list(genome.get("tags") or [])


def run_champion_recovery(db) -> Dict[str, Any]:
    from backend.database.models import AIStrategy, StrategyMemory
    from backend.database.connection import sqlite_write_commit

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    resumed = 0
    scanned = 0

    rows = (
        db.query(AIStrategy)
        .filter(AIStrategy.status == "paused")
        .all()
    )
    for strat in rows:
        tags = _strategy_tags(strat)
        if "champion_protected" not in tags:
            continue
        scanned += 1
        genome = dict(strat.genome or {})
        last_resume = genome.get("champion_last_resume_at")
        if last_resume:
            try:
                lr = datetime.fromisoformat(str(last_resume).replace("Z", "+00:00"))
                if lr.tzinfo is None:
                    lr = lr.replace(tzinfo=timezone.utc)
                if (now - lr).total_seconds() < _COOLDOWN_HOURS * 3600:
                    continue
            except Exception:
                pass

        mem = db.query(StrategyMemory).filter(
            StrategyMemory.strategy_id == strat.strategy_id
        ).first()
        if not mem or (mem.total_trades or 0) < 10:
            continue
        if (mem.win_rate or 0) < 0.50:
            continue
        if (mem.max_drawdown or 1.0) > 0.18:
            continue

        # ── 整改#19：MAP-Elites 行为格提示（恢复时记录当前 regime 对应 elite）──
        try:
            import os as _os
            if _os.getenv("MAP_ELITES_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on"):
                from backend.services.learning_core.map_elites_archive import get_archive, BehaviorDescriptor
                _tags = _strategy_tags(strat)
                _regime = next((t.replace("regime_", "") for t in _tags if t.startswith("regime_")), "ranging")
                _elite = get_archive().select_elite(BehaviorDescriptor.from_market(_regime, "mid"))
                if _elite:
                    genome["map_elites_hint"] = {
                        "regime": _regime,
                        "elite_fitness": _elite.fitness,
                        "behavior": _elite.behavior.key(),
                    }
                    logger.info(
                        "[ChampionRecovery][MAP-Elites#19] %s 恢复 regime=%s elite_fitness=%.3f",
                        strat.strategy_id, _regime, _elite.fitness,
                    )
        except Exception as _me_err:
            logger.debug("[ChampionRecovery][MAP-Elites#19] 跳过: %s", _me_err)

        strat.status = "active"
        genome["champion_last_resume_at"] = now.isoformat()
        genome.pop("pause_reason", None)
        strat.genome = genome
        resumed += 1
        logger.info("[ChampionRecovery] resume %s wr=%.2f", strat.strategy_id, mem.win_rate or 0)

    if resumed:
        sqlite_write_commit(db)
    return {"scanned": scanned, "resumed": resumed}
