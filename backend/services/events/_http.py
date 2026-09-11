# -*- coding: utf-8 -*-
"""事件采集器共用 HTTP 工具：代理选择、重试、结构化失败记录。

代理策略（与 derivatives_analytics_service / binance_user_stream 一致）：
  *.binance.com  → BINANCE_HTTPS_PROXY（缺省再退 HTTPS_PROXY）
  其它域         → EVENTS_HTTPS_PROXY → HTTPS_PROXY → BINANCE_HTTPS_PROXY
  EVENTS_HTTP_DIRECT=1 → 全部直连
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_FAILS: Dict[str, Dict[str, Any]] = {}
_FAILS_LOCK = threading.Lock()


def _truthy(v: Optional[str]) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def proxy_for(url: str) -> Optional[str]:
    if _truthy(os.getenv("EVENTS_HTTP_DIRECT")):
        return None
    host = (urlparse(url).hostname or "").lower()
    if "binance" in host:
        return os.getenv("BINANCE_HTTPS_PROXY") or os.getenv("HTTPS_PROXY") or None
    return (
        os.getenv("EVENTS_HTTPS_PROXY")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("BINANCE_HTTPS_PROXY")
        or None
    )


def ws_proxy_for(url: str) -> Optional[str]:
    """WebSocket 代理：优先 BINANCE_HTTP_PROXY（用户流同款），再退 HTTPS 代理。"""
    if _truthy(os.getenv("EVENTS_HTTP_DIRECT")):
        return None
    host = (urlparse(url).hostname or "").lower()
    if "binance" in host:
        return os.getenv("BINANCE_HTTP_PROXY") or os.getenv("BINANCE_HTTPS_PROXY") or os.getenv("HTTPS_PROXY") or None
    return os.getenv("EVENTS_HTTPS_PROXY") or os.getenv("HTTPS_PROXY") or None


def _env_num(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


class _BinanceBudget:
    """进程内 Binance REST 权重预算（所有事件采集器共用，保护同 IP 的用户流/下单不被我们的回填打到 429）。

    - 每次响应读 X-MBX-USED-WEIGHT-1M；≥ BINANCE_WEIGHT_SOFT_LIMIT（默认 1500/2400）→ 等到下一分钟边界
    - 相邻请求最小间隔 BINANCE_EVENTS_MIN_INTERVAL_SEC（默认 0.15s ≈ 400 req/min）
    - 429/418 → 按 Retry-After 全局暂停（缺省 429:60s / 418:300s），期间所有 Binance 请求排队等待
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pause_until = 0.0
        self.used_1m = 0
        self.used_ts = 0.0
        self.last_req = 0.0
        self.rate_limited_total = 0

    def before(self) -> None:
        soft = _env_num("BINANCE_WEIGHT_SOFT_LIMIT", 1500)
        min_gap = _env_num("BINANCE_EVENTS_MIN_INTERVAL_SEC", 0.15)
        while True:
            with self.lock:
                now = time.time()
                wait = 0.0
                if self.pause_until > now:
                    wait = self.pause_until - now
                elif self.used_1m >= soft and now - self.used_ts < 60:
                    wait = 60.0 - (now % 60.0) + 0.5
                    self.used_1m = 0  # 边界后视为重置；下一响应头会纠正
                elif now - self.last_req < min_gap:
                    wait = min_gap - (now - self.last_req)
                if wait <= 0:
                    self.last_req = now
                    return
            time.sleep(min(wait, 30.0))

    def after(self, resp: "httpx.Response") -> None:
        try:
            hdr = resp.headers.get("X-MBX-USED-WEIGHT-1M") or resp.headers.get("x-mbx-used-weight-1m")
            with self.lock:
                if hdr and str(hdr).isdigit():
                    self.used_1m = int(hdr)
                    self.used_ts = time.time()
                if resp.status_code in (429, 418):
                    ra = resp.headers.get("Retry-After")
                    secs = int(ra) if ra and str(ra).isdigit() else (60 if resp.status_code == 429 else 300)
                    self.pause_until = max(self.pause_until, time.time() + secs)
                    self.rate_limited_total += 1
                    logger.warning("[events.http] Binance %s，全局暂停 %ss（累计 %d 次）",
                                   resp.status_code, secs, self.rate_limited_total)
        except Exception:
            pass

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            now = time.time()
            return {"used_weight_1m": self.used_1m, "used_age_sec": round(now - self.used_ts, 1) if self.used_ts else None,
                    "paused_sec": round(max(0.0, self.pause_until - now), 1), "rate_limited_total": self.rate_limited_total}


BINANCE_BUDGET = _BinanceBudget()


def get_json(url: str, params: Optional[Dict[str, Any]] = None, *, timeout: float = 15.0,
             retries: int = 2, headers: Optional[Dict[str, str]] = None) -> Optional[Any]:
    """GET 并解析 JSON；失败返回 None（记录到 failure_report，不抛异常）。Binance 域自动过权重预算。"""
    host = (urlparse(url).hostname or "?").lower()
    proxy = proxy_for(url)
    is_binance = "binance" in host
    hdrs = {"User-Agent": "HyperAlphaArena-events/1.0", "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    last_err: Optional[str] = None
    for attempt in range(max(1, retries + 1)):
        try:
            if is_binance:
                BINANCE_BUDGET.before()
            with httpx.Client(proxy=proxy, timeout=timeout, headers=hdrs, follow_redirects=True) as client:
                r = client.get(url, params=params)
            if is_binance:
                BINANCE_BUDGET.after(r)
            if r.status_code == 429 or r.status_code == 418:
                last_err = f"rate_limited {r.status_code}"
                if not is_binance:
                    time.sleep(2.0 * (attempt + 1))
                continue
            if r.status_code >= 400:
                last_err = f"http {r.status_code}: {r.text[:120]}"
                if 400 <= r.status_code < 500 and r.status_code not in (408, 425):
                    break
                time.sleep(0.5 * (attempt + 1))
                continue
            _record_ok(host)
            return r.json()
        except Exception as exc:  # 网络/解析异常统一处理
            last_err = f"{type(exc).__name__}: {str(exc)[:120]}"
            time.sleep(0.5 * (attempt + 1))
    _record_fail(host, last_err or "unknown")
    logger.debug("[events.http] GET %s 失败: %s", url, last_err)
    return None


def _record_ok(host: str) -> None:
    with _FAILS_LOCK:
        rec = _FAILS.setdefault(host, {"consecutive_failures": 0, "last_error": None, "last_ok_ts": None, "last_fail_ts": None})
        rec["consecutive_failures"] = 0
        rec["last_ok_ts"] = time.time()


def _record_fail(host: str, err: str) -> None:
    with _FAILS_LOCK:
        rec = _FAILS.setdefault(host, {"consecutive_failures": 0, "last_error": None, "last_ok_ts": None, "last_fail_ts": None})
        rec["consecutive_failures"] = int(rec.get("consecutive_failures") or 0) + 1
        rec["last_error"] = err
        rec["last_fail_ts"] = time.time()


def failure_report() -> Dict[str, Dict[str, Any]]:
    with _FAILS_LOCK:
        out = {k: dict(v) for k, v in _FAILS.items()}
    out["_binance_budget"] = BINANCE_BUDGET.snapshot()
    return out
