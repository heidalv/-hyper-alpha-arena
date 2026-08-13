"""AI 自动选币 API — 启动/停止/状态/手动触发/历史/候选

会话内自动选币：仅 VIP / 管理员；且只能操作本账户会话（账户隔离）。
"""
import json
import logging
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.permissions import feature_allowed
from backend.database.connection import get_db
from backend.database.models import Account, FullAutoSession
from backend.services.auto_coin_selector import AUTO_COIN_INJECTED_DIR, auto_coin_scheduler

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auto-coin", tags=["Auto Coin Selector"])


def _assert_vip_session_auto_coin(request: Request, db: Session, session: FullAutoSession) -> None:
    """VIP/管理员才能开会话内 AI 选币；非管理员只能动自己账户的会话。"""
    state = request.scope.get("state", {}) or {}
    role = (state.get("role") or "user").lower()
    if role == "admin":
        return
    tier = (state.get("tier") or "free").lower()
    if not feature_allowed(tier, "ai_coin_select"):
        raise HTTPException(
            status_code=403,
            detail="会话内 AI 选币仅 VIP 可用，请升级 VIP 后再开启",
        )
    try:
        uid = int(state.get("user_id") or 0)
    except (TypeError, ValueError):
        uid = 0
    if not uid:
        raise HTTPException(status_code=401, detail="not authenticated")
    acc = db.query(Account).filter(Account.id == session.account_id).first()
    if not acc or int(acc.user_id) != uid:
        raise HTTPException(status_code=403, detail="只能操作自己账户下的会话（账户隔离）")


class AutoCoinStartResponse(BaseModel):
    success: bool
    session_id: Optional[str] = None
    exchange: Optional[str] = None
    error: Optional[str] = None


class AutoCoinStopResponse(BaseModel):
    success: bool
    session_id: Optional[str] = None
    error: Optional[str] = None


class AutoCoinStatusResponse(BaseModel):
    session_id: str
    running: bool
    # 2026-07-20：前端读 auto_coin_enabled，后端原只有 running 字段，导致前端永远
    # 显示 OFF。这里加一个兼容字段，值与 running 同步。
    auto_coin_enabled: Optional[bool] = None
    exchange: Optional[str] = None
    account_id: Optional[int] = None
    segment: Optional[str] = None
    last_scan_at: Optional[str] = None
    scan_interval: Optional[int] = None
    last_injected_symbols: Optional[List[str]] = None
    auto_symbols: Optional[List[str]] = None
    auto_coin_max_slots: Optional[int] = None  # 会话 AI 选币槽位上限 5~10
    candidate_pool: Optional[dict] = None
    inject_blocked_reason: Optional[str] = None
    error: Optional[str] = None
    # 统一选币：标明跟投 VIP 看板还是旧独立扫描
    source: Optional[str] = None
    source_label: Optional[str] = None
    rank_source: Optional[str] = None
    degraded: Optional[str] = None


class AutoCoinScanResponse(BaseModel):
    success: bool
    session_id: Optional[str] = None
    cycle_result: Optional[dict] = None
    error: Optional[str] = None


class AutoCoinHistoryItem(BaseModel):
    id: int
    session_id: str
    symbol: str
    exchange: str
    action: str
    scanner_score: Optional[float] = None
    scanner_rank: Optional[int] = None
    ai_confidence: Optional[float] = None
    ai_reason: Optional[str] = None
    suggested_tier: Optional[str] = None
    risk_note: Optional[str] = None
    removal_reason: Optional[str] = None
    created_at: Optional[str] = None


class AutoCoinHistoryResponse(BaseModel):
    session_id: str
    total: int
    page: int
    limit: int
    items: List[AutoCoinHistoryItem]


class AutoCoinCandidateItem(BaseModel):
    rank: int
    symbol: str
    scanner_score: float
    vol_score: Optional[float] = None
    trend_score: Optional[float] = None
    mom_score: Optional[float] = None
    vola_score: Optional[float] = None
    fund_score: Optional[float] = None
    market_cap: Optional[str] = None
    volume_24h: Optional[str] = None
    status: Optional[str] = None


class AutoCoinCandidatesResponse(BaseModel):
    session_id: str
    exchange: Optional[str] = None
    total_candidates: int
    candidates: List[AutoCoinCandidateItem]


