"""OpenCode �᰸���� �� Ӳ����У�� + Review Agent + �Զ� apply/reject��"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

WHITELIST_TUNING_KEYS = {
    "master_reduce_min_loss_pct",
    # tier_max_hold_sec ������ �� �� HOLD_TIME_TUNING_LOCKED�����ݹ������ǰ��ֹ OpenCode �ĳֲ�ʱ��
    "master_close_min_loss_pct_by_tier",
    "max_daily_trades",
    # OpenCode ��ѭ��ֻ�� MaturityController �߲���ť���ɽ�ϵ��/�׶���ֵ����
    # ����ֱ�Ӹ�����Ӳ��ֵ����֤����һ��ťԴ����
    "maturity_max_warmup_relief",
    "maturity_global_n1",
    "maturity_global_n2",
}


def _max_delta_pct() -> float:
    try:
        from backend.config.settings import OPENCODE_PATCH_MAX_DELTA_PCT
        return float(OPENCODE_PATCH_MAX_DELTA_PCT or 0.20)
    except Exception:
        return 0.20


def _review_min_confidence() -> float:
    try:
        from backend.config.settings import OPENCODE_REVIEW_MIN_CONFIDENCE
        return float(OPENCODE_REVIEW_MIN_CONFIDENCE or 0.7)
    except Exception:
        return 0.7


def _review_agent() -> str:
    try:
        from backend.config.settings import OPENCODE_AGENT_REVIEW
        return OPENCODE_AGENT_REVIEW or "review"
    except Exception:
        return "review"


def _review_model() -> str:
    try:
        from backend.services.llm_config_service import get_default_model_slug
        slug = get_default_model_slug(tier="deep")
        if slug:
            return slug
    except Exception:
        pass
    try:
        from backend.config.settings import OPENCODE_MODEL, OPENCODE_REVIEW_MODEL
        return OPENCODE_REVIEW_MODEL or OPENCODE_MODEL or "deepseek/deepseek-v4-flash"
    except Exception:
        return "deepseek/deepseek-v4-flash"


def _review_timeout_s() -> int:
    try:
        from backend.config.settings import OPENCODE_REVIEW_TIMEOUT_S
        return int(OPENCODE_REVIEW_TIMEOUT_S or 120)
    except Exception:
        return 120


def _defer_retry_s() -> int:
    try:
        from backend.config.settings import OPENCODE_REVIEW_DEFER_RETRY_S
        return int(OPENCODE_REVIEW_DEFER_RETRY_S or 3600)
    except Exception:
        return 3600


def _auto_review_enabled() -> bool:
    try:
        from backend.config.settings import OPENCODE_AUTO_REVIEW
        return bool(OPENCODE_AUTO_REVIEW)
    except Exception:
        return True


def _current_tuning_value(key: str) -> Optional[float]:
    from backend.services.runtime_tuning_store import get_all_tuning

    data = get_all_tuning()
    entry = data.get(key)
    if isinstance(entry, dict) and "value" in entry:
        try:
            return float(entry["value"])
        except (TypeError, ValueError):
            return None
    if isinstance(entry, (int, float)):
        return float(entry)
    return None


def validate_patches_hard(patches: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    """������ LLM ��Ӳ�Ž���"""
    errors: List[str] = []
    max_delta = _max_delta_pct()

    for i, p in enumerate(patches):
        if not isinstance(p, dict):
            errors.append(f"patch[{i}] not dict")
            continue

        ptype = (p.get("type") or "tuning").lower()
        key = str(p.get("key") or p.get("path") or "")
        val = p.get("value")

        if ptype == "shadow_py":
            # �������� OPENCODE_SHADOW_ENABLED=true �ҷ� live ������
            _shadow_ok = os.getenv("OPENCODE_SHADOW_ENABLED", "").lower() == "true"
            if not _shadow_ok:
                errors.append(f"patch[{i}] shadow disabled (OPENCODE_SHADOW_ENABLED not true)")
            # ������е� shadow ������֤
            continue
        if ptype in ("python", "py", "source") or key.endswith(".py"):
            errors.append(f"patch[{i}] python source modification forbidden")
            continue

        if ptype == "policy_yaml":
            if p.get("content"):
                errors.append(f"patch[{i}] full policy content not allowed")
                continue
            from backend.services.decision_policy_engine import parse_policy_patch_key

            policy_name, rule_id, field = parse_policy_patch_key(key, p.get("policy"))
            if policy_name.lower() != "master_close":
                errors.append(f"patch[{i}] policy {policy_name} not allowed")
                continue
            if not rule_id:
                errors.append(f"patch[{i}] policy_yaml requires rule id")
                continue
            # �������ֺϷ�д����rule_id.field���������� �� rule��dict value ���ֶΣ�
            if field is None and not isinstance(val, dict):
                errors.append(
                    f"patch[{i}] policy_yaml requires field key or dict value"
                )
                continue
            continue

        if key not in WHITELIST_TUNING_KEYS:
            errors.append(f"patch[{i}] key '{key}' not in whitelist")
            continue

        # [2026-07-11 �޸�] ԭ�߼�ֻ�� val �Ѿ��� int/float ʱ���� ��20% delta У�飬
        # �� val ���ַ���ռλ������ "<need baseline>"/"TBD"�����Ȳ�����Ҳ��У�飬
        # ֱ�ӷ��г�"�Ϸ�"�᰸��������"���᰸"��һ·�����˶��У�������д��
        # runtime_tuning.json ��Ⱦ��ֵ�����á������ WHITELIST_TUNING_KEYS ��ʽҪ��
        # ��ֵ���ͣ��� bool �� int/float ֱ�ӷ��У������ַ�������ת��������һ��Ӳ�ܾ���
        if isinstance(val, bool):
            errors.append(f"patch[{i}] {key} value must be numeric, got bool")
            continue
        if isinstance(val, str):
            try:
                val = float(val)
            except (TypeError, ValueError):
                errors.append(
                    f"patch[{i}] {key} value '{val[:40]}' ���ǺϷ���ֵ"
                    f"����ֹռλ��/��Ȼ������������� tuning_baseline ����������֣�"
                )
                continue
        if not isinstance(val, (int, float)):
            errors.append(f"patch[{i}] {key} value type {type(val).__name__} ����ֵ")
            continue

        cur = _current_tuning_value(key)
        if cur is not None and cur != 0:
            delta = abs(float(val) - cur) / abs(cur)
            if delta > max_delta:
                errors.append(
                    f"patch[{i}] {key} delta {delta:.1%} exceeds ��{max_delta:.0%}"
                )

    return len(errors) == 0, errors


def run_review_agent(
    db,
    proposal_row: Any,
    context_pack: Dict[str, Any],
) -> Dict[str, Any]:
    """���� Review Sidecar agent��"""
    from backend.services.opencode_bridge import (
        _extract_json,
        load_review_system_prompt,
        run_http_agent_message,
    )

    try:
        payload = json.loads(proposal_row.proposal_json or "{}")
    except Exception:
        payload = {}

    review_input = {
        "proposal": {
            "id": proposal_row.id,
            "severity": proposal_row.severity,
            "title": proposal_row.title,
            "patches": payload.get("patches") or [],
        },
        "context_pack": context_pack,
        "hard_validation": {"passed": True},
    }
    user_text = (
        "Review the proposal below per system instructions. "
        "Return ONLY valid JSON (no markdown fences).\n\n"
        f"--- REVIEW INPUT ---\n{json.dumps(review_input, ensure_ascii=False)}"
    )

    raw_text, err = run_http_agent_message(
        system_prompt=load_review_system_prompt(context_pack),
        user_text=user_text,
        agent=_review_agent(),
        model_slug=_review_model(),
        session_title=f"Proposal review #{proposal_row.id}",
        timeout_s=_review_timeout_s(),
    )
    if err:
        try:
            from backend.services.prompt_trace_service import append_prompt_trace

            append_prompt_trace(
                task_id="task_proposal_review",
                consumer="opencode_proposal_reviewer",
                ok=False,
                error=err,
                extra={"proposal_id": proposal_row.id},
            )
        except Exception:
            pass
        return {"decision": "defer", "confidence": 0.0, "reasons": [err], "error": err}

    try:
        from backend.services.prompt_trace_service import append_prompt_trace

        append_prompt_trace(
            task_id="task_proposal_review",
            consumer="opencode_proposal_reviewer",
            ok=True,
            extra={"proposal_id": proposal_row.id},
        )
    except Exception:
        pass

    result = _extract_json(raw_text or "")
    result["agent"] = _review_agent()
    result["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    return result


def _save_review_metadata(db, proposal_row: Any, review: Dict[str, Any]) -> None:
    from backend.database.connection import sqlite_write_commit

    try:
        payload = json.loads(proposal_row.proposal_json or "{}")
    except Exception:
        payload = {}
    payload["review"] = review
    proposal_row.proposal_json = json.dumps(payload, ensure_ascii=False)
    sqlite_write_commit(db)


def _emit_proposal_rejected_alert(db, proposal_row: Any, review: Dict[str, Any]) -> None:
    from backend.services.opencode_action_router import emit_proposal_rejected_alert

    emit_proposal_rejected_alert(db, proposal_row, review)


def review_and_apply_proposal(db, proposal_id: int) -> Dict[str, Any]:
    """Ӳ���� �� LLM ���� �� apply / reject / defer��"""
    from backend.database.models import OpenCodeEvolutionProposalDB
    from backend.services.opencode_context_pack import build_context_pack
    from backend.services.opencode_proposal_applier import apply_proposal, reject_proposal

    row = db.query(OpenCodeEvolutionProposalDB).filter(
        OpenCodeEvolutionProposalDB.id == proposal_id
    ).first()
    if not row:
        return {"proposal_id": proposal_id, "error": "not found"}
    if row.status != "pending":
        return {"proposal_id": proposal_id, "status": row.status, "skipped": "not pending"}

    try:
        payload = json.loads(row.proposal_json or "{}")
    except Exception:
        payload = {}
    patches = payload.get("patches") or []

    ok, hard_errors = validate_patches_hard(patches)
    if not ok:
        review = {
            "decision": "reject",
            "confidence": 1.0,
            "reasons": hard_errors,
            "risks": [],
            "source": "hard_validation",
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
        _save_review_metadata(db, row, review)
        reject_proposal(db, proposal_id, reason="; ".join(hard_errors)[:500])
        _emit_proposal_rejected_alert(db, row, review)
        return {"proposal_id": proposal_id, "status": "rejected", "review": review}

    try:
        from backend.config.settings import TRAINING_AUTO_APPLY_MAJOR
        from backend.services.training_phase_service import is_active

        if (
            TRAINING_AUTO_APPLY_MAJOR
            and is_active()
            and (row.severity or "").lower() == "major"
        ):
            review = {
                "decision": "approve",
                "confidence": 1.0,
                "reasons": ["training_auto_apply_major"],
                "source": "training_orchestrator",
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
            }
            _save_review_metadata(db, row, review)
            result = apply_proposal(db, proposal_id, patches_override=patches)
            result["review"] = review
            return result
    except Exception as err:
        logger.debug("[ProposalReviewer] training major auto: %s", err)

    context_pack = build_context_pack(db, window="24h", domain="ai")
    review = run_review_agent(db, row, context_pack)

    # P2-9: �����ض��᰸(major/critical)���ö�����������ǿ������
    if (row.severity or "").lower() in ("major", "critical") and not review.get("error"):
        try:
            from backend.services.opencode_bridge import run_multi_round_analysis
            # ��ȡ�᰸�漰�Ĺؼ� symbol
            _patches = payload.get("patches") or []
            _symbol = "BTC"  # Ĭ��
            for p in _patches:
                if isinstance(p, dict):
                    sym = p.get("symbol") or p.get("key", "").split(".")[0]
                    if sym and sym not in ("", "global"):
                        _symbol = str(sym).upper()
                        break
            multi_round = run_multi_round_analysis(
                symbol=_symbol,
                market_context={
                    "proposal": {
                        "id": row.id,
                        "title": row.title,
                        "severity": row.severity,
                        "patches": _patches,
                    },
                    "current_review": review,
                },
                rounds=4,
            )
            if multi_round.get("consensus_score") is not None:
                review["multi_round"] = {
                    "consensus_score": multi_round.get("consensus_score", 0),
                    "dissenting_points": multi_round.get("dissenting_points", []),
                    "rounds_completed": multi_round.get("rounds_completed", 0),
                }
                logger.info(
                    "[ProposalReviewer] P2-9 �������� #%d: consensus=%.1f rounds=%d",
                    row.id,
                    multi_round.get("consensus_score", 0),
                    multi_round.get("rounds_completed", 0),
                )
        except Exception as mr_err:
            logger.debug("[ProposalReviewer] P2-9 multi_round fallback: %s", mr_err)

    if review.get("error"):
        _save_review_metadata(db, row, review)
        return {"proposal_id": proposal_id, "status": "pending", "review": review, "deferred": True}

    decision = str(review.get("decision") or "defer").lower()
    confidence = float(review.get("confidence") or 0.0)
    min_conf = _review_min_confidence()

    if decision == "defer":
        defer_count = int((payload.get("review") or {}).get("defer_count") or 0) + 1
        review["defer_count"] = defer_count
        if defer_count >= 3:
            review["decision"] = "reject"
            review.setdefault("reasons", []).append("defer_max_retries_exceeded")
            decision = "reject"
        else:
            review["defer_at"] = datetime.now(timezone.utc).isoformat()

    if decision == "approve" and confidence < min_conf:
        decision = "defer"
        review["decision"] = "defer"
        review.setdefault("reasons", []).append(
            f"confidence {confidence:.2f} below threshold {min_conf:.2f}"
        )

    _save_review_metadata(db, row, review)

    if decision == "approve":
        approved = review.get("approved_patches") or patches
        if not isinstance(approved, list) or not approved:
            approved = patches
        result = apply_proposal(db, proposal_id, patches_override=approved)
        result["review"] = review
        return result

    if decision == "reject":
        reasons = review.get("reasons") or []
        reject_proposal(db, proposal_id, reason="; ".join(str(r) for r in reasons)[:500])
        _emit_proposal_rejected_alert(db, row, review)
        return {"proposal_id": proposal_id, "status": "rejected", "review": review}

    return {"proposal_id": proposal_id, "status": "pending", "review": review, "deferred": True}


def _should_retry_defer(review: Optional[Dict[str, Any]]) -> bool:
    if not review:
        return True
    decision = str(review.get("decision") or "").lower()
    # ���ݾɸ�ʽ������ Sidecar/API �����д�� defer_at/defer_count/error��
    # ��û�� decision=defer������ pending �᰸����������
    if decision != "defer" and not (
        review.get("defer_at")
        or review.get("defer_count")
        or review.get("error")
    ):
        return False
    reviewed_at = review.get("defer_at") or review.get("reviewed_at")
    if not reviewed_at:
        return True
    try:
        ts = datetime.fromisoformat(str(reviewed_at).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = time.time() - ts.timestamp()
        return age >= _defer_retry_s()
    except Exception:
        return True


def review_pending_proposals(db, limit: int = 10) -> Dict[str, Any]:
    """ɨ�� pending �᰸������"""
    if not _auto_review_enabled():
        return {"reviewed": 0, "skipped": "OPENCODE_AUTO_REVIEW=false"}

    from backend.database.models import OpenCodeEvolutionProposalDB

    rows = (
        db.query(OpenCodeEvolutionProposalDB)
        .filter(OpenCodeEvolutionProposalDB.status == "pending")
        .order_by(OpenCodeEvolutionProposalDB.id.asc())
        .limit(limit * 3)
        .all()
    )

    results: List[Dict[str, Any]] = []
    for row in rows:
        if len(results) >= limit:
            break
        try:
            payload = json.loads(row.proposal_json or "{}")
        except Exception:
            payload = {}
        review = payload.get("review")
        if review and not _should_retry_defer(review):
            continue
        results.append(review_and_apply_proposal(db, row.id))

    # P1-4: ������¼����ָ��
    _log_quality_summary(results)

    return {
        "reviewed": len(results),
        "results": results,
    }


def drain_pending_proposals(db, *, limit: int = 30, max_rounds: int = 3) -> Dict[str, Any]:
    """������������ pending �᰸�����ɨβ / �ֶ� drain����"""
    if not _auto_review_enabled():
        return {"drained": 0, "skipped": "OPENCODE_AUTO_REVIEW=false"}

    total = 0
    all_results: List[Dict[str, Any]] = []
    rounds = 0
    for _ in range(max(1, max_rounds)):
        out = review_pending_proposals(db, limit=limit)
        n = int(out.get("reviewed") or 0)
        total += n
        rounds += 1
        all_results.extend(out.get("results") or [])
        if n == 0:
            break

    logger.info("[ProposalReviewer] drain_pending: rounds=%d drained=%d", rounds, total)
    return {"drained": total, "rounds": rounds, "results": all_results[-20:]}


# �T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T
#  P1-4: �᰸�������
# �T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T

def _log_quality_summary(results: List[Dict[str, Any]]) -> None:
    """��¼�������������ժҪ��ͨ���ʡ��ܾ�ԭ��ֲ�����"""
    if not results:
        return
    approved = sum(1 for r in results if r.get("status") == "paper_applying")
    rejected = sum(1 for r in results if r.get("status") == "rejected")
    deferred = sum(1 for r in results if r.get("deferred"))
    hard_rejected = sum(
        1 for r in results
        if (r.get("review") or {}).get("source") == "hard_validation"
    )
    logger.info(
        "[ProposalQuality] ��������: total=%d approved=%d rejected=%d(hard=%d) deferred=%d "
        "ͨ����=%.0f%%",
        len(results), approved, rejected, hard_rejected, deferred,
        approved / max(len(results), 1) * 100,
    )


def get_proposal_quality_metrics(db) -> Dict[str, Any]:
    """��ȡ�᰸������ȫ��ָ�꣬��ǰ�˼��������ѡ�

    Returns:
        {
            "whitelist_pass_rate": 0.0-1.0,    # ������У��ͨ����
            "hard_reject_rate": 0.0-1.0,       # Ӳ�Ž��ܾ���
            "llm_review_pass_rate": 0.0-1.0,   # LLM ����ͨ����
            "avg_time_to_validate_h": float,   # ƽ����֤ʱ��
            "stale_pending_count": int,        # ��24hδ����� pending ��
            # ���� PnL ����ɹ�ָ�꣨�����᰸�Ƿ���ĸ���ӯ��������
            "improved_avg_pnl_boost": float,    # improved �᰸��ÿ�� PnL ƽ������($)
            "degraded_rate": 0.0-1.0,          # ��֤���˻�������Խ��Խ�ã�
            "total_pnl_impact": float,          # ���� validated �᰸�ۼ� PnL Ӱ��
        }
    """
    from backend.database.models import OpenCodeEvolutionProposalDB

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    all_proposals = db.query(OpenCodeEvolutionProposalDB).order_by(
        OpenCodeEvolutionProposalDB.id.desc()
    ).limit(200).all()

    if not all_proposals:
        return {"whitelist_pass_rate": 0, "hard_reject_rate": 0,
                "llm_review_pass_rate": 0, "avg_time_to_validate_h": 0,
                "stale_pending_count": 0,
                "improved_avg_pnl_boost": 0, "degraded_rate": 0, "total_pnl_impact": 0}

    # ͳ��Ӳ�Ž��ܾ���review source == hard_validation��
    hard_rejected = 0
    llm_approved = 0
    llm_total = 0
    total_reviewed = 0
    validate_durations_h: List[float] = []
    stale_pending = 0

    # PnL ����ɹ�ͳ��
    pnl_boosts: List[float] = []
    degraded_count = 0
    validated_evaluated = 0
    total_pnl_impact = 0.0

    for row in all_proposals:
        st = row.status or ""
        try:
            payload = json.loads(row.proposal_json or "{}")
        except Exception:
            payload = {}
        review = payload.get("review") or {}

        source = str(review.get("source") or "")
        decision = str(review.get("decision") or "")

        if st == "pending":
            created = row.created_at
            if created and created.tzinfo is not None:
                created = created.replace(tzinfo=None)
            if created:
                age_h = (now_naive - created).total_seconds() / 3600
                if age_h > 24:
                    stale_pending += 1

        if source == "hard_validation":
            hard_rejected += 1
            total_reviewed += 1
        elif decision in ("approve", "reject"):
            total_reviewed += 1
            llm_total += 1
            if decision == "approve":
                llm_approved += 1

        # ��֤ʱ��
        if st in ("paper_validated", "rolled_back", "inconclusive") and row.applied_at:
            applied = row.applied_at
            if applied.tzinfo is not None:
                applied = applied.replace(tzinfo=None)
            validated = row.validated_at or now_naive
            if validated.tzinfo is not None:
                validated = validated.replace(tzinfo=None)
            validate_durations_h.append(
                (validated - applied).total_seconds() / 3600
            )

        # PnL ����ͳ��ÿ���������᰸�� PnL �仯
        if st in ("paper_validated", "rolled_back") and row.after_json:
            try:
                after = json.loads(row.after_json or "{}")
                em = after.get("eval_metrics") or {}
                base_avg = float(em.get("baseline_avg_pnl") or 0)
                aft_avg = float(em.get("after_avg_pnl") or 0)
                verdict = after.get("verdict", "?")
                validated_evaluated += 1
                delta = aft_avg - base_avg
                total_pnl_impact += delta
                if verdict == "improved":
                    pnl_boosts.append(delta)
                elif verdict == "degraded":
                    degraded_count += 1
            except Exception:
                pass

    total = len(all_proposals)
    whitelist_pass = total - hard_rejected

    return {
        "total_scanned": total,
        "whitelist_pass_rate": round(whitelist_pass / max(total, 1), 3),
        "hard_reject_rate": round(hard_rejected / max(total_reviewed, 1), 3),
        "llm_review_pass_rate": round(llm_approved / max(llm_total, 1), 3),
        "avg_time_to_validate_h": round(
            sum(validate_durations_h) / max(len(validate_durations_h), 1), 1
        ),
        "stale_pending_count": stale_pending,
        "pending_total": sum(1 for r in all_proposals if (r.status or "") == "pending"),
        "paper_validated_total": sum(1 for r in all_proposals if (r.status or "") == "paper_validated"),
        # PnL �ɹ�ָ��
        "improved_avg_pnl_boost": round(
            sum(pnl_boosts) / max(len(pnl_boosts), 1), 2
        ),
        "degraded_rate": round(degraded_count / max(validated_evaluated, 1), 3),
        "total_pnl_impact": round(total_pnl_impact, 2),
        "net_positive_proposals": len(pnl_boosts),
    }
