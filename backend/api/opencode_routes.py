"""OpenCode 智能分析层 API。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.database.connection import get_db

router = APIRouter(prefix="/api/opencode", tags=["OpenCode"])

REPORT_DIR = os.path.join("data", "opencode_reports")
POLICY_DIR = os.path.join("data", "decision_policies")


class PacePatch(BaseModel):
    gear: str
    manual: bool = True


class TuningPatch(BaseModel):
    patches: Dict[str, Any]


@router.get("/status")
def opencode_status() -> Dict[str, Any]:
    from backend.services.opencode_bridge import get_bridge_status, health_check
    from backend.services.opencode_shadow_worker import shadow_status
    from backend.services.paper_pace_controller import paper_pace_controller

    try:
        from backend.services.opencode_sidecar import sidecar_status
        _sidecar = sidecar_status()
    except Exception:
        _sidecar = {}

    return {
        "bridge": get_bridge_status(),
        "serve_healthy": health_check(),
        "pace": paper_pace_controller.to_dict(),
        "shadow": shadow_status(),
        "sidecar": _sidecar,
    }


@router.get("/reports/latest")
def latest_report(
    window: str = Query("24h"),
    domain: str = Query("ai"),
    db=Depends(get_db),
) -> Dict[str, Any]:
    from backend.services.strategy_runtime_report import get_or_build_runtime_report, _report_is_empty

    data = get_or_build_runtime_report(db, window=window, domain=domain)
    if _report_is_empty(data):
        raise HTTPException(
            503,
            "runtime report empty: no closed trades in window; check DATABASE_URL and paper_positions",
        )
    return data


@router.post("/analyze")
def trigger_analysis(
    window: str = Query("24h"),
    domain: str = Query("ai"),
    db=Depends(get_db),
) -> Dict[str, Any]:
    from backend.services.opencode_bridge import run_scheduled_analysis
    return run_scheduled_analysis(db, window=window, domain=domain)


@router.get("/insights")
def list_insights(db=Depends(get_db), limit: int = 20) -> Dict[str, Any]:
    from backend.database.models import OpenCodeInsightDB

    rows = (
        db.query(OpenCodeInsightDB)
        .order_by(OpenCodeInsightDB.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "items": [
            {
                "id": r.id,
                "severity": r.severity,
                "title": r.title,
                "status": r.status,
                "category": r.category,
                "window": r.window,
                "domain": r.domain,
                "created_at": str(r.created_at),
            }
            for r in rows
        ],
        "open_major_count": sum(1 for r in rows if r.status == "open" and r.severity in ("major", "critical")),
    }


@router.get("/insights/{insight_id}")
def get_insight(insight_id: int, db=Depends(get_db)) -> Dict[str, Any]:
    from backend.database.models import OpenCodeInsightDB

    row = db.query(OpenCodeInsightDB).filter(OpenCodeInsightDB.id == insight_id).first()
    if not row:
        raise HTTPException(404, "insight not found")
    finding: Any = {}
    try:
        finding = json.loads(row.finding_json or "{}")
    except Exception:
        finding = {"raw": row.finding_json}
    return {
        "id": row.id,
        "severity": row.severity,
        "title": row.title,
        "status": row.status,
        "category": row.category,
        "window": row.window,
        "domain": row.domain,
        "source": row.source,
        "finding": finding,
        "created_at": str(row.created_at),
        "resolved_at": str(row.resolved_at) if row.resolved_at else None,
    }


@router.get("/proposals")
def list_proposals(db=Depends(get_db), status: Optional[str] = None) -> Dict[str, Any]:
    from backend.database.models import OpenCodeEvolutionProposalDB

    q = db.query(OpenCodeEvolutionProposalDB).order_by(OpenCodeEvolutionProposalDB.id.desc())
    if status:
        q = q.filter(OpenCodeEvolutionProposalDB.status == status)
    rows = q.limit(50).all()
    return {
        "items": [
            {
                "id": r.id,
                "title": r.title,
                "status": r.status,
                "patch_type": r.patch_type,
                "severity": r.severity,
                "created_at": str(r.created_at),
            }
            for r in rows
        ]
    }


@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: int, db=Depends(get_db)) -> Dict[str, Any]:
    from backend.database.models import OpenCodeEvolutionProposalDB

    row = db.query(OpenCodeEvolutionProposalDB).filter(
        OpenCodeEvolutionProposalDB.id == proposal_id
    ).first()
    if not row:
        raise HTTPException(404, "proposal not found")

    def _parse(text: Optional[str]) -> Any:
        try:
            return json.loads(text or "{}")
        except Exception:
            return {"raw": text}

    return {
        "id": row.id,
        "title": row.title,
        "status": row.status,
        "patch_type": row.patch_type,
        "severity": row.severity,
        "source": row.source,
        "proposal": _parse(row.proposal_json),
        "baseline": _parse(row.baseline_json),
        "after": _parse(row.after_json),
        "requires_paper_validation": row.requires_paper_validation,
        "requires_manual_live_confirm": row.requires_manual_live_confirm,
        "applied_at": str(row.applied_at) if row.applied_at else None,
        "validated_at": str(row.validated_at) if row.validated_at else None,
        "created_at": str(row.created_at),
        "updated_at": str(row.updated_at),
    }


@router.post("/proposals/{proposal_id}/apply")
def apply_proposal_route(proposal_id: int, db=Depends(get_db)) -> Dict[str, Any]:
    from backend.database.models import OpenCodeEvolutionProposalDB
    from backend.services.opencode_proposal_applier import apply_proposal
    from backend.services.opencode_proposal_reviewer import validate_patches_hard

    # 手动 apply 同样必须通过硬规则校验（白名单 + delta 上限 + policy 范围），
    # 杜绝「绕过 reviewer 直接改参」的后门——与自动 review 链路保持同一道闸。
    row = db.query(OpenCodeEvolutionProposalDB).filter(
        OpenCodeEvolutionProposalDB.id == proposal_id
    ).first()
    if not row:
        raise HTTPException(404, "proposal not found")
    try:
        payload = json.loads(row.proposal_json or "{}")
    except Exception:
        payload = {}
    patches = payload.get("patches") or []
    ok, errors = validate_patches_hard(patches)
    if not ok:
        raise HTTPException(400, f"hard validation failed: {'; '.join(errors)[:400]}")

    try:
        return apply_proposal(db, proposal_id)
    except ValueError as err:
        raise HTTPException(400, str(err)) from err


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal_route(
    proposal_id: int,
    db=Depends(get_db),
    reason: str = Query("", max_length=500),
) -> Dict[str, Any]:
    from backend.services.opencode_proposal_applier import reject_proposal

    try:
        return reject_proposal(db, proposal_id, reason=reason)
    except ValueError as err:
        raise HTTPException(400, str(err)) from err


@router.post("/proposals/evaluate-now")
def evaluate_proposals_now(
    db=Depends(get_db),
    force: bool = Query(False, description="忽略浸泡等待，post-apply 样本够即评 paper_applying"),
) -> Dict[str, Any]:
    """手动触发提案 paper 验证（post-apply 切片 + Pace 联动时长）。"""
    from backend.services.opencode_proposal_applier import evaluate_proposals_summary
    return evaluate_proposals_summary(db, force=force)


@router.post("/proposals/backfill")
def backfill_proposals(db=Depends(get_db)) -> Dict[str, Any]:
    from backend.services.opencode_proposal_applier import backfill_proposals_from_reports

    n = backfill_proposals_from_reports(db)
    return {"created": n}


@router.post("/proposals/review-all")
def review_all_proposals(db=Depends(get_db), limit: int = 10) -> Dict[str, Any]:
    from backend.services.opencode_proposal_reviewer import review_pending_proposals

    return review_pending_proposals(db, limit=limit)


@router.post("/proposals/drain-pending")
def drain_pending_proposals_route(
    db=Depends(get_db),
    limit: int = Query(30, ge=1, le=100),
    max_rounds: int = Query(3, ge=1, le=10),
) -> Dict[str, Any]:
    """多轮批量处理 pending 提案（启动扫尾可手动触发）。"""
    from backend.services.opencode_proposal_reviewer import drain_pending_proposals

    return drain_pending_proposals(db, limit=limit, max_rounds=max_rounds)


@router.post("/proposals/{proposal_id}/review")
def review_proposal_route(proposal_id: int, db=Depends(get_db)) -> Dict[str, Any]:
    from backend.services.opencode_proposal_reviewer import review_and_apply_proposal

    return review_and_apply_proposal(db, proposal_id)


@router.post("/proposals/{proposal_id}/rollback")
def rollback_proposal(proposal_id: int, db=Depends(get_db)) -> Dict[str, Any]:
    from backend.database.models import OpenCodeEvolutionProposalDB
    from backend.database.connection import sqlite_write_commit
    from backend.services.runtime_tuning_store import rollback_snapshot
    from backend.services.decision_policy_engine import rollback_policy_snapshot

    row = db.query(OpenCodeEvolutionProposalDB).filter(
        OpenCodeEvolutionProposalDB.id == proposal_id
    ).first()
    if not row:
        raise HTTPException(404, "proposal not found")
    if row.status not in ("paper_applying", "paper_validated", "applied", "validating"):
        raise HTTPException(400, "proposal not eligible for rollback")

    # 同时回滚 tuning 与 policy 两类快照——补齐此前「只回 tuning、policy 永久残留」
    # 的断点，纯 policy_yaml 提案也能被正确回滚。
    tuning_ok = rollback_snapshot(proposal_id)
    policy_n = rollback_policy_snapshot(proposal_id)
    if not tuning_ok and policy_n == 0:
        raise HTTPException(404, "rollback snapshot not found (neither tuning nor policy)")
    row.status = "rolled_back"
    sqlite_write_commit(db)
    return {
        "ok": True,
        "proposal_id": proposal_id,
        "status": row.status,
        "tuning_rolled_back": bool(tuning_ok),
        "policy_files_restored": policy_n,
    }


@router.get("/tuning")
def get_runtime_tuning() -> Dict[str, Any]:
    from backend.services.runtime_tuning_store import get_all_tuning
    return get_all_tuning()


@router.patch("/tuning")
def patch_runtime_tuning(body: TuningPatch) -> Dict[str, Any]:
    from backend.services.runtime_tuning_store import apply_patches, get_all_tuning

    if not body.patches:
        raise HTTPException(400, "patches required")
    applied = apply_patches(body.patches)
    return {"applied": applied, "tuning": get_all_tuning()}


@router.get("/reports/dir")
def reports_listing() -> Dict[str, Any]:
    if not os.path.isdir(REPORT_DIR):
        return {"files": []}
    files = sorted(
        [f for f in os.listdir(REPORT_DIR) if f.endswith((".md", ".json"))],
        reverse=True,
    )[:30]
    return {"files": files, "dir": REPORT_DIR}


@router.get("/reports/content")
def report_content(file: str = Query(...)) -> Dict[str, Any]:
    safe = os.path.basename(file)
    if safe != file or ".." in file:
        raise HTTPException(400, "invalid filename")
    path = os.path.join(REPORT_DIR, safe)
    if not os.path.isfile(path):
        raise HTTPException(404, "file not found")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    fmt = "markdown" if safe.endswith(".md") else "json"
    parsed: Any = None
    if fmt == "json":
        try:
            parsed = json.loads(content)
        except Exception:
            parsed = None
    return {"file": safe, "format": fmt, "content": content, "parsed": parsed}


@router.get("/config")
def opencode_config() -> Dict[str, Any]:
    from backend import config

    s = config.settings
    return {
        "OPENCODE_ENABLED": getattr(s, "OPENCODE_ENABLED", False),
        "OPENCODE_SERVER_URL": getattr(s, "OPENCODE_SERVER_URL", ""),
        "OPENCODE_AGENT_PLAN": getattr(s, "OPENCODE_AGENT_PLAN", ""),
        "OPENCODE_AGENT_BUILD": getattr(s, "OPENCODE_AGENT_BUILD", ""),
        "OPENCODE_AUTO_APPLY_MINOR_DEPRECATED": True,  # review 流程已接管，此开关不再生效
        "OPENCODE_PATCH_MAX_DELTA_PCT": getattr(s, "OPENCODE_PATCH_MAX_DELTA_PCT", 0.2),
        "OPENCODE_VALIDATION_HOURS": getattr(s, "OPENCODE_VALIDATION_HOURS", 24),
        "OPENCODE_VALIDATION_USE_PACE": getattr(s, "OPENCODE_VALIDATION_USE_PACE", True),
        "OPENCODE_MAJOR_ALERT_CHANNELS": getattr(s, "OPENCODE_MAJOR_ALERT_CHANNELS", ""),
        "OPENCODE_CLI_PATH": getattr(s, "OPENCODE_CLI_PATH", ""),
        "OPENCODE_BRIDGE_TRANSPORT": getattr(s, "OPENCODE_BRIDGE_TRANSPORT", "http"),
        "OPENCODE_REQUEST_TIMEOUT_S": getattr(s, "OPENCODE_REQUEST_TIMEOUT_S", 180),
        "OPENCODE_MAJOR_ALERT_COOLDOWN_S": getattr(s, "OPENCODE_MAJOR_ALERT_COOLDOWN_S", 3600),
        "OPENCODE_MAJOR_CREATE_PROPOSALS": getattr(s, "OPENCODE_MAJOR_CREATE_PROPOSALS", True),
        "OPENCODE_MAJOR_AUTO_APPLY": getattr(s, "OPENCODE_MAJOR_AUTO_APPLY", False),
        "OPENCODE_MAJOR_PACE_DOWNSHIFT_STEPS": getattr(s, "OPENCODE_MAJOR_PACE_DOWNSHIFT_STEPS", 1),
        "OPENCODE_MAJOR_PACE_FLOOR": getattr(s, "OPENCODE_MAJOR_PACE_FLOOR", "balanced"),
        "OPENCODE_MODEL": getattr(s, "OPENCODE_MODEL", "deepseek/deepseek-v4-pro"),
        "OPENCODE_SMALL_MODEL": getattr(s, "OPENCODE_SMALL_MODEL", "deepseek/deepseek-v4-flash"),
        "OPENCODE_AUTO_REVIEW": getattr(s, "OPENCODE_AUTO_REVIEW", True),
        "OPENCODE_AGENT_REVIEW": getattr(s, "OPENCODE_AGENT_REVIEW", "review"),
        "OPENCODE_REVIEW_MODEL": getattr(s, "OPENCODE_REVIEW_MODEL", "deepseek/deepseek-v4-flash"),
        "OPENCODE_REVIEW_MIN_CONFIDENCE": getattr(s, "OPENCODE_REVIEW_MIN_CONFIDENCE", 0.7),
        "OPENCODE_REVIEW_DEFER_RETRY_S": getattr(s, "OPENCODE_REVIEW_DEFER_RETRY_S", 3600),
        "OPENCODE_SHADOW_PORT": getattr(s, "OPENCODE_SHADOW_PORT", 8001),
        "OPENCODE_SHADOW_ENABLED": getattr(s, "OPENCODE_SHADOW_ENABLED", False),
        "PAPER_PACE_DEFAULT_GEAR": os.getenv("PAPER_PACE_DEFAULT_GEAR", "turbo"),
        # 以下开关目前代码无消费方/仅展示，列出以免误以为可调控行为：
        #  - OPENCODE_MAJOR_AUTO_APPLY: major 提案统一走 review 链路，此开关不生效
        #  - OPENCODE_BRIDGE_TRANSPORT: bridge 固定 http，transport 切换未接线
        #  - OPENCODE_AGENT_BUILD: build agent 未独立调用，复用 plan agent
        "_unwired_flags": [
            "OPENCODE_MAJOR_AUTO_APPLY",
            "OPENCODE_BRIDGE_TRANSPORT",
            "OPENCODE_AGENT_BUILD",
        ],
        "note": "修改以上开关需编辑 .env 并重启后端；_unwired_flags 列出的开关当前不生效",
    }


@router.get("/governor/ownership")
def governor_ownership() -> Dict[str, Any]:
    """参数所有权地图：每个受管 key 当前 owner / 目标值 / 生效值 / 候选意图。"""
    from backend.services.runtime_governor import runtime_governor as gov
    return {"ownership": gov.get_ownership_map()}


@router.get("/governor/decisions")
def governor_decisions(limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
    """仲裁决策日志：最近 N 条 key 归属变更（谁、为何、生效值）。"""
    from backend.services.runtime_governor import runtime_governor as gov
    return {"decisions": gov.recent_decisions(limit=limit)}


@router.get("/governor/funnel")
def proposal_funnel(db=Depends(get_db)) -> Dict[str, Any]:
    """提案成效漏斗：按状态计数 + 已评估提案的 improved/neutral/degraded 三态分布。

    用于回答「提案到底有没有用」：从 pending → applied → 验证结论的转化全景。
    """
    from backend.database.models import OpenCodeEvolutionProposalDB
    from datetime import datetime, timezone

    rows = db.query(OpenCodeEvolutionProposalDB).all()
    by_status: Dict[str, int] = {}
    verdicts: Dict[str, int] = {"improved": 0, "neutral": 0, "degraded": 0, "unevaluated": 0}
    inconclusive_reasons: Dict[str, int] = {}
    applying: List[Dict[str, Any]] = []
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)

    for r in rows:
        st = (r.status or "unknown")
        by_status[st] = by_status.get(st, 0) + 1
        verdict = None
        after: Dict[str, Any] = {}
        try:
            after = json.loads(r.after_json or "{}")
            verdict = after.get("verdict")
        except Exception:
            verdict = None
        verdicts[verdict if verdict in verdicts else "unevaluated"] += 1

        if st == "inconclusive":
            reason = str(after.get("eval_skipped") or "unknown")
            inconclusive_reasons[reason] = inconclusive_reasons.get(reason, 0) + 1

        if st == "paper_applying" and r.applied_at:
            applied_at = r.applied_at
            if applied_at.tzinfo is not None:
                applied_at = applied_at.replace(tzinfo=None)
            age_h = (now_naive - applied_at).total_seconds() / 3600.0
            try:
                baseline = json.loads(r.baseline_json or "{}")
                perf = baseline.get("baseline_perf") or {}
            except Exception:
                perf = {}
            applying.append({
                "id": r.id,
                "title": r.title,
                "applied_at": str(r.applied_at),
                "age_hours": round(age_h, 2),
                "post_apply_closed": after.get("post_apply_closed", 0),
                "baseline_wr": perf.get("win_rate"),
                "verdict_pending": True,
            })

    total = len(rows)
    applied_like = sum(
        by_status.get(s, 0)
        for s in ("applied", "paper_applying", "paper_validated", "validating")
    )
    evaluated = verdicts["improved"] + verdicts["neutral"] + verdicts["degraded"]
    evaluated = max(
        evaluated,
        sum(by_status.get(s, 0) for s in ("paper_validated", "rolled_back", "inconclusive")),
    )
    rolled = by_status.get("rolled_back", 0)
    from backend.services.proposal_validation_policy import validation_policy_for_gear

    try:
        from backend.services.training_phase_service import status_snapshot
        training = status_snapshot()
    except Exception:
        training = {}

    return {
        "total": total,
        "by_status": by_status,
        "verdicts": verdicts,
        "validation_policy": validation_policy_for_gear(),
        "inconclusive_reasons": inconclusive_reasons,
        "paper_applying": applying[:20],
        "training_phase": training,
        "funnel": {
            "created": total,
            "applied": applied_like,
            "evaluated": evaluated,
            "improved": verdicts["improved"],
        },
        "improve_rate": round(verdicts["improved"] / evaluated, 3) if evaluated else None,
        "rollback_rate": round(rolled / evaluated, 3) if evaluated else None,
    }


@router.get("/policies/master_close")
def get_master_close_policy() -> Dict[str, Any]:
    path = os.path.join(POLICY_DIR, "master_close.yaml")
    if not os.path.isfile(path):
        raise HTTPException(404, "policy not found")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return {"name": "master_close", "path": path, "content": content}


@router.get("/paper-pace")
def get_paper_pace() -> Dict[str, Any]:
    from backend.services.paper_pace_controller import paper_pace_controller
    return paper_pace_controller.to_dict()


@router.patch("/paper-pace")
def patch_paper_pace(body: PacePatch) -> Dict[str, Any]:
    from backend.services.paper_pace_controller import paper_pace_controller
    paper_pace_controller.set_gear(body.gear, manual=body.manual)
    return paper_pace_controller.to_dict()


@router.post("/paper-pace/unlock")
def unlock_paper_pace() -> Dict[str, Any]:
    from backend.services.paper_pace_controller import paper_pace_controller
    paper_pace_controller.unlock_manual()
    return paper_pace_controller.to_dict()


@router.post("/sidecar/start")
def sidecar_start() -> Dict[str, Any]:
    """手动启动或收养 OpenCode Sidecar（4096）。"""
    from backend.services.opencode_bridge import health_check
    from backend.services.opencode_sidecar import sidecar_status, start_sidecar

    result = start_sidecar()
    return {"result": result, "sidecar": sidecar_status(), "serve_healthy": health_check()}


@router.post("/sidecar/ensure")
def sidecar_ensure() -> Dict[str, Any]:
    """看门狗：Sidecar 不健康时尝试重启。"""
    from backend.services.opencode_sidecar import ensure_sidecar, sidecar_status
    from backend.services.opencode_bridge import health_check

    result = ensure_sidecar()
    return {"result": result, "sidecar": sidecar_status(), "serve_healthy": health_check()}


@router.post("/shadow/prepare/{proposal_id}")
def prepare_shadow(proposal_id: int, db=Depends(get_db)) -> Dict[str, Any]:
    from backend.services.opencode_shadow_worker import prepare_shadow_worktree
    return prepare_shadow_worktree(proposal_id, db)


@router.post("/shadow/start/{proposal_id}")
def start_shadow(proposal_id: int, db=Depends(get_db)) -> Dict[str, Any]:
    from backend.services.opencode_shadow_worker import start_shadow_server
    return start_shadow_server(proposal_id, db=db)


@router.post("/shadow/stop")
def stop_shadow() -> Dict[str, Any]:
    from backend.services.opencode_shadow_worker import stop_shadow_server, shadow_status
    stop_shadow_server()
    return shadow_status()


@router.get("/shadow/status")
def get_shadow_status() -> Dict[str, Any]:
    from backend.services.opencode_shadow_worker import shadow_status
    return shadow_status()


@router.get("/shadow/compare")
def shadow_compare_srr(
    window: str = Query("24h"),
    domain: str = Query("ai"),
) -> Dict[str, Any]:
    from backend.services.opencode_shadow_worker import compare_shadow_srr
    return compare_shadow_srr(window=window, domain=domain)


@router.get("/prompt-traces")
def prompt_traces(limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
    from backend.services.prompt_trace_service import recent_prompt_traces

    entries = recent_prompt_traces(limit=limit)
    return {"entries": entries, "count": len(entries)}


@router.get("/health/digest")
def opencode_health_digest(
    window_hours: int = Query(24, ge=1, le=168),
) -> Dict[str, Any]:
    """合并 log_digest + health_snapshot，供系统健康 Tab / context_pack 使用。"""
    from backend.services.health_snapshot_service import build_combined_digest
    return build_combined_digest(window_hours=window_hours)


@router.get("/health/log-tail")
def opencode_log_tail(lines: int = Query(200, ge=1, le=2000)) -> Dict[str, Any]:
    from backend.services.log_digest_service import tail_log_lines
    return tail_log_lines(lines=lines)


@router.post("/health/escalate")
def opencode_health_escalate(db=Depends(get_db)) -> Dict[str, Any]:
    """手动触发 ERROR→insight 升级（与 1h 定时 job 相同逻辑）。"""
    from backend.services.log_insight_escalation_service import run_health_digest_tick
    return run_health_digest_tick(db)
