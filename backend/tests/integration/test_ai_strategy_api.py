"""AI 策略 API 集成测试（/api/ai-strategies）。

[2026-09-02 重写] 原文件用三个互不相通的内存 SQLite engine + `patch(connection.engine)`
搭假库：`patch` 只替换模块属性，`get_db` 早已绑定了原 SessionLocal；三库表建在三个
独立的 :memory: 连接上互相看不见；鉴权还沿用早已退役的 X-API-Key。11 个用例自
写入以来从未真正跑通。现改为仓库现行集成测试范式（同 test_llm_config_byok /
test_auth_middleware）：

  - TestClient 打真实 core 库（PostgreSQL），不 mock DB；
  - 注册一个随机临时用户拿 JWT（register 白名单、直接签发 access_token）；
  - 用该 JWT 通过 /api/account/ 建一个归属该用户的账户作为策略挂载点；
  - 模块结束按外键反向清理：ai_strategies → 引用 accounts 的子表 → accounts → users。

覆盖：CRUD、activate/pause/archive 生命周期、422/404 错误路径、写端点鉴权、
以及多租户打标（ai_strategies.tenant_id = 账户归属 user_id，见 models._ai_strategy_stamp_tenant）。
"""
from __future__ import annotations

import secrets as _secrets
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import backend.main as main_module  # 触发 app 装配（含 auth middleware 注册）
from backend.database.connection import SessionLocal


# ── 基础设施 ────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client() -> TestClient:
    # 不用 `with TestClient(...)`：那会跑 lifespan（调度器/后台循环），集成测试不需要
    return TestClient(main_module.app)


def _unique(prefix: str = "aistrat") -> str:
    return f"{prefix}_{int(time.time() * 1000) % 10**9}_{_secrets.token_hex(3)}"


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin_delete_account_tree(account_id: int) -> None:
    """管理员穿透删除某账户及所有直接引用它的子行（外键全为 NO ACTION，必须手清）。

    动态从 information_schema 找出引用 accounts.id 的 (表, 列)，逐张删；单表失败
    用 SAVEPOINT 隔离并多轮重试，处理子表之间的二级依赖。
    """
    db = SessionLocal()
    try:
        db.execute(text("SET LOCAL app.is_admin = 'on'"))
        refs = db.execute(text(
            """
            SELECT tc.table_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = current_schema()
              AND ccu.table_name = 'accounts' AND ccu.column_name = 'id'
            """
        )).fetchall()
        pending = [(str(t), str(c)) for t, c in refs]
        for _ in range(3):
            still = []
            for tbl, col in pending:
                sp = db.begin_nested()
                try:
                    db.execute(text(f'DELETE FROM "{tbl}" WHERE "{col}" = :aid'), {"aid": account_id})
                    sp.commit()
                except Exception:
                    sp.rollback()
                    still.append((tbl, col))
            pending = still
            if not pending:
                break
        db.execute(text("DELETE FROM accounts WHERE id = :aid"), {"aid": account_id})
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


@pytest.fixture(scope="module")
def tenant(client):
    """注册临时用户 → (headers, user_id)；模块结束彻底清理。"""
    from backend.tests._user_cleanup import cleanup_user

    username = _unique()
    email = f"{username}@example.com"
    cleanup_user(username=username, email=email)
    reg = client.post(
        "/api/auth/register",
        json={"username": username, "email": email, "password": "S3cret-pw"},
    )
    assert reg.status_code == 200, reg.text
    body = reg.json()
    ctx = {"headers": _bearer(body["access_token"]), "user_id": int(body["user"]["id"]),
           "account_ids": []}
    yield ctx
    for aid in ctx["account_ids"]:
        _admin_delete_account_tree(aid)
    cleanup_user(username=username, email=email, user_id=ctx["user_id"],
                 extra_tables={"accounts": "user_id"})


