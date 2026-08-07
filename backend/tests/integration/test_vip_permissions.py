# backend/tests/integration/test_vip_permissions.py
"""阶段4 Task 4.4: VIP 三维权限体系 —— 功能门控 + 数据可见度 + 使用配额。

验证矩阵(对应规格 §6.2 三个权限维度)
------------------------------------
A. **功能门控(feature_allowed / require_feature)**:
   - free 缺 arbitrage / rebate / ai_decision_detail(RESTRICTED_FEATURES)。
   - pro 有中档集合(multi_exchange/backtest 等),仍缺 arbitrage。
   - vip 含 "*" → 全开。
   - 端点级:free JWT 打 GET /api/arbitrage/status → 403;vip JWT → 非 403
     (可能 200/500,只要不是 403 即说明门控放行);admin JWT → 全能放行。

B. **数据可见度(visible_fields)**:free/pro 隐藏 api_key/secret/decision_detail
   等敏感字段;vip 全见。

C. **使用配额(check_quota / incr_quota / require_quota)**:
   - free tenant 超过 strategy_count(3)→ require_quota 依赖抛 429。
   - 未达上限 → 放行。admin → 全能放行。

mint JWT 沿用 test_admin_routes.py:用 create_access_token 直接签发不同
tier / role 的 token,纯验权限依赖的判定逻辑,不依赖任何环境变量。
"""
from __future__ import annotations

import secrets as _secrets
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend.main as main_module  # 触发 app 装配(含 arbitrage/rebate router + 中间件)
from backend.core.permissions import (
    FEATURE_MATRIX,
    QUOTA_LIMITS,
    RESTRICTED_FEATURES,
    check_quota,
    feature_allowed,
    incr_quota,
    require_feature,
    require_quota,
    reset_quota,
    visible_fields,
)
from backend.core.security import create_access_token


@pytest.fixture(scope="module")
def client():
    return TestClient(main_module.app)


def _unique(prefix: str = "viptest") -> str:
    return f"{prefix}_{int(time.time() * 1000) % 10**9}_{_secrets.token_hex(3)}"


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# 用一个稳定的 tenant_id 做 quota 测试,避免与真实租户撞车;测试结束统一清零。
_QUOTA_TENANT = 99_999_001


@pytest.fixture(autouse=True)
def _reset_quota_store():
    """每个用例前后都清零配额计数,防止用例间相互污染。"""
    for resource in {r for rs in QUOTA_LIMITS.values() for r in rs}:
        reset_quota(_QUOTA_TENANT, resource)
    yield
    for resource in {r for rs in QUOTA_LIMITS.values() for r in rs}:
        reset_quota(_QUOTA_TENANT, resource)


# ═══════════════════════════════════════════════════════════════
# A. 功能门控:feature_allowed 纯逻辑
# ═══════════════════════════════════════════════════════════════


def test_free_tier_lacks_restricted_features():
    """free: 普通功能放行,受限功能(arbitrage/rebate/ai_decision_detail)拒绝。"""
    assert "*" not in FEATURE_MATRIX["free"]
    for feat in FEATURE_MATRIX["free"]:
        assert feature_allowed("free", feat), f"free 自身功能 {feat} 应放行"
    for feat in RESTRICTED_FEATURES:
        assert feature_allowed("free", feat) is False, (
            f"free 不应有 {feat}(RESTRICTED_FEATURES)"
        )


def test_pro_tier_has_mid_set_but_not_restricted():
    """pro: 拥有中档功能(multi_exchange/backtest),但仍无 arbitrage/rebate。"""
    assert "multi_exchange" in FEATURE_MATRIX["pro"]
    assert "backtest" in FEATURE_MATRIX["pro"]
    assert feature_allowed("pro", "backtest") is True
    assert feature_allowed("pro", "multi_exchange") is True
    # pro 仍拿不到受限集合
    for feat in RESTRICTED_FEATURES:
        assert feature_allowed("pro", feat) is False, (
            f"pro 不应有受限功能 {feat}"
        )


def test_vip_tier_has_everything():
    """vip: 含 '*' → 任意功能(含受限)放行。"""
    assert "*" in FEATURE_MATRIX["vip"]
    for feat in RESTRICTED_FEATURES:
        assert feature_allowed("vip", feat) is True, f"vip 应放行 {feat}"
    # 任意普通功能也放行
    assert feature_allowed("vip", "anything_unknown") is True


