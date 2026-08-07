# backend/tests/integration/test_rls_after_commit.py
"""阶段3 Task 3.2 致命陷阱守卫测试。

测试对象
--------
``backend/database/connection.py`` 的 ``_install_tenant_rls_hook`` 注册的
``begin`` 事件钩子 + ``backend/core/tenant.py`` 的 ContextVar。

为什么这是整个阶段3最关键的测试
-------------------------------
代码库有 521 处 ``db.commit()``。SQLAlchemy 2.0 autobegin 模式下,每次 commit
结束当前事务,``SET LOCAL app.tenant_id`` 随之失效。下一次查询 autobegin 一个
新事务,此时 GUC 已经没了 → ``current_setting('app.tenant_id', true)`` 返回 NULL
→ RLS 策略 fail-closed(隐藏行)或更糟(泄漏跨租户数据)。这是**静默数据损坏**,
不报错,极难发现。

本测试证明:多次 commit 后,GUC 仍被正确设置(因为 ``begin`` 事件在每次
autobegin 时重新设 SET LOCAL)。若此测试失败,说明钩子没在每次事务开始时触发,
必须修好才能继续阶段3。

仅在 PostgreSQL 上运行(SQLite 没有 SET LOCAL / GUC 概念)。
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from backend.core.tenant import set_request_identity, clear_request_identity
from backend.database.connection import DATABASE_URL, SessionLocal


pytestmark = pytest.mark.skipif(
    not (DATABASE_URL.lower().startswith("postgresql")
         or DATABASE_URL.lower().startswith("postgres")),
    reason="RLS GUC (SET LOCAL) 是 PostgreSQL 特性,SQLite 无此概念,跳过",
)


def test_tenant_guc_persists_across_commits():
    """根因验证:多次 commit 后,SET LOCAL app.tenant_id 仍生效(致命陷阱守卫)。

    若用一次性 SET LOCAL(如只在 get_db yield 时设),首次 commit 后 GUC 失效,
    current_setting 返回 NULL/空。begin 钩子应在每次事务开始(含 commit 后的
    autobegin)重设。这里连做 3 个事务来覆盖。
    """
    set_request_identity(tenant_id=999999, role="user")  # 测试专用租户
    try:
        db = SessionLocal()
        try:
            # 事务 1(autobegin)
            r1 = db.execute(
                text("SELECT current_setting('app.tenant_id', true) AS v")
            ).scalar()
            assert r1 == "999999", f"事务1 GUC 应为 '999999',实际 {r1!r}"

            db.commit()  # 结束事务 1 → 一次性 SET LOCAL 在此失效

            # 事务 2(autobegin)—— 这是致命陷阱的关键检查点
            r2 = db.execute(
                text("SELECT current_setting('app.tenant_id', true) AS v")
            ).scalar()
            assert r2 == "999999", (
                f"事务2 GUC 仍应为 '999999'(致命陷阱!commit 后 GUC 失效了),"
                f"实际 {r2!r}"
            )
            db.commit()

            # 事务 3 —— 再来一次确认稳定
            r3 = db.execute(
                text("SELECT current_setting('app.tenant_id', true) AS v")
            ).scalar()
            assert r3 == "999999", f"事务3 GUC 仍应为 '999999',实际 {r3!r}"
        finally:
            try:
                db.rollback()
            except Exception:
                pass
            db.close()
    finally:
        clear_request_identity()


def test_tenant_guc_reflects_different_tenants():
    """切换 ContextVar 的 tenant_id 后,新事务的 GUC 跟着变(动态读)。

    证明钩子不是缓存了第一次的值,而是每次事务开始都从 ContextVar 动态读。
    """
    db = SessionLocal()
    try:
        # 租户 A
        set_request_identity(tenant_id=111111, role="user")
        r_a = db.execute(
            text("SELECT current_setting('app.tenant_id', true) AS v")
        ).scalar()
        assert r_a == "111111", f"租户A GUC 应为 '111111',实际 {r_a!r}"
        db.commit()

        # 切换到租户 B(新事务)
        set_request_identity(tenant_id=222222, role="user")
        r_b = db.execute(
            text("SELECT current_setting('app.tenant_id', true) AS v")
        ).scalar()
        assert r_b == "222222", (
            f"切换后租户B GUC 应为 '222222'(动态读 ContextVar),实际 {r_b!r}"
        )
    finally:
        try:
            db.rollback()
        except Exception:
            pass
        db.close()
        clear_request_identity()


def test_tenant_guc_unset_when_no_identity():
    """无身份时 GUC 未设(current_setting 返回 NULL/空字符串)。

    未认证 / 运维通道 / 全局请求:set_request_identity(None) → 钩子跳过 SET LOCAL
    → current_setting('app.tenant_id', true) 第二参数 true 表示 "缺失时返回 NULL
    而非报错"。RLS 策略据此把 NULL 当 "无租户上下文" 处理。
    """
    clear_request_identity()  # 显式清空(也是默认状态)
    db = SessionLocal()
    try:
        r = db.execute(
            text("SELECT current_setting('app.tenant_id', true) AS v")
        ).scalar()
        # PG: NULL 或空字符串均视为 "未设置"。不同 PG 版本/驱动对 "从未设过"
        # 的 GUC 返回 NULL,但对 "设过又因 commit 回滚" 的可能返回 ''。两者都接受。
        assert r is None or r == "", f"无身份时 GUC 应为 NULL/空,实际 {r!r}"
    finally:
        try:
            db.rollback()
        except Exception:
            pass
        db.close()


def test_is_admin_guc_set_when_admin_role():
    """admin 角色 SET LOCAL app.is_admin = 'on'(阶段4 穿透接口,先验证连通)。"""
    set_request_identity(tenant_id=999999, role="admin")
    try:
        db = SessionLocal()
        try:
            r = db.execute(
                text("SELECT current_setting('app.is_admin', true) AS v")
            ).scalar()
            assert r == "on", f"admin 角色 GUC 应为 'on',实际 {r!r}"
        finally:
            try:
                db.rollback()
            except Exception:
                pass
            db.close()
    finally:
        clear_request_identity()
