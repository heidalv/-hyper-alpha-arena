"""Rule Sync Pipeline — 快照 diff、影响分析、自动暂停。"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import time
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen

from backend.database.connection import SessionLocal, sqlite_write_commit
from backend.database.models import (
    ExchangeRuleSnapshotDB,
    RebateEvolutionProposalDB,
    RuleAiAnalysisLogDB,
    RuleChangeEventDB,
)
from backend.services.rebate_arb.rule_registry import rule_registry
from backend.services.rebate_arb.schema import ensure_rebate_schema
from backend.services.rebate_arb.rule_sync_gate import rule_sync_gate

logger = logging.getLogger(__name__)


class RuleImpactAnalyzer:
    """LLM unavailable fallback: keyword + changed-text heuristics."""

    L4_KEYWORDS = ("停止", "暂停交易", "禁止交易", "terminate", "suspend trading", "not eligible", "规则不明", "conflict")
    L3_KEYWORDS = ("不再计", "资格", "eligible", "eligibility", "对冲要求", "持仓要求", "volume no longer", "api unavailable")
    L2_KEYWORDS = ("费率", "fee", "rebate", "倍率", "multiplier", "积分", "points", "vip")

    def analyze(self, *, source: Dict[str, Any], old_text: str, new_text: str) -> Dict[str, Any]:
        changed_text = self._changed_text(old_text, new_text)
        haystack = f"{source.get('title', '')}\n{changed_text}\n{new_text[:2000]}".lower()

        severity = "L1"
        reason = "文本有轻微变化，建议人工复核。"
        requires_code_change = False

        if any(k.lower() in haystack for k in self.L4_KEYWORDS):
            severity = "L4"
            reason = "检测到停止/禁止/冲突类关键词，保守判定为需要全局暂停并开发介入。"
            requires_code_change = True
        elif any(k.lower() in haystack for k in self.L3_KEYWORDS):
            severity = "L3"
            reason = "检测到资格、计分口径或执行逻辑变化，建议暂停受影响策略。"
            requires_code_change = True
        elif any(k.lower() in haystack for k in self.L2_KEYWORDS):
            severity = "L2"
            reason = "检测到费率、积分或返点参数变化，生成配置提案并进入 Paper 验证。"

        return {
            "severity": severity,
            "reason": reason,
            "changed_excerpt": changed_text[:1200],
            "affected_strategies": source.get("affected_strategies", []),
            "requires_code_change": requires_code_change,
            "manual_live_confirm_required": True,
        }

    def _changed_text(self, old_text: str, new_text: str) -> str:
        old_lines = [l.strip() for l in (old_text or "").splitlines() if l.strip()]
        new_lines = [l.strip() for l in (new_text or "").splitlines() if l.strip()]
        matcher = SequenceMatcher(None, old_lines, new_lines)
        chunks: List[str] = []
        for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
            if tag in ("replace", "insert"):
                chunks.extend(new_lines[j1:j2])
        return "\n".join(chunks) or (new_text or "")[:1200]


class RuleChangeDetector:
    """Create snapshots, rule_change_events and optional auto-pauses."""

    def __init__(self):
        self._analyzer = RuleImpactAnalyzer()

    def ingest_snapshot(
        self,
        *,
        source_id: str,
        content_text: str,
        title: Optional[str] = None,
        url: Optional[str] = None,
    ) -> Dict[str, Any]:
        ensure_rebate_schema()
        source = rule_registry.get_source(source_id)
        content = (content_text or "").strip()
        if not content:
            return {"success": False, "error": "content_text is required"}

        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        db = SessionLocal()
        try:
            latest = (
                db.query(ExchangeRuleSnapshotDB)
                .filter(ExchangeRuleSnapshotDB.source_id == source_id)
                .order_by(ExchangeRuleSnapshotDB.id.desc())
                .first()
            )
            if latest and latest.content_hash == content_hash:
                return {
                    "success": True,
                    "changed": False,
                    "snapshot_id": latest.id,
                    "message": "规则内容无变化",
                }

            snapshot = ExchangeRuleSnapshotDB(
                source_id=source.source_id,
                exchange=source.exchange,
                rule_type=source.rule_type,
                title=title or source.title,
                url=url or source.url,
                content_hash=content_hash,
                content_text=content,
                normalized_json=json.dumps({
                    "source_id": source.source_id,
                    "exchange": source.exchange,
                    "rule_type": source.rule_type,
                }, ensure_ascii=False),
                fetched_at=time.time(),
            )
            db.add(snapshot)
            db.flush()

            event = None
            analysis: Dict[str, Any] = {}
            if latest:
                analysis = self._analyzer.analyze(
                    source=source.to_dict(),
                    old_text=latest.content_text,
                    new_text=content,
                )
                event = RuleChangeEventDB(
                    source_id=source.source_id,
                    exchange=source.exchange,
                    rule_type=source.rule_type,
                    previous_snapshot_id=latest.id,
                    current_snapshot_id=snapshot.id,
                    severity=analysis["severity"],
                    affected_strategies_json=json.dumps(analysis["affected_strategies"], ensure_ascii=False),
                    diff_summary=analysis["reason"],
                    ai_analysis_json=json.dumps(analysis, ensure_ascii=False, default=str),
                    status="analyzed",
                    requires_code_change=bool(analysis.get("requires_code_change")),
                )
                db.add(event)
                db.flush()
                self._log_ai_analysis(db, event, analysis, source.to_dict())
                self._create_rule_proposal(db, event, analysis)
                self._maybe_auto_pause(db, source.to_dict(), event, analysis)

            sqlite_write_commit(db, label="rule_snapshot_ingest")
            return {
                "success": True,
                "changed": bool(latest),
                "snapshot_id": snapshot.id,
                "event_id": event.id if event else None,
                "analysis": analysis,
            }
        except Exception as e:
            db.rollback()
            logger.error("[RuleChangeDetector] ingest failed: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}
        finally:
            db.close()

    def fetch_source(self, source_id: str) -> Dict[str, Any]:
        """Fetch a registered source URL and ingest a text snapshot."""
        source = rule_registry.get_source(source_id)
        try:
            req = Request(
                source.url,
                headers={
                    "User-Agent": "HyperAlphaArena-RuleSync/1.0 (+rule-monitor)",
                    "Accept": "text/html,text/plain,*/*",
                },
            )
            with urlopen(req, timeout=12) as resp:
                raw = resp.read(1_000_000)
                charset = resp.headers.get_content_charset() or "utf-8"
                text = raw.decode(charset, errors="ignore")
            content = self._html_to_text(text)
            result = self.ingest_snapshot(
                source_id=source.source_id,
                content_text=content,
                title=source.title,
                url=source.url,
            )
            result["source_id"] = source.source_id
            result["exchange"] = source.exchange
            return result
        except Exception as e:
            logger.warning("[RuleChangeDetector] fetch failed %s: %s", source_id, e)
            return {"success": False, "source_id": source_id, "error": str(e)}

    def fetch_all_sources(self) -> Dict[str, Any]:
        """Fetch all registered rule sources sequentially."""
        results = []
        for source in rule_registry.list_sources():
            results.append(self.fetch_source(source["source_id"]))
        return {
            "success": True,
            "count": len(results),
            "changed_count": sum(1 for r in results if r.get("changed")),
            "failed_count": sum(1 for r in results if not r.get("success")),
            "results": results,
        }

    def mark_event(self, event_id: int, status: str) -> Dict[str, Any]:
        if status not in {"pending", "analyzed", "applied", "dismissed"}:
            return {"success": False, "error": "invalid status"}
        ensure_rebate_schema()
        db = SessionLocal()
        try:
            event = db.query(RuleChangeEventDB).filter(RuleChangeEventDB.id == event_id).first()
            if not event:
                return {"success": False, "error": "event not found"}
            before = self._event_to_dict(event)
            event.status = status
            sqlite_write_commit(db, label="rule_event_mark")
            after = self._event_to_dict(event)
            rule_sync_gate.record_audit(
                action=f"rule_event_{status}",
                target_type="rule_change",
                target_id=str(event_id),
                before=before,
                after=after,
                reason="rule change status updated",
                risk_acknowledged=status in {"applied", "dismissed"},
            )
            return {"success": True, "event": after}
        except Exception as e:
            db.rollback()
            return {"success": False, "error": str(e)}
        finally:
            db.close()

    def mark_proposal(self, proposal_id: int, status: str) -> Dict[str, Any]:
        if status not in {"pending", "paper_validated", "applied", "dismissed"}:
            return {"success": False, "error": "invalid status"}
        ensure_rebate_schema()
        db = SessionLocal()
        try:
            proposal = db.query(RebateEvolutionProposalDB).filter(RebateEvolutionProposalDB.id == proposal_id).first()
            if not proposal:
                return {"success": False, "error": "proposal not found"}
            before = {"id": proposal.id, "status": proposal.status, "source": proposal.source}
            proposal.status = status
            sqlite_write_commit(db, label="rebate_proposal_mark")
            rule_sync_gate.record_audit(
                action=f"proposal_{status}",
                target_type="proposal",
                target_id=str(proposal_id),
                before=before,
                after={"id": proposal_id, "status": status, "source": proposal.source},
                reason="proposal status updated",
                risk_acknowledged=status in {"applied", "dismissed"},
            )
            return {"success": True, "proposal_id": proposal_id, "status": status}
        except Exception as e:
            db.rollback()
            return {"success": False, "error": str(e)}
        finally:
            db.close()

    def _html_to_text(self, text: str) -> str:
        text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text or "")
        text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", "\n", text)
        text = html.unescape(text)
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
        lines = [line for line in lines if line]
        return "\n".join(lines[:5000])

    def list_events(self, *, status: str = "", limit: int = 100) -> Dict[str, Any]:
        ensure_rebate_schema()
        db = SessionLocal()
        try:
            query = db.query(RuleChangeEventDB)
            if status:
                query = query.filter(RuleChangeEventDB.status == status)
            rows = query.order_by(RuleChangeEventDB.id.desc()).limit(limit).all()
            events = [self._event_to_dict(row) for row in rows]
            return {"count": len(events), "events": events}
        finally:
            db.close()

    def analyze_event(self, event_id: int) -> Dict[str, Any]:
        ensure_rebate_schema()
        db = SessionLocal()
        try:
            event = db.query(RuleChangeEventDB).filter(RuleChangeEventDB.id == event_id).first()
            if not event:
                return {"success": False, "error": "event not found"}
            old = db.query(ExchangeRuleSnapshotDB).filter(ExchangeRuleSnapshotDB.id == event.previous_snapshot_id).first()
            cur = db.query(ExchangeRuleSnapshotDB).filter(ExchangeRuleSnapshotDB.id == event.current_snapshot_id).first()
            source = rule_registry.get_source(event.source_id)
            analysis = self._analyzer.analyze(
                source=source.to_dict(),
                old_text=old.content_text if old else "",
                new_text=cur.content_text if cur else "",
            )
            event.severity = analysis["severity"]
            event.affected_strategies_json = json.dumps(analysis["affected_strategies"], ensure_ascii=False)
            event.diff_summary = analysis["reason"]
            event.ai_analysis_json = json.dumps(analysis, ensure_ascii=False, default=str)
            event.status = "analyzed"
            event.requires_code_change = bool(analysis.get("requires_code_change"))
            self._log_ai_analysis(db, event, analysis, source.to_dict())
            self._create_rule_proposal(db, event, analysis)
            self._maybe_auto_pause(db, source.to_dict(), event, analysis)
            sqlite_write_commit(db, label="rule_event_analyze")
            return {"success": True, "event": self._event_to_dict(event)}
        except Exception as e:
            db.rollback()
            return {"success": False, "error": str(e)}
        finally:
            db.close()

    def _maybe_auto_pause(self, db, source: Dict[str, Any], event: RuleChangeEventDB, analysis: Dict[str, Any]) -> None:
        severity = analysis.get("severity")
        if severity not in ("L3", "L4"):
            return
        if not source.get("auto_pause_enabled"):
            return
        affected = analysis.get("affected_strategies") or []
        gate = rule_sync_gate.pause(
            strategies=affected,
            reason=f"RuleSync {severity}: {source.get('title')} - {analysis.get('reason')}",
            rebate_pause=(severity == "L4"),
            v3_pause=False,
            requires_code_change=bool(analysis.get("requires_code_change")),
            risk_acknowledged=False,
        )
        event.auto_pause_applied = True
        event.status = "applied" if gate else "analyzed"
        db.add(event)

    def _create_rule_proposal(self, db, event: RuleChangeEventDB, analysis: Dict[str, Any]) -> None:
        strategies = analysis.get("affected_strategies") or []
        for strategy in strategies or [None]:
            db.add(RebateEvolutionProposalDB(
                source="rule_sync",
                strategy_type=strategy,
                severity=analysis.get("severity", "L1"),
                title=f"规则同步提案：{event.exchange}/{event.rule_type} {analysis.get('severity')}",
                proposal_json=json.dumps({
                    "event_id": event.id,
                    "reason": analysis.get("reason"),
                    "changed_excerpt": analysis.get("changed_excerpt"),
                    "suggested_action": "pause_or_review" if analysis.get("severity") in ("L3", "L4") else "paper_validate",
                }, ensure_ascii=False, default=str),
                status="pending",
                requires_paper_validation=analysis.get("severity") in ("L1", "L2"),
                requires_manual_live_confirm=True,
                related_event_id=event.id,
            ))

    def _log_ai_analysis(
        self,
        db,
        event: RuleChangeEventDB,
        analysis: Dict[str, Any],
        source: Dict[str, Any],
    ) -> None:
        db.add(RuleAiAnalysisLogDB(
            event_id=event.id,
            source_id=event.source_id,
            exchange=event.exchange,
            severity=analysis.get("severity", "L1"),
            analyzer="heuristic_rule_engine",
            prompt_snapshot=json.dumps({
                "source": source,
                "mode": "fallback_no_llm",
            }, ensure_ascii=False, default=str),
            analysis_json=json.dumps(analysis, ensure_ascii=False, default=str),
        ))

    def _event_to_dict(self, row: RuleChangeEventDB) -> Dict[str, Any]:
        try:
            affected = json.loads(row.affected_strategies_json or "[]")
        except Exception:
            affected = []
        try:
            analysis = json.loads(row.ai_analysis_json or "{}")
        except Exception:
            analysis = {}
        return {
            "id": row.id,
            "source_id": row.source_id,
            "exchange": row.exchange,
            "rule_type": row.rule_type,
            "previous_snapshot_id": row.previous_snapshot_id,
            "current_snapshot_id": row.current_snapshot_id,
            "severity": row.severity,
            "affected_strategies": affected,
            "diff_summary": row.diff_summary,
            "analysis": analysis,
            "status": row.status,
            "auto_pause_applied": row.auto_pause_applied,
            "requires_code_change": row.requires_code_change,
            "created_at": str(row.created_at) if row.created_at else None,
        }


rule_change_detector = RuleChangeDetector()
