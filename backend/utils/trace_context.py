"""统一追踪 ID (trace_id) 上下文 —— 贯穿 paper/live/套利的日志关联。

设计目标:
- 每个 HTTP 请求 / 每个 tick / 每笔交易生成唯一 trace_id
- 通过 ContextVar 在同一线程/协程内自动传播
- LoggingFilter 将 trace_id 注入到每条日志记录，便于跨系统排查
- 背景任务（调度器 tick）也能手动绑定 trace_id

用法:
    # HTTP 请求中间件自动设置（见 middleware/trace_middleware.py）
    # 手动设置（调度器/后台任务）:
    from backend.utils.trace_context import bind_trace, new_trace, get_trace_id
    with bind_trace(new_trace("full_auto_tick")):
        ...  # 内部所有日志都带 trace_id

    # 日志输出: 2026-06-19 17:00:00 [INFO] [tr=a3f1...] backend.x:123 - ...
"""
from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Generator, Optional

# ── ContextVar: 协程/线程安全的 trace_id 存储 ──────────────────────
_trace_id_ctx: ContextVar[Optional[str]] = ContextVar("trace_id", default=None)

# 缩写前缀（节省日志空间）：trace_id 较长时显示前 8 位
_TRACE_ID_DISPLAY_LEN = 8


def generate_trace_id(prefix: str = "") -> str:
    """生成唯一 trace_id。

    Args:
        prefix: 可选前缀，标识来源（如 "tick", "req", "trade"）
                会以 "tick-" 形式拼接在 uuid 前。

    Returns:
        形如 "tick-a3f1b2c4d5e6" 的 trace_id（前缀短 + uuid 段）
    """
    uid = uuid.uuid4().hex[:12]  # 12 位足够区分，避免日志过长
    if prefix:
        # 清理前缀：只保留字母数字，限制长度
        clean = "".join(c for c in prefix if c.isalnum())[:10]
        return f"{clean}-{uid}" if clean else uid
    return uid


def new_trace(prefix: str = "") -> str:
    """generate_trace_id 的别名，语义更清晰。"""
    return generate_trace_id(prefix)


def get_trace_id() -> Optional[str]:
    """获取当前上下文的 trace_id（可能为 None）。"""
    return _trace_id_ctx.get()


def get_trace_id_short() -> str:
    """获取当前 trace_id 的短显示形式（前 8 位），无则返回 '-'。"""
    tid = _trace_id_ctx.get()
    if not tid:
        return "-"
    # 去掉前缀的 uuid 部分
    if "-" in tid:
        _, uid = tid.rsplit("-", 1)
        prefix_part = tid[: -len(uid) - 1]
        short = uid[:_TRACE_ID_DISPLAY_LEN]
        return f"{prefix_part}-{short}" if prefix_part else short
    return tid[:_TRACE_ID_DISPLAY_LEN]


def set_trace_id(trace_id: Optional[str]) -> None:
    """直接设置当前上下文的 trace_id（覆盖）。

    优先使用 bind_trace 上下文管理器；此函数用于需要手动清除的场景。
    """
    _trace_id_ctx.set(trace_id)


@contextmanager
def bind_trace(trace_id: str) -> Generator[str, None, None]:
    """上下文管理器：在 with 块内绑定 trace_id，退出时恢复。

    用法:
        with bind_trace(new_trace("tick")):
            logger.info("...")  # 日志自动带 trace_id
            await some_async_work()  # 协程内也传播

    Yields:
        绑定的 trace_id（便于传递给子函数）
    """
    token = _trace_id_ctx.set(trace_id)
    try:
        yield trace_id
    finally:
        _trace_id_ctx.reset(token)


# ── LoggingFilter: 注入 trace_id 到每条日志 ──────────────────────


class TraceIdFilter(logging.Filter):
    """日志过滤器：将当前 ContextVar 中的 trace_id 注入到 record。

    注入字段:
    - record.trace_id: 完整 trace_id（可能含前缀）
    - record.trace_id_short: 短显示形式（用于日志格式）

    在日志格式中使用 %(trace_id_short)s 即可显示。
    若上下文无 trace_id，trace_id_short 为 '-'。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        tid = _trace_id_ctx.get()
        record.trace_id = tid or ""
        record.trace_id_short = get_trace_id_short()
        return True  # 始终放行


def install_trace_filter(logger_name: str = "") -> None:
    """给指定 logger（默认 root）安装 TraceIdFilter。

    幂等：重复安装不会叠加多个 filter。
    """
    lg = logging.getLogger(logger_name)
    # 幂等检查
    for f in lg.filters:
        if isinstance(f, TraceIdFilter):
            return
    lg.addFilter(TraceIdFilter())
