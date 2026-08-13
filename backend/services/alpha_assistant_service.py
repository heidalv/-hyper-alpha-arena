"""Alpha 悬浮助手 — L1/L2 + 会话持久化 + 飞书同步入口。"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, Generator, Iterable, Optional

logger = logging.getLogger(__name__)

_SESSIONS: Dict[str, list] = {}
_SESSION_PENDING: Dict[str, Dict[str, Any]] = {}


def _build_l1_context(*, page_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from backend.services.health_snapshot_service import build_combined_digest

    combined = build_combined_digest(window_hours=24)
    return {
        "log_error_digest": combined.get("log_digest") or {},
        "health_apis_snapshot": combined.get("health_snapshot") or {},
        "page_context": page_context or {},
        "tool_results": {},
    }


def _render_system_prompt(ctx: Dict[str, Any]) -> str:
    from backend.services.prompt_registry import get_prompt_registry

    registry = get_prompt_registry()
    return registry.render_task(
        "task_assistant_chat",
        {
            "log_error_digest": ctx.get("log_error_digest"),
            "health_apis_snapshot": ctx.get("health_apis_snapshot"),
            "page_context": ctx.get("page_context"),
            "tool_results": ctx.get("tool_results"),
        },
    )


def _maybe_answer_locally(user_message: str, ctx: Dict[str, Any]) -> Optional[str]:
    text = (user_message or "").strip().lower()
    digest = ctx.get("log_error_digest") or {}
    health = ctx.get("health_apis_snapshot") or {}
    apis = health.get("apis") or {}

    if any(k in text for k in ("报错", "error", "日志", "异常")):
        total = int(digest.get("total_errors") or 0)
        if total <= 0:
            return "最近 24 小时 **backend.log** 未发现 ERROR/CRITICAL，系统日志看起来正常。"
        lines = [f"最近 24 小时共有 **{total}** 条 ERROR/CRITICAL。"]
        for item in (digest.get("entries") or [])[:3]:
            hint = item.get("severity_hint", "P2")
            lines.append(
                f"- [{hint}] `{item.get('logger', '?')}` ×{item.get('count', 0)}："
                f"{item.get('sample', '')[:120]}"
            )
        lines.append("\n可在 **OpenCode 智能中心 → 系统健康** Tab 查看完整列表。")
        return "\n".join(lines)

    if any(k in text for k in ("opencode", "在线", "sidecar", "bridge")):
        oc = (apis.get("opencode_status") or {}).get("data") or {}
        bridge = oc.get("bridge") or {}
        sidecar = oc.get("sidecar") or {}
        healthy = bool(oc.get("serve_healthy"))
        lines = [
            f"OpenCode Bridge：{'**在线**' if bridge.get('enabled') else '未启用'}",
            f"Sidecar 健康：{'**正常**' if healthy else '异常'}",
        ]
        if sidecar:
            lines.append(f"Sidecar 端口：{sidecar.get('port', '?')}，模式：{sidecar.get('mode', '?')}")
        if bridge.get("last_error"):
            lines.append(f"最近错误：{bridge['last_error']}")
        return "\n".join(lines)

    if any(k in text for k in ("漏斗", "提案", "进化")):
        funnel = (apis.get("proposal_funnel") or {}).get("data") or {}
        f = funnel.get("funnel") or {}
        pol = funnel.get("validation_policy") or {}
        mode_hint = ""
        if pol.get("mode") == "post_apply_slice":
            mode_hint = (
                f"\n\n验证模式：**应用后成交切片**（Pace={pol.get('gear', '?')}，"
                f"最少浸泡 {pol.get('min_age_hours', '?')}h，"
                f"凑够 5 笔 post-apply 平仓即可评估，不必等满 24h）。"
            )
        return (
            f"进化提案漏斗：创建 {f.get('created', 0)} → "
            f"已应用 {f.get('applied', 0)} → "
            f"已评估 {f.get('evaluated', 0)} → "
            f"改善 {f.get('improved', 0)}。"
            f"{mode_hint}"
            "\n\n模拟盘应用参数后即在验证；评估=对比应用前后 + 必要时自动回滚。"
        )

    if any(k in text for k in ("日报", "总结", "报表", "daily report")):
        from backend.services.assistant_daily_report_service import build_daily_report_text

        return build_daily_report_text()

    return None


def _is_confirm(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in ("确认", "confirm", "是的", "好", "ok", "yes", "y")


def _is_live_confirm(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in ("确认晋升", "confirm live", "确认 live", "确认上线")


def _sse(event: str, payload: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _persist_user_turn(
    db,
    *,
    session_id: Optional[str],
    user_message: str,
    channel: str = "web",
    feishu_chat_id: Optional[str] = None,
    feishu_open_id: Optional[str] = None,
):
    from backend.services.assistant_conversation_service import (
        append_message,
        resolve_or_create_conversation,
    )

    conv = resolve_or_create_conversation(
        db,
        session_uuid=session_id,
        channel=channel,
        feishu_chat_id=feishu_chat_id,
        feishu_open_id=feishu_open_id,
    )
    append_message(db, conv, role="user", content=user_message)
    return conv


def _persist_assistant_turn(
    db,
    conv,
    *,
    content: str,
    tool_result: Optional[Dict[str, Any]] = None,
) -> None:
    from backend.services.assistant_conversation_service import append_message

    append_message(db, conv, role="assistant", content=content, tool_result=tool_result)


def _process_chat(
    *,
    user_message: str,
    session_id: str,
    page_context: Optional[Dict[str, Any]] = None,
    channel: str = "web",
    feishu_chat_id: Optional[str] = None,
    feishu_open_id: Optional[str] = None,
    db=None,
) -> Dict[str, Any]:
    """核心对话逻辑（SSE / 飞书共用）。"""
    from backend.database.connection import SessionLocal

    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        conv = _persist_user_turn(
            db,
            session_id=session_id,
            user_message=user_message,
            channel=channel,
            feishu_chat_id=feishu_chat_id,
            feishu_open_id=feishu_open_id,
        )
        sid = conv.session_uuid
        history = _SESSIONS.setdefault(sid, [])
        history.append({"role": "user", "content": user_message})
        ctx = _build_l1_context(page_context=page_context)
        pending = _SESSION_PENDING.get(sid)

        from backend.services.alpha_assistant_tools import try_l2_action

        l2_reply, new_pending, tool_result = try_l2_action(
            user_message,
            session_id=sid,
            pending=pending,
            confirm=_is_confirm(user_message),
        )
        if new_pending is not None:
            _SESSION_PENDING[sid] = new_pending
        elif pending and (_is_confirm(user_message) or _is_live_confirm(user_message)):
            _SESSION_PENDING.pop(sid, None)

        if l2_reply:
            if tool_result:
                ctx["tool_results"] = tool_result
            history.append({"role": "assistant", "content": l2_reply})
            _persist_assistant_turn(db, conv, content=l2_reply, tool_result=tool_result)
            db.commit()
            db.refresh(conv)
            return {
                "session_id": sid,
                "conversation_id": conv.id,
                "title": conv.title,
                "content": l2_reply,
                "tool_result": tool_result,
                "deep_links": (tool_result or {}).get("deep_links"),
            }

        local = _maybe_answer_locally(user_message, ctx)
        if local:
            history.append({"role": "assistant", "content": local})
            _persist_assistant_turn(db, conv, content=local)
            db.commit()
            db.refresh(conv)
            return {
                "session_id": sid,
                "conversation_id": conv.id,
                "title": conv.title,
                "content": local,
                "tool_result": None,
                "deep_links": None,
            }

        system_prompt = _render_system_prompt(ctx)
        raw, err = None, None
        try:
            from backend.config.settings import OPENCODE_AGENT_PLAN
            from backend.services.llm_config_service import get_default_model_slug
            from backend.services.opencode_sidecar import ensure_sidecar
            from backend.services.opencode_bridge import run_http_agent_message

            ensure_sidecar()
            model_slug = get_default_model_slug(tier="deep", usage="assistant") or "deepseek/deepseek-v4-flash"
            # 与 OpenCode 智能分析一致：plan agent + 星标默认配置 deep 档
            raw, err = run_http_agent_message(
                system_prompt=system_prompt,
                user_text=user_message,
                agent=(OPENCODE_AGENT_PLAN or "plan").strip(),
                model_slug=model_slug.strip(),
                session_title="Alpha Assistant",
            )
            try:
                from backend.services.prompt_trace_service import append_prompt_trace

                append_prompt_trace(
                    task_id="task_assistant_chat",
                    consumer="alpha_assistant.api",
                    ok=not bool(err),
                    error=err,
                )
            except Exception:
                pass
        except Exception as exc:
            logger.warning("[AlphaAssistant] sidecar call failed: %s", exc)
            err = str(exc)

        if err or not raw:
            local_retry = _maybe_answer_locally(user_message, ctx)
            if local_retry:
                history.append({"role": "assistant", "content": local_retry})
                _persist_assistant_turn(db, conv, content=local_retry)
                db.commit()
                db.refresh(conv)
                return {
                    "session_id": sid,
                    "conversation_id": conv.id,
                    "title": conv.title,
                    "content": local_retry,
                    "tool_result": None,
                    "deep_links": None,
                }
            fallback = (
                "暂时无法连接 AI Sidecar。"
                f"\n\n本地摘要：24h ERROR **{int((ctx.get('log_error_digest') or {}).get('total_errors') or 0)}** 条。"
                "\n\nSidecar 应在后端启动时自动拉起（约 15–30 秒）。"
                "\n\n可尝试：OpenCode 智能中心 → 快捷控制台 → **启动 Sidecar**，"
                "或稍等 2 分钟后重试（看门狗会自动重启）。"
            )
            history.append({"role": "assistant", "content": fallback})
            _persist_assistant_turn(db, conv, content=fallback)
            db.commit()
            db.refresh(conv)
            return {
                "session_id": sid,
                "conversation_id": conv.id,
                "title": conv.title,
                "content": fallback,
                "tool_result": None,
                "deep_links": None,
            }

        history.append({"role": "assistant", "content": raw})
        _persist_assistant_turn(db, conv, content=raw)
        db.commit()
        db.refresh(conv)
        return {
            "session_id": sid,
            "conversation_id": conv.id,
            "title": conv.title,
            "content": raw,
            "tool_result": None,
            "deep_links": None,
        }
    finally:
        if own_db:
            db.close()


def chat_sync(
    *,
    user_message: str,
    session_id: Optional[str] = None,
    page_context: Optional[Dict[str, Any]] = None,
    channel: str = "web",
    feishu_chat_id: Optional[str] = None,
    feishu_open_id: Optional[str] = None,
) -> Dict[str, Any]:
    sid = session_id or str(uuid.uuid4())
    return _process_chat(
        user_message=user_message,
        session_id=sid,
        page_context=page_context,
        channel=channel,
        feishu_chat_id=feishu_chat_id,
        feishu_open_id=feishu_open_id,
    )


def _stream_text_deltas(text: str, *, chunk_size: int = 16) -> Iterable[str]:
    """将完整回复切成小块 SSE 输出，本地/L2 路径也能流式展示。"""
    if not text:
        return
    for i in range(0, len(text), chunk_size):
        yield text[i : i + chunk_size]


def _sse_keepalive() -> str:
    """防止代理/缓冲把长时间无输出的 SSE 连接掐掉。"""
    return ": keepalive\n\n"


def chat_stream(
    *,
    user_message: str,
    session_id: Optional[str] = None,
    page_context: Optional[Dict[str, Any]] = None,
) -> Iterable[str]:
    from backend.database.connection import SessionLocal

    sid = session_id or str(uuid.uuid4())
    yield _sse("status", {"phase": "preparing", "message": "已收到问题，正在准备…", "session_id": sid})

    db = SessionLocal()
    try:
        conv = _persist_user_turn(
            db,
            session_id=sid,
            user_message=user_message,
            channel="web",
        )
        sid = conv.session_uuid
        yield _sse("status", {"phase": "preparing", "message": "正在汇总系统日志与健康状态…"})

        history = _SESSIONS.setdefault(sid, [])
        history.append({"role": "user", "content": user_message})
        ctx = _build_l1_context(page_context=page_context)
        pending = _SESSION_PENDING.get(sid)

        from backend.services.alpha_assistant_tools import try_l2_action

        l2_reply, new_pending, tool_result = try_l2_action(
            user_message,
            session_id=sid,
            pending=pending,
            confirm=_is_confirm(user_message),
        )
        if new_pending is not None:
            _SESSION_PENDING[sid] = new_pending
        elif pending and (_is_confirm(user_message) or _is_live_confirm(user_message)):
            _SESSION_PENDING.pop(sid, None)

        def _finish(content: str, *, deep_links=None) -> Iterable[str]:
            history.append({"role": "assistant", "content": content})
            _persist_assistant_turn(db, conv, content=content, tool_result=tool_result if l2_reply else None)
            db.commit()
            db.refresh(conv)
            yield _sse(
                "done",
                {
                    "session_id": sid,
                    "conversation_id": conv.id,
                    "title": conv.title,
                    "deep_links": deep_links,
                },
            )

        if l2_reply:
            yield _sse("status", {"phase": "responding", "message": "正在整理操作结果…"})
            if tool_result:
                ctx["tool_results"] = tool_result
                if tool_result.get("deep_links"):
                    yield _sse("tool_result", tool_result)
            for delta in _stream_text_deltas(l2_reply):
                yield _sse("content", {"delta": delta})
            yield from _finish(l2_reply, deep_links=(tool_result or {}).get("deep_links"))
            return

        local = _maybe_answer_locally(user_message, ctx)
        if local:
            yield _sse("status", {"phase": "responding", "message": "正在生成本地摘要…"})
            for delta in _stream_text_deltas(local):
                yield _sse("content", {"delta": delta})
            yield from _finish(local)
            return

        system_prompt = _render_system_prompt(ctx)
        full_text = ""
        sidecar_err: Optional[str] = None
        try:
            from backend.config.settings import OPENCODE_AGENT_PLAN
            from backend.services.llm_config_service import get_default_model_slug
            from backend.services.opencode_sidecar import ensure_sidecar
            from backend.services.opencode_bridge import iter_http_agent_message_stream

            model_slug = (get_default_model_slug(tier="deep", usage="assistant") or "deepseek/deepseek-v4-flash").strip()
            model_label = model_slug.split("/")[-1] if "/" in model_slug else model_slug
            yield _sse(
                "status",
                {
                    "phase": "thinking",
                    "message": (
                        f"正在连接 OpenCode（{model_label}），深度思考通常需要 1-3 分钟，请耐心等待…"
                    ),
                    "model": model_slug,
                },
            )
            yield _sse_keepalive()

            ensure_sidecar()
            yield _sse("status", {"phase": "thinking", "message": "Sidecar 已连接，模型生成中…"})

            for event in iter_http_agent_message_stream(
                system_prompt=system_prompt,
                user_text=user_message,
                agent=(OPENCODE_AGENT_PLAN or "plan").strip(),
                model_slug=model_slug,
                session_title="Alpha Assistant",
            ):
                kind = event.get("type")
                if kind == "status":
                    payload: Dict[str, Any] = {"phase": event.get("phase") or "thinking"}
                    if event.get("message"):
                        payload["message"] = event["message"]
                    if event.get("elapsed_s") is not None:
                        payload["elapsed_s"] = event["elapsed_s"]
                    yield _sse("status", payload)
                    yield _sse_keepalive()
                elif kind == "content":
                    delta = str(event.get("delta") or "")
                    if delta:
                        full_text += delta
                        yield _sse("content", {"delta": delta})
                elif kind == "complete":
                    full_text = str(event.get("text") or full_text)
                elif kind == "error":
                    sidecar_err = str(event.get("message") or "sidecar error")
                    break

            try:
                from backend.services.prompt_trace_service import append_prompt_trace

                append_prompt_trace(
                    task_id="task_assistant_chat",
                    consumer="alpha_assistant.api",
                    ok=not bool(sidecar_err) and bool(full_text.strip()),
                    error=sidecar_err,
                )
            except Exception:
                pass
        except Exception as exc:
            logger.warning("[AlphaAssistant] sidecar stream failed: %s", exc)
            sidecar_err = str(exc)

        if sidecar_err or not full_text.strip():
            local_retry = _maybe_answer_locally(user_message, ctx)
            if local_retry:
                for delta in _stream_text_deltas(local_retry):
                    yield _sse("content", {"delta": delta})
                yield from _finish(local_retry)
                return
            fallback = (
                "暂时无法连接 AI Sidecar。"
                f"\n\n本地摘要：24h ERROR **{int((ctx.get('log_error_digest') or {}).get('total_errors') or 0)}** 条。"
                "\n\nSidecar 应在后端启动时自动拉起（约 15–30 秒）。"
                "\n\n可尝试：OpenCode 智能中心 → 快捷控制台 → **启动 Sidecar**，"
                "或稍等 2 分钟后重试（看门狗会自动重启）。"
            )
            for delta in _stream_text_deltas(fallback):
                yield _sse("content", {"delta": delta})
            yield from _finish(fallback)
            return

        yield from _finish(full_text.strip())
    finally:
        db.close()
