# backend/tests/integration/test_background_loops_rls.py
"""C1 守卫测试:后台交易循环 system_identity 穿透 RLS。

背景
----
APScheduler 后台交易循环(scalp/coordinator/midlong 等)跑在调度器自己的线程上,
不在 HTTP 请求上下文,因此 ``auth.py`` 中间件不会为它们设 ``tenant_id`` /
``is_admin``。若不显式设置,在非 superuser DB 角色下 RLS 会 fail-closed(0 行),
静默破坏交易(查不到 positions/balance → 直接 return → 不下单)。当前仅因
``laobao`` 是 superuser 才被掩盖。

修复
----
每个后台循环入口调用 ``set_system_identity()``(置 ``is_admin_var=True``),让 RLS
策略里现有的 ``current_setting('app.is_admin', true) = 'on'`` 短路分支生效 ——
系统循环是可信的(等同 admin)。

本测试验证什么
--------------
1. ``system_identity()`` 上下文管理器正确设置 ``is_admin_var=True``,且退出后恢复原值;
2. ``set_system_identity()`` 直接调用同样置 ``is_admin_var=True``;
3. 在 ``system_identity()`` 作用域内,通过 ``SessionLocal`` 的 RLS ``begin`` 钩子
   会把 ``app.is_admin`` GUC 设成 ``'on'``(这是真正让 RLS 短路的机制)。

注意:``SessionLocal`` 连的是 ``laobao``(superuser),superuser 本身就绕过 RLS,故这里
只能证明 GUC 被正确设上;真正的非 superuser 穿透证明由 ``test_rls_isolation.py`` 的
``test_admin_bypass_sees_all_positions`` 提供(用非 superuser 角色 + ``app.is_admin='on'``)。
本测试聚焦"上下文管理器是否设对了 ContextVar / GUC"这一层。

仅在 PostgreSQL 上运行(SQLite 无 RLS/GUC 概念)。
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from backend.core.tenant import (
    is_admin_var,
    set_system_identity,
    system_identity,
    tenant_id_var,
)
from backend.database.connection import DATABASE_URL, SessionLocal


pytestmark = pytest.mark.skipif(
    not (DATABASE_URL.lower().startswith("postgresql")
         or DATABASE_URL.lower().startswith("postgres")),
    reason="RLS GUC (SET LOCAL) 是 PostgreSQL 特性,SQLite 无此概念,跳过",
)


def test_set_system_identity_sets_admin_var():
    """set_system_identity() 直接调用置 is_admin_var=True。"""
    prev = is_admin_var.get()
    try:
        set_system_identity()
        assert is_admin_var.get() is True
    finally:
        is_admin_var.set(prev)


def test_system_identity_context_restores_prior_values():
    """system_identity() 进入时设 is_admin=True,退出后恢复进入前的值。

    覆盖两种入口态:从 is_admin=False 进入(常见),以及从 is_admin=True 进入
    (嵌套调用)。保证不污染同线程后续逻辑。
    """
    # 从 False 进入
    is_admin_var.set(False)
    tenant_id_var.set(42)
    with system_identity():
        assert is_admin_var.get() is True, "作用域内 is_admin 必须为 True"
    assert is_admin_var.get() is False, "退出后应恢复 False"
    assert tenant_id_var.get() == 42, "退出后 tenant_id 不应被改动"

    # 从 True 进入(嵌套场景)
    is_admin_var.set(True)
    with system_identity():
        assert is_admin_var.get() is True
    assert is_admin_var.get() is True, "退出后应恢复 True"


def test_system_identity_bypasses_rls_for_background_ops():
    """后台循环设 system_identity 后,RLS begin 钩子把 app.is_admin GUC 设为 'on'。

    这是 C1 的核心断言:ContextVar=is_admin=True 经 connection.py 的 begin 事件钩子
    翻译成 ``SET LOCAL app.is_admin='on'``,RLS 策略的 admin 短路分支据此放行。
    即便用 superuser 连接(本测试),GUC 也应被设上(superuser 绕 RLS 不影响 GUC 本身)。
    """
    with system_identity():
        db = SessionLocal()
        try:
            # 应能查询 tenant 作用域表(若 DB 有数据),不 fail-closed 报错
            n = db.execute(text("SELECT count(*) FROM positions")).scalar()
            assert n >= 0, "系统身份下查 positions 不应报错(穿透 RLS)"
            # 验证 admin GUC 被 begin 钩子设上(这是 RLS 短路的依据)
            guc = db.execute(
                text("SELECT current_setting('app.is_admin', true)")
            ).scalar()
            assert guc == "on", f"system_identity 下 app.is_admin 应为 'on',实际 {guc!r}"
        finally:
            try:
                db.rollback()
            except Exception:
                pass
            db.close()


def test_without_identity_guc_not_admin():
    """对照:未设 system_identity 时,app.is_admin GUC 不为 'on'(默认 False)。"""
    # 确保进入前 is_admin 为默认 False
    is_admin_var.set(False)
    db = SessionLocal()
    try:
        guc = db.execute(text("SELECT current_setting('app.is_admin', true)")).scalar()
        # 默认未设时 current_setting(..., true) 返回 NULL/空,绝非 'on'
        assert guc != "on", f"未设身份时 app.is_admin 不应为 'on',实际 {guc!r}"
    finally:
        try:
            db.rollback()
        except Exception:
            pass
        db.close()
