# -*- coding: utf-8 -*-
"""RiskEngine 运维接口（/api/ops/risk/*，v3 方向 7）。

  GET  /api/ops/risk/status            状态机 / 急停 / 连通性 / 配额 / 最近拦截 / 上次巡检
  GET  /api/ops/risk/quota             指定账户日开仓配额使用情况（单一来源 runtime_tuning）
  POST /api/ops/risk/state             人工设置 TradingState（active/reducing/halted，可带 ttl）
  POST /api/ops/risk/kill              启用急停 {reason, close_all?, dry_run?}
  POST /api/ops/risk/release           释放急停
  POST /api/ops/risk/black-swan        黑天鹅剧本（默认 dry_run=true 只出清单）
  POST /api/ops/risk/window            添加事件避险窗口 {hours, reason, symbols?}
  POST /api/ops/risk/tick              立即执行一次巡检（闪崩/回撤/过期回收）
  POST /api/ops/risk/command           飞书/机器人命令入口（/kill /release /state /halt /reduce /active）

写操作需要 RISK_COMMAND_TOKEN（.env）——请求头 X-Risk-Token 或飞书回调 header.token；
未配置 token 时仅允许本机回环地址调用（与 ops 其它接口一致的“运维本机”假设）。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ops/risk", tags=["ops-risk"])


# ─────────────────────────── 鉴权 ───────────────────────────
def _authorize(request: Request, token_in_body: Optional[str] = None) -> None:
    expected = (os.getenv("RISK_COMMAND_TOKEN", "") or "").strip()
    provided = (request.headers.get("X-Risk-Token") or token_in_body or "").strip()
    if expected:
        if provided != expected:
            raise HTTPException(status_code=401, detail="invalid risk token")
        return
    host = (request.client.host if request.client else "") or ""
    if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(status_code=403, detail="RISK_COMMAND_TOKEN 未配置，仅允许本机调用")


def _db():
    from backend.database.connection import SessionLocal
    from backend.core.tenant import set_system_identity
    set_system_identity()
    return SessionLocal()


# ─────────────────────────── 只读 ───────────────────────────
@router.get("/status")
def risk_status(account_id: Optional[int] = Query(None)) -> Dict[str, Any]:
    from backend.services.risk.risk_engine import get_risk_engine_v3
    db = _db()
    try:
        return get_risk_engine_v3().status(db, account_id)
    finally:
        db.close()


@router.get("/quota")
def risk_quota(account_id: int = Query(...)) -> Dict[str, Any]:
    from backend.services.risk import daily_quota
    db = _db()
    try:
        return daily_quota.status(db, account_id)
    finally:
        db.close()


# ─────────────────────────── 写操作 ───────────────────────────
@router.post("/state")
def risk_set_state(request: Request, body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.risk.trading_state import TradingState, get_state_store
    try:
        state = TradingState(str(body.get("state") or "").lower())
    except ValueError:
        raise HTTPException(status_code=400, detail="state 必须是 active/reducing/halted")
    ttl_h = body.get("ttl_hours")
    snap = get_state_store().set_state(
        state, reason=str(body.get("reason") or "manual"), source=str(body.get("source") or "api"),
        ttl_seconds=(float(ttl_h) * 3600.0 if ttl_h else None),
        position_scale=body.get("position_scale"),
    )
    return snap.to_dict()


@router.post("/kill")
def risk_kill(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.risk import kill_switch
    st = kill_switch.engage(str(body.get("reason") or "manual"), source=str(body.get("source") or "api"))
    out: Dict[str, Any] = {"kill_switch": st.to_dict()}
    if body.get("close_all"):
        from backend.services.risk.playbook import kill_close_all
        db = _db()
        try:
            out["close_all"] = kill_close_all(
                db, dry_run=bool(body.get("dry_run", True)), include_live=bool(body.get("include_live", True)),
                reason=f"kill_switch: {body.get('reason') or 'manual'}",
            )
        finally:
            db.close()
    return out


@router.post("/release")
def risk_release(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.risk import kill_switch
    st = kill_switch.release(source=str(body.get("source") or "api"), reason=str(body.get("reason") or ""))
    return {"kill_switch": st.to_dict()}


@router.post("/black-swan")
def risk_black_swan(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.risk.playbook import black_swan_playbook
    db = _db()
    try:
        return black_swan_playbook(
            db, dry_run=bool(body.get("dry_run", True)), reason=str(body.get("reason") or "black_swan"),
            hours=float(body.get("hours") or 24.0),
        )
    finally:
        db.close()


@router.post("/window")
def risk_add_window(request: Request, body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.risk.trading_state import get_state_store
    hours = float(body.get("hours") or 0)
    if hours <= 0:
        raise HTTPException(status_code=400, detail="hours 必须 > 0")
    syms = body.get("symbols")
    snap = get_state_store().add_no_open_window(
        until=time.time() + hours * 3600.0, reason=str(body.get("reason") or "manual"),
        symbols=[str(s) for s in syms] if isinstance(syms, list) and syms else None,
    )
    return {"no_open_windows": snap.no_open_windows}


@router.post("/tick")
def risk_tick(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.risk.risk_engine import get_risk_engine_v3
    return get_risk_engine_v3().tick()


# ─────────────────────────── 命令入口（飞书 / 机器人） ───────────────────────────
def _extract_command(payload: Dict[str, Any]) -> tuple:
    """兼容三种格式：{"command": "/kill 原因"}；飞书 v2 事件 im.message.receive_v1；纯 {"text": ...}。
    返回 (text, chat_id, token)。"""
    if not isinstance(payload, dict):
        return "", None, None
    if payload.get("command"):
        return str(payload["command"]), payload.get("chat_id"), payload.get("token")
    header = payload.get("header") or {}
    event = payload.get("event") or {}
    msg = event.get("message") or {}
    if msg:
        content = msg.get("content")
        text = ""
        try:
            text = (json.loads(content) or {}).get("text", "") if isinstance(content, str) else ""
        except Exception:
            text = str(content or "")
        # 去掉 @机器人 前缀
        text = " ".join(t for t in str(text).split() if not t.startswith("@_user"))
        return text, msg.get("chat_id"), header.get("token") or payload.get("token")
    if payload.get("text"):
        return str(payload["text"]), payload.get("chat_id"), payload.get("token")
    return "", None, payload.get("token")


def run_command(text: str, *, source: str = "command") -> Dict[str, Any]:
    """命令解析与执行（独立函数便于单测）。"""
    from backend.services.risk import kill_switch
    from backend.services.risk.trading_state import TradingState, get_state_store
    from backend.services.risk.risk_engine import get_risk_engine_v3

    parts = str(text or "").strip().split()
    if not parts:
        return {"ok": False, "reply": "空命令。可用：/kill [原因] /release /halt [原因] /reduce [小时] /active /state"}
    cmd = parts[0].lower().lstrip("/")
    arg = " ".join(parts[1:]).strip()
    if cmd in ("kill", "急停"):
        st = kill_switch.engage(arg or "feishu command", source=source)
        return {"ok": True, "reply": f"🛑 急停已启用（{st.source}）。实盘新开仓全部拦截；平仓不受影响。"}
    if cmd in ("release", "恢复"):
        st = kill_switch.release(source=source, reason=arg)
        return {"ok": True, "reply": ("✅ 急停已释放，状态 ACTIVE。" if not st.engaged else f"⚠️ 文件已清，但 env LIVE_KILL_SWITCH 仍为真，急停继续生效。")}
    if cmd in ("halt", "停"):
        get_state_store().set_state(TradingState.HALTED, reason=arg or "feishu halt", source=source)
        return {"ok": True, "reply": "⛔ TradingState=HALTED（全停，不自动恢复）。"}
    if cmd in ("reduce", "只平"):
        try:
            hours = float(arg.split()[0]) if arg else 24.0
        except ValueError:
            hours = 24.0
        get_state_store().set_state(TradingState.REDUCING, reason=f"feishu reduce {hours}h", source=source,
                                    ttl_seconds=hours * 3600.0)
        return {"ok": True, "reply": f"🔻 TradingState=REDUCING（只平不开），{hours:.0f} 小时后自动恢复。"}
    if cmd in ("active", "恢复交易"):
        ks = kill_switch.kill_switch_status()
        if ks.engaged:
            return {"ok": False, "reply": "急停仍生效，先 /release。"}
        get_state_store().set_state(TradingState.ACTIVE, reason=arg or "feishu active", source=source)
        return {"ok": True, "reply": "▶️ TradingState=ACTIVE。"}
    if cmd in ("state", "状态", "status"):
        s = get_risk_engine_v3().status()
        ts = s["trading_state"]
        ks = s["kill_switch"]
        tripped = [v for v, d in (s["connectivity"].get("venues") or {}).items() if d.get("tripped")]
        return {"ok": True, "reply": (
            f"状态: {ts['state']} (scale={ts['position_scale']}, source={ts['source']}, reason={ts['reason'][:80]})\n"
            f"急停: {'ON' if ks['engaged'] else 'off'} {ks['source']}\n"
            f"连通性熔断: {tripped or '无'}\n"
            f"配额: {s.get('quota_caps')}\n"
            f"拦截/放行: {s['counters'].get('blocked')}/{s['counters'].get('allowed')}"
        )}
    return {"ok": False, "reply": f"未知命令 {parts[0]}。可用：/kill /release /halt /reduce /active /state"}


@router.post("/command")
async def risk_command(request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    # 飞书 URL 校验握手
    if isinstance(payload, dict) and payload.get("type") == "url_verification":
        expected = (os.getenv("RISK_COMMAND_TOKEN", "") or "").strip()
        if expected and payload.get("token") != expected:
            raise HTTPException(status_code=401, detail="invalid token")
        return {"challenge": payload.get("challenge")}
    text, chat_id, token = _extract_command(payload if isinstance(payload, dict) else {})
    _authorize(request, token)
    result = run_command(text, source="feishu" if chat_id else "command")
    if chat_id:
        try:
            from backend.services.openclaw_notify import get_notifier
            get_notifier().send_sync_text_to_chat(str(chat_id), result["reply"], title="RiskEngine")
        except Exception as exc:
            logger.debug("[ops/risk/command] 回复飞书失败: %s", exc)
    return result
