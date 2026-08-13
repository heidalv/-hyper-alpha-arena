"""VIP 共用 AI 选币 API — /api/coin-select/*

权限：require_feature(ai_coin_select)；管理员可管理扫描。
采纳：短线 → auto_coin_symbols；长线 → session.symbols（手动确认）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.permissions import require_feature
from backend.core.request_identity import current_role, current_user_id, require_user_tenant
from backend.database.connection import get_db
from backend.database.models import Account, CoinSelectAdoption, CoinSelectCandidate, FullAutoSession, User

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/coin-select",
    tags=["coin-select"],
    dependencies=[Depends(require_feature("ai_coin_select"))],
)


def _user(db: Session, uid: int) -> User:
    u = db.query(User).filter(User.id == uid).first()
    if not u:
        raise HTTPException(404, "user not found")
    return u


def _is_admin(request: Request) -> bool:
    return current_role(request) == "admin"


def _feature_on(user: User, request: Request) -> bool:
    if _is_admin(request):
        return True
    return (getattr(user, "coin_select_enabled", None) or "false").lower() in ("true", "1", "yes", "on")


class SettingsPatch(BaseModel):
    enabled: Optional[bool] = None
    auto_follow_scalp: Optional[bool] = None
    default_session_id: Optional[str] = None
    account_id: Optional[int] = None
    account_enabled: Optional[bool] = None


class AdoptRequest(BaseModel):
    symbol: str
    horizon: str = Field(..., description="scalp|midlong")
    session_id: str
    candidate_id: Optional[int] = None


@router.get("/settings")
def get_settings(request: Request, db: Session = Depends(get_db)):
    uid, _ = require_user_tenant(request)
    user = _user(db, uid)
    return {
        "tier": user.tier,
        "role": getattr(user, "role", "user"),
        "enabled": _feature_on(user, request) if _is_admin(request) else (
            (user.coin_select_enabled or "false").lower() in ("true", "1", "yes")
        ),
        "coin_select_enabled": (user.coin_select_enabled or "false"),
        "auto_follow_scalp": (user.coin_select_auto_follow or "false"),
        "default_session_id": user.coin_select_default_session,
        "is_admin": _is_admin(request),
    }


@router.patch("/settings")
def patch_settings(body: SettingsPatch, request: Request, db: Session = Depends(get_db)):
    uid, _ = require_user_tenant(request)
    user = _user(db, uid)
    if body.enabled is not None:
        user.coin_select_enabled = "true" if body.enabled else "false"
    if body.auto_follow_scalp is not None:
        user.coin_select_auto_follow = "true" if body.auto_follow_scalp else "false"
    if body.default_session_id is not None:
        user.coin_select_default_session = body.default_session_id or None
    if body.account_id is not None and body.account_enabled is not None:
        acc = db.query(Account).filter(Account.id == body.account_id, Account.user_id == uid).first()
        if not acc:
            raise HTTPException(404, "account not found")
        acc.ai_coin_select_enabled = "true" if body.account_enabled else "false"
    db.commit()
    return get_settings(request, db)


@router.get("/board")
def get_board(
    request: Request,
    horizon: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None),
    max_trap: Optional[float] = Query(None),
    verdict: Optional[str] = Query(None),
    min_liquidity: Optional[float] = Query(None),
    sort_by: Optional[str] = Query("confidence"),
    db: Session = Depends(get_db),
):
    uid, _ = require_user_tenant(request)
    user = _user(db, uid)
    admin = _is_admin(request)
    if not admin and not _feature_on(user, request):
        raise HTTPException(403, "请先打开 VIP AI 选币开关")
    from backend.services.coin_select_platform_service import list_board

    return list_board(
        horizon=horizon,
        admin=admin,
        min_score=min_score,
        max_trap=max_trap,
        verdict=verdict,
        min_liquidity=min_liquidity,
        sort_by=sort_by or "confidence",
    )


@router.post("/adopt")
def adopt(body: AdoptRequest, request: Request, db: Session = Depends(get_db)):
    uid, _ = require_user_tenant(request)
    user = _user(db, uid)
    if not _is_admin(request) and not _feature_on(user, request):
        raise HTTPException(403, "请先打开 VIP AI 选币开关")

    horizon = (body.horizon or "").lower().strip()
    if horizon not in ("scalp", "midlong"):
        raise HTTPException(400, "horizon must be scalp or midlong")
    symbol = (body.symbol or "").upper().strip()
    if not symbol:
        raise HTTPException(400, "symbol required")

    session = db.query(FullAutoSession).filter(FullAutoSession.session_id == body.session_id).first()
    if not session:
        raise HTTPException(404, "session not found")
    # 会话归属：经 account.user_id
    acc = db.query(Account).filter(Account.id == session.account_id).first()
    if not acc or int(acc.user_id) != int(uid):
        if not _is_admin(request):
            raise HTTPException(403, "session not owned by current user")
    # 账户级开关
    acc_flag = (getattr(acc, "ai_coin_select_enabled", None) or "true").lower()
    if acc_flag in ("false", "0", "off", "no") and not _is_admin(request):
        raise HTTPException(403, "该交易账户已关闭 AI 选币（ai_coin_select_enabled）")

    from backend.services.full_auto_trading_service import full_auto_service

    # scalp → 短线 auto 池；midlong → AI 中线 sticky（绝不进固定长线表）
    if horizon == "midlong":
        auto_list = list(getattr(session, "auto_coin_symbols", None) or [])
        if symbol in auto_list:
            session.auto_coin_symbols = [s for s in auto_list if s != symbol]
            db.commit()
        from backend.services.auto_coin_selector import force_adopt_ai_mid_symbol

        result = force_adopt_ai_mid_symbol(body.session_id, symbol)
        if not result.get("success"):
            raise HTTPException(400, result.get("error") or "adopt midlong failed")
    else:
        result = full_auto_service.add_symbols(
            db, body.session_id, [symbol], is_auto_coin=True
        )
        if not result.get("success"):
            raise HTTPException(400, result.get("error") or "adopt failed")

    cand = None
    if body.candidate_id:
        cand = db.query(CoinSelectCandidate).filter(CoinSelectCandidate.id == body.candidate_id).first()
    if not cand:
        cand = (
            db.query(CoinSelectCandidate)
            .filter(
                CoinSelectCandidate.symbol == symbol,
                CoinSelectCandidate.horizon == horizon,
                CoinSelectCandidate.listed.is_(True),
            )
            .order_by(CoinSelectCandidate.id.desc())
            .first()
        )
    if cand:
        cand.adopt_count = int(cand.adopt_count or 0) + 1

    db.add(
        CoinSelectAdoption(
            user_id=uid,
            session_id=body.session_id,
            symbol=symbol,
            horizon=horizon,
            candidate_id=cand.id if cand else None,
        )
    )
    db.commit()
    return {"ok": True, "horizon": horizon, "symbol": symbol, "session": result}


@router.get("/sessions")
def list_my_sessions(request: Request, db: Session = Depends(get_db)):
    """当前登录用户可采纳的交易会话（严格账户隔离，不返回他人会话）。"""
    uid, _ = require_user_tenant(request)
    accounts = db.query(Account).filter(Account.user_id == uid).all()
    acc_ids = [a.id for a in accounts]
    acc_name = {a.id: a.name for a in accounts}
    if not acc_ids:
        return {
            "sessions": [],
            "account_count": 0,
            "hint": "当前登录用户下没有交易账户。请先在本账户创建/绑定交易账户并启动全自动会话（不会显示其他用户的会话）。",
        }
    rows = (
        db.query(FullAutoSession)
        .filter(
            FullAutoSession.account_id.in_(acc_ids),
            FullAutoSession.status.in_(("running", "defensive", "paused")),
        )
        .order_by(FullAutoSession.id.desc())
        .limit(50)
        .all()
    )
    # 兜底：会话 tenant_id 与账户归属不一致时，对齐为本用户（历史迁移残留）
    fixed = 0
    for s in rows:
        if getattr(s, "tenant_id", None) not in (None, uid):
            s.tenant_id = uid
            fixed += 1
    if fixed:
        db.commit()

    if not rows:
        return {
            "sessions": [],
            "account_count": len(acc_ids),
            "hint": "本账户下暂无运行中的全自动会话。请先启动会话后再采纳选币（paused/running/defensive 可见）。",
        }
    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "account_id": s.account_id,
                "account_name": acc_name.get(s.account_id) or f"账户{s.account_id}",
                "status": s.status,
                "symbols": s.symbols or [],
                "auto_coin_symbols": getattr(s, "auto_coin_symbols", None) or [],
            }
            for s in rows
        ],
        "account_count": len(acc_ids),
        "hint": None,
    }


@router.post("/scan-now")
async def scan_now(request: Request):
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    from backend.services.coin_select_platform_service import run_platform_scan

    return await run_platform_scan(force=True)


@router.get("/admin/detail")
def admin_detail(request: Request):
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    from backend.services.coin_select_platform_service import admin_scan_detail, list_board

    detail = admin_scan_detail()
    board = list_board(admin=True, include_rejected=True)
    return {**detail, "board": board}


class DelistBody(BaseModel):
    candidate_id: int
    listed: bool = False


@router.post("/admin/delist")
def admin_delist(body: DelistBody, request: Request, db: Session = Depends(get_db)):
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    row = db.query(CoinSelectCandidate).filter(CoinSelectCandidate.id == body.candidate_id).first()
    if not row:
        raise HTTPException(404, "candidate not found")
    row.listed = bool(body.listed)
    db.commit()
    return {"ok": True, "id": row.id, "listed": row.listed}
