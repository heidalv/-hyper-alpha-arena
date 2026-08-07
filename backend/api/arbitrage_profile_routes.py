"""Arbitrage Profile API — AI 交易员专用套利档案。"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database.connection import get_db, sqlite_write_commit
from backend.database.models import Account, ArbitragePaperAccountDB, ArbitrageProfileDB
from backend.services.rebate_arb.trader_llm_resolver import resolve_trader_llm_pair, sync_profile_llm_from_account

router = APIRouter(prefix="/api/accounts", tags=["Arbitrage Profiles"])


DEFAULT_POINTS_STRATEGIES = ["S8"]


class ArbitrageProfilePayload(BaseModel):
    enabled: bool = False
    mode: str = "paper"
    paper_account_id: Optional[int] = None
    paper_account_mode: str = "dedicated_arbitrage_paper"
    arbitrage_paper_account_id: Optional[int] = None
    enabled_strategies: List[str] = Field(default_factory=lambda: list(DEFAULT_POINTS_STRATEGIES))
    strategy_overrides: Dict[str, Any] = Field(default_factory=dict)
    wash_trade_profile: str = "balanced"
    ai_config_source: str = "manual"
    linked_llm_config_id: Optional[int] = None
    strategy_llm_config_id: Optional[int] = None
    execution_llm_config_id: Optional[int] = None


class AiGenerateProfileRequest(BaseModel):
    risk_profile: str = "balanced"
    total_equity: float = 300.0
    goal: str = ""
    target_strategies: List[str] = Field(default_factory=list)


def _loads_json(raw: str, default: Any) -> Any:
    try:
        return json.loads(raw or "")
    except Exception:
        return default


def _profile_to_dict(profile: ArbitrageProfileDB, account: Optional[Account] = None) -> Dict[str, Any]:
    strategy_llm, execution_llm = resolve_trader_llm_pair(account, profile)
    return {
        "id": profile.id,
        "account_id": profile.account_id,
        "enabled": bool(profile.enabled),
        "mode": profile.mode or "paper",
        "paper_account_id": profile.paper_account_id,
        "paper_account_mode": profile.paper_account_mode or "legacy_ai_paper",
        "arbitrage_paper_account_id": profile.arbitrage_paper_account_id,
        "enabled_strategies": _loads_json(profile.enabled_strategies_json, list(DEFAULT_POINTS_STRATEGIES)),
        "strategy_overrides": _loads_json(profile.strategy_overrides_json, {}),
        "wash_trade_profile": profile.wash_trade_profile or "balanced",
        "ai_config_source": profile.ai_config_source or "manual",
        "linked_llm_config_id": profile.linked_llm_config_id,
        "strategy_llm_config_id": strategy_llm,
        "execution_llm_config_id": execution_llm,
        "last_evolved_at": profile.last_evolved_at,
        "profile_snapshot": _loads_json(profile.profile_snapshot_json, {}),
    }


def _validate_account_and_paper(db: Session, account_id: int, paper_account_id: Optional[int]) -> Account:
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="交易员账户不存在")
    if paper_account_id:
        paper = db.query(Account).filter(Account.id == paper_account_id).first()
        if not paper:
            raise HTTPException(status_code=400, detail=f"Paper 账户 #{paper_account_id} 不存在")
        if (paper.account_type or "").upper() != "PAPER":
            raise HTTPException(status_code=400, detail=f"账户 #{paper_account_id} 不是 PAPER 类型")
        if paper.user_id != account.user_id:
            raise HTTPException(status_code=400, detail="Paper 账户与交易员不属于同一用户")
    return account


def _validate_arbitrage_paper(db: Session, account_id: int, arbitrage_paper_account_id: Optional[int]) -> None:
    if not arbitrage_paper_account_id:
        return
    account = db.query(Account).filter(Account.id == account_id).first()
    arb_paper = (
        db.query(ArbitragePaperAccountDB)
        .filter(ArbitragePaperAccountDB.id == arbitrage_paper_account_id)
        .first()
    )
    if not arb_paper:
        raise HTTPException(status_code=400, detail=f"套利 Paper 账户 #{arbitrage_paper_account_id} 不存在")
    if arb_paper.owner_account_id and account and arb_paper.owner_account_id != account.id:
        raise HTTPException(status_code=400, detail="套利 Paper 账户不属于该交易员")


@router.get("/{account_id}/arbitrage-profile")
def get_arbitrage_profile(account_id: int, db: Session = Depends(get_db)):
    """读取交易员专用套利档案；不存在时返回默认草稿。"""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="交易员账户不存在")
    profile = db.query(ArbitrageProfileDB).filter(ArbitrageProfileDB.account_id == account_id).first()
    if not profile:
        return {
            "id": None,
            "account_id": account_id,
            "enabled": False,
            "mode": "paper",
            "paper_account_id": None,
            "paper_account_mode": "dedicated_arbitrage_paper",
            "arbitrage_paper_account_id": None,
            "enabled_strategies": list(DEFAULT_POINTS_STRATEGIES),
            "strategy_overrides": {},
            "wash_trade_profile": "balanced",
            "ai_config_source": "manual",
            "linked_llm_config_id": getattr(account, "llm_config_id_deep", None) or getattr(account, "llm_config_id", None),
            "strategy_llm_config_id": getattr(account, "llm_config_id_deep", None) or getattr(account, "llm_config_id", None),
            "execution_llm_config_id": getattr(account, "llm_config_id", None) or getattr(account, "llm_config_id_deep", None),
            "last_evolved_at": None,
            "profile_snapshot": {},
        }
    return _profile_to_dict(profile, account)


@router.put("/{account_id}/arbitrage-profile")
def upsert_arbitrage_profile(
    account_id: int,
    payload: ArbitrageProfilePayload,
    db: Session = Depends(get_db),
):
    """保存交易员专用套利档案。"""
    _validate_account_and_paper(db, account_id, payload.paper_account_id)
    _validate_arbitrage_paper(db, account_id, payload.arbitrage_paper_account_id)
    profile = db.query(ArbitrageProfileDB).filter(ArbitrageProfileDB.account_id == account_id).first()
    if not profile:
        profile = ArbitrageProfileDB(account_id=account_id)
        db.add(profile)

    profile.enabled = bool(payload.enabled)
    profile.mode = payload.mode if payload.mode in ("paper", "live") else "paper"
    profile.paper_account_mode = (
        payload.paper_account_mode
        if payload.paper_account_mode in ("legacy_ai_paper", "dedicated_arbitrage_paper")
        else "legacy_ai_paper"
    )
    profile.paper_account_id = (
        payload.paper_account_id
        if profile.mode == "paper" and profile.paper_account_mode == "legacy_ai_paper"
        else None
    )
    profile.arbitrage_paper_account_id = (
        payload.arbitrage_paper_account_id
        if profile.mode == "paper" and profile.paper_account_mode == "dedicated_arbitrage_paper"
        else None
    )
    profile.enabled_strategies_json = json.dumps(
        sorted({s.upper() for s in payload.enabled_strategies if s.upper().startswith("S")}),
        ensure_ascii=False,
    )
    profile.strategy_overrides_json = json.dumps(payload.strategy_overrides, ensure_ascii=False, default=str)
    profile.wash_trade_profile = payload.wash_trade_profile
    profile.ai_config_source = payload.ai_config_source
    profile.linked_llm_config_id = payload.linked_llm_config_id
    if payload.strategy_llm_config_id is not None:
        profile.strategy_llm_config_id = payload.strategy_llm_config_id
    if payload.execution_llm_config_id is not None:
        profile.execution_llm_config_id = payload.execution_llm_config_id

    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="交易员账户不存在")
    sync_profile_llm_from_account(profile, account)
    strategy_llm, execution_llm = profile.strategy_llm_config_id, profile.execution_llm_config_id

    if profile.enabled:
        if not strategy_llm or not execution_llm:
            raise HTTPException(
                status_code=400,
                detail="交易员须配置「分析模型」与「执行模型」（在 AI 交易员编辑里设置，方向交易与套利共用）",
            )
        # 分析与执行共用同一个模型是允许的（单模型部署是常态），不再强制分开
        if (
            profile.paper_account_mode == "dedicated_arbitrage_paper"
            and profile.arbitrage_paper_account_id
        ):
            arb_row = (
                db.query(ArbitragePaperAccountDB)
                .filter(ArbitragePaperAccountDB.id == profile.arbitrage_paper_account_id)
                .first()
            )
            if arb_row:
                arb_row.owner_account_id = account_id

    profile.profile_snapshot_json = json.dumps({
        "saved_at": time.time(),
        "enabled": profile.enabled,
        "mode": profile.mode,
        "paper_account_id": profile.paper_account_id,
        "paper_account_mode": profile.paper_account_mode,
        "arbitrage_paper_account_id": profile.arbitrage_paper_account_id,
        "enabled_strategies": _loads_json(profile.enabled_strategies_json, []),
        "wash_trade_profile": profile.wash_trade_profile,
        "ai_config_source": profile.ai_config_source,
        "strategy_llm_config_id": profile.strategy_llm_config_id,
        "execution_llm_config_id": profile.execution_llm_config_id,
    }, ensure_ascii=False)

    sqlite_write_commit(db, label="upsert_arbitrage_profile")
    db.refresh(profile)
    return {"success": True, "profile": _profile_to_dict(profile, account)}


@router.post("/{account_id}/arbitrage-profile/ai-generate")
def ai_generate_arbitrage_profile(
    account_id: int,
    payload: AiGenerateProfileRequest,
    db: Session = Depends(get_db),
):
    """MVP: 根据风险偏好生成 300U 小资金 Playbook 草案。"""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="交易员账户不存在")

    risk = (payload.risk_profile or "balanced").lower()
    if risk == "conservative":
        strategies = ["S8"]
        wash_profile = "conservative"
        leverage = 5
    elif risk == "aggressive":
        strategies = ["S5", "S8"]
        wash_profile = "aggressive"
        leverage = 10
    else:
        strategies = ["S8"]
        wash_profile = "balanced"
        leverage = 8

    if payload.target_strategies:
        strategies = sorted({s.upper() for s in payload.target_strategies})

    return {
        "success": True,
        "source": "fallback_playbook_300u",
        "profile": {
            "enabled": True,
            "mode": "paper",
            "paper_account_id": None,
            "paper_account_mode": "dedicated_arbitrage_paper",
            "arbitrage_paper_account_id": None,
            "enabled_strategies": strategies,
            "wash_trade_profile": wash_profile,
            "linked_llm_config_id": getattr(account, "llm_config_id_deep", None) or getattr(account, "llm_config_id", None),
            "ai_config_source": "ai_generated",
            "strategy_overrides": {
                "S8": {"params": {"default_leverage": leverage, "hold_minutes": 60}},
                "S3": {"params": {"default_leverage": min(leverage, 5)}},
            },
        },
        "reasoning": [
            "采用 300U 小资金默认方案，优先启用 S3/S8。",
            "Live 应用必须人工确认；此接口只生成草案。",
        ],
    }
