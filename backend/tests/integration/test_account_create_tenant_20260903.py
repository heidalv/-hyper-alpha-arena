"""[2026-09-03 M1-1d] API 新建账户在 FORCE RLS 下的三件套必须建成且归属正确。

复现的病症（backend.log 04:59:03，账户 196）：
  Failed to create initial account snapshot ... InsufficientPrivilege（RLS WITH CHECK 拒）
  Failed to auto-create strategy ... 'trigger_mode' is an invalid keyword argument
  Failed to auto-create risk config ... InsufficientPrivilege

三条都被 try/except 吞成 WARNING，账户"建成功"但没有初始快照、没有默认策略配置（调度器
加载不到）、没有风控配置（UI 改风控 404）。根因：这三个 ORM 模型没声明 tenant_id 列
→ 自动填充钩子失效 → 新行落 DEFAULT 1 ≠ 请求租户；以及 AccountStrategyConfig 被传了不存在
的 trigger_mode。

本测试走真实 PostgreSQL + TestClient：注册临时用户（非租户 1），建账户，然后
  - 以管理员穿透核对三张表各有 1 行，且 tenant_id == 账户属主；
  - 以属主租户身份（非 admin）能看见这三行——这才是 RLS 下"数据真的属于用户"的证明；
  - 以另一个无关租户身份看不见（隔离未被打破）。
"""
from __future__ import annotations

import secrets as _secrets
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import backend.main as main_module
from backend.database.connection import SessionLocal

CHILD_TABLES = ("account_asset_snapshots", "account_strategy_configs", "risk_control_configs")


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(main_module.app)


def _unique(prefix: str = "acct_tenant") -> str:
    return f"{prefix}_{int(time.time() * 1000) % 10**9}_{_secrets.token_hex(3)}"


def _admin_cleanup_account(account_id: int) -> None:
    db = SessionLocal()
    try:
        db.execute(text("SET LOCAL app.is_admin = 'on'"))
        for t in CHILD_TABLES:
            db.execute(text(f"DELETE FROM {t} WHERE account_id = :aid"), {"aid": account_id})
        db.execute(text("DELETE FROM accounts WHERE id = :aid"), {"aid": account_id})
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


@pytest.fixture(scope="module")
def tenant(client):
    from backend.tests._user_cleanup import cleanup_user

    username = _unique()
    email = f"{username}@example.com"
    cleanup_user(username=username, email=email)
    reg = client.post("/api/auth/register",
                      json={"username": username, "email": email, "password": "S3cret-pw"})
    assert reg.status_code == 200, reg.text
    body = reg.json()
    ctx = {"headers": {"Authorization": f"Bearer {body['access_token']}"},
           "user_id": int(body["user"]["id"]), "account_ids": []}
    assert ctx["user_id"] != 1, "临时用户不应是租户 1，否则测不出 DEFAULT 1 泄漏"
    yield ctx
    for aid in ctx["account_ids"]:
        _admin_cleanup_account(aid)
    cleanup_user(username=username, email=email, user_id=ctx["user_id"],
                 extra_tables={"accounts": "user_id"})


@pytest.fixture(scope="module")
def account_id(client, tenant) -> int:
    resp = client.post(
        "/api/account/",
        json={"name": "tenant-stamp-test", "trading_mode": "paper",
              "selected_exchange": "hyperliquid", "initial_capital": 1234},
        headers=tenant["headers"],
    )
    assert resp.status_code in (200, 201), resp.text
    aid = int(resp.json()["id"])
    tenant["account_ids"].append(aid)
    return aid


def _rows_as(account_id: int, table: str, *, admin: bool = False, tenant_id: int | None = None):
    """在一个事务内以指定身份读取 (count, distinct tenant_ids)。"""
    db = SessionLocal()
    try:
        if admin:
            db.execute(text("SET LOCAL app.is_admin = 'on'"))
        elif tenant_id is not None:
            # SET 不接受绑定参数；int() 强转防注入
            db.execute(text(f"SET LOCAL app.tenant_id = '{int(tenant_id)}'"))
        rows = db.execute(
            text(f"SELECT tenant_id FROM {table} WHERE account_id = :aid"), {"aid": account_id}
        ).fetchall()
        return len(rows), sorted({r[0] for r in rows})
    finally:
        db.rollback()
        db.close()


@pytest.mark.parametrize("table", CHILD_TABLES)
def test_child_row_created_and_stamped_with_owner(account_id, tenant, table):
    n, tenants = _rows_as(account_id, table, admin=True)
    assert n == 1, f"{table} 应为新账户建成 1 行，实际 {n}（建行被 RLS 拒 / 构造器 TypeError 会被吞成 WARNING）"
    assert tenants == [tenant["user_id"]], f"{table}.tenant_id 应等于账户属主 {tenant['user_id']}，实际 {tenants}"


@pytest.mark.parametrize("table", CHILD_TABLES)
def test_owner_can_see_child_rows_under_rls(account_id, tenant, table):
    n, _ = _rows_as(account_id, table, tenant_id=tenant["user_id"])
    assert n == 1, f"属主以自己租户身份看不到 {table} 行 → RLS 下等于没建"


@pytest.mark.parametrize("table", CHILD_TABLES)
def test_other_tenant_cannot_see_child_rows(account_id, tenant, table):
    n, _ = _rows_as(account_id, table, tenant_id=tenant["user_id"] + 7_000_000)
    assert n == 0, f"无关租户看见了 {table} 行，隔离被打破"


def test_strategy_config_defaults(account_id, tenant):
    """默认策略配置内容与路由声明一致（间接证明构造器不再 TypeError）。"""
    db = SessionLocal()
    try:
        db.execute(text("SET LOCAL app.is_admin = 'on'"))
        row = db.execute(text(
            "SELECT trigger_interval, enabled FROM account_strategy_configs WHERE account_id = :aid"
        ), {"aid": account_id}).fetchone()
    finally:
        db.rollback()
        db.close()
    assert row is not None
    assert int(row[0]) == 300 and str(row[1]) == "true"
