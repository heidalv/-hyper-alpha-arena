"""Rule Sync Scheduler — 定时抓取六所规则源。"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)

RULE_SYNC_JOB_ID = "rebate_rule_sync_fetch_all"
DEFAULT_INTERVAL_SECONDS = 6 * 60 * 60


def is_rule_sync_enabled() -> bool:
    return os.getenv("RULE_SYNC_ENABLED", "true").lower() not in ("false", "0", "no")


def get_rule_sync_interval_seconds() -> int:
    raw = os.getenv("RULE_SYNC_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS))
    try:
        return max(900, int(raw))
    except Exception:
        return DEFAULT_INTERVAL_SECONDS


def run_rule_sync_fetch_all() -> Dict[str, Any]:
    """Synchronous task body used by APScheduler."""
    if not is_rule_sync_enabled():
        return {"success": False, "skipped": True, "reason": "RULE_SYNC_ENABLED=false"}
    try:
        from backend.services.rebate_arb.rule_change_detector import rule_change_detector
        result = rule_change_detector.fetch_all_sources()
        logger.info(
            "[RuleSyncScheduler] fetch_all done: changed=%s failed=%s",
            result.get("changed_count"),
            result.get("failed_count"),
        )

        # S8 symbol boost 动态刷新（官方 boost 每期会变，best-effort）
        try:
            from backend.services.rebate_arb.symbol_boost_store import refresh_from_exchange

            result["symbol_boost_refreshed"] = refresh_from_exchange()
        except Exception as boost_exc:
            logger.debug("[RuleSyncScheduler] symbol boost refresh skip: %s", boost_exc)

        return result
    except Exception as e:
        logger.warning("[RuleSyncScheduler] fetch_all failed: %s", e)
        return {"success": False, "error": str(e)}


def schedule_rule_sync_task(task_scheduler) -> bool:
    """Register Rule Sync polling task on the shared scheduler."""
    if not is_rule_sync_enabled():
        logger.info("[RuleSyncScheduler] disabled by RULE_SYNC_ENABLED=false")
        return False
    interval = get_rule_sync_interval_seconds()
    task_scheduler.add_interval_task(
        task_func=run_rule_sync_fetch_all,
        interval_seconds=interval,
        task_id=RULE_SYNC_JOB_ID,
        max_instances=1,
    )
    logger.info("[RuleSyncScheduler] registered every %ss", interval)
    return True
