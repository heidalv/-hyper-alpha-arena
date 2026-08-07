# backend/core/tenant.py
"""Per-request tenant context (ContextVar) for RLS.

中间件在请求开始时 ``set_request_identity()``;``connection.py`` 的 ``begin``
事件钩子每次事务开始读 ContextVar 设 ``SET LOCAL app.tenant_id``。

为什么用 ContextVar 而不是 thread.local
---------------------------------------
- ContextVar 是 asyncio 安全的:同一个 asyncio task 内的 await 链共享同一个值,
  且不同请求(不同 task)天然隔离,不会跨请求串租户身份。
- thread.local 在 asyncio + threadpool 混用下会出错:一个请求可能在多个 OS 线程
  上执行(run_in_executor),thread.local 取不到 → RLS 失效。ContextVar 在
  ``asyncio.Task`` 维度上正确传播。

为什么钩子要每次事务开始重设(致命陷阱)
---------------------------------------
代码库有 521 处 ``db.commit()``,SQLAlchemy 2.0 autobegin 模式下每次 commit
结束当前事务,``SET LOCAL`` 随之失效。若只在 ``get_db`` yield 时设一次,
首次 commit 后 GUC 就没了 → ``current_setting('app.tenant_id', true)`` 返回 NULL
→ RLS 策略 fail-closed(隐藏行)或更糟(泄漏)。这不是报错,是静默数据损坏。
``begin`` 事件在每次 autobegin(含 commit 后的新事务)时触发,保证 GUC 总在。
"""
from __future__ import annotations

import contextlib
import contextvars

# tenant_id: 当前请求的租户(users.id)。None = 未设置(全局/未认证/运维通道)。
tenant_id_var: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "tenant_id", default=None,
)
# is_admin: 超级管理员穿透 RLS(阶段4 admin bootstrap 用,先留接口)。
is_admin_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "is_admin", default=False,
)


def set_request_identity(tenant_id: int | None, role: str = "user") -> None:
    """中间件调用:设置当前请求的租户身份。

    - tenant_id: JWT claim 里的 users.id;None 表示无租户上下文(未认证/运维通道)。
    - role: "admin" 时同时置 is_admin=True(阶段4 RLS 穿透用)。
    """
    tenant_id_var.set(tenant_id)
    is_admin_var.set(role == "admin")


def clear_request_identity() -> None:
    """请求结束清理(可选,ContextVar 随请求上下文自然失效)。

    主要是防御性用法:在中间件 finally 里显式清掉,避免 ASGI 实现复用协程对象
    (理论上 Starlette 不会,但显式清理零成本且更稳)。
    """
    tenant_id_var.set(None)
    is_admin_var.set(False)


def set_system_identity() -> None:
    """后台/系统操作(非 HTTP 请求)设管理员级身份,穿透 RLS。

    APScheduler 后台交易循环(scalp/coordinator/midlong 等)不在 HTTP 请求上下文,
    中间件不会为它们设 tenant_id。若不设,RLS 会 fail-closed(0 行)破坏交易。
    系统循环是可信的(等同 admin),设 is_admin=True 走 RLS 短路。

    注意:只在确实无租户上下文的系统/运维通道调用,绝不可在处理用户请求时调用,
    否则会让该请求绕过 RLS 看到全部租户数据。
    """
    is_admin_var.set(True)
    # tenant_id 保持 None(is_admin 短路已足够;系统操作跨租户)


@contextlib.contextmanager
def system_identity():
    """上下文管理器:``with system_identity(): ...``(自动恢复原值)。

    后台循环每轮用 ``with system_identity():`` 包裹 DB 操作,确保 RLS 穿透。
    退出时恢复进入前的 tenant_id / is_admin,避免污染同线程后续逻辑
    (后台循环跑在 APScheduler 自己的线程上,理论上不与 HTTP 请求共享上下文,
    但显式恢复仍是好习惯,也方便在测试/混合调用场景下复用)。
    """
    prev_admin = is_admin_var.get()
    prev_tenant = tenant_id_var.get()
    set_system_identity()
    try:
        yield
    finally:
        is_admin_var.set(prev_admin)
        tenant_id_var.set(prev_tenant)