@pytest.fixture(scope="module")
def seed_account(client, tenant) -> int:
    """用 JWT 建一个归属临时用户的账户，返回 id。（路由前缀是单数 /api/account）"""
    resp = client.post(
        "/api/account/",
        json={"name": "aistrat-test-account", "trading_mode": "paper",
              "selected_exchange": "hyperliquid", "initial_capital": 1000},
        headers=tenant["headers"],
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    aid = int(body["id"])
    assert int(body["user_id"]) == tenant["user_id"], "账户必须归属当前登录用户（M0-2b）"
    tenant["account_ids"].append(aid)
    return aid


@pytest.fixture(scope="module")
def template_id() -> int:
    """AIStrategyCreateRequest.master_prompt_template_id 为必填 int，取库中任一模板。"""
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT id FROM prompt_templates ORDER BY id LIMIT 1")).fetchone()
    finally:
        db.close()
    if row is None:
        pytest.skip("prompt_templates 为空，无法构造合法创建请求")
    return int(row[0])


@pytest.fixture(autouse=True)
def _quiet_autonomous_loop():
    """activate/pause/archive 会向自主分析循环注册/注销；测试里不启动真实后台循环。"""
    from backend.services.autonomous_strategy_service import autonomous_service
    with patch.object(autonomous_service, "register_strategy", return_value=None), \
         patch.object(autonomous_service, "unregister_strategy", return_value=None):
        yield


def _create(client, headers, account_id, template_id, **overrides) -> dict:
    payload = {
        "name": overrides.pop("name", _unique("strat")),
        "account_id": account_id,
        "master_prompt_template_id": template_id,
        "timeframe_tier": overrides.pop("timeframe_tier", "short"),
        # 非空权重跳过创建路径里的因子引擎推荐（需要 K 线，慢且与本文件无关）
        "factor_weights": overrides.pop("factor_weights", {"rsi_14": 1.0}),
    }
    payload.update(overrides)
    resp = client.post("/api/ai-strategies", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ══════════════════════════════════════════════════════════════════


class TestAiStrategyCRUD:
    """核心 CRUD。"""

    def test_create_strategy_succeeds(self, client, tenant, seed_account, template_id):
        data = _create(client, tenant["headers"], seed_account, template_id,
                       name="Test Momentum Strategy", timeframe_tier="mid",
                       description="integration test strategy")
        assert data["strategy_id"].startswith("ai_strategy_")
        assert data["status"] == "draft"
        assert data["timeframe_tier"] == "mid"
        assert data["account_id"] == seed_account

    def test_create_stamps_tenant_from_account_owner(self, client, tenant, seed_account, template_id):
        """[2026-09-02] ai_strategies 挂 RLS 却从不打 tenant_id（全落默认租户 1）→ 非超级用户
        库角色下创建者看不到自己的策略。现应 = 账户归属 user_id。"""
        data = _create(client, tenant["headers"], seed_account, template_id)
        db = SessionLocal()
        try:
            db.execute(text("SET LOCAL app.is_admin = 'on'"))
            row = db.execute(text("SELECT tenant_id FROM ai_strategies WHERE strategy_id = :sid"),
                             {"sid": data["strategy_id"]}).fetchone()
        finally:
            db.rollback()
            db.close()
        assert row is not None
        assert row[0] == tenant["user_id"], f"tenant_id 应为账户归属者 {tenant['user_id']}，实际 {row[0]}"

    def test_list_strategies_returns_list(self, client, tenant, seed_account, template_id):
        created = _create(client, tenant["headers"], seed_account, template_id, name="List Test")
        resp = client.get("/api/ai-strategies", params={"account_id": seed_account},
                          headers=tenant["headers"])
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert isinstance(data, list)
        assert created["strategy_id"] in {s["strategy_id"] for s in data}
        assert all(s["account_id"] == seed_account for s in data), "account_id 过滤失效"

    def test_get_strategy_roundtrip(self, client, tenant, seed_account, template_id):
        created = _create(client, tenant["headers"], seed_account, template_id, name="Get Me")
        resp = client.get(f"/api/ai-strategies/{created['strategy_id']}", headers=tenant["headers"])
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "Get Me"

    def test_get_nonexistent_strategy_returns_404(self, client, tenant):
        resp = client.get("/api/ai-strategies/NONEXISTENT-999", headers=tenant["headers"])
        assert resp.status_code == 404

    def test_update_strategy(self, client, tenant, seed_account, template_id):
        created = _create(client, tenant["headers"], seed_account, template_id)
        resp = client.put(f"/api/ai-strategies/{created['strategy_id']}",
                          json={"name": "Renamed", "min_confidence": 0.7},
                          headers=tenant["headers"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "Renamed"
        assert body["min_confidence"] == pytest.approx(0.7)

    def test_delete_strategy(self, client, tenant, seed_account, template_id):
        created = _create(client, tenant["headers"], seed_account, template_id, name="Delete Me")
        sid = created["strategy_id"]
        del_resp = client.delete(f"/api/ai-strategies/{sid}", headers=tenant["headers"])
        assert del_resp.status_code == 204, del_resp.text
        assert client.get(f"/api/ai-strategies/{sid}", headers=tenant["headers"]).status_code == 404
        # 幂等：再删 → 404
        assert client.delete(f"/api/ai-strategies/{sid}", headers=tenant["headers"]).status_code == 404

    def test_create_without_auth_blocked(self, client, seed_account, template_id):
        """写端点无 JWT → 中间件 401（不落到业务层）。"""
        resp = client.post(
            "/api/ai-strategies",
            json={"name": "No Auth", "account_id": seed_account,
                  "master_prompt_template_id": template_id, "timeframe_tier": "short"},
        )
        assert resp.status_code == 401, resp.text


class TestAiStrategyActions:
    """activate / pause / archive 生命周期。"""

    def test_activate_draft_strategy(self, client, tenant, seed_account, template_id):
        sid = _create(client, tenant["headers"], seed_account, template_id)["strategy_id"]
        resp = client.post(f"/api/ai-strategies/{sid}/activate", headers=tenant["headers"])
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "active"
        assert data["activated_at"], "激活必须记录 activated_at"

    def test_pause_active_strategy(self, client, tenant, seed_account, template_id):
        sid = _create(client, tenant["headers"], seed_account, template_id)["strategy_id"]
        assert client.post(f"/api/ai-strategies/{sid}/activate", headers=tenant["headers"]).status_code == 200
        resp = client.post(f"/api/ai-strategies/{sid}/pause", headers=tenant["headers"])
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "paused"

    def test_archive_strategy(self, client, tenant, seed_account, template_id):
        sid = _create(client, tenant["headers"], seed_account, template_id)["strategy_id"]
        resp = client.post(f"/api/ai-strategies/{sid}/archive",
                           params={"reason": "integration_test"}, headers=tenant["headers"])
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "archived"

    def test_lifecycle_on_missing_strategy_returns_404(self, client, tenant):
        for action in ("activate", "pause", "archive"):
            resp = client.post(f"/api/ai-strategies/NONEXISTENT-404/{action}", headers=tenant["headers"])
            assert resp.status_code == 404, f"{action}: {resp.status_code} {resp.text}"


class TestErrorPaths:
    """非法输入的错误响应。"""

    def test_create_without_name_returns_422(self, client, tenant, seed_account, template_id):
        resp = client.post(
            "/api/ai-strategies",
            json={"account_id": seed_account, "master_prompt_template_id": template_id,
                  "timeframe_tier": "short"},
            headers=tenant["headers"],
        )
        assert resp.status_code == 422, resp.text

    def test_create_with_invalid_tier_returns_422(self, client, tenant, seed_account, template_id):
        resp = client.post(
            "/api/ai-strategies",
            json={"name": "Bad Tier", "account_id": seed_account,
                  "master_prompt_template_id": template_id, "timeframe_tier": "ultra"},
            headers=tenant["headers"],
        )
        assert resp.status_code == 422, resp.text

    def test_create_for_missing_account_returns_404(self, client, tenant, template_id):
        resp = client.post(
            "/api/ai-strategies",
            json={"name": "Orphan", "account_id": 2_000_000_000,
                  "master_prompt_template_id": template_id, "timeframe_tier": "short",
                  "factor_weights": {"rsi_14": 1.0}},
            headers=tenant["headers"],
        )
        assert resp.status_code == 404, resp.text

    def test_update_nonexistent_returns_404(self, client, tenant):
        resp = client.put("/api/ai-strategies/NONEXISTENT-123", json={"name": "NewName"},
                          headers=tenant["headers"])
        assert resp.status_code == 404


class TestModelsAndSchemas:
    """[2026-09-02] 自 backend/tests/test_ai_strategy_integration.py 迁入的仅存有效检查。
    该文件其余用例测的是已删除的 PromptTrainingSystem（08-17）与 Phase 2 废弃存根
    AIStrategyEngine，且用不存在的 Account 构造参数往真库写脏数据，自身从未跑通，已删。"""

    def test_create_request_defaults(self):
        from backend.api.ai_strategy_routes import AIStrategyCreateRequest
        req = AIStrategyCreateRequest(name="测试策略", account_id=1, master_prompt_template_id=1)
        assert req.trigger_mode == "hybrid"
        assert req.auto_execute is False and req.require_confirmation is True
        assert req.timeframe_tier == "mid"
        with pytest.raises(ValueError):
            AIStrategyCreateRequest(name="x", account_id=1, master_prompt_template_id=1,
                                    timeframe_tier="ultra")

    def test_strategy_id_is_required_at_db_level(self):
        """strategy_id NOT NULL：缺失时 flush 必须失败；全程在一个回滚事务内，不落库。"""
        from sqlalchemy.exc import IntegrityError
        from backend.database.models import AIStrategy
        db = SessionLocal()
        try:
            db.add(AIStrategy(name="测试", status="draft", account_id=1))
            with pytest.raises(IntegrityError):
                db.flush()
        finally:
            db.rollback()
            db.close()


class TestAuthEnforcement:
    """危险端点无凭证一律 401。"""

    DANGEROUS_PATHS = [
        ("POST", "/api/ai-strategies"),
        ("PUT", "/api/ai-strategies/test-123"),
        ("DELETE", "/api/ai-strategies/test-123"),
        ("GET", "/api/llm-configs/1/api-key"),
    ]

    @pytest.mark.parametrize("method,path", DANGEROUS_PATHS)
    def test_missing_auth_returns_401(self, client, method, path):
        resp = client.request(method, path, json={} if method in ("POST", "PUT") else None)
        assert resp.status_code == 401, f"{method} {path} should require auth: {resp.status_code}"