def test_unknown_feature_default_allowed_for_non_restricted():
    """非受限的未知功能默认放行(只拦 RESTRICTED_FEATURES 里的)。"""
    assert feature_allowed("free", "some_random_feature") is True


def test_unknown_tier_falls_back_to_empty():
    """未知 tier 当作空集合:普通功能仍放行(默认开放语义),受限功能拒绝。"""
    assert feature_allowed("ghost", "basic_chart") is True
    assert feature_allowed("ghost", "arbitrage") is False


# ═══════════════════════════════════════════════════════════════
# A. 功能门控:端点级(free → 403,vip/admin 放行)
# ═══════════════════════════════════════════════════════════════


def test_free_jwt_arbitrage_gets_403(client):
    """free JWT 打 GET /api/arbitrage/status → 403(require_feature 拒)。"""
    token = create_access_token(
        sub="1", tenant_id=1, tier="free", role="user"
    )
    resp = client.get("/api/arbitrage/status", headers=_bearer(token))
    assert resp.status_code == 403, (
        f"free 不应有 arbitrage(预期 403),实际 {resp.status_code}: {resp.text}"
    )
    assert "requires higher tier" in resp.json().get("detail", "")


def test_free_jwt_rebate_gets_403(client):
    """free JWT 打 GET /api/rebate/status → 403。"""
    token = create_access_token(
        sub="1", tenant_id=1, tier="free", role="user"
    )
    resp = client.get("/api/rebate/status", headers=_bearer(token))
    assert resp.status_code == 403, (
        f"free 不应有 rebate(预期 403),实际 {resp.status_code}: {resp.text}"
    )


def test_pro_jwt_arbitrage_still_403(client):
    """pro JWT 打 arbitrage 仍 403(受限,只 vip 可用)。"""
    token = create_access_token(
        sub="2", tenant_id=2, tier="pro", role="user"
    )
    resp = client.get("/api/arbitrage/status", headers=_bearer(token))
    assert resp.status_code == 403, (
        f"pro 也不应有 arbitrage(预期 403),实际 {resp.status_code}"
    )


def test_vip_jwt_arbitrage_not_forbidden(client):
    """vip JWT 打 GET /api/arbitrage/status → 非 403(门控放行;下游可能 200/500)。"""
    token = create_access_token(
        sub="3", tenant_id=3, tier="vip", role="user"
    )
    resp = client.get("/api/arbitrage/status", headers=_bearer(token))
    assert resp.status_code != 403, (
        f"vip 应被门控放行(不应 403),实际 {resp.status_code}: {resp.text}"
    )


def test_admin_jwt_bypasses_feature_gate(client):
    """admin JWT(tier=free 亦可)打 arbitrage → 非 403(admin 全能)。"""
    token = create_access_token(
        sub="4", tenant_id=4, tier="free", role="admin"
    )
    resp = client.get("/api/arbitrage/status", headers=_bearer(token))
    assert resp.status_code != 403, (
        f"admin 应全能放行(不应 403),实际 {resp.status_code}: {resp.text}"
    )


def test_admin_jwt_bypasses_rebate_gate(client):
    """admin JWT 打 rebate → 非 403。"""
    token = create_access_token(
        sub="5", tenant_id=5, tier="free", role="admin"
    )
    resp = client.get("/api/rebate/status", headers=_bearer(token))
    assert resp.status_code != 403, (
        f"admin rebate 应放行(不应 403),实际 {resp.status_code}: {resp.text}"
    )


# ═══════════════════════════════════════════════════════════════
# B. 数据可见度:visible_fields
# ═══════════════════════════════════════════════════════════════


def test_visible_fields_free_hides_api_key():
    """free: api_key/secret/decision_detail 等敏感字段被裁掉,普通字段保留。"""
    all_fields = ["symbol", "price", "api_key", "secret", "decision_detail", "qty"]
    out = visible_fields("free", all_fields)
    assert "api_key" not in out, "free 不应见 api_key"
    assert "secret" not in out
    assert "decision_detail" not in out
    assert "symbol" in out and "price" in out and "qty" in out
    # 顺序应保持稳定(便于前端渲染)
    assert out == ["symbol", "price", "qty"]


def test_visible_fields_pro_also_hides_sensitive():
    """pro(阶段4 简化)与 free 同隐藏集。"""
    out = visible_fields("pro", ["symbol", "api_key", "private_key"])
    assert out == ["symbol"]