def _get_session_or_404(db: Session, session_id: str) -> FullAutoSession:
    session = db.query(FullAutoSession).filter(FullAutoSession.session_id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
    return session


def _load_persisted_auto_symbols(db: Session, session_id: str) -> List[str]:
    """从 DB / 本地持久化文件读取 AI 选币，调度器未注册时也能展示。"""
    session = db.query(FullAutoSession).filter(FullAutoSession.session_id == session_id).first()
    symbols: List[str] = []
    seen: set[str] = set()
    for sym in (getattr(session, "auto_coin_symbols", None) or [] if session else []):
        key = str(sym).upper()
        if key and key not in seen:
            symbols.append(key)
            seen.add(key)
    injected_path = os.path.join(AUTO_COIN_INJECTED_DIR, f"{session_id}.json")
    try:
        if os.path.exists(injected_path):
            with open(injected_path, "r", encoding="utf-8") as f:
                for sym in json.load(f).get("symbols", []):
                    key = str(sym).upper()
                    if key and key not in seen:
                        symbols.append(key)
                        seen.add(key)
    except Exception as e:
        logger.debug(f"[AutoCoinRoutes] read injected file failed for {session_id}: {e}")
    # AI 选币隔离在 auto_coin_symbols，不得再与 session.symbols 求交——
    # 否则隔离后前端永远显示「暂无选出交易对」，用户误以为列表空/只剩固定币。
    return symbols


@router.get("/active-symbols")
def get_active_auto_symbols(db: Session = Depends(get_db)):
    """汇总所有运行中会话的 AI 选币（不依赖内存调度器）。"""
    sessions = (
        db.query(FullAutoSession)
        .filter(FullAutoSession.status.in_(["running", "defensive", "paused"]))
        .order_by(FullAutoSession.started_at.desc())
        .all()
    )
    merged: List[str] = []
    seen: set[str] = set()
    per_session: List[dict] = []
    for session in sessions:
        syms = _load_persisted_auto_symbols(db, session.session_id)
        if syms:
            per_session.append({
                "session_id": session.session_id,
                "auto_coin_enabled": bool(getattr(session, "auto_coin_enabled", False)),
                "auto_symbols": syms,
            })
        for sym in syms:
            if sym not in seen:
                merged.append(sym)
                seen.add(sym)
    return {"auto_symbols": merged, "sessions": per_session}


@router.post("/{session_id}/start", response_model=AutoCoinStartResponse)
def start_auto_coin(session_id: str, request: Request, db: Session = Depends(get_db)):
    session = _get_session_or_404(db, session_id)
    if not session.account_id:
        raise HTTPException(status_code=400, detail="会话未绑定交易账户")
    _assert_vip_session_auto_coin(request, db, session)

    session.auto_coin_enabled = True
    db.commit()
    auto_coin_scheduler.register_session(session_id, session.account_id)

    selector = auto_coin_scheduler.get_session_selector(session_id)
    exchange = None
    if selector:
        exchange = selector.resolve_exchange(db)

    return AutoCoinStartResponse(
        success=True,
        session_id=session_id,
        exchange=exchange,
    )


@router.post("/{session_id}/stop", response_model=AutoCoinStopResponse)
def stop_auto_coin(session_id: str, request: Request, db: Session = Depends(get_db)):
    session = _get_session_or_404(db, session_id)
    # 关闭不强制 VIP（允许降级用户关掉已开功能），但仍校验归属
    state = request.scope.get("state", {}) or {}
    if (state.get("role") or "").lower() != "admin":
        try:
            uid = int(state.get("user_id") or 0)
        except (TypeError, ValueError):
            uid = 0
        acc = db.query(Account).filter(Account.id == session.account_id).first()
        if not uid or not acc or int(acc.user_id) != uid:
            raise HTTPException(status_code=403, detail="只能操作自己账户下的会话（账户隔离）")
    session.auto_coin_enabled = False
    db.commit()
    auto_coin_scheduler.unregister_session(session_id)
    return AutoCoinStopResponse(success=True, session_id=session_id)


@router.get("/{session_id}/status", response_model=AutoCoinStatusResponse)
def get_auto_coin_status(session_id: str, db: Session = Depends(get_db)):
    status = auto_coin_scheduler.get_status(session_id)
    if status:
        return AutoCoinStatusResponse(**status)

    session = db.query(FullAutoSession).filter(FullAutoSession.session_id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")

    auto_symbols = _load_persisted_auto_symbols(db, session_id)
    _running = bool(getattr(session, "auto_coin_enabled", False))
    return AutoCoinStatusResponse(
        session_id=session_id,
        running=_running,
        # 2026-07-20：前端读 auto_coin_enabled，与 running 同步
        auto_coin_enabled=_running,
        account_id=session.account_id,
        auto_symbols=auto_symbols,
        auto_coin_max_slots=int(getattr(session, "auto_coin_max_slots", None) or 5),
        scan_interval=int(os.getenv("AUTO_COIN_SCAN_INTERVAL", "1800")),
        last_injected_symbols=auto_symbols,
        error=None if auto_symbols else "自动选币服务未注册",
    )


@router.post("/{session_id}/scan-now", response_model=AutoCoinScanResponse)
async def trigger_scan_now(session_id: str, request: Request, db: Session = Depends(get_db)):
    session = _get_session_or_404(db, session_id)
    _assert_vip_session_auto_coin(request, db, session)
    try:
        try:
            db.rollback()
        except Exception:
            pass
        _get_session_or_404(db, session_id)
        result = await auto_coin_scheduler.trigger_scan_now(session_id)
        return AutoCoinScanResponse(
            success=True,
            session_id=session_id,
            cycle_result=result,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        logger.error(f"[AutoCoinRoutes] scan-now failed for {session_id}: {e}")
        return AutoCoinScanResponse(success=False, session_id=session_id, error=str(e))


@router.get("/{session_id}/history", response_model=AutoCoinHistoryResponse)
def get_auto_coin_history(
    session_id: str,
    page: int = 1,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    _get_session_or_404(db, session_id)
    try:
        from backend.database.models import AutoCoinSelection
    except ImportError:
        return AutoCoinHistoryResponse(session_id=session_id, total=0, page=page, limit=limit, items=[])

    query = db.query(AutoCoinSelection).filter(AutoCoinSelection.session_id == session_id)
    total = query.count()
    items = (
        query.order_by(AutoCoinSelection.id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return AutoCoinHistoryResponse(
        session_id=session_id,
        total=total,
        page=page,
        limit=limit,
        items=[AutoCoinHistoryItem(
            id=item.id,
            session_id=item.session_id,
            symbol=item.symbol,
            exchange=item.exchange or "asterdex",
            action=item.action,
            scanner_score=item.scanner_score,
            scanner_rank=item.scanner_rank,
            ai_confidence=item.ai_confidence,
            ai_reason=item.ai_reason,
            suggested_tier=item.suggested_tier,
            risk_note=item.risk_note,
            removal_reason=item.removal_reason,
            created_at=item.created_at.isoformat() if item.created_at else None,
        ) for item in items],
    )


@router.get("/{session_id}/candidates", response_model=AutoCoinCandidatesResponse)
def get_auto_coin_candidates(session_id: str, db: Session = Depends(get_db)):
    _get_session_or_404(db, session_id)
    selector = auto_coin_scheduler.get_session_selector(session_id)
    if not selector or not selector._pool or not selector._pool.active:
        return AutoCoinCandidatesResponse(
            session_id=session_id,
            exchange=None,
            total_candidates=0,
            candidates=[],
        )

    pool = selector._pool
    exchange = selector._exchange or selector.resolve_exchange(db)
    all_candidates = list(pool.active.values())
    candidate_list = []
    for rank, c in enumerate(all_candidates, 1):
        candidate_list.append(AutoCoinCandidateItem(
            rank=rank,
            symbol=c.symbol,
            scanner_score=c.scanner_score or 0,
            vol_score=getattr(c, "vol_score", None),
            trend_score=getattr(c, "trend_score", None),
            mom_score=getattr(c, "mom_score", None),
            vola_score=getattr(c, "vola_score", None),
            fund_score=getattr(c, "fund_score", None),
            market_cap=getattr(c, "market_cap", None),
            volume_24h=getattr(c, "volume_24h", None),
            status=c.status,
        ))

    return AutoCoinCandidatesResponse(
        session_id=session_id,
        exchange=selector._exchange,
        total_candidates=len(candidate_list),
        candidates=candidate_list,
    )
