# backend/tests/integration/test_e2e_trading_under_rls.py
"""C1 修复的端到端铁证:后台交易循环在**非 superuser + 真实 RLS** 下能查到行。

测什么(为什么这个文件存在)
----------------------------
最终评审指出 C1 修复(后台循环 ``set_system_identity()`` → ``app.is_admin='on'``
→ RLS 短路)只有 GUC 单元测试,**没有任何测试证明真实的交易查询在真实的 RLS
下能返回行**。这是最大的测试缺口 —— C1 是 ship blocker,如果修复在
非 superuser 角色下失效,交易会静默 0 行而无人察觉。

本文件用与 ``test_rls_isolation.py`` 相同的 ``NOSUPERUSER NOBYPASSRLS`` 角色模式
(不是 superuser laobao —— superuser 永远绕过 RLS,测了等于没测),填这个缺口:

1. **system_identity 下后台查 paper_positions 见行**(C1 修复有效)。
2. **无 system_identity fail-closed(0 行)** —— 证明 RLS 真生效 + 修复是
   load-bearing(去掉修复就坏,不是 no-op)。
3. **HTTP tenant 隔离在非 superuser 下正常**(本租户可见、跨租户不可见)。
4. **TradeGate 方向冲突检查在 system_identity 下见既有仓位** —— 两重构
   (trade gate + RLS)的交叉点:gate 查 positions 防幽灵反向单,若 system_identity
   没生效,gate 查不到既有仓位 → 方向冲突漏检 → 幽灵反向单通过。

关键实现细节:hook-on-test-engine
--------------------------------
本测试为非 superuser 角色建**独立 engine**(``create_engine(role_url)``)。但
模块级的 ``engine``/``market_engine``/``analytics_engine`` 的 ``begin`` 事件钩子
(``_install_tenant_rls_hook`` 注册)只挂在**那三个** engine 上,**不会**自动挂到
测试新建的 engine 上。若直接用裸测试 engine,``ContextVar → 钩子 → SET LOCAL``
的链条在测试 engine 上断了 —— GUC 不被设,测试无论输赢都不证明 C1。

选择**方案 (a)**:对测试 engine 显式调用 ``_install_tenant_rls_hook(test_engine)``,
把**真实的生产钩子**挂上去。这样测试 engine 的每个事务 begin 都会读
``is_admin_var``/``tenant_id_var`` 并设 GUC —— 与生产 background loop 走完全相同
的代码路径,只是连接身份换成了非 superuser 角色。这比方案 (b)(用模块 engine +
``SET ROLE``)更干净:它真实复现"非 superuser 角色 + 生产钩子"的组合,且不污染
全局连接池。

仅在 PostgreSQL 上运行(SQLite 无 RLS/GUC 概念)。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from backend.database.connection import DATABASE_URL, engine, _install_tenant_rls_hook
from backend.core.tenant import (
    clear_request_identity,
    set_request_identity,
    set_system_identity,
)
from backend.services.trade_gate import TradeGate


pytestmark = pytest.mark.skipif(
    not (DATABASE_URL.lower().startswith("postgresql")
         or DATABASE_URL.lower().startswith("postgres")),
    reason="RLS 是 PostgreSQL 特性,SQLite 无此概念,跳过",
)


# ─── 测试专用非 superuser 角色(与 test_rls_isolation 隔离,避免相互干扰)───
_TEST_ROLE = "rls_test_e2e"
_TEST_PW = "rls_test_e2e_pw_2026"
# 默认租户 = default 用户(0004 迁移回填的属主);account_id=1 属该用户。
_DEFAULT_TENANT = 1
_DEFAULT_ACCOUNT = 1
_OTHER_TENANT = 999001  # 不存在的租户,用于验证跨租户隔离
_TEST_SYMBOL = "RLS-E2E-TEST"
_TEST_TABLES = ("paper_positions",)


def _insert_position_sql(symbol: str, tenant_id: int, side: str = "long") -> str:
    """构造一条 paper_positions 插入语句(superuser 绕 RLS 直接写)。

    覆盖所有 NOT NULL 且无 server_default 的列(mark_price/leverage/margin/
    unrealized_pnl/liquidation_price/partial_*/dca_*/reduce_count/peak_* 等)。
    """
    return (
        "INSERT INTO paper_positions (account_id, symbol, side, size, entry_price, "
        "mark_price, leverage, margin, unrealized_pnl, liquidation_price, "
        "partial_realized_pnl, partial_fee_paid, tp_level_reached, add_count, dca_count, "
        "dca_total_added, reduce_count, peak_unrealized_pnl, peak_pnl_pct, tenant_id, status) "
        f"VALUES ({_DEFAULT_ACCOUNT}, '{symbol}', '{side}', 1.0, 100.0, 100.0, 1.0, "
        f"0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0.0, 0, 0.0, 0.0, {int(tenant_id)}, 'open')"
    )


def _build_test_engine():
    """用非 superuser 角色连 alpha_arena,返回**已注册生产 RLS 钩子**的新 engine。

    强制 host=127.0.0.1:见 test_rls_isolation._as_test_role 的说明(IPv6 pg_hba
    对新建 LOGIN 角色认证不稳)。URL 对象直接传 create_engine,不要 str(url) ——
    SA 2.0 的 __str__ 会把密码渲染成 '***' 导致认证失败。
    """
    u = make_url(DATABASE_URL).set(
        username=_TEST_ROLE, password=_TEST_PW, host="127.0.0.1"
    )
    eng = create_engine(u, pool_pre_ping=True)
    # 方案 (a):把生产 begin 钩子挂到测试 engine,使 ContextVar → SET LOCAL 链条
    # 在非 superuser 连接上真实生效。这是本测试有效性的关键 —— 不挂则 GUC 不设,
    # 测试无论输赢都不证明 C1 修复。
    _install_tenant_rls_hook(eng)
    return eng


@pytest.fixture(scope="module")
def test_role():
    """建立非 superuser 测试角色 + 授权。模块级,整组测试复用。

    与 test_rls_isolation 的 rls_test_tenant **分开**(独立角色),避免两套测试
    并发跑时权限/角色生命周期相互干扰。用主连接(laobao superuser)建角色、授权。
    """
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
def seeded_position():
    """为每个测试插入一条 tenant_id=default 的诱饵 position,测后清理。

    用 superuser(laobao)写 → superuser 绕 RLS,可直接写任意 tenant_id。
    清理按专用 symbol 删(superuser 绕 RLS)。
    """
    with engine.connect() as c:
        c.execute(text(f"DELETE FROM paper_positions WHERE symbol='{_TEST_SYMBOL}'"))
        c.execute(text(_insert_position_sql(_TEST_SYMBOL, _DEFAULT_TENANT, "long")))
        c.commit()
    yield
    with engine.connect() as c:
        c.execute(text(f"DELETE FROM paper_positions WHERE symbol='{_TEST_SYMBOL}'"))
        c.commit()


@pytest.fixture(autouse=True)
def _reset_identity():
    """每个测试前后清空 ContextVar,防上一个测试的 is_admin/tenant 残留。

    ContextVar 是进程级状态(set 后跨测试持续),不清会让"无身份"测试意外带上
    前一个测试的 admin 穿透 → 假通过。autouse 确保每个测试干净起步。
    """
    clear_request_identity()
    yield
    clear_request_identity()


# ═══════════════════════════════════════════════════════════════════
# 测试 1:C1 修复在非 superuser RLS 下有效(system_identity 见行)
# ═══════════════════════════════════════════════════════════════════


def test_background_query_sees_rows_with_system_identity(test_role, seeded_position):
    """C1 修复铁证:非 superuser + set_system_identity 下,后台查 paper_positions
    能见到 superuser 灌的行。

    场景:后台交易循环(非 HTTP 线程)``set_system_identity()`` → begin 钩子读
    ``is_admin_var=True`` → ``SET LOCAL app.is_admin='on'`` → RLS 策略短路
    (``current_setting('app.is_admin', true) = 'on'`` 分支命中)→ 见全部行。

    若此测试失败(0 行),说明 C1 修复在非 superuser 下没生效 —— 钩子没在测试
    engine 上注册(检查 _install_tenant_rls_hook),或 RLS 策略没把 is_admin 分支
    纳入(检查 0005 迁移)。这是 ship blocker 的核心证据。
    """
    eng = _build_test_engine()
    try:
        set_system_identity()  # is_admin_var=True;tenant_id 保持 None(系统跨租户)
        with eng.connect() as c:
            n = c.execute(
                text(f"SELECT count(*) FROM paper_positions WHERE symbol='{_TEST_SYMBOL}'")
            ).scalar()
        assert n > 0, (
            "C1 修复在非 superuser RLS 下失效!后台 system_identity 查 paper_positions "
            f"为 0 行(实际 {n})。交易循环会静默 0 行破坏交易。"
        )
    finally:
        eng.dispose()


# ═══════════════════════════════════════════════════════════════════
# 测试 2:无 system_identity fail-closed(证明 RLS 真生效 + 修复 load-bearing)
# ═══════════════════════════════════════════════════════════════════


def test_background_query_fail_closed_without_system_identity(test_role, seeded_position):
    """RLS 真生效 + C1 修复是 load-bearing 的反证。

    不调 ``set_system_identity``(模拟"忘了加修复"或"后台循环漏写")→ ContextVar
    is_admin=False/tenant=None → begin 钩子不设任何 GUC →
    ``current_setting('app.is_admin', true)`` 返回 NULL ≠ 'on' → RLS 不短路 →
    paper_positions.tenant_id NOT NULL(无全局行)→ fail-closed 0 行。

    若此测试显示 > 0,说明要么角色是 superuser(检查 CREATE ROLE NOSUPERUSER),
    要么 RLS 没启用(检查 ENABLE/FORCE ROW LEVEL SECURITY)—— 测试无效。
    与测试 1 配对:测试 1 证明"加修复就好",测试 2 证明"不加就坏",两者合起来
    铁证 C1 修复是必需且有效的,不是 no-op 也不是假通过。
    """
    eng = _build_test_engine()
    try:
        # 不调 set_system_identity(关键:模拟无身份的后台线程)
        with eng.connect() as c:
            n = c.execute(
                text(f"SELECT count(*) FROM paper_positions WHERE symbol='{_TEST_SYMBOL}'")
            ).scalar()
        assert n == 0, (
            f"RLS 没真生效!无 system_identity 却看到 paper_positions {n} 行。"
            "要么角色是 superuser/BYPASSRLS,要么 RLS 未启用 —— 测试假通过。"
        )
    finally:
        eng.dispose()


# ═══════════════════════════════════════════════════════════════════
# 测试 3:HTTP 请求式 tenant 隔离在非 superuser 下正常(本租户可见/跨租户不可)
# ═══════════════════════════════════════════════════════════════════


def test_http_request_tenant_isolation_under_non_superuser(test_role, seeded_position):
    """HTTP 请求路径的 tenant 隔离在非 superuser 下正常工作。

    ``set_request_identity(tenant=default)``(HTTP 中间件风格,**不**是 system)
    → begin 钩子设 ``SET LOCAL app.tenant_id='1'`` → RLS 命中本租户分支
    (``tenant_id = current_setting('app.tenant_id', true)::int``)→ 见自己租户行。
    换成不存在的租户 → 看不到。

    与 test_rls_isolation 轻度重叠,但这里特意用**非 superuser 角色 + 生产 begin
    钩子**(而非裸 SET LOCAL),证明 HTTP 路径在真实非 superuser 部署下也走通。
    """
    eng = _build_test_engine()
    try:
        # 本租户可见
        set_request_identity(_DEFAULT_TENANT)  # tenant=1, is_admin=False
        with eng.connect() as c:
            n_own = c.execute(
                text(f"SELECT count(*) FROM paper_positions WHERE symbol='{_TEST_SYMBOL}'")
            ).scalar()
        assert n_own == 1, (
            f"非 superuser + request_identity(tenant={_DEFAULT_TENANT}) 应见本租户 1 行,"
            f"实际 {n_own}"
        )

        # 换成不存在的租户 → 看不到(fail-closed 到他人数据)
        clear_request_identity()
        set_request_identity(_OTHER_TENANT)  # tenant=999001,非本行属主
        with eng.connect() as c:
            n_other = c.execute(
                text(f"SELECT count(*) FROM paper_positions WHERE symbol='{_TEST_SYMBOL}'")
            ).scalar()
        assert n_other == 0, (
            f"跨租户泄漏!非 superuser + request_identity(tenant={_OTHER_TENANT}) "
            f"不应看到属 tenant={_DEFAULT_TENANT} 的行,实际 {n_other} 行"
        )
    finally:
        eng.dispose()


# ═══════════════════════════════════════════════════════════════════
# 测试 4:TradeGate 方向冲突检查在 system_identity 下见既有仓位
#         (两重构:trade gate + RLS 的交叉点)
# ═══════════════════════════════════════════════════════════════════


def test_trade_gate_position_query_under_system_identity(test_role, seeded_position):
    """TradeGate(交易统一闸)查 positions 防方向冲突,在 system_identity + 非 superuser
    下能见到既有仓位。

    场景:既有 LONG 仓位(seeded_position)→ 下 SELL 单 → gate.check 查到既有 LONG
    → 方向冲突 → ``allowed=False``。这覆盖两个重构的交叉点:
      - TradeGate(trade-execution-unified-gate):所有下单必经的闸,查 positions
        防幽灵反向单。
      - C1 RLS(system_identity):后台循环 set_system_identity 穿透 RLS。

    若 system_identity 没生效,gate 查 positions 会 fail-closed 0 行 → 查不到既有
    LONG → 方向冲突漏检 → SELL 单被放行 → 幽灵反向单开仓(系统失控)。本测试
    断言 gate **看到**既有仓位并**阻断**反向单,证明交叉点工作正常。

    对照:另起一个**不带** system_identity 的 session,同样调用 gate.check →
    既有仓位不可见 → 放行(这正是 C1 没修时的行为,证明修复 load-bearing)。
    """
    eng = _build_test_engine()
    TestSession = sessionmaker(bind=eng)
    try:
        # ── system_identity 下:gate 见既有 LONG → SELL 被阻断 ──
        set_system_identity()
        db = TestSession()
        try:
            gate = TradeGate()  # 独立实例,避免单例锁污染
            dec = gate.check(
                db, account_id=_DEFAULT_ACCOUNT, symbol=_TEST_SYMBOL,
                side="sell", leverage=1.0, tier=None,
            )
            assert not dec.allowed, (
                f"TradeGate 应阻断反向单(direction_conflict),但 allowed={dec.allowed}。"
                "说明 system_identity 没让 gate 查到既有仓位 → 交叉点失效。"
            )
            assert "direction_conflict" in dec.reason, (
                f"gate 阻断但理由不对,期望 direction_conflict,实际 {dec.reason!r}"
            )
        finally:
            db.close()
        clear_request_identity()

        # ── 对照:无 system_identity → gate 查不到既有仓位 → 放行(C1 未修时的坏行为)──
        db2 = TestSession()
        try:
            gate2 = TradeGate()
            dec2 = gate2.check(
                db2, account_id=_DEFAULT_ACCOUNT, symbol=_TEST_SYMBOL,
                side="sell", leverage=1.0, tier=None,
            )
            assert dec2.allowed, (
                "无 system_identity 下 gate 应 fail-closed 查不到仓位而放行(这是 C1 "
                f"未修时的坏行为,作为对照)。实际 allowed={dec2.allowed},reason={dec2.reason!r}"
            )
        finally:
            db2.close()
    finally:
        eng.dispose()
