"""训练期只读观测 API。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends

from backend.database.connection import get_db

router = APIRouter(prefix="/api/training-phase", tags=["TrainingPhase"])


@router.get("/status")
def training_phase_status(db=Depends(get_db)) -> Dict[str, Any]:
    from backend.services.training_phase_service import status_snapshot
    from backend.services.opencode_proposal_applier import evaluate_proposals_summary
    from backend.services.runtime_tuning_store import list_overlays

    snap = status_snapshot()
    snap["funnel"] = evaluate_proposals_summary(db)
    snap["overlays"] = list_overlays()
    return snap
