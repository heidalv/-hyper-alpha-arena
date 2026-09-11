"""RLS 测试的前置权限检测。

[2026-09-02] 背景
-----------------
RLS 系列测试（test_rls_isolation / test_admin_rls_bypass /
test_e2e_trading_under_rls / test_llm_config_byok / test_rls_after_commit）的
fixture 必须 CREATE ROLE 一个**非 superuser** 角色才能真正验证行级安全 ——
PostgreSQL 硬规则：superuser 与 BYPASSRLS 永远绕过 RLS，用特权账号去查会
"测过"但什么都没证明（假安全）。

这些用例的文档写于连接角色是 db_admin(superuser) 的时期。现在应用运行时账号
是 laobao：`rolsuper=false, rolcreaterole=false`。于是 fixture 首句
`DROP ROLE IF EXISTS ...` 直接抛 InsufficientPrivilege，整组共 19 个用例报
ERROR。

为什么不给 laobao 授 CREATEROLE
-------------------------------
那样确实能让这 19 个立刻变绿，但 laobao 是**应用运行时**账号，backend 全程用
它连库。给它建角色的能力等于给应用开了提权面：一旦应用被攻破，攻击者可自建
角色。而 RLS 存在的目的正是防越权 —— 为了测试变绿去削弱被测的安全模型，是本
末倒置。应用账号维持最小权限是对的，测试应当适配它。

如何在本机真正跑这批测试
------------------------
用一个具备 CREATEROLE 的独立账号，通过环境变量提供（**只在测试时设置，不要写
进 .env**）：

    $env:RLS_TEST_DATABASE_URL = "postgresql+psycopg://postgres:<pw>@127.0.0.1:5432/alpha_arena"
    pytest backend/tests/integration/test_rls_isolation.py

未设置且当前账号无权限时，这批用例 skip（而非 ERROR）——区分"环境不具备"与
"功能坏了"，避免真实回归被淹没在长期红灯里。
"""
from __future__ import annotations

import os
from typing import Optional

import pytest
from sqlalchemy import text


_PRIV_URL_ENV = "RLS_TEST_DATABASE_URL"
_cached: Optional[bool] = None


def privileged_database_url() -> str:
    """建角色用的连接串：优先环境变量，回退应用默认连接。"""
    override = (os.getenv(_PRIV_URL_ENV) or "").strip()
    if override:
        return override
    from backend.database.connection import DATABASE_URL
    return DATABASE_URL


def can_create_roles() -> bool:
    """当前用于建角色的连接是否真的具备该权限（结果缓存，避免重复往返）。"""
    global _cached
    if _cached is not None:
        return _cached
    _cached = False
    try:
        from sqlalchemy import create_engine

        eng = create_engine(privileged_database_url(), pool_pre_ping=True)
        try:
            with eng.connect() as c:
                _cached = bool(c.execute(text(
                    "SELECT rolsuper OR rolcreaterole "
                    "FROM pg_roles WHERE rolname = current_user"
                )).scalar())
        finally:
            eng.dispose()
    except Exception:
        _cached = False
    return _cached


SKIP_REASON = (
    "RLS 测试需要 CREATE ROLE 权限来建非 superuser 角色（否则测的是绕过 RLS 的"
    f"特权账号=假安全）。当前连接账号无此权限，且未设 {_PRIV_URL_ENV}。"
    f"启用方式：$env:{_PRIV_URL_ENV}='postgresql+psycopg://postgres:<pw>@127.0.0.1:5432/alpha_arena'。"
    "刻意不给应用运行时账号授 CREATEROLE —— 那会给应用开提权面，与 RLS 的防越权"
    "目的相悖。详见 backend/tests/integration/_rls_privileges.py"
)

requires_role_creation = pytest.mark.skipif(
    not can_create_roles(), reason=SKIP_REASON,
)
