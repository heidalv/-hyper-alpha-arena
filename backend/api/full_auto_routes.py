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
    tier: Optional[str] = None  # short|mid|long；固定币写入目标周期


@router.post("/add-symbols/{session_id}")
def add_symbols(session_id: str, request: AddSymbolsRequest, db: Session = Depends(get_db)):
    """向运行中的会话添加交易对"""
    if not request.symbols:
        raise HTTPException(status_code=400, detail="请至少选择一个交易对")
    result = full_auto_service.add_symbols(
        db, session_id, request.symbols, tier=request.tier,
    )
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


class FixedSymbolsByTierRequest(BaseModel):
    short: Optional[List[str]] = None
    mid: Optional[List[str]] = None
    long: Optional[List[str]] = None


@router.put("/fixed-symbols-by-tier/{session_id}")
@router.post("/fixed-symbols-by-tier/{session_id}")
def put_fixed_symbols_by_tier(
    session_id: str, request: FixedSymbolsByTierRequest, db: Session = Depends(get_db),
):
    """一次提交短/中/长固定币（必须来自交易对备选池）。

    同时挂 PUT/POST：旧进程未加载本路由时，PUT 会落到 SPA 的 GET catch-all → 405；
    前端统一走 POST，与 add-symbols / update-config 一致。
    """
    session = db.query(FullAutoSession).filter(
        FullAutoSession.session_id == session_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if session.status not in ("running", "defensive", "paused"):
        raise HTTPException(status_code=400, detail=f"会话状态为 {session.status}")

    from backend.services.auto_coin_selector import (
        _parse_by_tier_map,
        set_fixed_symbols_by_tier,
    )
    # 合并：未传的周期保留现有；空列表 [] 必须保留（不能 or 并集，否则三路焊死）
    cur = _parse_by_tier_map(getattr(session, "fixed_symbols_by_tier", None))
    legacy = [str(s).upper() for s in (session.symbols or []) if s]

    def _pick(req_val, key: str):
        if req_val is not None:
            return req_val
        if cur and key in cur:
            return cur.get(key) or []
        # 尚无分周期配置时，仅 long 可回退并集；短/中默认空，避免焊死
        if key == "long":
            return list(legacy)
        return []

    payload = {
        "short": _pick(request.short, "short"),
        "mid": _pick(request.mid, "mid"),
        "long": _pick(request.long, "long"),
    }
    result = set_fixed_symbols_by_tier(session_id, payload, db=db, enforce_backup_pool=True)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "保存失败"))
    db.refresh(session)
    try:
        full_auto_service._append_event(
            session,
            "fixed_symbols_by_tier_saved",
            (
                f"短={','.join(payload.get('short') or []) or '空'} | "
                f"中={','.join(payload.get('mid') or []) or '空'} | "
                f"长={','.join(payload.get('long') or []) or '空'}"
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
    full_auto_service._invalidate_session_status_cache(session_id)
    return result


@router.post("/auto-coin-mid/{session_id}/enable")
def enable_auto_coin_mid(session_id: str, http_request: Request, db: Session = Depends(get_db)):
    """开启会话中线 AI 选币（与短线 auto-coin 通道隔离）。"""
    return _set_auto_coin_mid(session_id, True, http_request, db)


@router.post("/auto-coin-mid/{session_id}/disable")
def disable_auto_coin_mid(session_id: str, http_request: Request, db: Session = Depends(get_db)):
    """关闭会话中线 AI 选币：不再新纳入 AI 中线币；已有 mid 仓继续管。"""
    return _set_auto_coin_mid(session_id, False, http_request, db)


def _set_auto_coin_mid(session_id: str, enabled: bool, http_request: Request, db: Session):
    session = db.query(FullAutoSession).filter(
        FullAutoSession.session_id == session_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if session.status not in ("running", "defensive", "paused"):
        raise HTTPException(status_code=400, detail=f"会话状态为 {session.status}")
    from backend.api.auto_coin_routes import _assert_vip_session_auto_coin
    _assert_vip_session_auto_coin(http_request, db, session)
    session.auto_coin_mid_enabled = bool(enabled)
    if session.auto_coin_mid_max_slots is None:
        session.auto_coin_mid_max_slots = 3
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"保存失败: {e}")
    if session_id in full_auto_service._running_sessions:
        full_auto_service._running_sessions[session_id]["auto_coin_mid_enabled"] = bool(enabled)
    full_auto_service._invalidate_session_status_cache(session_id)
    logger.info(
        "[FullAuto] auto_coin_mid_enabled=%s session=%s",
        enabled, session_id,
    )
    return {
        "success": True,
        "session_id": session_id,
        "auto_coin_mid_enabled": bool(enabled),
        "auto_coin_mid_max_slots": int(session.auto_coin_mid_max_slots or 3),
        "message": "中线AI选币已开启" if enabled else "中线AI选币已关闭（中线固定币仍可交易）",
    }


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
    auto_coin_max_slots: Optional[int] = None  # 5~10，短线 AI 选币槽位
    auto_coin_mid_enabled: Optional[bool] = None
    auto_coin_mid_max_slots: Optional[int] = None  # 1~5，中线 AI 选币槽位


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

    if (
        request.auto_coin_max_slots is not None
        or request.auto_coin_mid_enabled is not None
        or request.auto_coin_mid_max_slots is not None
    ):
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
    if request.auto_coin_mid_enabled is not None:
        session.auto_coin_mid_enabled = bool(request.auto_coin_mid_enabled)
        updated.append(f"auto_coin_mid_enabled={session.auto_coin_mid_enabled}")
    if request.auto_coin_mid_max_slots is not None:
        v = int(request.auto_coin_mid_max_slots)
        if v < 1 or v > 5:
            raise HTTPException(status_code=400, detail="auto_coin_mid_max_slots 须在 1-5")
        session.auto_coin_mid_max_slots = v
        updated.append(f"auto_coin_mid_max_slots={v}")

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
        if request.auto_coin_mid_enabled is not None:
            rs["auto_coin_mid_enabled"] = session.auto_coin_mid_enabled
        if request.auto_coin_mid_max_slots is not None:
            rs["auto_coin_mid_max_slots"] = session.auto_coin_mid_max_slots

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
    # [perf 2026-08-18] GUI 高频轮询：5s TTL 进程内缓存（GIL 竞争下命中路径≈0ms）。
    from backend.utils.ttl_cache import ttl_cached as _ttl_cached

    _cache_key = f"fullauto_sessions:{account_id or ''}:{status or ''}"

    def _build():
        return _build_sessions_list(db, account_id, status)

    return _ttl_cached(_cache_key, 10.0, _build)


def _build_sessions_list(db: Session, account_id: Optional[int], status: Optional[str]) -> list:
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

    # [perf 2026-08-18] N+1 批量化：此前每个会话 4~5 次小查询（余额/持仓/两个账户），
    # 50 会话 ≈ 250 次查询，在 GIL 竞争下 /sessions 实测 13.6s。现 3 次批量查询。
    _paper_ids = [s.paper_account_id for s in sessions if s.trading_mode == "paper" and s.paper_account_id]
    _acct_ids = {s.account_id for s in sessions} | set(_paper_ids)
    _balances: dict = {}
    _positions_by_acct: dict = {}
    try:
        if _paper_ids:
            _balances = {
                pb.account_id: pb
                for pb in db.query(PaperBalance).filter(PaperBalance.account_id.in_(_paper_ids)).all()
            }
            for pp in db.query(PaperPosition).filter(PaperPosition.account_id.in_(_paper_ids)).all():
                _positions_by_acct.setdefault(pp.account_id, []).append(pp)
    except Exception:
        pass
    _accounts = {
        a.id: a for a in db.query(Account).filter(Account.id.in_(_acct_ids)).all()
    } if _acct_ids else {}

    _backup_pool: list = []
    try:
        from backend.services.trading_pairs_config import get_user_trading_pairs
        _backup_pool = list(get_user_trading_pairs() or [])
    except Exception:
        _backup_pool = []
    try:
        from backend.services.auto_coin_selector import (
            _load_ai_mid_sticky as _load_mid_sticky,
            _parse_by_tier_map as _parse_by_tier,
        )
    except Exception:
        _load_mid_sticky = None  # type: ignore
        def _parse_by_tier(raw):  # type: ignore
            return raw if isinstance(raw, dict) else {}
    result = []
    for s in sessions:
        _pnl = s.total_pnl or 0
        _trades = s.total_trades or 0
        _wins = s.winning_trades or 0
        if s.trading_mode == "paper" and s.paper_account_id:
            try:
                _pb = _balances.get(s.paper_account_id)
                if _pb:
                    _init = float(_pb.initial_balance or 10000)
                    _eq = float(_pb.total_equity or _init)
                    _pnl = round(_eq - _init, 4)
                    _all_pp = list(_positions_by_acct.get(s.paper_account_id) or [])
                    if _pb.last_reset_at:
                        _lr = _pb.last_reset_at
                        _all_pp = [
                            pp for pp in _all_pp
                            if pp.opened_at and pp.opened_at >= _lr
                        ]
                    _trades = len(_all_pp)
                    _wins = sum(1 for pp in _all_pp if float(pp.unrealized_pnl or 0) + float(pp.partial_realized_pnl or 0) > 0)
            except Exception:
                pass
        _trader = _accounts.get(s.account_id)
        _paper_id = getattr(s, "paper_account_id", None)
        _paper = _accounts.get(_paper_id) if _paper_id else None
        _trading_id = _paper_id if (s.trading_mode == "paper" and _paper_id) else s.account_id
        _mid_syms: list = []
        if bool(getattr(s, "auto_coin_mid_enabled", False)) and _load_mid_sticky:
            try:
                _st = _load_mid_sticky(s.session_id) or {}
                _mid_syms = [
                    str(x).strip().upper()
                    for x in (_st.get("symbols") or [])
                    if x
                ][: int(getattr(s, "auto_coin_mid_max_slots", None) or 3)]
            except Exception:
                _mid_syms = []
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
            "auto_coin_mid_enabled": bool(getattr(s, "auto_coin_mid_enabled", False)),
            "auto_coin_mid_max_slots": int(getattr(s, "auto_coin_mid_max_slots", None) or 3),
            "auto_coin_mid_symbols": _mid_syms,
            "fixed_symbols_by_tier": _parse_by_tier(getattr(s, "fixed_symbols_by_tier", None)),
            "backup_pool": _backup_pool,
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
    # [2026-08-14] 中线状态明示：标签随运行时开关动态变化，
    # 避免"因子化已定案但旧 AI 仍在过渡执行"造成的显示混乱。
    # 三态：mlto_transition（旧AI执行）/ mid_paused（旧AI已停、因子路由未接线，
    # 只跑因子研究）/ factor_route（因子路由接管中线入场）。
    _mid_mode = "mlto_transition"
    _mid_label = "中线AI(过渡)"
    try:
        from backend.config.settings import MIDLONG_MID_VIA_MLTO as _mv
        from backend.config.settings import MIDLONG_MID_VIA_FACTOR_ROUTE as _fr
        if _mv:
            _mid_mode = "mlto_transition"
            _mid_label = "中线AI(过渡)"
        elif _fr:
            _mid_mode = "factor_route"
            _mid_label = "中线因子路由"
        else:
            _mid_mode = "mid_paused"
            _mid_label = "中线暂停(因子研究)"
    except Exception:
        pass
    # [2026-08-19] 长线真实节奏澄清：midlong 独立循环（mid+long 共用）的实际频率
    # 由 orchestrator 注册为 max(45, TIER_MID_AI_TICK_SEC)；intervals["long"]=TIER_LONG_AI_TICK_SEC
    # 只是「入场分析 due 节流」，不是循环频率。前端曾把 240 标成 tick 频率（误导）。
    _long_loop_sec = intervals.get("long", 240)
    try:
        from backend.config.settings import TIER_MID_AI_TICK_SEC as _mid_tick
        _long_loop_sec = max(45, int(_mid_tick or 45))
    except Exception:
        pass
    return {
        "intervals": intervals,
        "labels": {
            "coordinator": "协调器",
            "short": "短线因子",
            "mid": _mid_label,
            "long": "长线AI",
        },
        "mid_mode": _mid_mode,
        # 长线三层节奏：循环(实际扫描) / 入场节流 / 决策
        "long_loop_sec": _long_loop_sec,
        "long_entry_sec": int(intervals.get("long", 240)),
        "long_decision": "daily_closed_bar",
    }


@router.get("/tier-status/{session_id}")
def get_tier_status(session_id: str, db: Session = Depends(get_db)):
    """获取多周期并行交易状态（各 tier 的策略数、持仓、预算使用情况）"""
    # [perf 2026-08-18] GUI 高频轮询：5s TTL 缓存（GIL 竞争下命中≈0ms）。
    from backend.utils.ttl_cache import ttl_cached

    return ttl_cached(
        f"fullauto_tier_status:{session_id}",
        10.0,
        lambda: _tier_status_impl(session_id, db),
    )


def _tier_status_impl(session_id: str, db: Session) -> dict:
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

    # 三通道拆分：短线固定 / AI中线 / 固定长线
    from backend.services.auto_coin_selector import (
        _parse_by_tier_map,
        get_ai_mid_candidates_for_session as _get_ai_mid,
        get_fixed_symbols_for_session as _get_fixed,
        get_session_mid_ai_config,
    )
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
    _fixed_long = {str(s).upper() for s in (_get_fixed(session_id, tier="long") or set())}
    _fixed_mid = {str(s).upper() for s in (_get_fixed(session_id, tier="mid") or set())}
    _fixed_short = {str(s).upper() for s in (_get_fixed(session_id, tier="short") or set())}
    _fixed_set = _fixed_long  # lanes.fixed_long 兼容
    _ai_mid_set = {str(s).upper() for s in (_get_ai_mid(session_id, db=db) or [])}
    _mid_cfg = get_session_mid_ai_config(session_id, db=db)
    _by_tier = _parse_by_tier_map(getattr(session, "fixed_symbols_by_tier", None))

    from sqlalchemy import or_ as _or

    tiers = {}
    for t in ("short", "mid", "long"):
        tier_strats = [s for s in strats if (
            getattr(s, "timeframe_tier", None) == t
            or NATURE_TO_TIER.get((s.genome or {}).get("trade_nature", ""), "mid") == t
        )]
        if t == "long":
            positions = (
                db.query(_PP)
                .filter(
                    _PP.account_id == _trading_acct,
                    _PP.status == "open",
                    _or(
                        _PP.timeframe_tier == "long",
                        _PP.trade_nature.in_(("trend_follow", "position")),
                    ),
                )
                .all()
            )
        elif t == "mid":
            positions = (
                db.query(_PP)
                .filter(
                    _PP.account_id == _trading_acct,
                    _PP.status == "open",
                    _or(
                        _PP.timeframe_tier == "mid",
                        _PP.trade_nature == "swing",
                    ),
                )
                .all()
            )
        else:
            positions = db.query(_PP).filter(
                _PP.account_id == _trading_acct,
                _PP.status == "open",
                _or(
                    _PP.timeframe_tier == "short",
                    _PP.trade_nature == "scalp",
                ),
            ).all()
        margin_used = sum(float(p.margin or 0) for p in positions)
        budget = total_equity * TIER_BUDGET_ALLOCATION.get(t, 0.3)
        max_margin = total_equity * TIER_MAX_MARGIN_PCT.get(t, 0.4)

        if t == "long":
            _tier_symbols = sorted(_fixed_long)
        elif t == "mid":
            _pos_syms = {str(p.symbol).upper() for p in positions if p.symbol}
            _tier_symbols = sorted(_fixed_mid | _ai_mid_set | _pos_syms)
        else:
            _short_ai = _auto_coin_set if getattr(session, "auto_coin_enabled", False) else set()
            _tier_symbols = sorted(_fixed_short | _short_ai)

        tiers[t] = {
            "label": {"short": "短线", "mid": "AI中线", "long": "固定长线"}[t],
            "strategy_count": len(tier_strats),
            "active_count": sum(1 for s in tier_strats if s.status == "active"),
            "paused_count": sum(1 for s in tier_strats if s.status == "paused"),
            "position_count": len(positions),
            "position_count_mid": 0,
            "position_count_long_only": len(positions) if t == "long" else 0,
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
        "lanes": {
            "fixed_long": sorted(_fixed_long),
            "fixed_mid": sorted(_fixed_mid),
            "fixed_short": sorted(_fixed_short),
            "ai_mid": sorted(_ai_mid_set),
            "auto_coin": sorted(_auto_coin_set),
        },
        "fixed_symbols_by_tier": _by_tier or {
            "short": sorted(_fixed_short),
            "mid": sorted(_fixed_mid),
            "long": sorted(_fixed_long),
        },
        "auto_coin_mid_enabled": bool(_mid_cfg.get("enabled")),
        "auto_coin_mid_max_slots": int(_mid_cfg.get("max_slots") or 3),
        "tiers": tiers,
    }


@router.get("/tier-activity/{session_id}")
def get_tier_activity(session_id: str, limit: int = 60, db: Session = Depends(get_db)):
    """获取各周期最近的策略决策记录（实时滚动，含 hold/拦截/未执行，按 tier 分类）。"""
    # [perf 2026-08-18] GUI 高频轮询：5s TTL 缓存（GIL 竞争下命中≈0ms）。
    from backend.utils.ttl_cache import ttl_cached

    return ttl_cached(
        f"fullauto_tier_activity:{session_id}:{limit}",
        10.0,
        lambda: _tier_activity_impl(session_id, limit, db),
    )


def _tier_activity_impl(session_id: str, limit: int, db: Session) -> dict:
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
    from backend.services.auto_coin_selector import (
        get_ai_mid_candidates_for_session as _get_ai_mid_act,
        get_fixed_symbols_for_session as _get_fixed_act,
    )
    _auto_syms = set()
    try:
        _raw_auto = getattr(session, "auto_coin_symbols", None) or []
        if isinstance(_raw_auto, str):
            import json as _j
            _raw_auto = _j.loads(_raw_auto) if _raw_auto else []
        _auto_syms = {str(x).strip().upper() for x in _raw_auto if x}
    except Exception:
        _auto_syms = set()
    # 分周期固定白名单：并集会把 mid 币误当 long 兜底
    _fixed_long = {str(s).upper() for s in (_get_fixed_act(session_id, tier="long") or set())}
    _fixed_mid = {str(s).upper() for s in (_get_fixed_act(session_id, tier="mid") or set())}
    _fixed_syms = _fixed_long | _fixed_mid
    _ai_mid_syms = {str(s).upper() for s in (_get_ai_mid_act(session_id, db=db) or [])}
    # 已开仓但列表被刷掉的币：仍按真实持仓性质归桶（续管），避免 AAVE 这类短线仓窜进长线
    _open_short = set()
    _open_mid = set()
    _open_long = set()
    try:
        from backend.database.models import PaperPosition as _PP2
        _acct = (
            session.paper_account_id
            if (session.trading_mode == "paper" and getattr(session, "paper_account_id", None))
            else session.account_id
        )
        if _acct:
            for _p in db.query(_PP2).filter(_PP2.account_id == _acct, _PP2.status == "open").all():
                _su = str(_p.symbol or "").upper()
                _tt = str(_p.timeframe_tier or "").lower()
                _tn = str(_p.trade_nature or "").lower()
                if _tt == "short" or _tn == "scalp":
                    _open_short.add(_su)
                elif _tt == "mid" or _tn == "swing":
                    _open_mid.add(_su)
                elif _tt == "long" or _tn in ("trend_follow", "position"):
                    _open_long.add(_su)
    except Exception:
        pass

    for s in snapshots:
        _snap_reasoning = str(s.ai_reasoning or "")
        if "中长线AI强制→" in _snap_reasoning:
            continue
        tier = (s.tier or "mid").lower()
        if tier not in result:
            continue
        _sym_u = str(s.symbol or "").upper()
        _lane = str(getattr(s, "source_lane", None) or "").lower()
        # 归桶优先级：显式 snapshot.tier / lane > 持仓续管兜底 > 白名单
        # 禁止「有 mid 仓就把该币所有 long 分析吞进中线」（BTC 长线消失根因之一）
        if _lane in ("scalp", "scalp_lane") or tier == "short":
            _bucket = "short"
        elif tier == "long":
            _bucket = "long"
        elif tier == "mid":
            _bucket = "mid"
        elif _sym_u in _open_short or (
            _sym_u in _auto_syms and _sym_u not in _ai_mid_syms and _sym_u not in _fixed_syms
        ):
            _bucket = "short"
        elif _sym_u in _open_mid or _sym_u in _ai_mid_syms or _sym_u in _fixed_mid:
            _bucket = "mid"
        elif _sym_u in _open_long or _sym_u in _fixed_long:
            _bucket = "long"
        else:
            _bucket = "short"  # 未知杂项默认短线，禁止污染长线

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
            "tier_tag": _bucket,
            "lane_note": (
                "持仓续管" if (
                    (_bucket == "short" and _sym_u in _open_short and _sym_u not in _auto_syms)
                    or (_bucket == "mid" and _sym_u in _open_mid and _sym_u not in _ai_mid_syms)
                ) else ""
            ),
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
                "SELECT te.id, te.ts, t.symbol, te.payload_json, t.llm_conviction, t.tier "
                "FROM mlto_thesis_events te "
                "JOIN mlto_thesis t ON t.thesis_id = te.thesis_id "
                "WHERE t.session_id = :sid AND t.tier IN ('long', 'mid') "
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
            _ev_tier = str(ev[5] or "long").lower()
            if _ev_tier not in ("mid", "long"):
                _ev_tier = "long"
            result[_ev_tier].append({
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
                "tier_tag": _ev_tier,
            })
    except Exception as _mlto_err:
        logger.debug(f"[TierActivity] 补充 MLTO thesis 历史失败: {_mlto_err}")

    # 按时间倒序（含月日，避免跨日错序），截断到 limit
    for t in result:
        result[t] = sorted(result[t], key=lambda x: x.get("time") or "", reverse=True)[:limit]

    return result


# ══════════════════════════════════════════════════════
#  P0-D 透明化：冷却矩阵 + 门禁拦截事件流（只读）
# ══════════════════════════════════════════════════════

_BLOCK_EVENT_MARKERS = (
    "block", "blocked", "cooldown", "circuit", "defensive_entry",
    "veto", "reject", "gate", "loss_freeze", "tier_budget", "rebound_gate",
)


def _is_block_event(event_type: str) -> bool:
    _et = (event_type or "").lower()
    return any(m in _et for m in _BLOCK_EVENT_MARKERS)


@router.get("/cooldowns/{session_id}")
def get_session_cooldowns(session_id: str, db: Session = Depends(get_db)):
    """P0-D 只读：会话交易账户的冷却矩阵（全平/减仓/AI反向 三类冷却 + 分周期遮挡）。

    不修改任何冷却状态；数据源为 reentry_cooldown.get_cooldown_snapshot。
    """
    session = db.query(FullAutoSession).filter(
        FullAutoSession.session_id == session_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    _trading_acct = (
        session.paper_account_id
        if (session.trading_mode == "paper" and getattr(session, "paper_account_id", None))
        else session.account_id
    )
    if not _trading_acct:
        raise HTTPException(status_code=404, detail="会话未绑定交易账户")

    try:
        from backend.services.reentry_cooldown import get_cooldown_snapshot
        snapshot = get_cooldown_snapshot(_trading_acct)
    except Exception as e:
        logger.warning("[P0-D] cooldown snapshot failed: %s", e)
        snapshot = {"error": str(e)[:120]}

    # P0-E 透明化：周期级熔断快照（只读）
    try:
        from backend.services.tier_circuit_breaker import get_tier_circuit_snapshot
        snapshot["tier_circuit"] = get_tier_circuit_snapshot(_trading_acct)
    except Exception as e:
        logger.warning("[P0-E] tier circuit snapshot failed: %s", e)
        snapshot["tier_circuit"] = {"error": str(e)[:120]}

    snapshot["session_id"] = session_id
    snapshot["trading_account_id"] = _trading_acct
    return snapshot


@router.get("/events/{session_id}")
def get_session_events(session_id: str, limit: int = 60, mode: str = "blocks",
                       db: Session = Depends(get_db)):
    """P0-D 只读：会话门禁拦截事件流（session.event_log 过滤，最新在前）。

    mode=blocks: 仅阻断/冷却/门禁类事件（默认）；mode=all: 全部事件。
    """
    session = db.query(FullAutoSession).filter(
        FullAutoSession.session_id == session_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    events = list(session.event_log or [])
    if mode != "all":
        events = [e for e in events if _is_block_event(str(e.get("event") or ""))]
    # 最新在前
    events.sort(key=lambda e: str(e.get("time") or ""), reverse=True)
    return {
        "session_id": session_id,
        "mode": mode,
        "total": len(events),
        "events": events[: max(1, min(int(limit), 200))],
    }


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
def debug_scheduler_state(
    session_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """诊断：查看调度器状态和已注册的任务。

    session_id 可选：传入时按该会话读取 DB 状态；缺省时取内存 running_sessions
    的第一个（或 DB 中最新 running/defensive 会话），无任何活跃会话时 db_session 相关字段为 null。
    """
    # [perf 2026-08-18] 诊断页轮询：5s TTL。
    from backend.utils.ttl_cache import ttl_cached

    return ttl_cached(
        f"fullauto_scheduler_state:{session_id or ''}", 5.0,
        lambda: _scheduler_state_impl(session_id, db),
    )


def _scheduler_state_impl(session_id: Optional[str], db: Session):
    try:
        from backend.services.scheduler import task_scheduler
        running = task_scheduler.is_running()
        jobs = task_scheduler.get_job_info() if task_scheduler.scheduler else []
        # 定位展示用的 DB session：参数优先 → 内存 running 会话 → DB 最新活跃会话
        db_session = None
        if session_id:
            db_session = db.query(FullAutoSession).filter(
                FullAutoSession.session_id == session_id
            ).first()
        else:
            _running = list(full_auto_service._running_sessions.keys())
            if _running:
                db_session = db.query(FullAutoSession).filter(
                    FullAutoSession.session_id == _running[0]
                ).first()
            if not db_session:
                db_session = (
                    db.query(FullAutoSession)
                    .filter(FullAutoSession.status.in_(["running", "defensive"]))
                    .order_by(FullAutoSession.created_at.desc())
                    .first()
                )
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


@router.get("/debug/portfolio-budget")
def debug_portfolio_budget():
    """诊断：统一冻结台账（FreezeCoordinator）+ 组合预算原始状态。
    风控止血后冻结不可见是盲区，此端点让前端/运维直接看到冻结原因与剩余时间。"""
    try:
        from backend.services.risk_management.freeze_coordinator import status as _fz_status
        st = _fz_status()
        return st
    except Exception as e:
        try:
            from backend.services.risk_management.portfolio_budget import portfolio_budget
            import time as _t
            st = portfolio_budget.status()
            now = _t.time()
            st["_now"] = now
            for key in ("global_frozen_until",):
                v = st.get(key)
                if isinstance(v, (int, float)) and v > 0:
                    st[key + "_remaining_s"] = max(0, int(v - now))
            for grp in ("account_frozen", "strategy_frozen", "key_frozen"):
                d = st.get(grp) or {}
                st[grp] = {str(k): {"until": v, "remaining_s": max(0, int(v - now))} for k, v in d.items()}
            return st
        except Exception as e2:
            return {"error": str(e), "fallback_error": str(e2)}
