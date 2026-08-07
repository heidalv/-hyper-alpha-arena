"""ASGI 中间件：为每个 HTTP 请求绑定 trace_id。

为每个进入的 HTTP 请求生成（或复用客户端传入的）trace_id，
绑定到 ContextVar，使请求处理链路内的所有日志都带同一 trace_id。

设计:
- 纯 ASGI 中间件（与 rate_limit.py 一致，避免 BaseHTTPMiddleware 的 GIL 延迟）
- 优先复用客户端 X-Request-ID 头；缺失则生成新的
- 响应头回写 X-Request-ID，便于前端/调用方关联
- 静态文件 / websocket 请求也绑定（便于 WS 日志追踪）

用法（main.py）:
    from .middleware.trace import TraceMiddleware
    app.add_middleware(TraceMiddleware)
"""
from __future__ import annotations

import logging
import os

from backend.utils.trace_context import (
    bind_trace,
    generate_trace_id,
    set_trace_id,
)

logger = logging.getLogger(__name__)

_TRACE_ENABLED = os.getenv("TRACE_ID_ENABLED", "true").lower() in ("true", "1", "yes")
# 客户端可传入的请求头名（复用语义）
_REQUEST_ID_HEADER = "x-request-id"
# 白名单：这些 path 不生成 trace（减少噪音）
_SKIP_PREFIXES = ("/static", "/favicon", "/docs", "/openapi.json", "/redoc")


class TraceMiddleware:
    """纯 ASGI 中间件 —— 为每个 HTTP 请求绑定 trace_id。

    trace_id 传播链:
        客户端 X-Request-ID (可选)
          → 中间件生成/复用 trace_id
          → ContextVar (bind_trace)
          → 请求处理链路所有日志带 trace_id
          → 响应头 X-Request-ID 回写
    """

    def __init__(self, app):
        self.app = app
        if _TRACE_ENABLED:
            logger.info("[Trace] trace_id 中间件已启用")
        else:
            logger.info("[Trace] trace_id 中间件已禁用 (TRACE_ID_ENABLED=false)")

    async def __call__(self, scope, receive, send):
        if not _TRACE_ENABLED or scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        # 静态/文档路径跳过（但仍透传请求）
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return

        # 从请求头提取 X-Request-ID（若客户端传入则复用）
        client_trace_id = None
        for header_name, header_value in scope.get("headers", []):
            try:
                name = header_name.decode("latin-1").lower()
            except Exception:
                continue
            if name == _REQUEST_ID_HEADER:
                try:
                    client_trace_id = header_value.decode("latin-1").strip()
                except Exception:
                    client_trace_id = None
                break

        # 生成或复用 trace_id
        trace_id = client_trace_id or generate_trace_id("req")

        # 包装 send 以回写响应头 X-Request-ID
        async def send_with_trace_id(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append(
                    (
                        _REQUEST_ID_HEADER.encode("latin-1"),
                        trace_id.encode("latin-1"),
                    )
                )
                message = dict(message)
                message["headers"] = headers
            await send(message)

        # 在 ContextVar 内执行下游处理
        with bind_trace(trace_id):
            await self.app(scope, receive, send_with_trace_id)
