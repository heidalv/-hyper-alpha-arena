# backend/tests/integration/test_rls_isolation.py
"""阶段3 Task 3.3 RLS 跨租户隔离验证(最关键的安全测试)。

测试什么
--------
``backend/alembic/versions/0005_rls_policies.py`` 在每张租户作用域表上建的
``tenant_isolation`` policy:
    USING/WITH CHECK (
        <col> = current_setting('app.tenant_id', true)::int   -- 本租户
        OR <col> IS NULL                                       -- 全局行
        OR current_setting('app.is_admin', true) = 'on'        -- admin 穿透
    )

为什么不能用 SessionLocal() 直接测(关键)
-----------------------------------------
生产/开发库的 Alembic 连接角色(laobao)在 PostgreSQL 里是 **superuser**
(见 ``SELECT rolsuper FROM pg_roles WHERE rolname='laobao'``)。PostgreSQL 硬规则:
**superuser 与 BYPASSRLS 角色永远绕过 RLS,FORCE 也覆盖不了。** 因此用
``SessionLocal()``(连 laobao)跑 ``SELECT count(*) FROM positions``,无论设不设
``app.tenant_id``,看到的都是全部行 —— 这会"测过"但根本没证明 RLS 生效(假安全)。

正确的做法
----------
创建一个**非 superuser** 的测试角色 ``rls_test_tenant``,授予 schema USAGE +
目标表 SELECT,再用一个连该角色的独立 engine 验证 RLS 真的过滤行。非 superuser
身份下 policy 才会真正参与查询规划。这是整个阶段3 的"铁证":即便应用层漏写
WHERE,DB 也拦得住跨租户访问。

两张被测表
----------
- ``positions``(A.1,tenant_id NOT NULL):测本租户/跨租户/admin/fail-closed。
  它没有 NULL tenant 行,故这里不验证"全局行"分支。
- ``dingtalk_notifications``(A.1 nullable,tenant_id 可空):专门测
  ``tenant_id IS NULL`` 的全局行分支(NULL 行对所有租户可见)。

仅在 PostgreSQL 上运行(SQLite 无 RLS/GUC 概念)。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from backend.database.connection import DATABASE_URL, engine


pytestmark = pytest.mark.skipif(
    not (DATABASE_URL.lower().startswith("postgresql")
         or DATABASE_URL.lower().startswith("postgres")),
    reason="RLS 是 PostgreSQL 特性,SQLite 无此概念,跳过",
)


# 测试专用非 superuser 角色(密码/名称写死,测试自身负责建/删)。
_TEST_ROLE = "rls_test_tenant"
_TEST_PW = "rls_test_pw_2026"
_TENANT_A = 777001
_TENANT_B = 777002
_GLOBAL_TENANT = None  # tenant_id IS NULL 的全局行
_TEST_TABLES = ("positions", "dingtalk_notifications")


def _as_test_role():
    """用非 superuser 测试角色连 alpha_arena,返回新 engine。

    强制 host=127.0.0.1:本机 PG 的 pg_hba 在 IPv4 环回上对 scram-sha-256 放行,
    而 ``localhost`` 会被 psycopg 同时解析为 ::1 与 127.0.0.1,某些 IPv6 规则
    配置下对新创建的 LOGIN 角色认证失败(实测 laobao 走 localhost 可连,新建
    角色走 localhost 失败、走 127.0.0.1 成功)。固定 IPv4 环回最稳。

    注意:必须把 URL **对象**直接传给 create_engine,不能用 str(url) ——
    SQLAlchemy 1.4+ 的 URL.__str__ 会把密码渲染成 '***' (防日志泄漏),传字符串
    进 create_engine 会让密码变成字面量 '***',导致认证失败。
    """
    u = make_url(DATABASE_URL)
    test_url = u.set(
        username=_TEST_ROLE, password=_TEST_PW, host="127.0.0.1"
    )
    return create_engine(test_url, pool_pre_ping=True)


@pytest.fixture(scope="module")
def test_role():
    """建立非 superuser 测试角色 + 授权。模块级,整组测试复用。

    用主连接(laobao superuser)建角色、授权。该角色对两张被测表只有 SELECT,
    且因为是普通角色 → 受 RLS 约束。"""
    # CREATE ROLE 的 PASSWORD 子句不接受绑定参数(与 SET LOCAL 同样的 PG 限制),
    # 故把名字/密码内联。两者均为本文件写死的常量,无注入面。
    # 注意 SQLAlchemy 2.0 autobegin:Connection 退出时不会自动提交,DDL 必须
    # 显式 commit(),否则 CREATE ROLE 被回滚 → 角色不存在 → 测试连接失败。
    with engine.connect() as c:
        c.execute(text(f"DROP ROLE IF EXISTS {_TEST_ROLE}"))
        c.execute(
            text(
                f"CREATE ROLE {_TEST_ROLE} LOGIN PASSWORD '{_TEST_PW}' "
                "NOSUPERUSER NOBYPASSRLS"
            )
        )
        c.execute(text("GRANT USAGE ON SCHEMA public TO " + _TEST_ROLE))
        for t in _TEST_TABLES:
            c.execute(text(f"GRANT SELECT ON {t} TO {_TEST_ROLE}"))
        c.commit()
    yield _TEST_ROLE
    # teardown:收回权限再删角色
    with engine.connect() as c:
        try:
            for t in _TEST_TABLES:
                c.execute(text(f"REVOKE SELECT ON {t} FROM {_TEST_ROLE}"))
            c.execute(text("REVOKE USAGE ON SCHEMA public FROM " + _TEST_ROLE))
        except Exception:
            pass
        c.commit()
        try:
            c.execute(text("DROP ROLE IF EXISTS " + _TEST_ROLE))
            c.commit()
        except Exception:
            pass


@pytest.fixture()
def seeded_rows():
    """为每个测试插入固定 tenant_id 的诱饵行,测后清理。

    用 superuser(laobao)连接写入 → superuser 绕过 RLS,可直接插入任意
    tenant_id 的行(含 NULL)。

    positions(tenant_id NOT NULL):BTC-A/ETH-A 属 A,SOL-B 属 B。
    dingtalk_notifications(tenant_id NULLABLE):DING-A 属 A,DING-NULL 为全局。
    """
    pos_rows = [
        ("BTC-A", _TENANT_A),
        ("ETH-A", _TENANT_A),
        ("SOL-B", _TENANT_B),
    ]
    ding_rows = [
        ("DING-A", _TENANT_A),
        ("DING-NULL", _GLOBAL_TENANT),  # 全局行:对所有租户可见
    ]
    with engine.connect() as c:
        for symbol, tenant in pos_rows:
            c.execute(
                text(
                    "INSERT INTO positions (version, account_id, symbol, name, "
                    "market, quantity, available_quantity, avg_cost, tenant_id) "
                    "VALUES ('0', 1, :sym, :sym, 'spot', 0, 0, 0, :tid)"
                ),
                {"sym": symbol, "tid": tenant},
            )
        for symbol, tenant in ding_rows:
            c.execute(
                text(
                    "INSERT INTO dingtalk_notifications "
                    "(event_type, content, symbol, tenant_id) "
                    "VALUES ('test', :content, :sym, :tid)"
                ),
                # content(text) 与 symbol(varchar) 类型不同,用独立参数避免
                # psycopg 的 AmbiguousParameter 推断冲突。
                {"content": symbol, "sym": symbol, "tid": tenant},
            )
        c.commit()  # SA 2.0:必须显式提交,否则插入被回滚
    yield pos_rows, ding_rows
    # 清理:按本测试专用的 symbol 删(superuser 绕 RLS)
    with engine.connect() as c:
        c.execute(
            text(
                "DELETE FROM positions WHERE symbol IN "
                "('BTC-A','ETH-A','SOL-B')"
            )
        )
        c.execute(
            text(
                "DELETE FROM dingtalk_notifications WHERE symbol IN "
                "('DING-A','DING-NULL')"
            )
        )
        c.commit()


def _query_as(table, col, tenant_id, is_admin=False, what="symbol"):
    """以非 superuser 测试角色连接,设 GUC 后查目标表可见的 what 列集合。

    - tenant_id=int → SET LOCAL app.tenant_id='<int>'
    - tenant_id=None → 不设(模拟无身份)
    - is_admin=True → 额外 SET LOCAL app.is_admin='on'
    """
    eng = _as_test_role()
    try:
        with eng.connect() as c:
            if tenant_id is not None:
                c.execute(text("SET LOCAL app.tenant_id = '" + str(int(tenant_id)) + "'"))
            if is_admin:
                c.execute(text("SET LOCAL app.is_admin = 'on'"))
            rows = c.execute(text(f"SELECT {what} FROM {table}")).fetchall()
        return {r[0] for r in rows}
    finally:
        eng.dispose()


def _count_as(table, tenant_id, is_admin=False):
    """以非 superuser 测试角色连接,设 GUC 后查目标表可见行数。"""
    eng = _as_test_role()
    try:
        with eng.connect() as c:
            if tenant_id is not None:
                c.execute(text("SET LOCAL app.tenant_id = '" + str(int(tenant_id)) + "'"))
            if is_admin:
                c.execute(text("SET LOCAL app.is_admin = 'on'"))
            n = c.execute(text(f"SELECT count(*) FROM {table}")).scalar()
        return n
    finally:
        eng.dispose()


# ─── positions:本租户 / 跨租户 / fail-closed ───


def test_cross_tenant_blocked(test_role, seeded_rows):
    """核心断言:租户 A 只看到自己的 positions,看不到 B 的行。

    若失败(出现 B 的 SOL-B),说明 RLS 没过滤 → 跨租户泄漏。
    """
    seen = _query_as("positions", "tenant_id", _TENANT_A)
    assert "SOL-B" not in seen, (
        f"RLS 泄漏!租户 {_TENANT_A} 看到了租户 {_TENANT_B} 的行 {seen!r}"
    )
    assert {"BTC-A", "ETH-A"} <= seen, (
        f"RLS 过滤过度?租户 {_TENANT_A} 看不到自己的行 {seen!r}"
    )
    assert _count_as("positions", _TENANT_A) == 2


def test_other_tenant_sees_nothing_of_a(test_role, seeded_rows):
    """租户 B 看不到租户 A 的任何行(对称验证)。"""
    seen = _query_as("positions", "tenant_id", _TENANT_B)
    assert "BTC-A" not in seen and "ETH-A" not in seen, (
        f"RLS 泄漏!租户 B 看到了 A 的行 {seen!r}"
    )
    assert seen == {"SOL-B"}, f"租户B 应只见 SOL-B,实际 {seen!r}"


def test_fail_closed_without_identity(test_role, seeded_rows):
    """无 tenant_id(fail-closed):positions 看不到任何行。

    positions.tenant_id 是 NOT NULL(无全局行),故无身份时应 0 行。
    安全底线:未认证/运维通道误查,不泄漏任何租户数据。
    """
    seen = _query_as("positions", "tenant_id", None)
    assert seen == set(), (
        f"fail-closed 失败!无身份却看到 positions {seen!r}"
    )
    assert _count_as("positions", None) == 0


def test_admin_bypass_sees_all_positions(test_role, seeded_rows):
    """is_admin=on 穿透 RLS:positions 看到全部 3 行,不限本租户。"""
    n = _count_as("positions", None, is_admin=True)  # 无 tenant 的 admin 也全见
    assert n == 3, f"admin 应穿透 RLS 看到 positions 全部 3 行,实际 {n}"


# ─── dingtalk_notifications:全局行(NULL tenant)分支 ───


def test_global_null_row_visible_to_all(test_role, seeded_rows):
    """tenant_id IS NULL 的全局行,所有租户都应看到。

    这是用户确认的设计:全局行(无属主)对每个租户可见。
    租户 A 和 B 都应看到 DING-NULL。
    """
    seen_a = _query_as("dingtalk_notifications", "symbol", _TENANT_A)
    seen_b = _query_as("dingtalk_notifications", "symbol", _TENANT_B)
    assert "DING-NULL" in seen_a, f"全局行应被租户A看到 {seen_a!r}"
    assert "DING-NULL" in seen_b, f"全局行应被租户B看到 {seen_b!r}"
    # 租户 A 还看到自己的 DING-A;B 不应看到 DING-A(跨租户隔离)
    assert "DING-A" in seen_a, f"租户A 应看到自己的 DING-A {seen_a!r}"
    assert "DING-A" not in seen_b, (
        f"RLS 泄漏!租户 B 看到了 A 的 DING-A {seen_b!r}"
    )


def test_fail_closed_still_sees_global_rows(test_role, seeded_rows):
    """无身份时:有属主的行被隐藏,但全局行(NULL)仍可见(policy 的 NULL 分支)。

    这与 positions 的 fail-closed 形成对照:positions 无 NULL 行 → 0 行;
    dingtalk 有 NULL 行 → 仅 NULL 行可见。
    """
    seen = _query_as("dingtalk_notifications", "symbol", None)
    assert "DING-A" not in seen, (
        f"fail-closed 失败!无身份却看到有属主的 DING-A {seen!r}"
    )
    assert "DING-NULL" in seen, (
        f"无身份时应仍看到全局 NULL 行(设计如此) {seen!r}"
    )
