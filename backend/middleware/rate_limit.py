"""
Lightweight in-memory rate limiter for API endpoints.

Uses a simple token-bucket approach. No external dependencies.
Configured via environment variables:

  RATE_LIMIT_ENABLED=true          (default: true in production)
  RATE_LIMIT_LLM_GENERATE=5/min   (default: 5)
  RATE_LIMIT_LLM_APIKEY=3/min     (default: 3)
  RATE_LIMIT_TRADING=10/min       (default: 10)

[fix] 改为纯 ASGI 中间件，避免 BaseHTTPMiddleware 在多线程高 GIL 竞争下
     导致的请求处理延迟。
"""
from __future__ import annotations

import time
import threading
import logging
import os
from collections import defaultdict

from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# ── Rate limit rules ────────────────────────────────────────────

# Format: (path_prefix, method, max_requests, window_seconds)
# Ordered by specificity — first match wins.
_RULES: list[tuple[str, str, int, int]] = []

_RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() in ("true", "1", "yes")


def _init_rules():
    if _RULES:
        return
    _RULES.extend([
        (
            "/api/ai-strategies/generate-framework",
            "POST",
            int(os.getenv("RATE_LIMIT_LLM_GENERATE", "5")),
            60,
        ),
        (
            "/api/ai-strategies/generate-signals",
            "POST",
            int(os.getenv("RATE_LIMIT_LLM_GENERATE", "5")),
            60,
        ),
        (
            "/api/llm-configs/",  # only /api/llm-configs/N/api-key (suffix match)
            "GET",
            int(os.getenv("RATE_LIMIT_LLM_APIKEY", "3")),
            60,
            True,  # suffix_only: only apply when path ends with /api-key
        ),
        (
            "/api/ai-trading/",  # execute-suggestion
            "POST",
            int(os.getenv("RATE_LIMIT_TRADING", "10")),
            60,
        ),
    ])


# ── Token bucket ────────────────────────────────────────────────

class TokenBucket:
    """Thread-safe sliding-window counter."""

    def __init__(self):
        self._windows: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, key: str, max_req: int, window_sec: int) -> bool:
        now = time.time()
        cutoff = now - window_sec

        with self._lock:
            timestamps = self._windows[key]
            # Evict expired entries
            while timestamps and timestamps[0] < cutoff:
                timestamps.pop(0)

            if len(timestamps) >= max_req:
                return False

            timestamps.append(now)
            return True


_bucket = TokenBucket()


# ── Middleware ──────────────────────────────────────────────────

class RateLimitMiddleware:
    """Pure ASGI middleware — apply rate limits to matched API endpoints."""

    def __init__(self, app):
        self.app = app
        _init_rules()
        if not _RATE_LIMIT_ENABLED:
            logger.info("[RateLimit] Disabled (RATE_LIMIT_ENABLED=false)")
        else:
            logger.info(
                "[RateLimit] Enabled with %d rule(s): %s",
                len(_RULES),
                [(rule[0], rule[1]) for rule in _RULES],
            )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not _RATE_LIMIT_ENABLED:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET")

        for rule in _RULES:
            prefix, rule_method, max_req, window_sec = rule[:4]
            suffix_only = rule[4] if len(rule) > 4 else False
            if rule_method != method:
                continue
            if not path.startswith(prefix):
                continue
            if suffix_only and not path.endswith("/api-key"):
                continue

            # Extract client IP from ASGI scope
            client = scope.get("client")
            client_ip = client[0] if client else "unknown"
            bucket_key = f"{prefix}:{client_ip}"

            if not _bucket.is_allowed(bucket_key, max_req, window_sec):
                logger.warning(
                    "[RateLimit] Blocked %s %s from %s (limit: %d/%ds)",
                    method, path, client_ip, max_req, window_sec,
                )
                response = JSONResponse(
                    status_code=429,
                    content={
                        "detail": f"Rate limit exceeded. Max {max_req} requests per {window_sec}s.",
                        "retry_after_seconds": window_sec,
                    },
                )
                await response(scope, receive, send)
                return

            break  # first matching rule applies

        await self.app(scope, receive, send)
