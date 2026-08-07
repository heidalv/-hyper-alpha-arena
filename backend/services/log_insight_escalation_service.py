"""ERROR 日志模式自动升级为 OpenCode system_ops insight（Phase 2）。"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SEVERITY_MAP = {"P0": "critical", "P1": "major", "P2": "minor"}
_GEAR_ORDER = ("turbo", "warm", "balanced", "conservative")


def _dedupe_key(logger_name: str, pattern: str) -> str:
    raw = f"{logger_name}|{pattern}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:32]


def _title_for_entry(entry: Dict[str, Any]) -> str:
    logger_name = entry.get("logger") or "unknown"
    sample = (entry.get("sample") or "")[:80]
    if "Run not found" in sample:
        return "QAA tick 反复失败（Run 丢失）"
    if "ForeignKeyViolation" in sample or "strategy_trades" in sample:
        return "学习闭环写入失败（外键）"
    if "All retries exhausted" in sample:
        return f"K线/数据重试耗尽：{logger_name}"
    return f"{logger_name} 反复报错（1h×{entry.get('count', 0)}）"


def _threshold_for_hint(hint: str, *, default: int, p0: int, p1: int) -> int:
    if hint == "P0":
        return p0
    if hint == "P1":
        return p1
    return default


def _has_open_dedupe(db, dedupe: str, *, hours: int = 24) -> bool:
    from backend.database.models import OpenCodeInsightDB

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
    rows = (
        db.query(OpenCodeInsightDB)
        .filter(
            OpenCodeInsightDB.status == "open",
            OpenCodeInsightDB.category == "system_ops",
            OpenCodeInsightDB.created_at >= cutoff,
        )
        .all()
    )
    for row in rows:
        try:
            finding = json.loads(row.finding_json or "{}")
        except Exception:
            finding = {}
        if finding.get("dedupe_key") == dedupe:
            return True
        if dedupe in (row.title or ""):
            return True
    return False


def escalate_log_errors_to_insights(
    db,
    *,
    window_hours: int = 1,
    min_count: int = 10,
    p0_min_count: int = 3,
    p1_min_count: int = 5,
) -> Dict[str, Any]:
    """规则引擎：1h 内高频 ERROR → OpenCodeInsightDB（24h 去重）。"""
    from backend.database.models import OpenCodeInsightDB
    from backend.database.connection import sqlite_write_commit
    from backend.services.log_digest_service import build_digest

    digest = build_digest(window_hours=window_hours)
    created = 0
    skipped = 0
    active_keys: List[str] = []

    for entry in digest.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        count = int(entry.get("count") or 0)
        hint = str(entry.get("severity_hint") or "P2")
        threshold = _threshold_for_hint(
            hint, default=min_count, p0=p0_min_count, p1=p1_min_count,
        )
        if count < threshold:
            continue

        logger_name = str(entry.get("logger") or "unknown")
        pattern = str(entry.get("pattern") or entry.get("sample") or "")
        dedupe = _dedupe_key(logger_name, pattern)
        active_keys.append(dedupe)

        if _has_open_dedupe(db, dedupe):
            skipped += 1
            continue

        sev = _SEVERITY_MAP.get(hint, "minor")
        if hint == "P2" and count >= min_count * 2:
            sev = "major"

        finding = {
            "dedupe_key": dedupe,
            "logger": logger_name,
            "pattern": pattern,
            "count_1h": count,
            "severity_hint": hint,
            "sample": entry.get("sample"),
            "escalation_rule": f"count>={threshold} in {window_hours}h",
        }
        db.add(
            OpenCodeInsightDB(
                window=f"{window_hours}h",
                domain="system",
                severity=sev,
                category="system_ops",
                title=_title_for_entry(entry)[:256],
                finding_json=json.dumps(finding, ensure_ascii=False),
                status="open",
                source="log_escalation",
            )
        )
        created += 1
        logger.info(
            "[LogEscalation] 新建 insight: %s (%s, count=%s)",
            finding.get("dedupe_key"), sev, count,
        )

    if created:
        sqlite_write_commit(db)

    resolved = resolve_recovered_system_ops(db, active_dedupe_keys=active_keys)
    return {
        "window_hours": window_hours,
        "scanned_errors": digest.get("total_errors", 0),
        "created": created,
        "skipped_dedupe": skipped,
        "resolved": resolved,
    }


def resolve_recovered_system_ops(db, *, active_dedupe_keys: List[str]) -> int:
    """ERROR 模式不再出现时，自动 resolve 对应 system_ops insight。"""
    from backend.database.models import OpenCodeInsightDB
    from backend.database.connection import sqlite_write_commit

    active = set(active_dedupe_keys)
    rows = (
        db.query(OpenCodeInsightDB)
        .filter(
            OpenCodeInsightDB.status == "open",
            OpenCodeInsightDB.category == "system_ops",
            OpenCodeInsightDB.source == "log_escalation",
        )
        .all()
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    resolved = 0
    for row in rows:
        try:
            finding = json.loads(row.finding_json or "{}")
        except Exception:
            finding = {}
        key = finding.get("dedupe_key")
        if key and key not in active:
            row.status = "resolved"
            row.resolved_at = now
            resolved += 1
    if resolved:
        sqlite_write_commit(db)
        logger.info("[LogEscalation] 自动 resolve %d 条已恢复 insight", resolved)
    return resolved


def run_health_digest_tick(db) -> Dict[str, Any]:
    """1h 定时：health digest 快照 + ERROR 升级。"""
    from backend.services.health_snapshot_service import build_combined_digest

    combined = build_combined_digest(window_hours=24)
    escalation = escalate_log_errors_to_insights(db, window_hours=1)

    # P1-5: 洞察去噪 — 限制 system_ops 开放数量 + 自动清理过期
    _cap_system_ops_insights(db)

    return {
        "digest_errors_24h": (combined.get("log_digest") or {}).get("total_errors", 0),
        "health_ok": (combined.get("health_snapshot") or {}).get("ok_count", 0),
        "escalation": escalation,
    }


# ══════════════════════════════════════════════════════
#  P1-5: 洞察去噪 — system_ops 数量封顶 + 过期自动 resolve
# ══════════════════════════════════════════════════════

# 最大开放的 system_ops 洞察数（超出则按创建时间关闭最旧的）
_MAX_OPEN_SYSTEM_OPS = 30
# system_ops 洞察最长开放天数（超期自动 resolve）
_MAX_SYSTEM_OPS_AGE_DAYS = 7


def _cap_system_ops_insights(db) -> int:
    """P1-5: 限制 system_ops 洞察开放数量，避免噪声淹没真实信号。

    - 超过 _MAX_OPEN_SYSTEM_OPS 条 open → 关闭最旧的（按 created_at）
    - 超过 _MAX_SYSTEM_OPS_AGE_DAYS 天的 open → 自动 resolve
    - 返回（capped, aged_out）的合计关闭数
    """
    from backend.database.models import OpenCodeInsightDB
    from backend.database.connection import sqlite_write_commit

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    age_cutoff = now_naive - timedelta(days=_MAX_SYSTEM_OPS_AGE_DAYS)
    closed = 0

    # 1. 关闭超龄 system_ops
    stale = (
        db.query(OpenCodeInsightDB)
        .filter(
            OpenCodeInsightDB.status == "open",
            OpenCodeInsightDB.category == "system_ops",
            OpenCodeInsightDB.created_at < age_cutoff,
        )
        .all()
    )
    for row in stale:
        row.status = "resolved"
        row.resolved_at = now_naive
        closed += 1
    if closed:
        logger.info(
            "[LogEscalation] P1-5 自动 resolve %d 条超龄 system_ops（>%d 天）",
            closed, _MAX_SYSTEM_OPS_AGE_DAYS,
        )

    # 2. 限制总数：如果 open 的 system_ops 超过 _MAX_OPEN_SYSTEM_OPS，关闭最旧的
    open_rows = (
        db.query(OpenCodeInsightDB)
        .filter(
            OpenCodeInsightDB.status == "open",
            OpenCodeInsightDB.category == "system_ops",
        )
        .order_by(OpenCodeInsightDB.created_at.asc())
        .all()
    )
    excess = len(open_rows) - _MAX_OPEN_SYSTEM_OPS
    if excess > 0:
        for row in open_rows[:excess]:
            row.status = "resolved"
            row.resolved_at = now_naive
            closed += 1
        logger.warning(
            "[LogEscalation] P1-5 open system_ops=%d 超过上限 %d，"
            "关闭最旧 %d 条",
            len(open_rows), _MAX_OPEN_SYSTEM_OPS, excess,
        )

    if closed:
        sqlite_write_commit(db)
    return closed
