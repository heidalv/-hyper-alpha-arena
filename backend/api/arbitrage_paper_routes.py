"""Arbitrage Paper Account API — 套利专用模拟账户体系。"""

from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database.connection import get_db
from backend.services.rebate_arb.arbitrage_paper_account_service import (
    arbitrage_paper_account_service,
)


router = APIRouter(prefix="/api/arbitrage-paper", tags=["Arbitrage Paper"])


class CreateArbitragePaperAccountRequest(BaseModel):
    name: str = "套利 Paper 账户"
    total_equity: float = Field(default=300.0, gt=0)
    owner_account_id: Optional[int] = None
    preset_id: str = "small_300u_standard"
    risk_profile: str = "balanced"


class UpdateArbitragePaperAccountRequest(BaseModel):
    name: Optional[str] = None
    risk_profile: Optional[str] = None


class ResetArbitragePaperAccountRequest(BaseModel):
    total_equity: float = Field(default=300.0, gt=0)
    preset_id: str = "small_300u_standard"
    clear_ledger: bool = True


class UpdateBalancesRequest(BaseModel):
    balances: Dict[str, float]


class ApplyPresetRequest(BaseModel):
    preset_id: str
    total_equity: Optional[float] = None


class ValidateStartRequest(BaseModel):
    strategies: List[str] = Field(default_factory=lambda: ["S8"])


class StartPaperRequest(BaseModel):
    strategies: List[str] = Field(default_factory=lambda: ["S8"])


class BindTraderRequest(BaseModel):
    trader_account_id: int = Field(..., gt=0)


@router.get("/bindable-traders")
def list_bindable_traders(paper_account_id: Optional[int] = None, db: Session = Depends(get_db)):
    """已开启专用套利、且策略/执行双模型已配置的交易员列表。"""
    return {
        "traders": arbitrage_paper_account_service.list_bindable_traders(db, paper_account_id),
    }


@router.post("/accounts/{account_id}/bind-trader")
def bind_trader(account_id: int, payload: BindTraderRequest, db: Session = Depends(get_db)):
    try:
        return arbitrage_paper_account_service.bind_trader(db, account_id, payload.trader_account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/accounts/{account_id}/unbind-trader")
def unbind_trader(account_id: int, db: Session = Depends(get_db)):
    try:
        return arbitrage_paper_account_service.unbind_trader(db, account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/strategy-runtime")
def list_strategy_runtime():
    from backend.services.rebate_arb.strategy_runtime_registry import list_runtime_specs
    return {"strategies": list_runtime_specs()}


@router.get("/presets")
def list_presets(db: Session = Depends(get_db)):
    return {"presets": arbitrage_paper_account_service.list_presets(db)}


@router.get("/accounts")
def list_accounts(owner_account_id: Optional[int] = None, db: Session = Depends(get_db)):
    return {"accounts": arbitrage_paper_account_service.list_accounts(db, owner_account_id)}


@router.post("/accounts")
def create_account(payload: CreateArbitragePaperAccountRequest, db: Session = Depends(get_db)):
    try:
        return {
            "success": True,
            "account": arbitrage_paper_account_service.create_account(
                db,
                name=payload.name,
                total_equity=payload.total_equity,
                owner_account_id=payload.owner_account_id,
                preset_id=payload.preset_id,
                risk_profile=payload.risk_profile,
            ),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/accounts/{account_id}/dashboard")
def get_account_dashboard(account_id: int, db: Session = Depends(get_db)):
    try:
        return arbitrage_paper_account_service.get_dashboard(db, account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/accounts/{account_id}")
def get_account(account_id: int, db: Session = Depends(get_db)):
    try:
        return arbitrage_paper_account_service.get_account(db, account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.patch("/accounts/{account_id}")
def update_account(account_id: int, payload: UpdateArbitragePaperAccountRequest, db: Session = Depends(get_db)):
    try:
        return {
            "success": True,
            "account": arbitrage_paper_account_service.update_account(
                db,
                account_id,
                name=payload.name,
                risk_profile=payload.risk_profile,
            ),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/accounts/{account_id}/reset")
def reset_account(account_id: int, payload: ResetArbitragePaperAccountRequest, db: Session = Depends(get_db)):
    try:
        return {
            "success": True,
            "account": arbitrage_paper_account_service.reset_account(
                db,
                account_id,
                total_equity=payload.total_equity,
                preset_id=payload.preset_id,
                clear_ledger=payload.clear_ledger,
            ),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    try:
        return arbitrage_paper_account_service.delete_account(db, account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/accounts/{account_id}/balances")
def update_balances(account_id: int, payload: UpdateBalancesRequest, db: Session = Depends(get_db)):
    try:
        return {
            "success": True,
            "account": arbitrage_paper_account_service.update_balances(db, account_id, payload.balances),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/accounts/{account_id}/apply-preset")
def apply_preset(account_id: int, payload: ApplyPresetRequest, db: Session = Depends(get_db)):
    try:
        return {
            "success": True,
            "account": arbitrage_paper_account_service.apply_preset(
                db,
                account_id,
                payload.preset_id,
                payload.total_equity,
            ),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/accounts/{account_id}/validate-start")
def validate_start(account_id: int, payload: ValidateStartRequest, db: Session = Depends(get_db)):
    try:
        return arbitrage_paper_account_service.validate_start(db, account_id, payload.strategies)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/accounts/{account_id}/start")
def start_paper_verification(account_id: int, payload: StartPaperRequest, db: Session = Depends(get_db)):
    """启动套利 Paper 验证（绑定资金池、启用策略、后台 tick）。"""
    try:
        result = arbitrage_paper_account_service.start_paper_verification(
            db, account_id, payload.strategies
        )
        if not result.get("success"):
            return JSONResponse(status_code=400, content=result)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/accounts/{account_id}/stop")
def stop_paper_verification(account_id: int, db: Session = Depends(get_db)):
    """停止套利 Paper 验证后台 tick。"""
    try:
        return arbitrage_paper_account_service.stop_paper_verification(db, account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/session")
def get_paper_session():
    """当前 Paper 验证后台会话状态。"""
    from backend.services.rebate_arb.arbitrage_paper_session_runner import (
        arbitrage_paper_session_runner,
    )

    return arbitrage_paper_session_runner.get_status()


@router.get("/qaa/last-run")
def get_qaa_last_run():
    """最近一次 rebate_arb QAA WorkflowRun 摘要（只读）。"""
    from backend.services.rebate_arb.qaa_rebate_tick import get_last_qaa_workflow_run
    from backend.services.rebate_arb.strategy_runtime_registry import list_runtime_specs
    from backend.services.rebate_arb.capital_coordinator import capital_coordinator

    return {
        "last_run": get_last_qaa_workflow_run(),
        "strategies": list_runtime_specs(),
        "strategy_sub_pools": capital_coordinator.get_strategy_sub_pool_status(),
    }
