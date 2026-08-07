"""全自动交易 API — 极简接口，用户只需选交易对+开启"""
import json
import logging
import threading
import time
import traceback
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database.connection import SessionLocal, get_db
from backend.database.models import FullAutoSession, Account
from backend.services.full_auto_trading_service import full_auto_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/full-auto", tags=["Full Auto Trading"])


class StartRequest(BaseModel):
    account_id: int
    paper_account_id: Optional[int] = None
    symbols: List[str]
    risk_level: str = "moderate"
    risk_mode: str = "ai_dynamic"
    trading_mode: str = "paper"
    auto_coin_enabled: bool = False
    arb_enabled: bool = False
    arbitrage_profile_id: Optional[int] = None
    paper_account_mode: str = "legacy_ai_paper"
    arbitrage_paper_account_id: Optional[int] = None
    profile_override: Optional[Dict[str, Any]] = None
    # 会话级交易所覆盖：留空=跟随账户配置(account.selected_exchange)。
    # 仅影响行情订阅/快照采集；实盘下单仍按 account.selected_exchange。
    active_exchange: Optional[str] = None


class StartResponse(BaseModel):
    success: bool
    session_id: Optional[str] = None
    symbols: Optional[List[str]] = None
    risk_level: Optional[str] = None
    risk_mode: Optional[str] = None
    trading_mode: Optional[str] = None
    auto_coin_enabled: Optional[bool] = None
    paper_account_id: Optional[int] = None
    paper_account_name: Optional[str] = None
    trading_account_id: Optional[int] = None
    arb_enabled: Optional[bool] = None
    paper_account_mode: Optional[str] = None
    arbitrage_paper_account_id: Optional[int] = None
    arbitrage_profile: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@router.post("/start", response_model=StartResponse)
def start_full_auto(request: StartRequest, http_request: Request, db: Session = Depends(get_db)):
    """启动全自动交易 — 只需选交易对 + 风险偏好"""
    if not request.symbols:
        raise HTTPException(status_code=400, detail="请至少选择一个交易对")
    uses_dedicated_arb_paper = (
        request.arb_enabled
        and request.paper_account_mode == "dedicated_arbitrage_paper"
        and request.arbitrage_paper_account_id
    )
    if (request.trading_mode or "paper").lower() == "paper" and not request.paper_account_id and not uses_dedicated_arb_paper:
        raise HTTPException(status_code=400, detail="模拟盘模式必须选择「模拟账户(资金池)」")

    auto_coin = bool(request.auto_coin_enabled)
    if auto_coin:
        from backend.core.permissions import feature_allowed
        state = http_request.scope.get("state", {}) or {}
        role = (state.get("role") or "user").lower()
        tier = (state.get("tier") or "free").lower()
        if role != "admin" and not feature_allowed(tier, "ai_coin_select"):
            raise HTTPException(
                status_code=403,
                detail="会话内 AI 选币仅 VIP 可用，请升级 VIP 后再开启（或先关闭 auto_coin 启动会话）",
            )
        # 账户隔离：只能用自己的交易账户开会话
        if role != "admin":
            try:
                uid = int(state.get("user_id") or 0)
            except (TypeError, ValueError):
                uid = 0
            acc = db.query(Account).filter(Account.id == request.account_id).first()
            if not uid or not acc or int(acc.user_id) != uid:
                raise HTTPException(status_code=403, detail="只能用自己的交易账户启动会话")

    result = full_auto_service.start_session(
        db=db,
        account_id=request.account_id,
        symbols=request.symbols,
        risk_level=request.risk_level,
        risk_mode=request.risk_mode,
        trading_mode=request.trading_mode,
        paper_account_id=request.paper_account_id,
        auto_coin_enabled=auto_coin,
        arb_enabled=request.arb_enabled,
        arbitrage_profile_id=request.arbitrage_profile_id,
        paper_account_mode=request.paper_account_mode,
        arbitrage_paper_account_id=request.arbitrage_paper_account_id,
        profile_override=request.profile_override,
        active_exchange=request.active_exchange,
    )
    return StartResponse(**result)


@router.post("/stop/{session_id}")
def stop_full_auto(session_id: str, db: Session = Depends(get_db)):
    """停止全自动交易"""
    return full_auto_service.stop_session(db, session_id)


