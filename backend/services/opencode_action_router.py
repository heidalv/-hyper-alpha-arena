"""OpenCode Action Router — 按 severity 分流洞察 / 提案 / 告警 / 节奏。"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_major_last_ts: Dict[str, float] = {}


@dataclass
class KnowledgeItem:
    """统一知识条目 — Phase 2 整合：三系统共用数据结构"""
    source: str          # "opencode" | "evolution" | "learning_loop"
    category: str        # "insight" | "lesson" | "narrative" | "pattern" | "param_wisdom"
    severity: str = "info"  # "info" | "warning" | "major" | "critical"
    title: str = ""
    finding_json: Dict[str, Any] = field(default_factory=dict)
    strategy_id: Optional[str] = None  # 关联策略（可选）
    expires_at: Optional[datetime] = None


def ensure_global_strategy_memory(db):
    """确保 `_global_` 跨策略记忆容器存在（修复 OpenCode 闭环断点）。

    历史断点：OpenCode 产出的 lessons 需写入 `_global_` StrategyMemory，
    但该记录从未被创建，导致 merge_opencode_lessons 永远静默返回 0，
    lessons 进不了 trading_analysts 的 prompt（闭环断裂）。

    StrategyMemory.strategy_id 外键指向 ai_strategies.strategy_id，故需先建
    一个 `_global_` AIStrategy 哨兵（status=archived，不参与实盘选币）。
    无任何账户时优雅跳过（返回 None），不抛异常。
    """
    from backend.database.models import AIStrategy, Account, StrategyMemory
    from backend.database.connection import sqlite_write_commit

    try:
        mem = db.query(StrategyMemory).filter(StrategyMemory.strategy_id == "_global_").first()
        if mem:
            return mem

        sentinel = db.query(AIStrategy).filter(AIStrategy.strategy_id == "_global_").first()
        if not sentinel:
            acct = db.query(Account).order_by(Account.id.asc()).first()
            if not acct:
                logger.debug("[ActionRouter] 无账户，暂不创建 _global_ 哨兵策略")
                return None
            sentinel = AIStrategy(
                strategy_id="_global_",
                name="OpenCode Global Memory",
                description="OpenCode 跨策略经验聚合容器（系统哨兵，不参与选币/交易）",
                account_id=acct.id,
                status="archived",
                auto_execute=False,
                require_confirmation=True,
                learning_enabled=False,
            )
            db.add(sentinel)
            db.flush()

        mem = StrategyMemory(strategy_id="_global_", key_lessons=[])
        db.add(mem)
        sqlite_write_commit(db)
        logger.info("[ActionRouter] 已创建 _global_ 跨策略记忆容器")
        return mem
    except Exception as exc:
        logger.warning("[ActionRouter] 创建 _global_ 记忆失败(非致命): %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return None


def merge_opencode_lessons(db, findings: List[Dict[str, Any]], *, severity: str = "info") -> int:
    from backend.database.connection import sqlite_write_commit

    mem = ensure_global_strategy_memory(db)
    if not mem:
        logger.debug("[ActionRouter] skip lesson merge: _global_ StrategyMemory 不可用")
        return 0

    from backend.services.lesson_utils import lesson_dedupe_key

    merged = 0
    for f in findings:
        msg = (f.get("message") or f.get("lesson") or "").strip()
        if not msg:
            continue
        lessons = list(mem.key_lessons or [])
        entry = {
            "type": "opencode_insight",
            "lesson": msg[:300],
            "severity": severity,
            "source": "opencode",
            "category": f.get("category", "general"),
        }
        dedupe = lesson_dedupe_key(entry)
        seen = {lesson_dedupe_key(l) for l in lessons if isinstance(l, dict)}
        if dedupe not in seen:
            lessons.append(entry)
            mem.key_lessons = lessons[-20:]
            merged += 1
    if merged:
        sqlite_write_commit(db)
    return merged


def unified_knowledge_ingest(
    db,
    items: List[KnowledgeItem],
    *,
    trigger_rag_index: bool = True,
) -> int:
    """统一知识摄入 — Phase 2 整合：全系统唯一写入入口。

    写入目标：
      1. _global_ StrategyMemory.key_lessons（主存储，JSON 数组）
      2. OpenCodeInsightDB（结构化查询，仅 severity >= warning）
      3. RAG ChromaDB 增量索引（异步，仅 category in {insight, lesson, pattern}）

    去重：通过 lesson_utils.merge_lessons 实现。
    """
    from backend.database.connection import sqlite_write_commit
    from backend.database.models import OpenCodeInsightDB
    from backend.services.lesson_utils import merge_lessons

    if not items:
        return 0

    # 1) 写入 _global_ StrategyMemory
    mem = ensure_global_strategy_memory(db)
    if not mem:
        logger.warning("[UnifiedKnowledge] _global_ StrategyMemory 不可用，跳过知识摄入")
        return 0

    existing = list(mem.key_lessons or [])
    new_entries = []
    for item in items:
        entry = {
            "type": f"{item.source}_{item.category}",
            "source": item.source,
            "category": item.category,
            "severity": item.severity,
            "title": (item.title or "")[:256],
            "lesson": json.dumps(item.finding_json, ensure_ascii=False)[:500],
            "ingested_at": datetime.utcnow().isoformat(),
        }
        if item.strategy_id:
            entry["strategy_id"] = item.strategy_id
        if item.expires_at:
            entry["expires_at"] = item.expires_at.isoformat()
        new_entries.append(entry)

    merged = merge_lessons(existing, new_entries)
    mem.key_lessons = merged
    ingested_count = len(merged) - len(existing)
    if ingested_count > 0 or len(merged) != len(existing):
        sqlite_write_commit(db)
        logger.info(
            "[UnifiedKnowledge] 知识池更新: +%d 条 (总 %d 条) | sources=%s",
            max(0, ingested_count),
            len(merged),
            ",".join(sorted(set(i.source for i in items))),
        )

    # 2) 写入 OpenCodeInsightDB（所有级别，带去重）
    insight_count = 0
    for item in items:
        # 去重：检查同 title + severity + status='open' 是否已存在
        existing_insight = db.query(OpenCodeInsightDB).filter(
            OpenCodeInsightDB.title == (item.title or item.category)[:256],
            OpenCodeInsightDB.severity == item.severity,
            OpenCodeInsightDB.status == "open",
        ).first()
        if existing_insight:
            logger.debug("[UnifiedKnowledge] insight 去重跳过: %s", item.title)
            continue
        db.add(OpenCodeInsightDB(
            window="unified",
            domain="ai",
            severity=item.severity,
            category=item.category,
            title=(item.title or item.category)[:256],
            finding_json=json.dumps(item.finding_json, ensure_ascii=False),
            status="open",
            source=item.source,
        ))
        insight_count += 1
    if insight_count:
        sqlite_write_commit(db)

    # 3) 触发 RAG 增量索引（异步，避免阻塞调用方）
    if trigger_rag_index and ingested_count > 0:
        try:
            rag_items = [i for i in items if i.category in ("insight", "lesson", "pattern")]
            if rag_items:
                import threading
                def _async_rag_index():
                    try:
                        from backend.services.rag_knowledge_service import rag_knowledge_service
                        from backend.database.connection import SessionLocal
                        _rdb = SessionLocal()
                        try:
                            rag_knowledge_service._index_unified_knowledge(_rdb, rag_items)
                        finally:
                            _rdb.close()
                    except Exception as _e:
                        logger.debug("[UnifiedKnowledge] RAG 异步索引跳过: %s", _e)
                _t = threading.Thread(target=_async_rag_index, daemon=True)
                _t.start()
        except Exception:
            pass

    return max(0, ingested_count)


def _save_insights(db, result: Dict[str, Any], window: str, domain: str) -> None:
    from backend.database.models import OpenCodeInsightDB
    from backend.database.connection import sqlite_write_commit

    severity = str(result.get("severity") or "info")
    findings = result.get("findings") or []
    for f in findings:
        if not isinstance(f, dict):
            continue
        title = (f.get("message") or f.get("category") or "finding")[:256]
        db.add(OpenCodeInsightDB(
            window=window,
            domain=domain,
            severity=severity,
            category=_infer_insight_category(f),
            title=title,
            finding_json=json.dumps(f, ensure_ascii=False),
            status="open",
            source="opencode",
        ))
    sqlite_write_commit(db)


def persist_strategic_audit_insights(
    db,
    audit_type: str,
    result: Dict[str, Any],
    *,
    strategy_id: Optional[str] = None,
    window: str = "12h",
) -> int:
    """将战略级审计产出写入 OpenCodeInsightDB，供 Context Pack / open_issues 复用。

    决策审计、市场叙事、跨周期挖掘、策略代码审计原先只写日志或 StrategyMemory，
    前端 open_insights 与后续分析无法引用。本函数补齐结构化落库，并在同类别
    新结果写入前关闭旧的 open 记录（策略代码审计按 strategy_id 区分）。
    """
    if not result or result.get("error") or result.get("skipped"):
        return 0

    findings: List[Dict[str, Any]] = []
    severity = "warning"

    if audit_type == "decision_audit":
        grade = str(result.get("overall_grade") or "").upper()
        severity = {"A": "info", "B": "info", "C": "warning", "D": "major"}.get(grade, "warning")
        findings.append({
            "category": "decision_audit",
            "message": (
                f"决策审计 grade={grade} "
                f"top_mistake={(result.get('top_mistake') or '')[:120]}"
            ),
            "overall_grade": grade,
            "blind_spots": result.get("blind_spots") or [],
            "top_mistake": result.get("top_mistake"),
            "suggestions": result.get("suggestions") or [],
        })
        for spot in (result.get("blind_spots") or [])[:5]:
            if spot:
                findings.append({
                    "category": "decision_audit",
                    "message": f"决策盲点: {spot}"[:256],
                    "blind_spot": spot,
                })

    elif audit_type == "regime_journal":
        trend = result.get("dominant_trend", "?")
        conf = float(result.get("confidence") or 0)
        severity = "info" if conf >= 0.6 else "warning"
        findings.append({
            "category": "regime_journal",
            "message": f"市场叙事 trend={trend} conf={conf:.2f}",
            "dominant_trend": trend,
            "confidence": conf,
            "narrative": (result.get("narrative") or "")[:500],
        })

    elif audit_type == "cross_cycle":
        patterns = result.get("patterns") or []
        summary = result.get("actionable_summary") or result.get("summary") or ""
        if summary or patterns:
            findings.append({
                "category": "cross_cycle",
                "message": f"跨周期模式 {len(patterns)}条: {summary[:180]}",
                "pattern_count": len(patterns),
                "summary": summary,
            })
        for p in patterns[:5]:
            if isinstance(p, dict):
                name = p.get("name") or "pattern"
                findings.append({
                    "category": "cross_cycle",
                    "message": f"跨周期模式: {name}"[:256],
                    "pattern": p,
                })

    elif audit_type == "strategy_code_audit":
        assessment = str(result.get("overall_assessment") or "").lower()
        severity = {"healthy": "info", "concerning": "warning", "critical": "major"}.get(
            assessment, "warning",
        )
        sid = strategy_id or "unknown"
        findings.append({
            "category": "strategy_code_audit",
            "message": f"策略代码审计 {sid}: {assessment}",
            "strategy_id": sid,
            "overall_assessment": assessment,
            "confidence": result.get("confidence"),
        })
        for issue in (result.get("top_issues") or [])[:3]:
            if isinstance(issue, dict):
                findings.append({
                    "category": "strategy_code_audit",
                    "message": f"[{sid}] {issue.get('issue', '')}"[:256],
                    "strategy_id": sid,
                    "issue": issue,
                })
        for sug in (result.get("suggestions") or [])[:3]:
            if isinstance(sug, dict):
                findings.append({
                    "category": "strategy_code_audit",
                    "message": f"[{sid}] 建议: {sug.get('description', '')}"[:256],
                    "strategy_id": sid,
                    "suggestion": sug,
                })
    else:
        logger.debug("[ActionRouter] 未知 audit_type=%s，跳过落库", audit_type)
        return 0

    if not findings:
        return 0

    category = findings[0].get("category", audit_type)
    _resolve_superseded_audit_insights(db, category, strategy_id=strategy_id)
    _save_insights(db, {"severity": severity, "findings": findings}, window=window, domain="ai")
    logger.info(
        "[ActionRouter] 战略审计落库 audit=%s category=%s findings=%d severity=%s",
        audit_type, category, len(findings), severity,
    )
    return len(findings)


def _resolve_superseded_audit_insights(
    db,
    category: str,
    *,
    strategy_id: Optional[str] = None,
) -> None:
    """关闭同类别（策略审计则同 strategy_id）的旧 open 洞察，避免 12h 任务重复堆积。"""
    from backend.database.models import OpenCodeInsightDB
    from backend.database.connection import sqlite_write_commit

    try:
        rows = (
            db.query(OpenCodeInsightDB)
            .filter(
                OpenCodeInsightDB.status == "open",
                OpenCodeInsightDB.category == category,
            )
            .all()
        )
        resolved = 0
        for row in rows:
            if strategy_id:
                try:
                    fj = json.loads(row.finding_json or "{}")
                except Exception:
                    fj = {}
                if fj.get("strategy_id") not in (None, strategy_id):
                    continue
            row.status = "resolved"
            resolved += 1
        if resolved:
            sqlite_write_commit(db)
            logger.debug(
                "[ActionRouter] 关闭旧审计洞察 category=%s strategy=%s count=%d",
                category, strategy_id, resolved,
            )
    except Exception as exc:
        logger.debug("[ActionRouter] 关闭旧审计洞察失败: %s", exc)


def _infer_insight_category(finding: Dict[str, Any]) -> str:
    """给旧式纯 message finding 补分类，避免 general 告警无法自动恢复。"""
    category = str(finding.get("category") or "").strip().lower()
    if category:
        return category
    text = json.dumps(finding, ensure_ascii=False).lower()
    if "win_rate" in text or "rolling 24h" in text or "胜率" in text:
        return "win_rate"
    if "master_close" in text or "master running" in text or "master_running" in text:
        return "master_close"
    if "avg_pnl" in text or "每笔" in text or "盈亏比" in text:
        return "expectancy"
    return "general"


def resolve_stale_insights(db, *, stale_days: int = 7) -> int:
    """自动关闭已恢复/过期的 open 洞察，避免永远 open 持续喂收紧信号。

    解决断点：洞察创建后只 open、从不 resolve，Context Pack 的 open_issues
    会无限累积，让 OpenCode 持续看到「旧问题」而不断收紧。

    规则：
      - win_rate 类：最新 24h SRR 胜率回升到 ≥45% → resolve。
      - master_close 类：最新 master_close_loss_ratio ≤ 40% → resolve。
      - 任何 open 洞察超过 stale_days 天 → resolve（过期自然关闭）。
    """
    from datetime import datetime, timedelta, timezone
    from backend.database.models import OpenCodeInsightDB
    from backend.database.connection import sqlite_write_commit
    from backend.services.strategy_runtime_report import load_latest_report

    try:
        rows = db.query(OpenCodeInsightDB).filter(OpenCodeInsightDB.status == "open").all()
    except Exception as exc:
        logger.warning("[ActionRouter] 读取 open 洞察失败: %s", exc)
        return 0
    if not rows:
        return 0

    report = load_latest_report("24h", "ai") or {}
    cur_wr = float(report.get("win_rate") or 0)
    cur_mc = float(report.get("master_close_loss_ratio") or 0)
    cur_pnl = float(report.get("total_pnl") or 0)
    breaches = report.get("rule_breaches") or []
    report_generated_at = None
    try:
        raw_generated_at = report.get("generated_at")
        if raw_generated_at:
            report_generated_at = datetime.fromisoformat(str(raw_generated_at).replace("Z", "+00:00"))
            if report_generated_at.tzinfo is not None:
                report_generated_at = report_generated_at.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        report_generated_at = None
    report_healthy = cur_wr >= 0.45 and cur_pnl >= 0 and not breaches
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stale_cutoff = now - timedelta(days=stale_days)

    resolved = 0
    for r in rows:
        _resolve = False
        try:
            finding = json.loads(r.finding_json or "{}")
        except Exception:
            finding = {"message": r.title or ""}
        cat = (r.category or "").lower()
        inferred_cat = _infer_insight_category(finding)
        text = f"{r.title or ''} {json.dumps(finding, ensure_ascii=False)}".lower()
        created = r.created_at
        has_newer_healthy_report = bool(
            report_generated_at is None
            or created is None
            or created <= report_generated_at
        )
        if cat == "win_rate" and cur_wr >= 0.45:
            _resolve = True
        elif inferred_cat == "win_rate" and cur_wr >= 0.45 and has_newer_healthy_report:
            _resolve = True
        elif cat == "master_close" and cur_mc <= 0.40:
            _resolve = True
        elif inferred_cat == "master_close" and cur_mc <= 0.40 and has_newer_healthy_report:
            _resolve = True
        elif inferred_cat == "expectancy" and report_healthy and has_newer_healthy_report:
            _resolve = True
        elif (
            (r.source or "").lower() == "opencode"
            and cat == "general"
            and report_healthy
            and has_newer_healthy_report
            and ("rolling 24h" in text or "胜率" in text or "每笔" in text or "盈亏比" in text)
        ):
            _resolve = True
        else:
            if created is not None and created <= stale_cutoff:
                _resolve = True
        if _resolve:
            r.status = "resolved"
            r.resolved_at = now
            resolved += 1
    if resolved:
        sqlite_write_commit(db)
        logger.info("[ActionRouter] 自动关闭 %d 条已恢复/过期洞察", resolved)
    return resolved


def _pace_floor() -> str:
    try:
        from backend.config.settings import OPENCODE_MAJOR_PACE_FLOOR
        floor = (OPENCODE_MAJOR_PACE_FLOOR or "balanced").strip().lower()
        return floor if floor in ("turbo", "warm", "balanced", "conservative") else "balanced"
    except Exception:
        return "balanced"


def emit_proposal_rejected_alert(db, proposal_row: Any, review: Dict[str, Any]) -> None:
    """提案被拒绝：告警 + pace 降档。"""
    from backend.config.settings import (
        OPENCODE_MAJOR_ALERT_CHANNELS,
        OPENCODE_MAJOR_ALERT_COOLDOWN_S,
        OPENCODE_MAJOR_PACE_DOWNSHIFT_STEPS,
    )
    from backend.services.paper_pace_controller import paper_pace_controller

    channels = [c.strip() for c in (OPENCODE_MAJOR_ALERT_CHANNELS or "feishu,panel").split(",") if c.strip()]
    cooldown = int(OPENCODE_MAJOR_ALERT_COOLDOWN_S or 3600)
    key = f"proposal_reject_{proposal_row.id}"
    now = time.time()
    if now - _major_last_ts.get(key, 0) < cooldown:
        return
    _major_last_ts[key] = now

    reasons = review.get("reasons") or []
    title = f"OpenCode proposal #{proposal_row.id} rejected"
    body = "; ".join(str(r) for r in reasons)[:500] or str(review.get("decision", "reject"))

    if "feishu" in channels:
        try:
            import asyncio
            from backend.services.openclaw_notify import notify_system_event, NotifyLevel

            msg = f"{title}\n{body}"
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(notify_system_event(msg, level=NotifyLevel.CRITICAL))
            except RuntimeError:
                asyncio.run(notify_system_event(msg, level=NotifyLevel.CRITICAL))
        except Exception as err:
            logger.warning("[ActionRouter] feishu reject: %s", err)

    if "panel" in channels:
        try:
            from backend.database.models import FullAutoSession
            from backend.services.full_auto_trading_service import full_auto_trading_service

            sessions = db.query(FullAutoSession).filter(FullAutoSession.status == "running").all()
            for s in sessions:
                full_auto_trading_service._append_event(
                    s, "opencode_proposal_rejected", body[:500], severity="warning",
                )
        except Exception as err:
            logger.warning("[ActionRouter] panel reject: %s", err)

    steps = int(OPENCODE_MAJOR_PACE_DOWNSHIFT_STEPS or 1)
    if steps > 0:
        paper_pace_controller.force_downshift(
            steps, reason=f"proposal_reject_{proposal_row.id}", floor=_pace_floor(),
        )


def _emit_major_alerts(db, result: Dict[str, Any], breaches: List[str]) -> None:
    from backend.config.settings import (
        OPENCODE_MAJOR_ALERT_CHANNELS,
        OPENCODE_MAJOR_ALERT_COOLDOWN_S,
        OPENCODE_MAJOR_PACE_DOWNSHIFT_STEPS,
    )
    from backend.services.paper_pace_controller import paper_pace_controller

    channels = [c.strip() for c in (OPENCODE_MAJOR_ALERT_CHANNELS or "feishu,panel").split(",") if c.strip()]
    cooldown = int(OPENCODE_MAJOR_ALERT_COOLDOWN_S or 3600)
    key = "major"
    now = time.time()
    if now - _major_last_ts.get(key, 0) < cooldown:
        return
    _major_last_ts[key] = now

    title = f"OpenCode major: {result.get('severity', 'major')}"
    body = "\n".join(breaches[:5]) or json.dumps(result.get("findings") or [])[:500]

    if "feishu" in channels:
        try:
            import asyncio
            from backend.services.openclaw_notify import notify_system_event, NotifyLevel

            msg = f"{title}\n{body}"
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(notify_system_event(msg, level=NotifyLevel.CRITICAL))
            except RuntimeError:
                asyncio.run(notify_system_event(msg, level=NotifyLevel.CRITICAL))
        except Exception as err:
            logger.warning("[ActionRouter] feishu: %s", err)

    if "panel" in channels:
        try:
            from backend.database.models import FullAutoSession
            from backend.services.full_auto_trading_service import full_auto_trading_service

            sessions = db.query(FullAutoSession).filter(FullAutoSession.status == "running").all()
            for s in sessions:
                full_auto_trading_service._append_event(
                    s, "opencode_major_alert", body[:500], severity="critical",
                )
        except Exception as err:
            logger.warning("[ActionRouter] panel: %s", err)

    steps = int(OPENCODE_MAJOR_PACE_DOWNSHIFT_STEPS or 1)
    if steps > 0:
        paper_pace_controller.force_downshift(
            steps, reason="opencode_major", floor=_pace_floor(),
        )


def route_analysis_result(
    db,
    result: Dict[str, Any],
    *,
    window: str = "24h",
    domain: str = "ai",
) -> Dict[str, Any]:
    severity = str(result.get("severity") or "info").lower()
    findings = result.get("findings") or []
    patches = result.get("patches") or []

    # Phase 2 整合: 通过统一知识池单一写入入口（避免与 unified_knowledge_ingest 重复写 OpenCodeInsightDB）
    knowledge_items = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        msg = (f.get("message") or f.get("lesson") or "").strip()
        knowledge_items.append(KnowledgeItem(
            source="opencode",
            category=_infer_insight_category(f),
            severity=severity,
            title=msg[:200] if msg else "finding",
            finding_json=f,
        ))
    if knowledge_items:
        unified_knowledge_ingest(db, knowledge_items, trigger_rag_index=True)

    proposal_id = None
    review_result = None
    if patches:
        try:
            from backend.config.settings import OPENCODE_AUTO_REVIEW, OPENCODE_MAJOR_CREATE_PROPOSALS
            from backend.services.opencode_proposal_applier import create_proposal

            if severity in ("major", "critical") and not OPENCODE_MAJOR_CREATE_PROPOSALS:
                pass
            else:
                first_msg = ""
                if findings and isinstance(findings[0], dict):
                    first_msg = str(findings[0].get("message") or "")[:80]
                title = f"OpenCode {severity} {window}" + (f": {first_msg}" if first_msg else "")
                import hashlib
                dedupe = hashlib.sha256(
                    json.dumps(patches, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest()
                proposal_id = create_proposal(
                    db, patches, severity=severity, title=title, dedupe_key=dedupe,
                )
                if proposal_id and OPENCODE_AUTO_REVIEW:
                    from backend.services.opencode_proposal_reviewer import review_and_apply_proposal
                    review_result = review_and_apply_proposal(db, proposal_id)
        except Exception as err:
            logger.error("[ActionRouter] proposal: %s", err, exc_info=True)

    breaches = []
    for f in findings:
        if isinstance(f, dict) and f.get("message"):
            breaches.append(str(f["message"]))
    if severity in ("major", "critical"):
        if not patches:
            _emit_major_alerts(db, result, breaches)

    return {
        "severity": severity,
        "findings_n": len(findings),
        "proposal_id": proposal_id,
        "patches_n": len(patches),
        "review": review_result,
    }


# ══════════════════════════════════════════════════════
#  Phase 3: GovernanceArbiter — 参数调优冲突仲裁器
# ══════════════════════════════════════════════════════

class GovernanceArbiter:
    """参数调优冲突仲裁器 — Phase 3 整合。

    解决 NSGA-II 数学进化 与 OpenCode 提案系统 同时修改同一参数时的覆盖冲突。

    优先级（从高到低）:
      1. 人工手动调参 (manual) — 不可覆盖
      2. OpenCode 提案·评估通过 (paper_validated, to_live=true)
      3. NSGA-II 进化结果 (promoted champion)
      4. OpenCode 提案·paper 阶段 (paper_applying)
      5. NSGA-II 实验性变体 (non-promoted)
    """

    # 优先级常量
    PRIORITY_MANUAL = 10
    PRIORITY_PROPOSAL_VALIDATED = 8
    PRIORITY_NSGA2_PROMOTED = 6
    PRIORITY_PROPOSAL_PAPER = 4
    PRIORITY_NSGA2_EXPERIMENTAL = 2

    # 参数所有权映射：每个参数 key 的主要调优来源
    OWNERSHIP_MAP = {
        "master_reduce_min_loss_pct": "proposal",
        "tier_max_hold_sec": "nsga2",
        "master_close_min_loss_pct_by_tier": "proposal",
        "max_daily_trades": "nsga2",
        "maturity_max_warmup_relief": "nsga2",
        "maturity_global_n1": "nsga2",
        "maturity_global_n2": "nsga2",
    }

    @staticmethod
    def resolve_priority(source: str, context: str = "") -> int:
        """根据来源和上下文返回优先级数值。"""
        s = source.lower()
        c = context.lower()
        if "manual" in s:
            return GovernanceArbiter.PRIORITY_MANUAL
        if "proposal" in s and "validated" in c:
            return GovernanceArbiter.PRIORITY_PROPOSAL_VALIDATED
        if "nsga" in s and "promoted" in c:
            return GovernanceArbiter.PRIORITY_NSGA2_PROMOTED
        if "proposal" in s:
            return GovernanceArbiter.PRIORITY_PROPOSAL_PAPER
        if "nsga" in s:
            return GovernanceArbiter.PRIORITY_NSGA2_EXPERIMENTAL
        return 0

    @staticmethod
    def arbitrate(
        param_key: str,
        new_value: float,
        new_priority: int,
        current_value: float = None,
        current_priority: int = 0,
        new_source: str = "unknown",
    ) -> Tuple[bool, str]:
        """仲裁参数写入请求。

        Returns:
            (是否允许写入, 原因)
        """
        if new_priority > current_priority:
            return True, f"P{new_priority}>P{current_priority}"
        if new_priority == current_priority:
            return True, "equal_priority"
        return False, f"P{new_priority}<P{current_priority}_rejected"

    @staticmethod
    def log_governance(
        db,
        param_key: str,
        old_value,
        new_value,
        source: str,
        decision: str,
    ) -> None:
        """写入治理日志（使用 Python logger + DB session event）。"""
        logger.info(
            "[Governance] %s | %s: %s -> %s | src=%s",
            decision,
            param_key,
            old_value,
            new_value,
            source,
        )
