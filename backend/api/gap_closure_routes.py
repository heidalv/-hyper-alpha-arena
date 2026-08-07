"""GAP 闭环 API — 决策审计 / RuntimeGovernor / ReplayHarness / HMAC 链。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/gap-closure", tags=["gap-closure"])


class RuntimePatchBody(BaseModel):
    keys: dict
    reason: str = ""


@router.get("/health")
def gap_closure_health():
    from backend.services.budget_service import budget_service
    from backend.services.runtime_governor import runtime_governor
    return {
        "ok": True,
        "modules": {
            "TradeProposal": True,
            "BudgetService": budget_service is not None,
            "RuntimeGovernor": runtime_governor is not None,
            "AuditChain": True,
            "ReplayHarness": True,
            "ATASProposer": True,
        },
    }


@router.get("/decisions/recent")
def recent_decisions(
    limit: int = Query(50, ge=1, le=200),
    symbol: Optional[str] = None,
    tier: Optional[str] = None,
    source_lane: Optional[str] = None,
):
    from backend.database.connection import AnalyticsSessionLocal
    from backend.database.models import DecisionSnapshot

    db = AnalyticsSessionLocal()
    try:
        q = db.query(DecisionSnapshot).order_by(DecisionSnapshot.timestamp.desc())
        if symbol:
            q = q.filter(DecisionSnapshot.symbol == symbol.upper())
        if tier:
            q = q.filter(DecisionSnapshot.tier == tier.lower())
        if source_lane:
            q = q.filter(DecisionSnapshot.source_lane == source_lane)
        rows = q.limit(limit).all()
        out = []
        for r in rows:
            verdict = getattr(r, "evaluate_verdict_json", None) or {}
            code_reason = None
            if isinstance(verdict, dict):
                code_reason = verdict.get("code_reason") or verdict.get("reason")
            out.append({
                "id": r.id,
                "symbol": r.symbol,
                "tier": r.tier,
                "action": r.action,
                "confidence": r.confidence,
                "source_lane": getattr(r, "source_lane", None),
                "proposal_id": getattr(r, "proposal_id", None),
                "evaluate_verdict": verdict,
                "code_reason": code_reason,
                "executed": getattr(r, "executed", None),
                "execution_channel": getattr(r, "execution_channel", None),
                "content_hash": getattr(r, "content_hash", None),
                "reasoning": (r.ai_reasoning or "")[:500],
                "timestamp": str(r.timestamp) if r.timestamp else None,
            })
        return {"count": len(out), "decisions": out}
    finally:
        db.close()


@router.get("/audit/chain")
def audit_chain(
    limit: int = Query(100, ge=1, le=500),
    symbol: Optional[str] = None,
):
    from backend.database.connection import AnalyticsSessionLocal
    from backend.database.models import DecisionSnapshot
    from backend.services.audit_chain_service import verify_chain

    db = AnalyticsSessionLocal()
    try:
        q = db.query(DecisionSnapshot).order_by(DecisionSnapshot.id.asc())
        if symbol:
            q = q.filter(DecisionSnapshot.symbol == symbol.upper())
        rows = q.limit(limit).all()
        records = []
        for r in rows:
            records.append({
                "id": r.id,
                "symbol": r.symbol,
                "content_hash": getattr(r, "content_hash", None),
                "prev_hash": getattr(r, "prev_hash", None),
                "proposal_json": getattr(r, "proposal_json", None),
                "evaluate_verdict_json": getattr(r, "evaluate_verdict_json", None),
                "timestamp": str(r.timestamp) if r.timestamp else None,
            })
        verification = verify_chain(records)
        return {"records": records, "verification": verification}
    finally:
        db.close()


@router.post("/audit/reconcile")
def audit_reconcile(hours: int = Query(24, ge=1, le=168)):
    from backend.services.snapshot_reconcile_service import reconcile_recent_snapshots
    return reconcile_recent_snapshots(hours=hours)


@router.get("/runtime/pending")
def runtime_pending_patches():
    from backend.services.runtime_governor import runtime_governor
    return {"pending": runtime_governor.list_pending()}


@router.get("/runtime/tuning")
def runtime_tuning_current():
    from backend.services.runtime_tuning_store import get_all_tuning
    return {"tuning": get_all_tuning()}


@router.post("/runtime/propose")
def runtime_propose_patch(body: RuntimePatchBody):
    from backend.services.runtime_governor import runtime_governor
    patch = runtime_governor.propose_patch(body.keys, body.reason)
    return {"ok": True, "patch": patch.to_dict()}


@router.post("/runtime/approve/{patch_id}")
def runtime_approve_patch(patch_id: str):
    from backend.services.runtime_governor import runtime_governor
    if not runtime_governor.approve(patch_id):
        raise HTTPException(status_code=404, detail="patch not found or approve failed")
    return {"ok": True, "patch_id": patch_id}


@router.post("/runtime/reject/{patch_id}")
def runtime_reject_patch(patch_id: str):
    from backend.services.runtime_governor import runtime_governor
    runtime_governor.reject(patch_id)
    return {"ok": True, "patch_id": patch_id}


@router.get("/replay/run")
def replay_run(
    symbol: str = Query("BTC"),
    tier: str = Query("mid"),
    proposer: str = Query("rule", description="rule|atas"),
):
    from backend.services.replay.replay_harness import replay_harness
    report = replay_harness.run(
        symbol=symbol.upper(),
        tier=tier.lower(),
        proposer=proposer.lower(),
    )
    return report.to_dict()


@router.get("/replay/batch")
def replay_batch(
    symbols: str = Query("BTC", description="逗号分隔多标的"),
    tiers: str = Query("short,mid,long", description="逗号分隔 tier，默认三周期全覆盖"),
    proposer: str = Query("rule", description="rule|atas"),
):
    from backend.services.replay.replay_harness import replay_harness
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    tier_list = [t.strip().lower() for t in tiers.split(",") if t.strip()]
    batch = replay_harness.run_batch(sym_list, tier_list, proposer=proposer.lower())
    return batch.to_dict()


@router.get("/budget/layers")
def budget_layers(equity: float = Query(10000.0, gt=0)):
    from backend.services.budget_service import budget_service
    layers = {}
    # 中长线合并后 swing 不再是独立 layer——mid 已并入 long。
    # 仅暴露 scalp/trend 两层；budget_service 内仍保留 swing 层映射，
    # 供既有 DB 仓位（trade_nature='swing'）回放用途，但 API 不再单独展示。
    for layer in ("scalp", "trend"):
        layers[layer] = {
            "cap": budget_service.get_layer_cap(layer, equity),
            "used": budget_service.get_used_margin(layer),
            "available": budget_service.get_layer_budget(layer, equity),
            "allocation": budget_service.layer_allocations.get(layer),
        }
    return {"equity": equity, "layers": layers}