@router.delete("/{session_id}")
def delete_full_auto(session_id: str, db: Session = Depends(get_db)):
    """删除全自动交易会话（先停止再删除记录）"""
    return full_auto_service.delete_session(db, session_id)


@router.post("/pause/{session_id}")
def pause_full_auto(session_id: str, db: Session = Depends(get_db)):
    """暂停全自动交易"""
    return full_auto_service.pause_session(db, session_id)


@router.post("/resume/{session_id}")
def resume_full_auto(session_id: str, db: Session = Depends(get_db)):
    """恢复全自动交易"""
    return full_auto_service.resume_session(db, session_id)


class AddSymbolsRequest(BaseModel):
    symbols: List[str]


@router.post("/add-symbols/{session_id}")
def add_symbols(session_id: str, request: AddSymbolsRequest, db: Session = Depends(get_db)):
    """向运行中的会话添加交易对"""
    if not request.symbols:
        raise HTTPException(status_code=400, detail="请至少选择一个交易对")
    result = full_auto_service.add_symbols(db, session_id, request.symbols)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "添加失败"))
    return result


class RemoveSymbolsRequest(BaseModel):
    symbols: List[str]


@router.post("/remove-symbols/{session_id}")
def remove_symbols(session_id: str, request: RemoveSymbolsRequest, db: Session = Depends(get_db)):
    """从运行中的会话移除交易对"""
    if not request.symbols:
        raise HTTPException(status_code=400, detail="请至少选择一个交易对")
    result = full_auto_service.remove_symbols(db, session_id, request.symbols)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "移除失败"))
    return result


class UpdateConfigRequest(BaseModel):
    """会话配置更新请求 — 所有字段可选，只更新提交的字段。

    2026-07-20：支持运行中会话热更新风险/风控参数，无需重启会话。
    交易模式(trading_mode)和账户绑定(account_id/paper_account_id)不支持热改，
    需停止后重建会话。
    """
    risk_level: Optional[str] = None
    risk_mode: Optional[str] = None
    max_concurrent_strategies: Optional[int] = None
    max_total_drawdown_pct: Optional[float] = None
    daily_loss_limit_pct: Optional[float] = None
    active_exchange: Optional[str] = None
    auto_coin_max_slots: Optional[int] = None  # 5~10，会话 AI 选币槽位


