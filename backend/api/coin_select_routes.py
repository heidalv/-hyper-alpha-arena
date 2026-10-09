"""VIP 共用 AI 选币 API — /api/coin-select/*

权限：require_feature(ai_coin_select)；管理员可管理扫描。
采纳：midlong → AI 中线 sticky（force_adopt_ai_mid_symbol，绝不进固定长线表）。
[2026-09-17] 短线车道已停（SCALP_OPEN_DISABLED=true）：adopt 不再接受 scalp，
平台扫描也只产 midlong 看板；auto_follow_scalp（短线自动跟投）随之移除。
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
    default_session_id: Optional[str] = None
    account_id: Optional[int] = None
    account_enabled: Optional[bool] = None


class AdoptRequest(BaseModel):
    symbol: str
    horizon: str = Field(..., description="midlong")
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
        "default_session_id": user.coin_select_default_session,
        "is_admin": _is_admin(request),
    }


@router.patch("/settings")
def patch_settings(body: SettingsPatch, request: Request, db: Session = Depends(get_db)):
    uid, _ = require_user_tenant(request)
    user = _user(db, uid)
    if body.enabled is not None:
        user.coin_select_enabled = "true" if body.enabled else "false"
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
    if horizon not in ("mid", "long"):
        raise HTTPException(400, "horizon must be mid or long（短线已停；中/长线是独立周期分开采纳）")
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

    from backend.services.auto_coin_selector import (
        force_adopt_ai_long_symbol,
        force_adopt_ai_mid_symbol,
    )

    # [2026-09-17] 中线/长线独立采纳：mid → AI 中线 sticky；long → AI 长线 sticky
    # （均不进固定表、互不占槽；scalp 分支已随短线车道停用移除）
    if horizon == "long":
        result = force_adopt_ai_long_symbol(body.session_id, symbol)
    else:
        auto_list = list(getattr(session, "auto_coin_symbols", None) or [])
        if symbol in auto_list:
            session.auto_coin_symbols = [s for s in auto_list if s != symbol]
            db.commit()
        result = force_adopt_ai_mid_symbol(body.session_id, symbol)
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


@router.get("/ai-pools")
def ai_pools(request: Request, db: Session = Depends(get_db)):
    """[2026-09-18] 当前 AI 池全景（看板页与会话页共用的同一份账）。

    返回每个 running 会话的 mid/long 池（与交易循环同源），并给每个池内币
    标注最新看板判定（verdict/conf/direction/年龄）——把「看板推荐」与
    「会话实际在池」之间的桥显式化，消灭两套真相。
    """
    uid, _ = require_user_tenant(request)
    from sqlalchemy import text as _sa_text
    from backend.services.ai_coin_unified import get_tier_state

    rows = db.execute(
        _sa_text(
            "SELECT session_id, account_id FROM full_auto_sessions "
            "WHERE status IN ('running','defensive','paused')"
        )
    ).all()
    out = []
    for sid, acc_id in rows:
        pools = {}
        for tier in ("mid", "long"):
            st = get_tier_state(str(sid), tier)
            syms = [str(s).upper() for s in (st.get("symbols") or [])]
            detail = []
            for s in syms:
                j = db.execute(
                    _sa_text(
                        "SELECT horizon, lower(ai_verdict), confidence, "
                        "lower(direction_bias), listed, "
                        "EXTRACT(EPOCH FROM (now()-created_at))/3600.0 "
                        "FROM coin_select_candidates WHERE upper(symbol)=:s "
                        "AND horizon=:h ORDER BY id DESC LIMIT 1"
                    ),
                    {"s": s, "h": tier},
                ).first()
                detail.append({
                    "symbol": s,
                    "verdict": j[1] if j else None,
                    "confidence": float(j[2]) if j and j[2] is not None else None,
                    "direction": j[3] if j else None,
                    "listed": bool(j[4]) if j else False,
                    "judged_age_h": round(float(j[5] or 0), 1) if j else None,
                })
            pools[tier] = {
                "symbols": syms,
                "detail": detail,
                "reason": str(st.get("reason") or ""),
                "updated_at": st.get("updated_at"),
            }
        out.append({
            "session_id": str(sid),
            "account_id": acc_id,
            "pools": pools,
        })
    return {"sessions": out}


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


# ==================== 混合打分中心（ADR-23 · HC-v1） ====================

class HybridRunBody(BaseModel):
    symbols: Optional[List[str]] = None
    llm: Optional[bool] = None


@router.get("/hybrid/status")
def hybrid_status(request: Request):
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    from backend.services.hybrid_scoring import service

    return service.status()


@router.post("/hybrid/run")
def hybrid_run(body: HybridRunBody, request: Request):
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    from backend.services.hybrid_scoring import service

    return service.run_cycle(symbols=body.symbols, force=True, llm_enabled=body.llm)


@router.post("/hybrid/evaluate")
def hybrid_evaluate(request: Request):
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    from backend.services.hybrid_scoring import service

    return service.evaluate_hits()


@router.get("/hybrid/report")
def hybrid_report(request: Request, weeks: int = Query(4, ge=1, le=12)):
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    from backend.services.hybrid_scoring import report

    return report.weekly_report(weeks=weeks)
