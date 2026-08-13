"""OpenCode Bridge — 通过 Sidecar HTTP Session 调用 plan agent（DeepSeek 在 opencode.json 内配置）。"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Generator, Iterable, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

REPORT_DIR = os.path.join("data", "opencode_reports")
SYSTEM_PROMPT_PATH = os.path.join("backend", "prompts", "opencode_analysis_system.md")
REVIEW_SYSTEM_PROMPT_PATH = os.path.join("backend", "prompts", "opencode_proposal_review_system.md")
_last_error: Optional[str] = None
_last_ok_ts: float = 0.0


def get_bridge_status() -> Dict[str, Any]:
    return {
        "enabled": _is_enabled(),
        "transport": "http",
        "last_error": _last_error,
        "last_ok_ts": _last_ok_ts,
        "server_url": _server_url(),
        "model": _model(),
        "sidecar_healthy": health_check(),
    }


def _is_enabled() -> bool:
    try:
        from backend.config.settings import OPENCODE_ENABLED
        return bool(OPENCODE_ENABLED)
    except Exception:
        return False


def _server_url() -> str:
    try:
        from backend.config.settings import OPENCODE_SERVER_URL
        return OPENCODE_SERVER_URL.rstrip("/")
    except Exception:
        return "http://127.0.0.1:4096"


def _timeout() -> int:
    try:
        from backend.config.settings import OPENCODE_REQUEST_TIMEOUT_S
        return int(OPENCODE_REQUEST_TIMEOUT_S)
    except Exception:
        # 仅用于 HTTP 连接/Session 创建等快速操作；模型推理走流式 SSE，不受此限制
        return 180


def _multi_round_timeout_s() -> int:
    try:
        from backend.config.settings import OPENCODE_MULTI_ROUND_TIMEOUT_S
        return int(OPENCODE_MULTI_ROUND_TIMEOUT_S or 120)
    except Exception:
        return 120


def _model() -> str:
    try:
        from backend.services.llm_config_service import get_default_model_slug
        slug = get_default_model_slug(tier="deep")
        if slug:
            return slug
    except Exception as exc:
        logger.debug("[OpenCodeBridge] 默认模型通道读取失败: %s", exc)
    try:
        from backend.config.settings import OPENCODE_MODEL
        return OPENCODE_MODEL or "deepseek/deepseek-v4-flash"
    except Exception:
        return "deepseek/deepseek-v4-flash"


def _agent_plan() -> str:
    try:
        from backend.config.settings import OPENCODE_AGENT_PLAN
        return OPENCODE_AGENT_PLAN or "plan"
    except Exception:
        return "plan"


def _parse_model_slug(slug: str) -> Tuple[str, str]:
    """``deepseek/deepseek-v4-flash`` → (providerID, modelID)."""
    slug = (slug or "").strip()
    if "/" in slug:
        provider, model_id = slug.split("/", 1)
        return provider.strip(), model_id.strip()
    return "deepseek", slug


def _should_stream_agent_message(model_slug: str, user_text: str, session_title: str = "") -> bool:
    """OpenCode sidecar 深度/长文任务默认走 prompt_async + /event 流式。"""
    if os.getenv("LLM_DISABLE_STREAMING", "false").lower() in ("1", "true", "yes", "on"):
        return False
    try:
        _provider, model_id = _parse_model_slug(model_slug)
        from backend.services.llm_config_service import is_reasoning_model
        if is_reasoning_model(model_id):
            return True
    except Exception:
        model_id = model_slug
    text_len = len(user_text or "")
    try:
        long_prompt_chars = int(os.getenv("LLM_STREAM_PROMPT_CHARS", "6000"))
    except Exception:
        long_prompt_chars = 6000
    if text_len >= long_prompt_chars:
        return True
    title = (session_title or "").lower()
    return any(
        marker in title
        for marker in (
            "assistant", "opencode", "audit", "analysis", "review", "multiround",
            "hermes", "evolution", "genesis", "report", "deep", "loss",
        )
    )


def health_check() -> bool:
    global _last_error, _last_ok_ts
    url = _server_url()
    try:
        # trust_env=False：sidecar 恒为本地 127.0.0.1，禁止走系统/Privoxy 代理
        # （否则本地请求被代理劫持，返回 500 导致误判 sidecar 离线）
        with httpx.Client(timeout=5.0, trust_env=False) as client:
            r = client.get(f"{url}/global/health")
            if r.status_code < 500:
                data = r.json() if r.content else {}
                if data.get("healthy") is False:
                    _last_error = "sidecar unhealthy"
                    return False
                _last_ok_ts = time.time()
                _last_error = None
                return True
    except Exception as err:
        _last_error = str(err)
    return False


def _extract_first_json_object(text: str) -> Optional[Dict[str, Any]]:
    r"""扫描出文本中第一个完整且可解析的 JSON 对象。

    解决贪心正则 `\{[\s\S]*\}` 的退化：当 LLM 在正式 JSON 之前先输出一段
    思维链/推理（其中夹杂 `{...}` 片段）时，贪心正则会从首个 `{` 一路贪到
    末尾 `}`，跨越非 JSON 文本导致 json.loads 失败，进而让 L3 架构进化、
    L4 策略创生等引擎「拿到 None 产出却谎报 status=ok」。

    本函数从首个 `{` 开始，按括号深度（并正确处理字符串字面量与转义）找到
    其配平的 `}`；若这段切片可解析为对象则返回，否则从该 `{` 之后继续找下一
    个 `{` 重试。这样：
      - 思维链里的 `{"reasoning":"x"}`（在正式 JSON 之前）会被解析，但若其后
        还有更大、更完整的正式对象，调用方（如下方 `_extract_json`）会优先取
        最后一个成功的；
      - 正式 JSON 后的尾随文本/垃圾括号不影响（对象已配平提前结束）。
    """
    if not text:
        return None
    n = len(text)
    start = 0
    results = []
    while start < n:
        brace = text.find("{", start)
        if brace < 0:
            break
        depth = 0
        in_str = False
        esc = False
        end = -1
        i = brace
        while i < n:
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            i += 1
        if end < 0:
            # 到结尾仍未配平：剩余文本无完整对象
            break
        candidate = text[brace : end + 1]
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                results.append((brace, obj))
        except json.JSONDecodeError:
            pass
        start = end + 1
    # 取最靠后的可解析对象：LLM 的最终答案几乎总在思维链之后，
    # 优先靠右可跳过思考过程里的伪 JSON 片段。
    return results[-1][1] if results else None


def _extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # 兼容老的 `{...}` 抽取语义，但改用平衡括号扫描器，正确跳过 LLM 思维链
    # 中夹杂的伪 JSON 片段，返回第一个真正可解析的对象。
    obj = _extract_first_json_object(text)
    if obj is not None:
        return obj
    return {"severity": "info", "findings": [{"message": text[:2000]}], "raw": text}


def _load_system_prompt(path: str = SYSTEM_PROMPT_PATH, fallback: str = "") -> str:
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    if fallback:
        return fallback
    return (
        "You are Alpha Arena read-only analyst. Return ONLY JSON with keys: "
        "severity, domain, findings, actions, patches."
    )


def run_http_agent_message(
    *,
    system_prompt: str,
    user_text: str,
    agent: str,
    model_slug: str,
    session_title: str = "Alpha Arena agent",
    timeout_s: Optional[float] = None,
    allow_stream: bool = True,
) -> Tuple[Optional[str], Optional[str]]:
    """Sidecar HTTP Session 单次对话；返回 (assistant_text, error)。"""
    global _last_error, _last_ok_ts

    if not health_check():
        err = _last_error or "sidecar unavailable"
        return None, err

    if allow_stream and _should_stream_agent_message(model_slug, user_text, session_title):
        return collect_http_agent_stream_text(
            system_prompt=system_prompt,
            user_text=user_text,
            agent=agent,
            model_slug=model_slug,
            session_title=session_title,
            idle_timeout_s=max(float(timeout_s or _timeout()), 300.0),
            max_duration_s=max(float(timeout_s or _timeout()) * 4, 900.0),
            log_prefix=f"OpenCode:{session_title}",
        )

    provider_id, model_id = _parse_model_slug(model_slug)
    base = _server_url()
    timeout = float(timeout_s if timeout_s is not None else _timeout())
    session_body = {
        "agent": agent,
        "title": session_title,
        "model": {"providerID": provider_id, "id": model_id},
    }
    message_body = {
        "agent": agent,
        "model": {"providerID": provider_id, "modelID": model_id},
        "system": system_prompt,
        "tools": {"write": True, "edit": True, "bash": True},
        "parts": [{"type": "text", "text": user_text}],
    }

    # trust_env=False：sidecar 恒为本地，禁止走系统/Privoxy 代理（见 health_check 注释）
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        sess_resp = client.post(f"{base}/session", json=session_body)
        if sess_resp.status_code >= 400:
            _last_error = f"session create {sess_resp.status_code}: {sess_resp.text[:500]}"
            return None, _last_error

        session_id = (sess_resp.json() or {}).get("id")
        if not session_id:
            _last_error = "session create: missing id"
            return None, _last_error

        msg_resp = client.post(f"{base}/session/{session_id}/message", json=message_body)
        if msg_resp.status_code >= 400:
            _last_error = f"message {msg_resp.status_code}: {msg_resp.text[:500]}"
            return None, _last_error

        payload = msg_resp.json() or {}
        raw_text = _collect_assistant_text(payload)
        if not raw_text.strip():
            _last_error = "empty assistant response"
            return None, _last_error

        _last_ok_ts = time.time()
        _last_error = None
        return raw_text, None


def iter_http_agent_message_stream(
    *,
    system_prompt: str,
    user_text: str,
    agent: str,
    model_slug: str,
    session_title: str = "Alpha Arena agent",
    idle_timeout_s: float = 300.0,
    max_duration_s: float = 3600.0,
) -> Generator[Dict[str, Any], None, None]:
    """Sidecar 流式对话：订阅 /event，prompt_async 后逐 token 产出 delta。

    超时策略：最后收到 token/心跳后 idle_timeout_s 内无新数据才判定超时，
    而非固定总耗时（适合 DeepSeek 等长推理模型）。
    """
    global _last_error, _last_ok_ts

    if not health_check():
        yield {"type": "error", "message": _last_error or "sidecar unavailable"}
        return

    provider_id, model_id = _parse_model_slug(model_slug)
    base = _server_url()
    connect_timeout = float(_timeout())
    session_body = {
        "agent": agent,
        "title": session_title,
        "model": {"providerID": provider_id, "id": model_id},
    }
    message_body = {
        "agent": agent,
        "model": {"providerID": provider_id, "modelID": model_id},
        "system": system_prompt,
        "tools": {"write": True, "edit": True, "bash": True},
        "parts": [{"type": "text", "text": user_text}],
    }

    event_q: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
    stop_listener = threading.Event()

    def _listen_events() -> None:
        try:
            with httpx.Client(timeout=None, trust_env=False) as client:
                with client.stream("GET", f"{base}/event") as resp:
                    for line in resp.iter_lines():
                        if stop_listener.is_set():
                            break
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw:
                            continue
                        try:
                            event_q.put(json.loads(raw))
                        except json.JSONDecodeError:
                            continue
        except Exception as err:
            event_q.put({"type": "_listener_error", "message": str(err)})
        finally:
            event_q.put(None)

    listener = threading.Thread(target=_listen_events, daemon=True)
    listener.start()
    time.sleep(0.25)

    def _ensure_listener() -> None:
        nonlocal listener
        if listener.is_alive():
            return
        stop_listener.clear()
        listener = threading.Thread(target=_listen_events, daemon=True)
        listener.start()
        time.sleep(0.25)

    oc_session_id: Optional[str] = None
    text_parts: List[str] = []
    completed = False
    listener_err: Optional[str] = None

    try:
        with httpx.Client(timeout=connect_timeout, trust_env=False) as client:
            sess_resp = client.post(f"{base}/session", json=session_body)
            if sess_resp.status_code >= 400:
                _last_error = f"session create {sess_resp.status_code}: {sess_resp.text[:500]}"
                yield {"type": "error", "message": _last_error}
                return

            oc_session_id = (sess_resp.json() or {}).get("id")
            if not oc_session_id:
                _last_error = "session create: missing id"
                yield {"type": "error", "message": _last_error}
                return

            prompt_resp = client.post(
                f"{base}/session/{oc_session_id}/prompt_async",
                json=message_body,
            )
            if prompt_resp.status_code >= 400:
                _last_error = f"prompt_async {prompt_resp.status_code}: {prompt_resp.text[:500]}"
                yield {"type": "error", "message": _last_error}
                return

            start = time.time()
            last_activity = time.time()
            wait_start = time.time()
            last_heartbeat = 0.0
            while time.time() - start < max_duration_s:
                if time.time() - last_activity > idle_timeout_s:
                    _last_error = f"stream idle timeout after {idle_timeout_s}s"
                    yield {"type": "error", "message": _last_error}
                    return
                try:
                    ev = event_q.get(timeout=1.0)
                except queue.Empty:
                    if not text_parts:
                        elapsed = int(time.time() - wait_start)
                        if elapsed >= 3 and elapsed - last_heartbeat >= 5:
                            last_heartbeat = elapsed
                            last_activity = time.time()
                            yield {
                                "type": "status",
                                "phase": "thinking",
                                "message": f"模型深度思考中，已等待 {elapsed} 秒…",
                                "elapsed_s": elapsed,
                            }
                    continue
                if ev is None:
                    # SSE 监听线程结束不等于模型完成；尝试重连 /event 并继续等待
                    if completed:
                        break
                    _ensure_listener()
                    continue
                if ev.get("type") == "_listener_error":
                    listener_err = str(ev.get("message") or "event stream failed")
                    break

                last_activity = time.time()
                props = ev.get("properties") or {}
                if props.get("sessionID") != oc_session_id:
                    continue

                ev_type = ev.get("type") or ""

                # 优先探测 API 错误：message.updated 的 info.error 携带上游失败
                # （如 deepseek 402 Insufficient Balance）。若不在这里捕获，下面会
                # 误把「用户消息 part」当成 assistant 内容回显，导致整条链路静默 0 产出。
                if ev_type == "message.updated":
                    info = props.get("info") or {}
                    api_err = info.get("error")
                    if api_err:
                        data = api_err.get("data") if isinstance(api_err, dict) else None
                        msg = (data.get("message") if isinstance(data, dict) else None) or str(api_err)
                        _last_error = f"模型 API 错误: {msg}"
                        yield {"type": "error", "message": _last_error}
                        return
                    if info.get("role") == "assistant" and info.get("time", {}).get("completed"):
                        completed = True
                        break
                    continue

                # 用户消息的 part 事件会先到达; 只在确认不是用户输入回显时才采集文本,
                # 避免把用户输入误当 assistant 输出回显.
                if ev_type == "message.part.updated":
                    part = props.get("part") or {}
                    if part.get("type") == "text" and part.get("text"):
                        merged = str(part["text"])
                        already = "".join(text_parts)
                        is_user_echo = merged.strip() == (user_text or "").strip()
                        if is_user_echo and not text_parts:
                            continue
                        if merged and merged != already:
                            remainder = merged[len(already):]
                            if remainder:
                                text_parts.append(remainder)
                                yield {"type": "content", "delta": remainder}
                    continue

                if ev_type == "message.part.delta":
                    field = props.get("field") or "text"
                    delta = str(props.get("delta") or "")
                    if not delta:
                        continue
                    if field == "reasoning":
                        yield {
                            "type": "status",
                            "phase": "thinking",
                            "message": "模型正在推理分析…",
                            "delta": delta,
                        }
                    elif field == "text":
                        text_parts.append(delta)
                        yield {"type": "content", "delta": delta}
                    continue

            if listener_err and not text_parts:
                yield {"type": "error", "message": listener_err}
                return

            if not completed and time.time() - start >= max_duration_s:
                _last_error = f"stream max duration {max_duration_s}s exceeded"
                yield {"type": "error", "message": _last_error}
                return

            full_text = "".join(text_parts).strip()
            if not full_text and not completed:
                raw_text, err = run_http_agent_message(
                    system_prompt=system_prompt,
                    user_text=user_text,
                    agent=agent,
                    model_slug=model_slug,
                    session_title=session_title,
                    timeout_s=max(connect_timeout, 3600.0),
                    allow_stream=False,
                )
                if err or not raw_text:
                    yield {"type": "error", "message": err or "empty assistant response"}
                    return
                for i in range(0, len(raw_text), 24):
                    yield {"type": "content", "delta": raw_text[i : i + 24]}
                full_text = raw_text

            if not full_text.strip():
                _last_error = "empty assistant response"
                yield {"type": "error", "message": _last_error}
                return

            _last_ok_ts = time.time()
            _last_error = None
            yield {"type": "complete", "text": full_text}
    finally:
        stop_listener.set()


def collect_http_agent_stream_text(
    *,
    system_prompt: str,
    user_text: str,
    agent: str,
    model_slug: str,
    session_title: str = "Alpha Arena agent",
    idle_timeout_s: float = 300.0,
    max_duration_s: float = 3600.0,
    log_prefix: str = "Hermes",
) -> Tuple[Optional[str], Optional[str]]:
    """流式 Sidecar 对话并收集完整回复；按 token 空闲超时，非固定总耗时。"""
    global _last_error, _last_ok_ts

    text_parts: List[str] = []
    stream_err: Optional[str] = None

    try:
        for ev in iter_http_agent_message_stream(
            system_prompt=system_prompt,
            user_text=user_text,
            agent=agent,
            model_slug=model_slug,
            session_title=session_title,
            idle_timeout_s=idle_timeout_s,
            max_duration_s=max_duration_s,
        ):
            ev_type = ev.get("type") or ""
            if ev_type == "content":
                delta = str(ev.get("delta") or "")
                if delta:
                    text_parts.append(delta)
            elif ev_type == "complete":
                final = str(ev.get("text") or "")
                if final:
                    if text_parts:
                        merged = "".join(text_parts)
                        if final != merged:
                            text_parts = [final]
                    else:
                        text_parts = [final]
                break
            elif ev_type == "error":
                stream_err = str(ev.get("message") or "stream error")
                break
            elif ev_type == "status":
                msg = str(ev.get("message") or "")
                if msg:
                    logger.info("[%s] %s", log_prefix, msg)
    except Exception as exc:
        stream_err = str(exc)

    raw = "".join(text_parts).strip()
    if stream_err and not raw:
        _last_error = stream_err
        return None, stream_err
    if not raw:
        err = stream_err or "empty assistant response"
        _last_error = err
        return None, err

    _last_ok_ts = time.time()
    _last_error = None
    return raw, None


def _load_analysis_system_prompt(context_pack: Optional[Dict[str, Any]] = None) -> str:
    pack = context_pack or {}
    try:
        from backend.services.prompt_registry import get_prompt_registry

        return get_prompt_registry().render_task(
            "task_trading_runtime_analysis",
            {
                "window": pack.get("window", "24h"),
                "domain": pack.get("domain", "ai"),
                "runtime_report": pack.get("runtime_report") or {},
                "data_quality": pack.get("data_quality") or {},
                "log_error_digest": pack.get("log_error_digest") or {},
                "whitelist_keys": pack.get("whitelist_keys") or [],
                "whitelist_policy_note": pack.get("whitelist_policy_note") or "",
                "tuning_baseline": pack.get("tuning_baseline") or {},
            },
            consumer="opencode_analysis",
        )
    except Exception as err:
        logger.debug("[OpenCodeBridge] analysis prompt registry fallback: %s", err)
    return _load_system_prompt(SYSTEM_PROMPT_PATH)


def load_review_system_prompt(context_pack: Optional[Dict[str, Any]] = None) -> str:
    pack = context_pack or {}
    try:
        from backend.services.prompt_registry import get_prompt_registry

        return get_prompt_registry().render_task(
            "task_proposal_review",
            {
                "context_pack": pack,
                "proposal_patches": pack.get("proposal_patches") or [],
                "baseline_perf": pack.get("baseline_perf") or {},
            },
            consumer="opencode_review",
        )
    except Exception as err:
        logger.debug("[OpenCodeBridge] review prompt registry fallback: %s", err)
    return _load_system_prompt(
        REVIEW_SYSTEM_PROMPT_PATH,
        fallback=(
            "You are Alpha Arena proposal reviewer. Return ONLY JSON: "
            "decision, confidence, approved_patches, reasons, risks."
        ),
    )


def _collect_assistant_text(message_payload: Dict[str, Any]) -> str:
    parts = message_payload.get("parts") or []
    chunks: List[str] = []
    for part in parts:
        if part.get("type") == "text" and part.get("text"):
            chunks.append(str(part["text"]))
    if chunks:
        return "\n".join(chunks).strip()
    return json.dumps(message_payload, ensure_ascii=False)


def _save_analysis_report(raw_text: str, result: Dict[str, Any]) -> Dict[str, str]:
    os.makedirs(REPORT_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    md_path = os.path.join(REPORT_DIR, f"analysis_{ts}.md")
    json_path = os.path.join(REPORT_DIR, f"analysis_{ts}.json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(raw_text)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return {"markdown": md_path, "json": json_path}


def _run_http_analysis(
    context_path: str,
    *,
    prompt_extra: str = "",
) -> Dict[str, Any]:
    """调用本机 OpenCode Sidecar；DeepSeek Key/模型由 opencode.json + Sidecar 环境提供。

    使用流式 SSE 路径（iter_http_agent_message_stream），模型持续产出 token 就不会超时。
    超时控制改为"最后收到 token 后 _STREAM_IDLE_TIMEOUT_S 秒内无新数据"，而非固定总耗时。
    """
    global _last_error

    with open(context_path, encoding="utf-8") as f:
        context_text = f.read()

    try:
        context_pack = json.loads(context_text)
    except json.JSONDecodeError:
        context_pack = {}

    instruction = (
        "Analyze the context JSON below per system instructions. "
        "Return ONLY valid JSON (no markdown fences)."
    )
    if prompt_extra:
        instruction += " " + prompt_extra
    user_text = f"{instruction}\n\n--- CONTEXT JSON ---\n{context_text}"

    # ── 流式路径：逐 token 收集，无固定总耗时上限 ──
    _STREAM_IDLE_TIMEOUT_S = 300  # 最后收到 token 后 300s 内无新数据才判定超时
    text_parts: List[str] = []
    last_token_ts = time.time()
    stream_err: Optional[str] = None

    try:
        for ev in iter_http_agent_message_stream(
            system_prompt=_load_analysis_system_prompt(context_pack),
            user_text=user_text,
            agent=_agent_plan(),
            model_slug=_model(),
            session_title="Alpha Arena analysis",
        ):
            ev_type = ev.get("type") or ""
            if ev_type == "content":
                delta = str(ev.get("delta") or "")
                if delta:
                    text_parts.append(delta)
                    last_token_ts = time.time()
            elif ev_type == "complete":
                text_parts.append(str(ev.get("text") or ""))
                break
            elif ev_type == "error":
                stream_err = str(ev.get("message") or "stream error")
                break
            elif ev_type == "status":
                # 心跳：刷新 idle 计时器，防止长推理被误判超时
                last_token_ts = time.time()

            # 空闲超时检测（仅在没有 error/complete 时生效）
            if time.time() - last_token_ts > _STREAM_IDLE_TIMEOUT_S:
                stream_err = f"stream idle timeout after {_STREAM_IDLE_TIMEOUT_S}s"
                logger.warning("[OpenCodeBridge] 流式空闲超时: %.0fs 无新 token", _STREAM_IDLE_TIMEOUT_S)
                break
    except Exception as exc:
        stream_err = str(exc)

    raw_text = "".join(text_parts).strip()

    # 流式失败 → 降级尝试同步（兼容旧 Sidecar 或无 SSE 支持的环境）
    if stream_err and not raw_text:
        logger.warning(
            "[OpenCodeBridge] 流式失败(%s)，降级尝试同步调用", stream_err,
        )
        raw_text, err = run_http_agent_message(
            system_prompt=_load_analysis_system_prompt(context_pack),
            user_text=user_text,
            agent=_agent_plan(),
            model_slug=_model(),
            session_title="Alpha Arena analysis (fallback sync)",
        )
        if err:
            stream_err = err

    if not raw_text:
        try:
            from backend.services.prompt_trace_service import append_prompt_trace
            append_prompt_trace(
                task_id="task_trading_runtime_analysis",
                consumer="opencode_bridge.plan",
                ok=False,
                error=stream_err or "empty response",
            )
        except Exception:
            pass
        return {"severity": "info", "findings": [], "error": stream_err or "empty response"}

    try:
        from backend.services.prompt_trace_service import append_prompt_trace
        append_prompt_trace(
            task_id="task_trading_runtime_analysis",
            consumer="opencode_bridge.plan",
            ok=True,
            extra={"window": context_pack.get("window"), "domain": context_pack.get("domain")},
        )
    except Exception:
        pass

    result = _extract_json(raw_text)
    result["report_paths"] = _save_analysis_report(raw_text, result)
    result["transport"] = "http-stream"
    return result


def run_plan_analysis(
    context_path: str,
    *,
    prompt_extra: str = "",
) -> Dict[str, Any]:
    global _last_error
    if not _is_enabled():
        return {"severity": "info", "findings": [], "skipped": "OPENCODE_ENABLED=false"}

    if not os.path.isfile(context_path):
        _last_error = f"context not found: {context_path}"
        return {"severity": "info", "findings": [], "error": _last_error}

    try:
        return _run_http_analysis(context_path, prompt_extra=prompt_extra)
    except httpx.TimeoutException:
        _last_error = "timeout"
        return {"severity": "info", "findings": [], "error": "timeout"}
    except Exception as err:
        _last_error = str(err)
        logger.error("[OpenCodeBridge] %s", err, exc_info=True)
        return {"severity": "info", "findings": [], "error": str(err)}


def run_scheduled_analysis(db, window: str = "24h", domain: str = "ai") -> Dict[str, Any]:
    from backend.services.opencode_context_pack import build_context_pack, save_context_pack

    pack = build_context_pack(db, window=window, domain=domain)
    path = save_context_pack(pack)
    dq = pack.get("data_quality") or {}
    closed_n = int(dq.get("runtime_report_total_closed") or 0)
    min_n = 5
    try:
        from backend.services.training_phase_service import min_analysis_closed

        min_n = min_analysis_closed()
    except Exception:
        pass
    if not dq.get("sufficient_for_analysis"):
        msg = (
            f"数据不足：{window} 已平仓 {closed_n} 笔（需要 >={min_n}），"
            f"跳过 OpenCode 分析，不写入洞察/提案"
        )
        logger.warning("[OpenCodeBridge] %s context=%s", msg, path)
        return {
            "severity": "info",
            "findings": [],
            "skipped": "insufficient_trade_data",
            "message": msg,
            "context_path": path,
            "data_quality": dq,
        }

    logger.info(
        "[OpenCodeBridge] ✅ 数据充足（window=%s domain=%s tc=%d），"
        "开始调用 LLM 分析… context=%s",
        window, domain, closed_n, path,
    )
    result = run_plan_analysis(path, prompt_extra=f"window={window} domain={domain}")
    result["context_path"] = path
    result["data_quality"] = dq

    # P0-3: 明确记录分析结果
    error = result.get("error")
    if error:
        logger.error("[OpenCodeBridge] ❌ LLM 分析失败( %s/%s ): %s", window, domain, error)
        # 尝试降级：构建仅统计摘要的 result，供洞察系统消费
        try:
            from backend.services.strategy_runtime_report import generate_report
            srr = generate_report(db, window=window, domain=domain)
            tc = int(srr.total_closed or 0)
            avg_pnl = (float(srr.total_pnl or 0) / max(tc, 1))
            result["stats_fallback"] = {
                "total_closed": tc,
                "win_rate": float(srr.win_rate or 0),
                "total_pnl": float(srr.total_pnl or 0),
                "avg_pnl_per_trade": round(avg_pnl, 2),
                "note": "LLM timeout/degraded — stats only, no AI insights",
            }
        except Exception:
            pass
    else:
        sev = result.get("severity", "info")
        findings_n = len(result.get("findings") or [])
        patches_n = len(result.get("patches") or [])
        logger.info(
            "[OpenCodeBridge] ✅ LLM 分析完成( %s/%s ): severity=%s findings=%d patches=%d",
            window, domain, sev, findings_n, patches_n,
        )

    from backend.services.opencode_action_router import route_analysis_result
    route_analysis_result(db, result, window=window, domain=domain)
    return result


# ══════════════════════════════════════════════════════
#  Phase 6: 周期增强层 — 策略深度诊断 + 决策质量审计
# ══════════════════════════════════════════════════════

def run_strategy_deep_dive(db, strategy_id: str) -> Dict[str, Any]:
    """
    策略级深度诊断：满足任一条件即触发 LLM 诊断：
    1. ≥10笔 且 胜率<45%（传统筛选）
    2. ≥10笔 且 每笔期望收益 < 0（负期望值，胜率高也可能亏钱）
    3. ≥10笔 且 盈亏比 < 1.5（均亏接近均盈，风险调整差）
    输出：策略失败根因 + 是否需要暂停/调整参数/更换symbol。
    """
    if not _is_enabled():
        return {"severity": "info", "findings": [], "skipped": "OPENCODE_ENABLED=false"}

    from backend.services.strategy_runtime_report import get_or_build_runtime_report

    try:
        # 取该策略的运行时报告
        srr = get_or_build_runtime_report(
            db, window="24h", domain="ai", force_refresh=False,
        )
        strategies_detail = srr.get("strategies_detail") or []
        target = None
        for s in strategies_detail:
            if s.get("strategy_id") == strategy_id:
                target = s
                break

        if not target:
            return {"severity": "info", "findings": [], "skipped": f"strategy {strategy_id} not found in SRR"}

        total_trades = int(target.get("total_trades") or 0)
        win_rate = float(target.get("win_rate") or 0)
        total_pnl = float(target.get("total_pnl") or 0)
        avg_pnl = total_pnl / max(total_trades, 1)
        avg_win_pct = float(target.get("avg_win_pct") or 0)
        avg_loss_pct = float(target.get("avg_loss_pct") or 0)
        profit_factor = abs(avg_win_pct / avg_loss_pct) if avg_loss_pct != 0 else 0

        if total_trades < 10:
            return {
                "severity": "info", "findings": [],
                "skipped": f"trades={total_trades} — 不足诊断最低样本量",
            }

        # 多维度触发条件
        triggers = []
        if win_rate < 0.45:
            triggers.append(f"低胜率({win_rate:.1%})")
        if avg_pnl < 0:
            triggers.append(f"负期望值(${avg_pnl:+.2f}/笔)")
        if 0 < profit_factor < 1.5 and total_trades >= 10:
            triggers.append(f"低盈亏比({profit_factor:.1f})")

        if not triggers:
            return {
                "severity": "info", "findings": [],
                "skipped": (
                    f"trades={total_trades} win_rate={win_rate:.1%} "
                    f"avg_pnl=${avg_pnl:+.2f} pf={profit_factor:.1f} — 无需诊断"
                ),
            }

        logger.info(
            "[OpenCodeBridge] 策略深度诊断触发 %s: %s",
            strategy_id, ", ".join(triggers),
        )

        system = _load_system_prompt()
        user_text = (
            f"## 策略深度诊断: {strategy_id}\n\n"
            f"- 24h交易数: {total_trades}\n"
            f"- 胜率: {win_rate:.1%}\n"
            f"- 总PnL: ${total_pnl:+.2f}\n"
            f"- 每笔期望收益: ${avg_pnl:+.2f}\n"
            f"- 均盈: {avg_win_pct*100:.2f}% | 均亏: {avg_loss_pct*100:.2f}%\n"
            f"- 盈亏比: {profit_factor:.1f}\n"
            f"- 触发原因: {', '.join(triggers)}\n"
            f"- 亏损原因分布: {json.dumps(target.get('loss_reasons') or {}, ensure_ascii=False)}\n"
            f"- Symbol分布: {json.dumps(target.get('symbol_perf') or {}, ensure_ascii=False)}\n\n"
            f"请诊断该策略问题的根因。"
            f"注意：胜率仅为参考指标之一。如果胜率正常但每笔期望收益为负，"
            f"说明盈亏比差（大亏小赚），这才是真正的致命问题。"
            f"给出：暂停/调参/换symbol/收紧止损 的建议。"
            f"输出JSON格式: {{\"root_cause\": \"...\", \"action\": \"pause|retune|resymbol|tighten_sl|keep\", \"reason\": \"...\", \"confidence\": 0.0}}"
        )

        raw, err = run_http_agent_message(
            system_prompt=system,
            user_text=user_text,
            agent=_agent_plan(),
            model_slug=_model(),
            session_title=f"Strategy Deep Dive: {strategy_id}",
        )
        if err:
            return {"severity": "info", "findings": [], "error": err}

        result = _extract_json(raw or "")
        logger.info(
            f"[OpenCodeBridge] 策略深度诊断 {strategy_id}: "
            f"action={result.get('action', '?')} confidence={result.get('confidence', 0)}"
        )
        return result
    except Exception as exc:
        logger.error("[OpenCodeBridge] strategy_deep_dive: %s", exc, exc_info=True)
        return {"severity": "info", "findings": [], "error": str(exc)}


# ══════════════════════════════════════════════════════
#  P0.2: 策略代码级深度审计（128K 上下文）
# ══════════════════════════════════════════════════════

STRATEGY_CODE_AUDIT_SYSTEM_PROMPT_PATH = os.path.join(
    "backend", "prompts", "strategy_code_audit_system.md"
)


def run_strategy_code_audit(
    db,
    strategy_id: str,
    *,
    inject_full_context: bool = True,
) -> Dict[str, Any]:
    """P0.2: 策略代码级深度审计 — 注入完整K线+21因子+决策序列到128K上下文。

    与 run_strategy_deep_dive 的区别：
    - deep_dive: 仅统计摘要 → 适合快速诊断
    - code_audit: 全量原始数据注入 → LLM 可发现隐藏的模式/边界/逻辑缺陷

    输入：
    - 策略最近 50 笔交易的完整 K 线序列（1h/4h）
    - 21 因子入场快照（funding_rate/OI/爆仓/稳定币流等）
    - 每次决策的完整 decision_context
    - 策略的当前 Prompt 模板

    输出：
    - 至少 3 条具体的策略改进建议
    - 每条建议包含：问题描述 + 修正方向 + 预期效果 + 实施难度
    - 发现隐藏的市场条件依赖（某些因子在特定市况下信号失效）
    """
    if not _is_enabled():
        return {"severity": "info", "findings": [], "skipped": "OPENCODE_ENABLED=false"}

    from backend.database.models import (
        AIStrategy, StrategyTrade, StrategyMemory,
        AccountPromptBinding,
    )
    from backend.database.connection import SessionLocal

    try:
        # ── 1. 加载策略 ──
        strategy = db.query(AIStrategy).filter(
            AIStrategy.strategy_id == strategy_id
        ).first()
        if not strategy:
            return {"severity": "info", "findings": [], "skipped": f"strategy {strategy_id} not found"}

        # ── 2. 获取最近交易 + 上下文 ──
        trades = (
            db.query(StrategyTrade)
            .filter(
                StrategyTrade.strategy_id == strategy_id,
                StrategyTrade.status == "closed",
            )
            .order_by(StrategyTrade.closed_at.desc())
            .limit(50)
            .all()
        )

        if len(trades) < 10:
            return {
                "severity": "info",
                "findings": [],
                "skipped": f"仅{len(trades)}笔交易，不足以做深度审计",
            }

        # ── 3. 构建交易决策序列（含因子快照） ──
        decision_sequence = []
        for t in trades:
            dc = t.decision_context if isinstance(t.decision_context, dict) else {}
            entry_snap = dc.get("entry_snapshot", {}) or {}
            fingerprint = dc.get("fingerprint_at_entry", {}) or {}

            decision_sequence.append({
                "symbol": t.symbol,
                "side": t.side,
                "entry_price": float(t.entry_price or 0),
                "exit_price": float(t.exit_price or 0),
                "pnl": float(t.pnl or 0),
                "pnl_pct": float(t.pnl_pct or 0),
                "pnl_pct_round": round(float(t.pnl_pct or 0) * 100, 2),
                "duration_min": int((getattr(t, "duration_seconds", 0) or 0) / 60),
                "close_reason": dc.get("close_reason", "?"),
                "confidence": float(t.confidence or 0),
                # 加密因子快照
                "factors": {
                    "funding_rate": fingerprint.get("funding_rate", 0),
                    "oi_change_pct": fingerprint.get("oi_change_pct", 0),
                    "liquidation_imbalance": fingerprint.get("liquidation_imbalance", 0),
                    "stablecoin_flow": fingerprint.get("stablecoin_flow", 0),
                    "volume_ratio": entry_snap.get("volume_ratio", 1.0),
                    "rsi_14": entry_snap.get("rsi_14", 50),
                    "ema_trend": entry_snap.get("ema_trend", "flat"),
                    "regime": fingerprint.get("regime_at_entry", "ranging"),
                    "adx": entry_snap.get("adx", 0),
                    "volatility": fingerprint.get("volatility_30d", 0),
                },
                "trend": {
                    "direction": fingerprint.get("trend_direction", "neutral"),
                    "strength": fingerprint.get("trend_strength", 0),
                },
                "opened_at": str(t.opened_at),
                "closed_at": str(t.closed_at),
            })

        # ── 4. 获取策略当前 Prompt ──
        strategy_prompt = ""
        try:
            bindings = db.query(AccountPromptBinding).filter(
                AccountPromptBinding.strategy_id == strategy_id
            ).order_by(AccountPromptBinding.updated_at.desc()).first()
            if bindings and bindings.prompt_text:
                strategy_prompt = bindings.prompt_text[:3000]
        except Exception:
            pass

        # ── 5. 获取策略记忆 ──
        memory = db.query(StrategyMemory).filter(
            StrategyMemory.strategy_id == strategy_id
        ).first()
        memory_summary = {}
        if memory:
            memory_summary = {
                "win_rate": getattr(memory, "win_rate", 0),
                "total_trades": getattr(memory, "total_trades", 0),
                "sharpe": getattr(memory, "sharpe_ratio", 0),
                "best_regime": getattr(memory, "best_regime", ""),
                "worst_regime": getattr(memory, "worst_regime", ""),
                "discovered_rules": list(getattr(memory, "discovered_rules", []) or [])[-5:],
            }

        # ── 6. 统计摘要 ──
        win_count = sum(1 for t in trades if (t.pnl or 0) > 0)
        total_pnl = sum(float(t.pnl or 0) for t in trades)
        pnl_pcts = [float(t.pnl_pct or 0) for t in trades]
        avg_win = sum(p for p in pnl_pcts if p > 0) / max(win_count, 1)
        avg_loss = sum(p for p in pnl_pcts if p < 0) / max(len(trades) - win_count, 1)

        # 按币种分组统计
        symbol_perf = {}
        for t in trades:
            sym = t.symbol or "?"
            if sym not in symbol_perf:
                symbol_perf[sym] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
            symbol_perf[sym]["trades"] += 1
            if (t.pnl or 0) > 0:
                symbol_perf[sym]["wins"] += 1
            symbol_perf[sym]["total_pnl"] += float(t.pnl or 0)

        # 按市况分组统计
        regime_perf = {}
        for t in trades:
            dc = (t.decision_context or {}) if isinstance(t.decision_context, dict) else {}
            fp = dc.get("fingerprint_at_entry", {}) or {}
            reg = fp.get("regime_at_entry", "unknown")
            if reg not in regime_perf:
                regime_perf[reg] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
            regime_perf[reg]["trades"] += 1
            if (t.pnl or 0) > 0:
                regime_perf[reg]["wins"] += 1
            regime_perf[reg]["total_pnl"] += float(t.pnl or 0)

        # ── 7. 构建审计 Prompt ──
        system = _load_system_prompt_for_code_audit()

        user_text_parts = [
            f"## 策略代码级深度审计: {strategy_id}",
            "",
            f"### 策略概要",
            f"- 策略名称: {getattr(strategy, 'name', strategy_id)}",
            f"- 交易标的: {getattr(strategy, 'symbols', [])}",
            f"- 当前状态: {getattr(strategy, 'status', '?')}",
            f"- 模板ID: {(getattr(strategy, 'genome', None) or {}).get('source_template_id', 'N/A')}",
            "",
            f"### 绩效摘要（最近{len(trades)}笔）",
            f"- 总交易: {len(trades)} | 胜: {win_count} | 负: {len(trades)-win_count}",
            f"- 胜率: {win_count/len(trades):.1%}",
            f"- 总PnL: ${total_pnl:+.2f}",
            f"- 均盈: {avg_win*100:.2f}% | 均亏: {avg_loss*100:.2f}%",
            f"- 盈亏比: {abs(avg_win/avg_loss) if avg_loss != 0 else 0:.1f}",
            "",
            f"### 按币种绩效",
        ]
        for sym, perf in sorted(symbol_perf.items(), key=lambda x: x[1]["total_pnl"]):
            wr = perf["wins"] / max(perf["trades"], 1)
            user_text_parts.append(
                f"  {sym}: {perf['trades']}笔, 胜率{wr:.0%}, PnL ${perf['total_pnl']:+.2f}"
            )

        user_text_parts.extend([
            "",
            f"### 按市况绩效",
        ])
        for reg, perf in sorted(regime_perf.items(), key=lambda x: x[1]["total_pnl"]):
            wr = perf["wins"] / max(perf["trades"], 1)
            user_text_parts.append(
                f"  {reg}: {perf['trades']}笔, 胜率{wr:.0%}, PnL ${perf['total_pnl']:+.2f}"
            )

        user_text_parts.extend([
            "",
            f"### 策略当前Prompt（摘要）",
            f"```",
            strategy_prompt or "(无法获取)",
            f"```",
            "",
            f"### 策略记忆",
            json.dumps(memory_summary, ensure_ascii=False, indent=2),
            "",
            f"### 近期交易决策序列（含21因子快照）",
            json.dumps(decision_sequence, ensure_ascii=False, indent=2),
            "",
            "---",
            "",
            "## 审计要求",
            "",
            "请基于以上完整数据，产出策略代码级深度审计。",
            "重点分析：",
            "1. 因子有效性：哪些因子在最近交易中信号失效？哪些因子与PnL的相关性最高？",
            "2. 市况依赖：该策略在什么市况下表现最好/最差？是否有隐藏的市况条件依赖？",
            "3. 止损/止盈设置：当前SL/TP是否合理？是否有大量被扫止损后反转的交易？",
            "4. 入场时机：是否有频繁的假突破入场？入场信号是否需要增加滤波条件？",
            "5. 币种选择：当前symbol list中哪些币种拖累绩效？应增/减哪些币种？",
            "6. Prompt 优化：当前Prompt是否存在误导性描述或缺失关键约束？",
            "7. 加密特异性：资金费率在入场时的绝对值是否与后续PnL有显著关系？周末交易表现是否显著低于工作日？",
            "",
            "输出JSON格式：",
            "{",
            "  \"overall_assessment\": \"healthy|concerning|critical\",",
            "  \"top_issues\": [{\"issue\": \"...\", \"evidence\": \"...\", \"severity\": \"high|medium|low\"}],",
            "  \"suggestions\": [{\"category\": \"factor|regime|sl_tp|entry|symbol|prompt|crypto_specific\", \"description\": \"...\", \"expected_impact\": \"...\", \"implementation_difficulty\": \"easy|medium|hard\"}],",
            "  \"factor_analysis\": [{\"factor\": \"...\", \"correlation_with_pnl\": 0.0, \"effectiveness\": \"high|medium|low|reversed\", \"note\": \"...\"}],",
            "  \"crypto_specific_findings\": [{\"finding\": \"...\", \"evidence\": \"...\"}],",
            "  \"confidence\": 0.0",
            "}",
        ])

        user_text = "\n".join(user_text_parts)

        # ── 8. 调用 LLM 审计 ──
        raw, err = run_http_agent_message(
            system_prompt=system,
            user_text=user_text,
            agent=_agent_plan(),
            model_slug=_model(),
            session_title=f"Strategy Code Audit: {strategy_id}",
        )
        if err:
            return {"severity": "info", "findings": [], "error": err}

        result = _extract_json(raw or "")

        # 确保建议数量达标（≥3条）
        suggestions = result.get("suggestions", [])
        if len(suggestions) < 3:
            result["suggestions"] = list(suggestions)
            result["suggestions_insufficient"] = True
            result["note"] = f"审计仅产出{len(suggestions)}条建议（预期≥3），可能需要人工复核"

        logger.info(
            f"[OpenCodeBridge] 策略代码审计 {strategy_id}: "
            f"assessment={result.get('overall_assessment', '?')}, "
            f"suggestions={len(suggestions)}, "
            f"confidence={result.get('confidence', 0)}"
        )

        # ── 9. 审计结果注入 StrategyMemory ──
        try:
            memory = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == strategy_id
            ).first()
            if memory:
                audit_history = list(memory.audit_history or [])
                audit_history.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "type": "code_audit",
                    "overall_assessment": result.get("overall_assessment"),
                    "top_issues": result.get("top_issues", [])[:3],
                    "suggestions_count": len(suggestions),
                    "confidence": result.get("confidence", 0),
                })
                # 保留最近 10 次审计记录
                memory.audit_history = audit_history[-10:]
                db.commit()
                logger.debug(f"[OpenCodeBridge] 审计结果已写入 StrategyMemory {strategy_id}")
        except Exception as me:
            logger.debug(f"[OpenCodeBridge] StrategyMemory 更新跳过: {me}")

        return result

    except Exception as exc:
        logger.error("[OpenCodeBridge] strategy_code_audit: %s", exc, exc_info=True)
        return {"severity": "info", "findings": [], "error": str(exc)}


def _load_system_prompt_for_code_audit() -> str:
    """加载策略代码审计专用的系统提示词。"""
    # 优先使用专用 prompt 文件
    if os.path.isfile(STRATEGY_CODE_AUDIT_SYSTEM_PROMPT_PATH):
        with open(STRATEGY_CODE_AUDIT_SYSTEM_PROMPT_PATH, encoding="utf-8") as f:
            return f.read()

    # 降级：内联 prompt
    return (
        "You are Alpha Arena strategy code auditor. "
        "Your job is to find hidden patterns, logic flaws, and improvement opportunities "
        "in AI trading strategy decisions. "
        "You have access to full decision sequences with 21-factor snapshots. "
        "Cryptocurrency-specific factors (funding rate, OI, liquidations, stablecoin flows) "
        "are FIRST-CLASS indicators — analyze them with higher weight than traditional TA factors. "
        "Special attention to: weekend performance degradation, funding rate extremes, "
        "BTC correlation effects on altcoins, and regime-specific factor effectiveness. "
        "Return ONLY valid JSON. No markdown fences."
    )


def run_decision_audit(db, since_hours: int = 6) -> Dict[str, Any]:
    """
    决策质量审计：取最近N笔 MasterController 决策，对比后续实际市场走势，
    评判方向/时机/止损质量。
    """
    if not _is_enabled():
        return {"severity": "info", "findings": [], "skipped": "OPENCODE_ENABLED=false"}

    from backend.database.models import StrategyTrade
    from datetime import timedelta

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        recent_trades = (
            db.query(StrategyTrade)
            .filter(
                StrategyTrade.status == "closed",
                StrategyTrade.closed_at >= cutoff,
                ~StrategyTrade.strategy_id.like("rebate_%"),
            )
            .order_by(StrategyTrade.closed_at.desc())
            .limit(30)
            .all()
        )

        if len(recent_trades) < 5:
            return {"severity": "info", "findings": [], "skipped": f"仅{len(recent_trades)}笔交易，不足以审计"}

        # 构建交易摘要
        trade_summaries = []
        for t in recent_trades:
            trade_summaries.append({
                "symbol": t.symbol,
                "side": t.side,
                "pnl": float(t.pnl or 0),
                "pnl_pct": float(t.pnl_pct or 0),
                "entry_price": float(t.entry_price or 0),
                "exit_price": float(t.exit_price or 0),
                "close_reason": (t.decision_context or {}).get("close_reason", "?") if isinstance(t.decision_context, dict) else "?",
                "duration_min": int((getattr(t, "duration_seconds", 0) or 0) / 60),
                "strategy_id": t.strategy_id,
            })

        total_profit = sum(t["pnl"] for t in trade_summaries)
        win_count = sum(1 for t in trade_summaries if t["pnl"] > 0)

        system = _load_system_prompt()
        user_text = (
            f"## 决策质量审计（最近{since_hours}h）\n\n"
            f"- 总交易: {len(trade_summaries)}笔\n"
            f"- 胜率: {win_count}/{len(trade_summaries)} ({win_count/len(trade_summaries):.1%})\n"
            f"- 总PnL: ${total_profit:+.2f}\n\n"
            f"交易明细:\n{json.dumps(trade_summaries, ensure_ascii=False, indent=2)}\n\n"
            f"请审计以上决策质量，输出JSON:\n"
            f"{{\"overall_grade\": \"A|B|C|D\", \"blind_spots\": [\"...\"], \"top_mistake\": \"...\", \"suggestions\": [\"...\"]}}"
        )

        raw, err = run_http_agent_message(
            system_prompt=system,
            user_text=user_text,
            agent=_agent_plan(),
            model_slug=_model(),
            session_title=f"Decision Audit {since_hours}h",
        )
        if err:
            return {"severity": "info", "findings": [], "error": err}

        result = _extract_json(raw or "")
        logger.info(
            f"[OpenCodeBridge] 决策审计完成: grade={result.get('overall_grade', '?')} "
            f"blind_spots={len(result.get('blind_spots') or [])}"
        )
        return result
    except Exception as exc:
        logger.error("[OpenCodeBridge] decision_audit: %s", exc, exc_info=True)
        return {"severity": "info", "findings": [], "error": str(exc)}


# ══════════════════════════════════════════════════════
#  Phase 7: 战略层 — 市场状态叙事（跨周期挖掘见下方 P2.3）
# ══════════════════════════════════════════════════════

def run_regime_narrative_update(db) -> Dict[str, Any]:
    """
    市场状态叙事更新：取最近7天 market_envs 序列，生成市场演变叙事。
    """
    if not _is_enabled():
        return {"severity": "info", "findings": [], "skipped": "OPENCODE_ENABLED=false"}

    import glob as _glob

    try:
        # 从日志中读取最近的市场状态快照
        from backend.services.strategy_runtime_report import get_or_build_runtime_report
        srr = get_or_build_runtime_report(
            db, window="7d", domain="ai", force_refresh=False,
        )

        market_summary = srr.get("market_summary") or {}
        if not market_summary:
            return {"severity": "info", "findings": [], "skipped": "无市场数据"}

        system = _load_system_prompt()
        user_text = (
            f"## 市场状态叙事更新（7天）\n\n"
            f"市场摘要:\n{json.dumps(market_summary, ensure_ascii=False, indent=2)}\n\n"
            f"基于以上7天市场数据序列，撰写一段市场状态演变叙事：\n"
            f"主导趋势是什么？什么时候发生了转变？当前处于什么阶段？\n"
            f"输出JSON: {{\"narrative\": \"...\", \"dominant_trend\": \"bullish|bearish|ranging|transitioning\", \"confidence\": 0.0}}"
        )

        raw, err = run_http_agent_message(
            system_prompt=system,
            user_text=user_text,
            agent=_agent_plan(),
            model_slug=_model(),
            session_title="Regime Narrative Update 7d",
        )
        if err:
            return {"severity": "info", "findings": [], "error": err}

        result = _extract_json(raw or "")

        # 存储到 regime journal 文件
        journal_path = os.path.join("data", "opencode_reports", "regime_journal.jsonl")
        os.makedirs(os.path.dirname(journal_path), exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "narrative": result.get("narrative", ""),
            "dominant_trend": result.get("dominant_trend", "?"),
            "confidence": result.get("confidence", 0),
        }
        with open(journal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        logger.info(
            f"[OpenCodeBridge] 市场叙事更新: trend={entry['dominant_trend']} "
            f"confidence={entry['confidence']}"
        )
        return result
    except Exception as exc:
        logger.error("[OpenCodeBridge] regime_narrative: %s", exc, exc_info=True)
        return {"severity": "info", "findings": [], "error": str(exc)}


# ══════════════════════════════════════════════════════
#  P1.1: 多轮自我对弈推理链
# ══════════════════════════════════════════════════════

MULTI_ROUND_SYSTEM_PROMPT_PATH = os.path.join(
    "backend", "prompts", "opencode_multi_round_system.md"
)


def _load_multi_round_system_prompt() -> str:
    """加载多轮自我对弈推理的系统提示词"""
    if os.path.isfile(MULTI_ROUND_SYSTEM_PROMPT_PATH):
        with open(MULTI_ROUND_SYSTEM_PROMPT_PATH, encoding="utf-8") as f:
            return f.read()
    # 降级：内联 prompt
    return (
        "You are Alpha Arena multi-round reasoning engine.\n"
        "You will conduct SELF-PLAY reasoning across multiple rounds.\n"
        "For each round, you act as a different persona (bull, bear, neutral analyst)\n"
        "and debate the trade decision from different angles.\n"
        "Final round synthesizes all views into ONE actionable JSON proposal.\n"
        "CRYPTO-SPECIFIC: Always weight funding rate, OI, liquidations as FIRST-CLASS factors.\n"
        "Return ONLY valid JSON. No markdown fences."
    )


def run_multi_round_analysis(
    symbol: str,
    market_context: Dict[str, Any],
    rounds: int = 4,
) -> Dict[str, Any]:
    """
    多轮自我对弈推理链：OpenCode 以多个角色（bull/bear/neutral）
    对同一市场情境进行分析和辩论，最终合成一个可执行的 proposal。

    Args:
        symbol: 交易对
        market_context: 市场上下文（K线、因子、三周期视图等）
        rounds: 推理轮数（默认4：bull→bear→neutral→synthesize）

    Returns:
        {
            "proposal": {...},          # 最终可执行 proposal
            "debate_log": [...],         # 每轮推理摘要
            "consensus_score": 0.0-1.0,  # 共识度
            "dissenting_points": [...],  # 分歧点
        }
    """
    if not _is_enabled():
        return {"proposal": {}, "skipped": "OPENCODE_ENABLED=false"}

    try:
        env_level = 0
        try:
            from backend.config.settings import AI_EVOLUTION_LEVEL
            env_level = int(AI_EVOLUTION_LEVEL)
        except Exception:
            pass
        if env_level < 2:
            return {"proposal": {}, "skipped": f"AI_EVOLUTION_LEVEL={env_level}<2"}
    except Exception:
        pass

    try:
        personality_order = ["bull", "bear", "neutral", "synthesize"]
        actual_rounds = min(max(rounds, 2), len(personality_order))
        personas = personality_order[:actual_rounds]

        debate_log: List[Dict[str, str]] = []
        previous_arguments: List[str] = []

        system = _load_multi_round_system_prompt()
        context_json = json.dumps(market_context, ensure_ascii=False, indent=2)
        round_timeout_s = _multi_round_timeout_s()
        logger.info(
            "[OpenCodeBridge] MultiRound start %s: rounds=%d timeout_per_round=%ss",
            symbol,
            actual_rounds,
            round_timeout_s,
        )

        for i, persona in enumerate(personas):
            # 构建轮次 prompt
            if persona == "bull":
                round_text = (
                    f"## Round 1: Bull Case\n\n"
                    f"You are the BULL analyst for {symbol}.\n"
                    f"Argue STRONGLY why this is a buying opportunity.\n"
                    f"Focus on: positive funding rate signals, OI buildup, bullish TA patterns,\n"
                    f"strong support levels, and any momentum indicators.\n\n"
                    f"Market Context:\n{context_json}\n\n"
                    f"Output JSON: {{\"persona\": \"bull\", \"direction\": \"long\", \"confidence\": 0.0-1.0, \"key_arguments\": [\"...\"], \"risks_acknowledged\": [\"...\"]}}"
                )

            elif persona == "bear":
                prev_bull = debate_log[-1].get("arguments", "") if debate_log else ""
                round_text = (
                    f"## Round 2: Bear Case\n\n"
                    f"You are the BEAR analyst for {symbol}.\n"
                    f"Argue STRONGLY against the bull case. Challenge every bullish argument.\n"
                    f"Focus on: negative funding rates, high OI with liquidations risk,\n"
                    f"bearish TA patterns, overhead resistance, volume divergence.\n\n"
                    f"Bull's arguments you MUST address:\n{prev_bull}\n\n"
                    f"Market Context:\n{context_json}\n\n"
                    f"Output JSON: {{\"persona\": \"bear\", \"direction\": \"short\", \"confidence\": 0.0-1.0, \"key_arguments\": [\"...\"], \"bull_counterpoints\": [\"...\"]}}"
                )

            elif persona == "neutral":
                prev_text = "\n".join([d.get("arguments", "") for d in debate_log[-2:]])
                round_text = (
                    f"## Round 3: Neutral Arbitrage\n\n"
                    f"You are the NEUTRAL arbiter for {symbol}.\n"
                    f"Review both sides and identify:\n"
                    f"1. Which arguments from EACH side are valid?\n"
                    f"2. What did both sides MISS?\n"
                    f"3. Is there an arbiter edge (e.g., spread, basis, cross-exchange)?\n\n"
                    f"Previous debate:\n{prev_text}\n\n"
                    f"Output JSON: {{\"persona\": \"neutral\", \"bull_strong_points\": [\"...\"], \"bear_strong_points\": [\"...\"], \"missing_factors\": [\"...\"], \"arbiter_edge\": \"...\"}}"
                )

            else:  # synthesize
                all_prev = "\n---\n".join([
                    json.dumps(d, ensure_ascii=False) for d in debate_log
                ])
                round_text = (
                    f"## Final Round: Synthesis & Proposal\n\n"
                    f"You are the SYNTHESIZER for {symbol}.\n"
                    f"Review ALL previous rounds and produce a single actionable proposal.\n"
                    f"Weight each argument by evidence quality, not by conviction.\n\n"
                    f"Complete debate record:\n{all_prev}\n\n"
                    f"Market Context:\n{context_json}\n\n"
                    f"Output JSON:\n"
                    f"{{\n"
                    f"  \"final_direction\": \"long|short|neutral\",\n"
                    f"  \"confidence\": 0.0-1.0,\n"
                    f"  \"entry_price_range\": {{\"low\": 0, \"high\": 0}},\n"
                    f"  \"stop_loss\": 0,\n"
                    f"  \"take_profit_1\": 0,\n"
                    f"  \"position_sizing\": \"normal|reduced|minimal\",\n"
                    f"  \"rationale\": \"...\",\n"
                    f"  \"key_risks\": [\"...\"],\n"
                    f"  \"dissenting_points\": [\"...\"],\n"
                    f"  \"consensus_score\": 0.0\n"
                    f"}}"
                )

            logger.info(
                "[OpenCodeBridge] MultiRound R%d/%d %s for %s start",
                i + 1,
                actual_rounds,
                persona,
                symbol,
            )
            _round_t0 = time.time()
            raw, err = run_http_agent_message(
                system_prompt=system,
                user_text=round_text,
                agent=_agent_plan(),
                model_slug=_model(),
                session_title=f"MultiRound-{symbol}-R{i+1}-{persona}",
                timeout_s=round_timeout_s,
            )

            if err:
                debate_log.append({
                    "persona": persona,
                    "round": i + 1,
                    "error": err,
                    "arguments": "",
                })
                logger.warning(
                    f"[OpenCodeBridge] MultiRound R{i+1} {persona}: {err}"
                )
                continue

            result = _extract_json(raw or "")
            debate_log.append({
                "persona": persona,
                "round": i + 1,
                "result": result,
                "arguments": json.dumps(result, ensure_ascii=False),
            })
            previous_arguments.append(json.dumps(result, ensure_ascii=False))

            logger.info(
                f"[OpenCodeBridge] MultiRound R{i+1} {persona} for {symbol}: "
                f"direction={result.get('direction', result.get('final_direction', '?'))} "
                f"elapsed={time.time() - _round_t0:.1f}s"
            )

        # 提取最终 proposal（从最后一轮）
        final_result = debate_log[-1].get("result", {}) if debate_log else {}

        # 计算共识度：bull/bear方向一致度
        consensus_score = 0.0
        directions = []
        for d in debate_log[:-1]:  # 排除 synthesis
            r = d.get("result", {})
            d_dir = r.get("direction", "")
            if d_dir:
                directions.append(d_dir)
        if len(directions) >= 2 and len(set(directions)) == 1:
            consensus_score = 0.9
        elif len(directions) >= 2:
            consensus_score = 0.5

        # 提取分歧点
        dissenting = []
        if final_result.get("dissenting_points"):
            dissenting = final_result["dissenting_points"]

        return {
            "proposal": final_result,
            "debate_log": debate_log,
            "consensus_score": consensus_score,
            "dissenting_points": dissenting,
            "rounds_completed": len([d for d in debate_log if "error" not in d]),
        }

    except Exception as exc:
        logger.error("[OpenCodeBridge] multi_round_analysis: %s", exc, exc_info=True)
        return {"proposal": {}, "error": str(exc)}


def run_strategy_deep_dive_enhanced(db, strategy_id: str) -> Dict[str, Any]:
    """
    P1.4 增强: 策略深度诊断 — 注入 128K 上下文（完整K线+21因子+决策序列）。
    相比原有 run_strategy_deep_dive，本版本注入更丰富的上下文数据。
    """
    if not _is_enabled():
        return {"severity": "info", "findings": [], "skipped": "OPENCODE_ENABLED=false"}

    from backend.database.models import StrategyTrade, AIStrategy, StrategyMemory

    try:
        strategy = db.query(AIStrategy).filter(
            AIStrategy.strategy_id == strategy_id
        ).first()
        if not strategy:
            return {"severity": "info", "findings": [], "error": f"策略{strategy_id}不存在"}

        # 取最近60笔交易
        trades = (
            db.query(StrategyTrade)
            .filter(
                StrategyTrade.strategy_id == strategy_id,
                StrategyTrade.status == "closed",
            )
            .order_by(StrategyTrade.closed_at.desc())
            .limit(60)
            .all()
        )

        if len(trades) < 5:
            return {"severity": "info", "findings": [], "skipped": f"仅{len(trades)}笔交易"}

        # ── 构建增强上下文 ──
        # 1. 交易决策序列（含因子快照）
        decision_sequence = []
        for t in trades[:30]:
            dc = {}
            if isinstance(t.decision_context, dict):
                dc = t.decision_context
            decision_sequence.append({
                "symbol": t.symbol,
                "side": t.side,
                "pnl": float(t.pnl or 0),
                "pnl_pct": float(t.pnl_pct or 0) * 100,
                "entry_price": float(t.entry_price or 0),
                "exit_price": float(t.exit_price or 0),
                "close_reason": dc.get("close_reason", "?"),
                "duration_min": int((getattr(t, "duration_seconds", 0) or 0) / 60),
                "signals": dc.get("signals", {}),
                "orchestrator": dc.get("orchestrator", {}),
            })

        win_count = sum(1 for t in trades if (t.pnl or 0) > 0)
        total_pnl = sum(float(t.pnl or 0) for t in trades)
        avg_win = sum(
            float(t.pnl_pct or 0) for t in trades if (t.pnl or 0) > 0
        ) / max(win_count, 1)
        avg_loss = sum(
            float(t.pnl_pct or 0) for t in trades if (t.pnl or 0) < 0
        ) / max(len(trades) - win_count, 1)

        # 2. 策略记忆
        memory_summary = {}
        mem = db.query(StrategyMemory).filter(
            StrategyMemory.strategy_id == strategy_id
        ).first()
        if mem:
            memory_summary = {
                "key_lessons": list(mem.key_lessons or [])[-10:],
                "factor_weights": mem.factor_weights or {},
                "best_regime": mem.best_regime,
                "worst_regime": mem.worst_regime,
            }

        # 3. 按币种绩效
        symbol_perf: Dict[str, dict] = {}
        for t in trades:
            sym = t.symbol or "?"
            if sym not in symbol_perf:
                symbol_perf[sym] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
            symbol_perf[sym]["trades"] += 1
            if (t.pnl or 0) > 0:
                symbol_perf[sym]["wins"] += 1
            symbol_perf[sym]["total_pnl"] += float(t.pnl or 0)

        # 4. 构建增强 prompt
        system = _load_system_prompt_for_code_audit()

        user_text_parts = [
            f"## 策略深度诊断（增强版）: {strategy_id}",
            "",
            f"### 策略概要",
            f"- 名称: {getattr(strategy, 'name', strategy_id)}",
            f"- 标的: {getattr(strategy, 'symbols', [])}",
            f"- 状态: {getattr(strategy, 'status', '?')}",
            "",
            f"### 绩效摘要（最近{len(trades)}笔）",
            f"- 总交易: {len(trades)} | 胜: {win_count} | 负: {len(trades)-win_count}",
            f"- 胜率: {win_count/max(len(trades),1):.1%}",
            f"- 总PnL: ${total_pnl:+.2f}",
            f"- 均盈: {avg_win*100:.2f}% | 均亏: {avg_loss*100:.2f}%",
            f"- 盈亏比: {abs(avg_win/avg_loss) if avg_loss else 0:.1f}",
            "",
            f"### 按币种绩效",
        ]
        for sym, perf in sorted(symbol_perf.items(), key=lambda x: x[1]["total_pnl"]):
            wr = perf["wins"] / max(perf["trades"], 1)
            user_text_parts.append(
                f"  {sym}: {perf['trades']}笔, 胜率{wr:.0%}, PnL ${perf['total_pnl']:+.2f}"
            )

        user_text_parts.extend([
            "",
            "### 策略记忆",
            json.dumps(memory_summary, ensure_ascii=False, indent=2),
            "",
            "### 近期交易决策序列（含因子快照+Orchestrator三周期）",
            json.dumps(decision_sequence, ensure_ascii=False, indent=2),
            "",
            "---",
            "## 增强审计要求（P1.4）",
            "",
            "请产出策略代码级深度审计，特别关注：",
            "1. 因子有效性衰减：哪些因子信号在最近10笔中已反转？",
            "2. 三周期失调：short/mid/long bias是否出现系统性矛盾？",
            "3. 止损紧度：均亏/均盈比是否暗示止损/止盈设置不当？",
            "4. 资金费率陷阱：高费率入场是否系统性地导致低PnL？",
            "5. BTC锚定检测：山寨币交易PnL是否80%+与BTC同向（说明缺乏独立alpha）？",
            "6. 周末效应：周末交易胜率是否显著低于工作日？",
            "7. Prompt优化：当前策略prompt是否缺失关键约束？",
            "",
            "输出JSON格式：",
            "{",
            "  \"overall_health\": \"healthy|concerning|critical\",",
            "  \"factor_decay\": [{\"factor\": \"...\", \"recent_effectiveness\": 0.0, \"action\": \"keep|downgrade|remove\"}],",
            "  \"three_cycle_conflicts\": [{\"pattern\": \"...\", \"frequency\": 0, \"impact\": \"...\"}],",
            "  \"sl_tp_analysis\": {\"avg_win_pct\": 0.0, \"avg_loss_pct\": 0.0, \"recommendation\": \"...\"},",
            "  \"funding_rate_impact\": {\"correlation\": 0.0, \"finding\": \"...\"},",
            "  \"btc_correlation\": {\"altcoin_pnl_btc_corr\": 0.0, \"alpha_independence\": \"high|medium|low\"},",
            "  \"weekend_effect\": {\"weekend_win_rate\": 0.0, \"weekday_win_rate\": 0.0, \"significant\": true|false},",
            "  \"suggestions\": [{\"category\": \"...\", \"description\": \"...\", \"priority\": \"high|medium|low\"}]",
            "}",
        ])

        user_text = "\n".join(user_text_parts)

        raw, err = run_http_agent_message(
            system_prompt=system,
            user_text=user_text,
            agent=_agent_plan(),
            model_slug=_model(),
            session_title=f"Enhanced Strategy Audit: {strategy_id}",
        )
        if err:
            return {"severity": "info", "findings": [], "error": err}

        result = _extract_json(raw or "")
        logger.info(
            f"[OpenCodeBridge] 增强策略审计 {strategy_id}: "
            f"health={result.get('overall_health', '?')}, "
            f"suggestions={len(result.get('suggestions') or [])}"
        )
        return result

    except Exception as exc:
        logger.error("[OpenCodeBridge] strategy_deep_dive_enhanced: %s", exc, exc_info=True)
        return {"severity": "info", "findings": [], "error": str(exc)}


# ═══════════════════════════════════════════════════════════
#  P2.3 跨周期规律挖掘 — 识别 ≥3个 short/mid/long 联动模式
# ═══════════════════════════════════════════════════════════

def run_cross_cycle_pattern_mining(
    db,
    *,
    symbol: str = "BTC",
    days: int = 30,
) -> Dict[str, Any]:
    """
    跨周期规律挖掘：识别短期/中期/长期三个时间尺度间的联动模式。

    使用 OpenCode 分析 15m/1h/4h 三周期K线数据，
    挖掘联动规律：
    - 15m突破→1h趋势确认→4h支撑
    - 1h超卖→4h支撑位反弹→15m短线入场
    - 4h方向→1h确认→15m执行（2+1确认框架）

    Returns:
        {
            "patterns": [...],          # 发现的跨周期模式
            "pattern_count": int,
            "reliability_scores": [...],  # 可靠性评分
            "actionable_summary": str,
        }
    """
    if not _is_enabled():
        return {"skipped": "OpenCode未启用"}

    try:
        # 1. 获取三周期K线摘要
        # 修复（2026-06-24）：原代码调 pool.get_kline_series() —— 该方法不存在于
        # UnifiedDataPool（UnifiedDataPool 只有 get_multi_freq_klines，而那个内部也调
        # get_kline_series 同样会崩），导致 cross_cycle_pattern_mining 从未产出任何模式。
        # 改用 market_data.get_kline_data()，它返回 dict 列表 [{"close","high","low","volume"}]，
        # 是整个系统标准的 K 线获取接口（hypothesis_scan 等也用它）。
        from backend.services.market_data import get_kline_data

        def _get_summary(interval: str, limit: int) -> Dict[str, Any]:
            raw_kls = get_kline_data(symbol, period=interval, count=limit)
            if not raw_kls or len(raw_kls) < 20:
                return {"error": f"{interval}数据不足"}

            # get_kline_data 返回 dict 列表，统一提取字段
            closes = [float(k.get("close", 0)) for k in raw_kls]
            highs = [float(k.get("high", 0)) for k in raw_kls]
            lows = [float(k.get("low", 0)) for k in raw_kls]
            volumes = [float(k.get("volume", 0) or 0) for k in raw_kls]

            changes = []
            for i in range(1, len(closes)):
                if closes[i - 1] != 0:
                    changes.append(round((closes[i] - closes[i - 1]) / closes[i - 1] * 100, 4))

            import numpy as np
            return {
                "interval": interval,
                "count": len(closes),
                "current": closes[-1],
                "high": max(highs),
                "low": min(lows),
                "range_pct": round((max(highs) - min(lows)) / min(lows) * 100, 2),
                "change_5pct": changes[-5:] if len(changes) >= 5 else changes,
                "change_std": round(float(np.std(changes)), 4) if changes else 0,
                "vol_last": volumes[-1],
                "vol_avg": float(np.mean(volumes)) if volumes else 0,
                "vol_trend": "rising" if sum(volumes[-5:]) > sum(volumes[-10:-5]) * 1.2 else "falling",
                "swing_points_sample": _extract_swing_points(highs, lows, closes, num=5),
            }

        short_summary = _get_summary("15m", 96)
        mid_summary = _get_summary("1h", 24 * days)
        long_summary = _get_summary("4h", 6 * days)

        # 2. 构建挖掘 prompt
        system = (
            "You are Alpha Arena Cross-Cycle Pattern Mining Engine."
            "Your job: discover RELIABLE coordination patterns across 3 timeframes.\n\n"
            "CRYPTO-SPECIFIC PATTERNS TO SEARCH:\n"
            "1. 15m breakout → 1h trend confirmation → 4h structure validation\n"
            "2. 15m oversold → 1h support bounce → 4h bullish divergence\n"
            "3. 4h trend direction → 1h entry signal → 15m execution precision\n"
            "4. Volume surge on 15m → price level attracts 1h/4h participants\n"
            "5. Weekend patterns: thin liquidity → shorter cycles dominate\n"
            "6. Funding rate divergence: 15m extreme rate → 4h mean reversion\n\n"
            "For each pattern found, provide: name, trigger_conditions, reliability (0-1),"
            "crypto_context, example_setups.\n"
            "Return ONLY valid JSON, no markdown fences."
        )

        user_text = json.dumps({
            "task": "cross_cycle_pattern_mining",
            "symbol": symbol,
            "timeframes": {
                "short_15m": short_summary,
                "mid_1h": mid_summary,
                "long_4h": long_summary,
            },
            "requirements": {
                "min_patterns": 3,
                "pattern_types": ["entry_confirmation", "exit_warning", "trend_alignment", "divergence_detection"],
            },
        }, ensure_ascii=False, indent=2)

        raw, err = run_http_agent_message(
            system_prompt=system,
            user_text=user_text,
            agent=_agent_plan(),
            model_slug=_model(),
            session_title=f"Cross-Cycle Mining: {symbol}",
        )

        if err:
            logger.warning(f"[OpenCodeBridge] cross_cycle_pattern_mining failed: {err}")
            return {"skipped": f"LLM调用失败: {err}"}

        result = _extract_json(raw or "")
        patterns = result.get("patterns", [])

        logger.info(
            f"[OpenCodeBridge] 跨周期挖掘 {symbol}: "
            f"发现 {len(patterns)} 个联动模式"
        )

        # 3. 落库到策略记忆
        try:
            from backend.database.models import StrategyMemory
            from datetime import timezone as _tz
            import uuid as _uuid

            memory = (
                db.query(StrategyMemory)
                .filter(StrategyMemory.strategy_id == f"cross_cycle_{symbol}")
                .first()
            )
            if not memory:
                memory = StrategyMemory(
                    id=str(_uuid.uuid4()),
                    strategy_id=f"cross_cycle_{symbol}",
                    key_lessons=[],
                    created_at=datetime.now(_tz.utc),
                )
                db.add(memory)

            lessons = list(memory.key_lessons or [])
            lessons.append({
                "type": "cross_cycle_pattern",
                "ts": datetime.now(_tz.utc).isoformat(),
                "symbol": symbol,
                "pattern_count": len(patterns),
                "patterns": patterns,
            })
            memory.key_lessons = lessons[-30:]
            db.commit()
        except Exception as se:
            logger.debug(f"[OpenCodeBridge] 跨周期挖掘落库失败: {se}")

        return {
            "patterns": patterns,
            "pattern_count": len(patterns),
            "reliability_scores": [
                p.get("reliability", 0) for p in patterns
            ],
            "actionable_summary": result.get("summary", ""),
        }

    except Exception as exc:
        logger.error("[OpenCodeBridge] cross_cycle_pattern_mining: %s", exc, exc_info=True)
        return {"error": str(exc)}


def _extract_swing_points(
    highs: list, lows: list, closes: list,
    *, num: int = 5
) -> List[Dict[str, Any]]:
    """从K线中提取最近几个摆荡点（局部极值）"""
    import numpy as np
    if len(closes) < num * 3:
        return []

    closes_arr = np.array(closes)
    peaks = []
    troughs = []

    # 简化版摆动点检测
    for i in range(3, len(closes_arr) - 3):
        if closes_arr[i] == max(closes_arr[i - 3 : i + 4]):
            peaks.append({"idx": i, "price": float(closes_arr[i]), "type": "peak"})
        if closes_arr[i] == min(closes_arr[i - 3 : i + 4]):
            troughs.append({"idx": i, "price": float(closes_arr[i]), "type": "trough"})

    all_points = sorted(peaks + troughs, key=lambda p: p["idx"])[-num:]
    return [{"price": p["price"], "type": p["type"]} for p in all_points]
