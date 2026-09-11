# -*- coding: utf-8 -*-
"""统一告警出口（v3 方向 6）：分级 P0/P1/P2，三通道并发（飞书 / Telegram / 通用 webhook），带去重节流。

  P0  停机/资金：急停、熔断、交易所异常、对账差额 —— 所有通道
  P1  策略退回影子 / 任务连续失败 / 风控降级 —— 所有通道
  P2  数据延迟 / 任务滞后 —— 默认只飞书（ALERT_P2_ALL_CHANNELS=true 时全通道）

通道配置（.env）：
  飞书       沿用 openclaw_notify（data/notification_config.json）
  Telegram   ALERT_TELEGRAM_BOT_TOKEN + ALERT_TELEGRAM_CHAT_ID
  webhook    ALERT_WEBHOOK_URLS（逗号分隔，POST JSON {level,title,text,ts,source,dedupe_key}）
去重：同 dedupe_key 在 ALERT_DEDUPE_SEC（默认 600s）内只发一条；P0 默认 120s。
所有发送都是 best-effort：任何通道失败只记日志，绝不向调用方抛异常。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_LEVEL_TO_FEISHU = {"P0": "critical", "P1": "warning", "P2": "info"}
_dedupe: Dict[str, float] = {}
_dedupe_lock = threading.Lock()
_stats: Dict[str, int] = {"sent": 0, "deduped": 0, "feishu_ok": 0, "telegram_ok": 0, "webhook_ok": 0, "errors": 0}
_recent: List[Dict[str, Any]] = []   # 最近 100 条（/api/ops/alerts/recent）


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or default).strip()


def _env_true(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def channels_configured() -> Dict[str, bool]:
    feishu = False
    try:
        from backend.services.openclaw_notify import get_notifier
        n = get_notifier()
        cfg = getattr(n, "_config", None) or getattr(n, "config", None)
        feishu = bool(getattr(cfg, "enabled", False)) if cfg is not None else False
    except Exception:
        feishu = False
    return {
        "feishu": feishu,
        "telegram": bool(_env("ALERT_TELEGRAM_BOT_TOKEN") and _env("ALERT_TELEGRAM_CHAT_ID")),
        "webhook": bool(_env("ALERT_WEBHOOK_URLS")),
    }


def _should_send(dedupe_key: Optional[str], level: str) -> bool:
    if not dedupe_key:
        return True
    window = float(_env("ALERT_DEDUPE_SEC", "600") or 600)
    if level == "P0":
        window = min(window, float(_env("ALERT_P0_DEDUPE_SEC", "120") or 120))
    now = time.time()
    with _dedupe_lock:
        last = _dedupe.get(dedupe_key, 0.0)
        if now - last < window:
            _stats["deduped"] += 1
            return False
        _dedupe[dedupe_key] = now
        # 清理过期键
        if len(_dedupe) > 2000:
            for k in [k for k, t in _dedupe.items() if now - t > 3600]:
                _dedupe.pop(k, None)
    return True


def _send_feishu(level: str, title: str, text: str) -> bool:
    try:
        from backend.services.openclaw_notify import get_notifier
        return bool(get_notifier().send_sync(text, title=title, level=_LEVEL_TO_FEISHU.get(level, "info"), event_type="system"))
    except Exception as exc:
        logger.debug("[alerts] feishu fail: %s", exc)
        return False


def _send_telegram(level: str, title: str, text: str) -> bool:
    token, chat = _env("ALERT_TELEGRAM_BOT_TOKEN"), _env("ALERT_TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    try:
        import httpx
        body = f"[{level}] {title}\n{text}"[:3900]
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       json={"chat_id": chat, "text": body, "disable_web_page_preview": True}, timeout=8.0)
        return r.status_code == 200
    except Exception as exc:
        logger.debug("[alerts] telegram fail: %s", exc)
        return False


def _send_webhooks(payload: Dict[str, Any]) -> int:
    urls = [u.strip() for u in _env("ALERT_WEBHOOK_URLS").split(",") if u.strip()]
    if not urls:
        return 0
    ok = 0
    try:
        import httpx
        for u in urls:
            try:
                r = httpx.post(u, json=payload, timeout=8.0)
                if 200 <= r.status_code < 300:
                    ok += 1
            except Exception as exc:
                logger.debug("[alerts] webhook %s fail: %s", u, exc)
    except Exception as exc:
        logger.debug("[alerts] httpx unavailable: %s", exc)
    return ok


def send_alert(level: str, title: str, text: str, *, dedupe_key: Optional[str] = None,
               source: str = "system", async_send: bool = True) -> Dict[str, Any]:
    """统一入口。level ∈ {P0,P1,P2}。默认后台线程发送，不阻塞调用方。"""
    level = str(level or "P1").upper()
    if level not in ("P0", "P1", "P2"):
        level = "P1"
    if not _should_send(dedupe_key, level):
        return {"sent": False, "deduped": True}
    payload = {"level": level, "title": str(title)[:200], "text": str(text)[:4000], "ts": time.time(),
               "source": source, "dedupe_key": dedupe_key}
    with _dedupe_lock:
        _recent.append(payload)
        del _recent[:-100]
        _stats["sent"] += 1

    def _do():
        try:
            all_channels = level in ("P0", "P1") or _env_true("ALERT_P2_ALL_CHANNELS", False)
            if _send_feishu(level, title, text):
                _stats["feishu_ok"] += 1
            if all_channels:
                if _send_telegram(level, title, text):
                    _stats["telegram_ok"] += 1
                _stats["webhook_ok"] += _send_webhooks(payload)
        except Exception as exc:  # pragma: no cover
            _stats["errors"] += 1
            logger.debug("[alerts] send error: %s", exc)

    if async_send:
        threading.Thread(target=_do, name=f"alert-{level}", daemon=True).start()
    else:
        _do()
    return {"sent": True, "deduped": False, "level": level}


def alerts_status() -> Dict[str, Any]:
    with _dedupe_lock:
        recent = list(_recent[-20:])
    return {"channels": channels_configured(), "stats": dict(_stats), "recent": recent}