def test_visible_fields_vip_sees_all():
    """vip: 全部字段原样返回(含敏感字段),顺序不变。"""
    all_fields = ["symbol", "api_key", "secret", "factor_detail"]
    out = visible_fields("vip", all_fields)
    assert out == all_fields


# ═══════════════════════════════════════════════════════════════
# C. 使用配额:check_quota / incr_quota / require_quota
# ═══════════════════════════════════════════════════════════════


def test_quota_under_limit_allowed():
    """free tenant 未达 strategy_count 上限(3)→ check_quota True。"""
    limit = QUOTA_LIMITS["free"]["strategy_count"]
    for _ in range(limit):
        assert check_quota(_QUOTA_TENANT, "strategy_count", "free") is True
        incr_quota(_QUOTA_TENANT, "strategy_count")
    # 用满后应 False
    assert check_quota(_QUOTA_TENANT, "strategy_count", "free") is False


def test_quota_vip_higher_limit():
    """vip 的 strategy_count 上限(100)远高于 free(3)。"""
    assert QUOTA_LIMITS["vip"]["strategy_count"] > QUOTA_LIMITS["free"]["strategy_count"]
    # vip 仍受限于其上限(非"无限")
    for _ in range(QUOTA_LIMITS["vip"]["strategy_count"]):
        incr_quota(_QUOTA_TENANT, "strategy_count")
    assert check_quota(_QUOTA_TENANT, "strategy_count", "vip") is False
    # 重置后 vip 又可用
    reset_quota(_QUOTA_TENANT, "strategy_count")
    assert check_quota(_QUOTA_TENANT, "strategy_count", "vip") is True


def test_quota_unlimited_resource():
    """未在 QUOTA_LIMITS 登记的 resource → 无限制,check_quota 恒 True。"""
    for _ in range(50):
        incr_quota(_QUOTA_TENANT, "mystery_resource")
    assert check_quota(_QUOTA_TENANT, "mystery_resource", "free") is True


def _make_state(*, role="user", tier="free", tenant_id=_QUOTA_TENANT):
    """构造一个最小的 request.scope["state"](供依赖直接消费)。"""
    return {"role": role, "tier": tier, "tenant_id": tenant_id, "user_id": "100"}


class _FakeRequest:
    """最小 Request 替身:require_feature/require_quota 只读 scope["state"]。"""

    def __init__(self, state):
        self.scope = {"state": state}


def test_require_quota_raises_429_when_exceeded():
    """free tenant 用满 strategy_count 后,require_quota 依赖抛 429。"""
    limit = QUOTA_LIMITS["free"]["strategy_count"]
    for _ in range(limit):
        incr_quota(_QUOTA_TENANT, "strategy_count")

    dep = require_quota("strategy_count")
    with pytest.raises(HTTPException) as exc:
        dep(_FakeRequest(_make_state(tier="free")))
    assert exc.value.status_code == 429
    assert "strategy_count" in exc.value.detail and "free" in exc.value.detail


def test_require_quota_passes_when_under_limit():
    """未超限时,require_quota 依赖静默 return(不抛)。"""
    dep = require_quota("strategy_count")
    # 应不抛任何异常
    dep(_FakeRequest(_make_state(tier="free")))


def test_require_quota_admin_bypasses():
    """admin 即使已超限也放行(全能)。"""
    limit = QUOTA_LIMITS["free"]["strategy_count"]
    for _ in range(limit):
        incr_quota(_QUOTA_TENANT, "strategy_count")

    dep = require_quota("strategy_count")
    # role=admin → 不抛
    dep(_FakeRequest(_make_state(role="admin", tier="free")))


def test_require_feature_dep_admin_bypasses():
    """require_feature 依赖对 admin 放行(即便 tier=free + 受限功能)。"""
    dep = require_feature("arbitrage")
    dep(_FakeRequest(_make_state(role="admin", tier="free")))  # 不抛


def test_require_feature_dep_free_blocked():
    """require_feature 依赖对 free + 受限功能 → 403。"""
    dep = require_feature("arbitrage")
    with pytest.raises(HTTPException) as exc:
        dep(_FakeRequest(_make_state(tier="free")))
    assert exc.value.status_code == 403


def test_require_feature_dep_vip_allowed():
    """require_feature 依赖对 vip + 受限功能 → 静默放行。"""
    dep = require_feature("arbitrage")
    dep(_FakeRequest(_make_state(tier="vip")))  # 不抛