@router.post("/update-config/{session_id}")
def update_config(session_id: str, request: UpdateConfigRequest, http_request: Request, db: Session = Depends(get_db)):
    """更新运行中会话的配置参数（风险等级/风控/交易所）。

    - 交易模式、账户绑定不支持热改（需停止重建）。
    - 交易对增删请用 /add-symbols 和 /remove-symbols。
    """
    session = db.query(FullAutoSession).filter(
        FullAutoSession.session_id == session_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if session.status not in ("running", "defensive", "paused"):
        raise HTTPException(status_code=400, detail=f"会话状态为 {session.status}，运行/防守/暂停中可更新配置")

    if request.auto_coin_max_slots is not None:
        from backend.api.auto_coin_routes import _assert_vip_session_auto_coin
        _assert_vip_session_auto_coin(http_request, db, session)
    updated = []
    if request.risk_level is not None:
        if request.risk_level not in ("conservative", "moderate", "aggressive"):
            raise HTTPException(status_code=400, detail="risk_level 必须为 conservative/moderate/aggressive")
        session.risk_level = request.risk_level
        updated.append(f"risk_level={request.risk_level}")
    if request.risk_mode is not None:
        if request.risk_mode not in ("ai_dynamic", "conservative", "aggressive"):
            raise HTTPException(status_code=400, detail="risk_mode 必须为 ai_dynamic/conservative/aggressive")
        session.risk_mode = request.risk_mode
        updated.append(f"risk_mode={request.risk_mode}")
    if request.max_concurrent_strategies is not None:
        v = int(request.max_concurrent_strategies)
        if v < 1 or v > 100:
            raise HTTPException(status_code=400, detail="max_concurrent_strategies 须在 1-100")
        session.max_concurrent_strategies = v
        updated.append(f"max_concurrent_strategies={v}")
    if request.max_total_drawdown_pct is not None:
        v = float(request.max_total_drawdown_pct)
        if v <= 0 or v > 1:
            raise HTTPException(status_code=400, detail="max_total_drawdown_pct 须在 (0, 1]")
        session.max_total_drawdown_pct = v
        updated.append(f"max_total_drawdown_pct={v}")
    if request.daily_loss_limit_pct is not None:
        v = float(request.daily_loss_limit_pct)
        if v <= 0 or v > 1:
            raise HTTPException(status_code=400, detail="daily_loss_limit_pct 须在 (0, 1]")
        session.daily_loss_limit_pct = v
        updated.append(f"daily_loss_limit_pct={v}")
    if request.active_exchange is not None:
        session.active_exchange = request.active_exchange.strip() or None
        updated.append(f"active_exchange={session.active_exchange}")
    if request.auto_coin_max_slots is not None:
        v = int(request.auto_coin_max_slots)
        if v < 5 or v > 10:
            raise HTTPException(status_code=400, detail="auto_coin_max_slots 须在 5-10")
        session.auto_coin_max_slots = v
        updated.append(f"auto_coin_max_slots={v}")

    if not updated:
        return {"success": True, "session_id": session_id, "updated": [], "message": "无字段需要更新"}

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"[FullAuto] update_config 失败 {session_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"保存失败: {e}")

    # 同步内存中的运行态
    if session_id in full_auto_service._running_sessions:
        rs = full_auto_service._running_sessions[session_id]
        if request.risk_level is not None:
            rs["risk_level"] = request.risk_level
        if request.risk_mode is not None:
            rs["risk_mode"] = request.risk_mode
        if request.active_exchange is not None:
            rs["active_exchange"] = session.active_exchange
        if request.auto_coin_max_slots is not None:
            rs["auto_coin_max_slots"] = session.auto_coin_max_slots

    # 槽位变更：同步内存池上限，并立刻触发补仓（不依赖会话是否在内存 dict）
    if request.auto_coin_max_slots is not None:
        try:
            from backend.services.auto_coin_selector import auto_coin_scheduler
            sel = getattr(auto_coin_scheduler, "_selectors", {}).get(session_id)
            if sel is not None and hasattr(sel, "_pool"):
                sel._pool.max_active = int(session.auto_coin_max_slots)
            if getattr(session, "auto_coin_enabled", False):
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(auto_coin_scheduler.trigger_scan_now(session_id))
                    updated.append("triggered_slot_refill")
                except RuntimeError:
                    # 无运行中的 loop（极少见）：同步跑一轮
                    asyncio.run(auto_coin_scheduler.trigger_scan_now(session_id))
                    updated.append("triggered_slot_refill_sync")
        except Exception as e:
            logger.warning(f"[FullAuto] 槽位变更后补仓触发失败 {session_id}: {e}")

    full_auto_service._invalidate_session_status_cache(session_id)
    logger.info(f"[FullAuto] 会话 {session_id} 配置更新: {updated}")
    return {
        "success": True,
        "session_id": session_id,
        "updated": updated,
        "message": f"已更新: {', '.join(updated)}",
    }


@router.get("/status/{session_id}")
def get_status(session_id: str, db: Session = Depends(get_db)):
    """获取会话完整状态"""
    status = full_auto_service.get_session_status(db, session_id)
    if not status:
        raise HTTPException(status_code=404, detail="会话不存在")
    return status


@router.post("/health-check/{session_id}")
def trigger_health_check(session_id: str, db: Session = Depends(get_db), sync: bool = False):
    """手动触发一次健康检查（调试/验证用）。sync=true 时同步执行并返回错误信息。"""
    session = db.query(FullAutoSession).filter(
        FullAutoSession.session_id == session_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if session.status != "running":
        raise HTTPException(status_code=400, detail=f"会话状态为 {session.status}，需 running")

    if sync:
        import traceback
        _err = None
        t0 = __import__('time').time()
        try:
            full_auto_service._run_health_check(session_id)
        except Exception as e:
            _err = str(e)
            _tb = traceback.format_exc()
        elapsed = __import__('time').time() - t0
        db.refresh(session)
        return {
            "sync": True,
            "session_id": session_id,
            "elapsed_seconds": round(elapsed, 1),
            "error": _err,
            "traceback": _tb if _err else None,
            "db_last_health_check": str(session.last_health_check_at),
            "db_event_log_count": len(session.event_log or []),
        }

    import threading
    t = threading.Thread(
        target=full_auto_service._run_health_check_safe,
        args=(session_id,),
        daemon=True,
    )
    t.start()
    return {"triggered": True, "session_id": session_id, "note": "健康检查已在后台启动"}


@router.get("/health-check-stream/{session_id}")
def stream_health_check(session_id: str):
    """SSE 调试接口：边跑健康检查边输出进度，避免 Pro 深度分析看起来像卡死。"""

    def _sse(event: str, payload: Dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def _event_generator():
        done = threading.Event()
        result: Dict[str, Any] = {"error": None, "traceback": None}

        db = SessionLocal()
        try:
            session = db.query(FullAutoSession).filter(
                FullAutoSession.session_id == session_id
            ).first()
            if not session:
                yield _sse("error", {"message": "会话不存在", "session_id": session_id})
                return
            if session.status != "running":
                yield _sse(
                    "error",
                    {
                        "message": f"会话状态为 {session.status}，需 running",
                        "session_id": session_id,
                    },
                )
                return
        finally:
            db.close()

        def _worker():
            try:
                full_auto_service._run_health_check(session_id)
            except Exception as exc:
                result["error"] = str(exc)
                result["traceback"] = traceback.format_exc()
            finally:
                done.set()

        started = time.time()
        th = threading.Thread(target=_worker, daemon=True, name=f"fullauto-health-{session_id}")
        th.start()
        yield _sse("start", {"session_id": session_id, "message": "健康检查已开始"})

        last_event_count = None
        while not done.wait(timeout=3):
            payload: Dict[str, Any] = {
                "session_id": session_id,
                "elapsed_seconds": round(time.time() - started, 1),
                "message": "健康检查运行中",
            }
            db = SessionLocal()
            try:
                session = db.query(FullAutoSession).filter(
                    FullAutoSession.session_id == session_id
                ).first()
                if session:
                    events = session.event_log or []
                    payload.update({
                        "status": session.status,
                        "last_health_check_at": str(session.last_health_check_at),
                        "event_log_count": len(events),
                    })
                    if events and len(events) != last_event_count:
                        payload["last_event"] = events[-1]
                        last_event_count = len(events)
            except Exception as exc:
                payload["poll_error"] = str(exc)
            finally:
                db.close()
            yield _sse("heartbeat", payload)

        elapsed = round(time.time() - started, 1)
        db = SessionLocal()
        try:
            session = db.query(FullAutoSession).filter(
                FullAutoSession.session_id == session_id
            ).first()
            if session:
                result.update({
                    "session_status": session.status,
                    "last_health_check_at": str(session.last_health_check_at),
                    "event_log_count": len(session.event_log or []),
                })
        finally:
            db.close()

        if result.get("error"):
            yield _sse("error", {"session_id": session_id, "elapsed_seconds": elapsed, **result})
        else:
            yield _sse("done", {"session_id": session_id, "elapsed_seconds": elapsed, **result})

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions")
def list_sessions(
    account_id: Optional[int] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """列出全自动会话。

    - status: 可选过滤（running/paused/defensive/stopped），逗号分隔多个。
    - 排序：活跃会话（running/defensive/paused）在前，stopped 在后。
    """
    from backend.database.models import PaperBalance, PaperPosition
    from sqlalchemy import case

    q = db.query(FullAutoSession)
    if account_id:
        q = q.filter(FullAutoSession.account_id == account_id)
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if len(statuses) == 1:
            q = q.filter(FullAutoSession.status == statuses[0])
        elif statuses:
            q = q.filter(FullAutoSession.status.in_(statuses))
    # 活跃会话优先排序，其次按创建时间倒序
    active_order = case(
        (FullAutoSession.status == "running", 0),
        (FullAutoSession.status == "defensive", 1),
        (FullAutoSession.status == "paused", 2),
        (FullAutoSession.status == "stopped", 3),
        else_=4,
    )
    sessions = q.order_by(active_order, FullAutoSession.created_at.desc()).limit(50).all()
    result = []
    for s in sessions:
        _pnl = s.total_pnl or 0
        _trades = s.total_trades or 0
        _wins = s.winning_trades or 0
        if s.trading_mode == "paper" and s.paper_account_id:
            try:
                _pb = db.query(PaperBalance).filter(PaperBalance.account_id == s.paper_account_id).first()
                if _pb:
                    _init = float(_pb.initial_balance or 10000)
                    _eq = float(_pb.total_equity or _init)
                    _pnl = round(_eq - _init, 4)
                    _pos_q = db.query(PaperPosition).filter(PaperPosition.account_id == s.paper_account_id)
                    if _pb.last_reset_at:
                        _pos_q = _pos_q.filter(PaperPosition.opened_at >= _pb.last_reset_at)
                    _all_pp = _pos_q.all()
                    _trades = len(_all_pp)
                    _wins = sum(1 for pp in _all_pp if float(pp.unrealized_pnl or 0) + float(pp.partial_realized_pnl or 0) > 0)
            except Exception:
                pass
        _trader = db.query(Account).filter(Account.id == s.account_id).first()
        _paper_id = getattr(s, "paper_account_id", None)
        _paper = db.query(Account).filter(Account.id == _paper_id).first() if _paper_id else None
        _trading_id = _paper_id if (s.trading_mode == "paper" and _paper_id) else s.account_id
        result.append({
            "session_id": s.session_id,
            "account_id": s.account_id,
            "account_name": _trader.name if _trader else f"账户#{s.account_id}",
            "paper_account_id": _paper_id,
            "paper_account_name": _paper.name if _paper else None,
            "trading_account_id": _trading_id,
            "status": s.status,
            "symbols": s.symbols,
            "auto_coin_enabled": bool(getattr(s, "auto_coin_enabled", False)),
            "auto_coin_symbols": getattr(s, "auto_coin_symbols", None) or [],
            "auto_coin_max_slots": int(getattr(s, "auto_coin_max_slots", None) or 5),
            "risk_level": s.risk_level,
            "risk_mode": getattr(s, "risk_mode", None) or "ai_dynamic",
            "trading_mode": s.trading_mode,
            "active_exchange": getattr(s, "active_exchange", None),
            "arb_enabled": bool(getattr(s, "arb_enabled", False)),
            "paper_account_mode": getattr(s, "paper_account_mode", None) or "legacy_ai_paper",
            "max_concurrent_strategies": getattr(s, "max_concurrent_strategies", 25),
            "max_total_drawdown_pct": getattr(s, "max_total_drawdown_pct", 0.30),
            "daily_loss_limit_pct": getattr(s, "daily_loss_limit_pct", 0.05),
            "total_strategies_created": s.total_strategies_created or 0,
            "active_count": len(s.active_strategy_ids or []),
            "total_pnl": _pnl,
            "total_trades": _trades,
            "win_rate": min(100.0, (_wins / _trades * 100) if _trades else 0),
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "stopped_at": s.stopped_at.isoformat() if s.stopped_at else None,
            "created_at": s.created_at.isoformat() if getattr(s, "created_at", None) else None,
        })
    return result


@router.get("/tick-intervals")
def get_tick_intervals():
    """获取三周期实际调度间隔（运行时值，含 PAPER_FAST_TRIAL 覆盖）。"""
    try:
        from backend.services.tier_tick_scheduler import get_intervals
        intervals = get_intervals()
    except Exception:
        intervals = {"coordinator": 30, "short": 30, "mid": 120, "long": 240}
    return {
        "intervals": intervals,
        "labels": {
            "coordinator": "协调器",
            "short": "短线因子",
            "mid": "中线AI",
            "long": "长线AI",
        },
    }


@router.get("/tier-status/{session_id}")
def get_tier_status(session_id: str, db: Session = Depends(get_db)):
    """获取多周期并行交易状态（各 tier 的策略数、持仓、预算使用情况）"""
    from backend.database.models import AIStrategy as _AIStrategy, PaperPosition as _PP
    from backend.config.settings import TIER_BUDGET_ALLOCATION, TIER_MAX_MARGIN_PCT
    from backend.services.sub_position_manager import NATURE_TO_TIER

    session = db.query(FullAutoSession).filter(
        FullAutoSession.session_id == session_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    active_ids = session.active_strategy_ids or []
    strats = db.query(_AIStrategy).filter(
        _AIStrategy.strategy_id.in_(active_ids),
        _AIStrategy.status.in_(["active", "paused"]),
    ).all() if active_ids else []

    _trading_acct = (
        session.paper_account_id
        if (session.trading_mode == "paper" and getattr(session, "paper_account_id", None))
        else session.account_id
    )
    total_equity = 0
    try:
        from backend.services.paper_trading_engine import paper_engine
        bal = paper_engine.get_balance(db, _trading_acct) or {}
        total_equity = float(bal.get("total_equity", 0))
    except Exception:
        pass

    # 2026-07-20：AI 选币币种不做长线（只做短线/中线）。
    # 长线 tier 的 symbols 排除 AI 选币，短线/中线仍显示全部。
    _auto_coin_set = {
        str(s).strip().upper()
        for s in (getattr(session, "auto_coin_symbols", None) or [])
        if s
    }
    _session_symbol_set = {
        str(s).strip().upper()
        for s in (getattr(session, "symbols", None) or [])
        if s
    }

    from sqlalchemy import or_ as _or

    tiers = {}
    for t in ("short", "mid", "long"):
        tier_strats = [s for s in strats if (
            getattr(s, "timeframe_tier", None) == t
            or NATURE_TO_TIER.get((s.genome or {}).get("trade_nature", ""), "mid") == t
        )]
        # 2026-08-02：Agent 监控「长线 Trend (含中周期)」只读 tiers.long。
        # 存量 swing 仓多为 timeframe_tier=mid，若 long 只数 long 会显示持仓=0。
        # 长线桶合并 mid+long（及 nature∈swing/trend_follow/position）以匹配 UI 文案。
        if t == "long":
            positions = (
                db.query(_PP)
                .filter(
                    _PP.account_id == _trading_acct,
                    _PP.status == "open",
                    _or(
                        _PP.timeframe_tier.in_(("mid", "long")),
                        _PP.trade_nature.in_(("swing", "trend_follow", "position")),
                    ),
                )
                .all()
            )
            # mid 策略一并计入（中长线一体调度）
            _mid_strats = [s for s in strats if (
                getattr(s, "timeframe_tier", None) == "mid"
                or NATURE_TO_TIER.get((s.genome or {}).get("trade_nature", ""), "") == "mid"
            )]
            _seen_sid = {id(s) for s in tier_strats}
            for _ms in _mid_strats:
                if id(_ms) not in _seen_sid:
                    tier_strats.append(_ms)
                    _seen_sid.add(id(_ms))
        else:
            positions = db.query(_PP).filter(
                _PP.account_id == _trading_acct,
                _PP.status == "open",
                _PP.timeframe_tier == t,
            ).all()
        margin_used = sum(float(p.margin or 0) for p in positions)
        budget = total_equity * TIER_BUDGET_ALLOCATION.get(t, 0.3)
        max_margin = total_equity * TIER_MAX_MARGIN_PCT.get(t, 0.4)

        # 2026-07-20：symbols 统计逻辑
        # - 短线/中线：显示 session.symbols 里的全部币种（固定交易对 + AI 选币）。
        #   仪表盘反映"会话正在交易的币种"，不论该 tier 策略是 active 还是 paused。
        # - 长线：排除 AI 选币（AI 选币不做长线交易），只显示固定交易对 BTC/ETH/SOL。
        #   这样仪表盘短/中线能看到 AI 选币，长线只有固定交易对。
        if t == "long":
            _tier_symbols = sorted(_session_symbol_set - _auto_coin_set)
        else:
            _tier_symbols = sorted(_session_symbol_set)

        _pos_mid = sum(
            1 for p in positions
            if (getattr(p, "timeframe_tier", None) or "").lower() == "mid"
            or (getattr(p, "trade_nature", None) or "").lower() == "swing"
        )
        tiers[t] = {
            "label": {"short": "短线", "mid": "中线", "long": "长线(含中周期)"}[t],
            "strategy_count": len(tier_strats),
            "active_count": sum(1 for s in tier_strats if s.status == "active"),
            "paused_count": sum(1 for s in tier_strats if s.status == "paused"),
            "position_count": len(positions),
            "position_count_mid": _pos_mid if t == "long" else 0,
            "position_count_long_only": (len(positions) - _pos_mid) if t == "long" else len(positions),
            "margin_used": round(margin_used, 2),
            "budget_allocated": round(budget, 2),
            "budget_max": round(max_margin, 2),
            "budget_utilization": round(margin_used / budget * 100, 1) if budget > 0 else 0,
            "symbols": _tier_symbols,
        }

    return {
        "session_id": session_id,
        "total_equity": round(total_equity, 2),
        "tier_budget_allocation": TIER_BUDGET_ALLOCATION,
        "tiers": tiers,
    }


@router.get("/tier-activity/{session_id}")
def get_tier_activity(session_id: str, limit: int = 60, db: Session = Depends(get_db)):
    """获取各周期最近的策略决策记录（实时滚动，含 hold/拦截/未执行，按 tier 分类）。"""
    from backend.database.connection import AnalyticsSessionLocal
    from backend.database.models import DecisionSnapshot as _DS
    import json as _json

    session = db.query(FullAutoSession).filter(
        FullAutoSession.session_id == session_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    adb = AnalyticsSessionLocal()
    try:
        snapshots = adb.query(_DS).order_by(_DS.id.desc()).limit(limit * 5).all()
    finally:
        adb.close()

    result: dict = {"short": [], "mid": [], "long": []}
    # 会话 AI 选币集合：主控常把无策略币误标 mid，活动面板需纠正归短线
    _auto_syms = set()
    try:
        _raw_auto = getattr(session, "auto_coin_symbols", None) or []
        if isinstance(_raw_auto, str):
            import json as _j
            _raw_auto = _j.loads(_raw_auto) if _raw_auto else []
        _auto_syms = {str(x).strip().upper() for x in _raw_auto if x}
    except Exception:
        _auto_syms = set()

    for s in snapshots:
        # [中长线合并] 过滤主循环调度桩：reasoning 为 "[中长线AI强制→TrendAgent/SwingAgent
        # LLM]" 的占位记录表示该符号已委派独立循环、主循环未做真实分析，不应出现在
        # 活动面板（否则满屏"观望 [中长线AI强制→...]"误导运营）。
        _snap_reasoning = str(s.ai_reasoning or "")
        if "中长线AI强制→" in _snap_reasoning:
            continue
        tier = (s.tier or "mid").lower()
        if tier not in result:
            continue
        _sym_u = str(s.symbol or "").upper()
        _lane = str(getattr(s, "source_lane", None) or "").lower()
        # AI 选币池只做短线：活动面板一律进 short，避免主控误标 mid 混入「长线(含中周期)」
        if _sym_u in _auto_syms:
            _bucket = "short"
        elif tier == "mid":
            # UI「长线(含中周期)」：固定币的 mid 并入 long
            _bucket = "long"
        else:
            _bucket = tier

        action_raw = str(s.action or "hold").lower()
        action_cn = {
            "buy": "开多", "sell": "开空", "long": "开多", "short": "开空",
            "close": "平仓", "reduce": "减仓", "hold": "观望", "wait": "观望",
        }.get(action_raw, action_raw)

        verdict = {}
        try:
            if s.evaluate_verdict_json:
                verdict = s.evaluate_verdict_json if isinstance(s.evaluate_verdict_json, dict) else _json.loads(s.evaluate_verdict_json)
        except Exception:
            pass

        _ts = s.timestamp
        result[_bucket].append({
            "id": f"snap-{s.id}",
            "time": _ts.strftime("%m-%d %H:%M:%S") if _ts else "",
            "symbol": s.symbol or "",
            "action": action_cn,
            "executed": bool(s.executed),
            "allowed": verdict.get("allowed"),
            "confidence": round(float(s.confidence or 0), 0) if s.confidence else 0,
            "block_reason": str(verdict.get("reason", ""))[:80],
            "source": str(s.source_lane or ""),
            "reasoning": (s.ai_reasoning or "")[:120],
            "direction": s.direction or "",
            "tier_tag": "short" if _bucket == "short" and _sym_u in _auto_syms else tier,
        })

    for t in result:
        result[t] = result[t][:limit]

    # 补充 MLTO 分析历史：读 mlto_thesis_events(thesis_update) 追加流，
    # 不再只读当前 mlto_thesis 快照（每 symbol 一行会被覆盖，面板看不到历史）。
    try:
        from backend.database.connection import AnalyticsSessionLocal as _ASL2
        from sqlalchemy import text as _sa_text
        adb2 = _ASL2()
        try:
            _ev_lim = max(int(limit), 40)
            _events = adb2.execute(_sa_text(
                "SELECT te.id, te.ts, t.symbol, te.payload_json, t.llm_conviction "
                "FROM mlto_thesis_events te "
                "JOIN mlto_thesis t ON t.thesis_id = te.thesis_id "
                "WHERE t.session_id = :sid AND t.tier = 'long' "
                "AND te.event_type = 'thesis_update' "
                "ORDER BY te.ts DESC LIMIT :lim"
            ), {"sid": session_id, "lim": _ev_lim}).fetchall()
        finally:
            adb2.close()
        for ev in _events:
            _payload = {}
            try:
                _raw = ev[3]
                _payload = _raw if isinstance(_raw, dict) else _json.loads(_raw or "{}")
            except Exception:
                _payload = {}
            _summary = str(_payload.get("summary") or "")[:120]
            if not _summary:
                continue
            _dir = str(_payload.get("direction") or "").lower()
            _ts = ev[1]
            _conv = _payload.get("conviction")
            if _conv is None:
                _conv = ev[4]
            result["long"].append({
                "id": f"mlto-ev-{ev[0]}",
                "time": _ts.strftime("%m-%d %H:%M:%S") if _ts else "",
                "symbol": ev[2] or "",
                "action": "分析",
                "executed": False,
                "allowed": None,
                "confidence": round(float(_conv or 0), 0),
                "block_reason": "",
                "source": "mlto_thesis_events",
                "reasoning": _summary,
                "direction": _dir,
                "tier_tag": "long",
            })
    except Exception as _mlto_err:
        logger.debug(f"[TierActivity] 补充 MLTO thesis 历史失败: {_mlto_err}")

    # 按时间倒序（含月日，避免跨日错序），截断到 limit
    for t in result:
        result[t] = sorted(result[t], key=lambda x: x.get("time") or "", reverse=True)[:limit]

    return result


@router.post("/cleanup-stale-strategies")
def cleanup_stale_strategies(db: Session = Depends(get_db)):
    """清理所有已停止会话的残留策略（处理历史遗留数据）"""
    result = full_auto_service.cleanup_stale_strategies(db)
    return result


@router.post("/merge-duplicates/{session_id}")
def merge_duplicate_strategies(session_id: str, db: Session = Depends(get_db)):
    """
    一键合并同币种重复 full_auto 策略：每币种只保留一条（按活跃/成交数/更新时间），
    其余暂停并移入已终止列表，避免界面与执行层多实例混乱。
    """
    result = full_auto_service.merge_duplicate_strategies(db, session_id)
    if not result.get("success"):
        raise HTTPException(
            status_code=400,
            detail=result.get("error", "合并失败"),
        )
    return result


@router.get("/debug/scheduler-state")
def debug_scheduler_state(db: Session = Depends(get_db)):
    """诊断：查看调度器状态和已注册的任务"""
    try:
        from backend.services.scheduler import task_scheduler
        running = task_scheduler.is_running()
        jobs = task_scheduler.get_job_info() if task_scheduler.scheduler else []
        # 直接从 DB 读取 session 状态（绕过内存缓存）
        db_session = db.query(FullAutoSession).filter(
            FullAutoSession.session_id == "fa_2313766258"
        ).first()
        return {
            "scheduler_started": task_scheduler._started,
            "scheduler_running": running,
            "apscheduler_running": task_scheduler.scheduler.running if task_scheduler.scheduler else None,
            "job_count": len(jobs),
            "jobs": [{"id": j["id"], "next_run": str(j["next_run_time"])} for j in jobs],
            "running_sessions": list(full_auto_service._running_sessions.keys()),
            "unified_tick_count": dict(full_auto_service._unified_tick_count),
            "unified_loop_running": dict(full_auto_service._unified_loop_running),
            "unified_loop_started": {
                k: f"{__import__('time').time() - v:.0f}s ago"
                for k, v in getattr(full_auto_service, '_unified_loop_started', {}).items()
            },
            "db_session_status": db_session.status if db_session else None,
            "db_last_health_check": str(db_session.last_health_check_at) if db_session else None,
            "db_event_log_count": len(db_session.event_log or []) if db_session else 0,
            "db_event_log_last_3": [
                {"time": e.get("time",""), "event": e.get("event","")}
                for e in (db_session.event_log or [])[-3:]
            ] if db_session else [],
        }
    except Exception as e:
        return {"error": str(e)}


@router.post("/debug/force-register-tick/{session_id}")
def debug_force_register_tick(session_id: str):
    """诊断：强制重新注册 tick loop（不经过 pause/resume）"""
    try:
        full_auto_service._register_health_check(session_id, 90)
        return {
            "registered": True,
            "session_id": session_id,
            "tick_count": full_auto_service._unified_tick_count.get(session_id, 0),
            "scheduler_running": full_auto_service._is_session_tick_active(session_id) if hasattr(full_auto_service, '_is_session_tick_active') else "unknown",
        }
    except Exception as e:
        return {"error": str(e)}
