# backend/core/permissions.py
"""阶段4 路由级权限依赖。

设计要点(纵深防御)
-------------------
身份与权限分三层,互相独立、互为兜底:

1. **中间件层(``JWTAuthMiddleware``)**:校验 JWT 签名/过期,把
   ``user_id`` / ``tenant_id`` / ``tier`` / ``role`` 注入 ``scope["state"]``,
   并据 ``role`` 设 ``app.is_admin`` GUC(阶段4 Task 4.2 的 RLS 穿透入口)。
   中间件只回答"你是谁 / 你是否登录",不回答"你能不能干这件事"。

2. **路由层-身份(``require_admin``)**:从 ``request.scope["state"]`` 读
   ``role``,非 admin 一律 403。这是业务级授权闸门 —— 即使中间件放行了一个
   登录的普通 user(合法持有有效 JWT),只要他不是 admin,就进不来
   ``/api/admin/*``。

3. **路由层-三维权限(本模块 ``require_feature`` / ``require_quota`` +
   ``visible_fields``)**:对应规格 §6.2 的三个权限维度 ——
   (A)功能门控(tier→功能矩阵)、(B)数据可见度(按 tier 裁字段)、
   (C)使用配额(计数/上限)。阶段4 起步:功能矩阵硬编码为常量表、配额用
   内存 dict(spec §6.2 明确"硬编码为常量表(起步)"),后续再迁 Redis / DB。

三层缺一不可:中间件保证了 ``scope["state"]`` 来自可信的 JWT 解码
(而非客户端伪造);``require_admin`` / ``require_feature`` / ``require_quota``
在此可信基础上再做业务判定。任何一层被绕过(例如中间件白名单误配、或路由忘挂
依赖),其它层仍能兜住。

**admin 全能放行**:三个业务依赖(``require_admin`` 不涉及)在 ``role == "admin"``
时一律 return,不受 tier / 配额约束 —— admin 即"上帝视角",与规格 §6.2 一致。

为什么不直接读 ContextVar
-------------------------
``is_admin_var``(tenant.py)也能拿到 admin 标志,但 ``scope["state"]`` 是
中间件写、路由读的"官方契约",且能一并取到 ``user_id``(审计日志要记谁操作的)。
ContextVar 主要服务于 connection.py 的 begin 钩子(设 GUC),路由层读 state 更直接。
"""
from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import Callable, List

from fastapi import HTTPException, Request


# ═══════════════════════════════════════════════════════════════
# 维度 A: 功能门控(tier → 功能矩阵)
# ═══════════════════════════════════════════════════════════════

# tier → 允许的功能集合。"*" 表示全开放(vip)。
# 起步硬编码为常量表(规格 §6.2:"硬编码为常量表(起步)")。
FEATURE_MATRIX: dict[str, set[str]] = {
    "free": {"scalp", "paper_trading", "basic_chart"},
    "pro": {
        "scalp",
        "paper_trading",
        "basic_chart",
        "multi_exchange",
        "advanced_strategy",
        "backtest",
    },
    "vip": {"*"},
}

# 仅 vip 可用的"高阶"功能:即便没显式列在 tier 的集合里,也一律拒绝。
# free/pro 调到这些 → 403。
# 仅 vip/admin 可用的高阶功能（free/pro → 403）
RESTRICTED_FEATURES: set[str] = {
    "arbitrage",
    "rebate",
    "ai_decision_detail",
    "ai_coin_select",  # VIP 共用 AI 选币特色
}


def feature_allowed(tier: str, feature: str) -> bool:
    """判断某 tier 是否允许使用某功能。

    - tier 含 ``"*"`` → 全开放(vip)。
    - feature 在该 tier 的显式白名单里 → 放行。
    - feature 不在受限集合里 → 默认放行(普通功能)。
    - feature 在受限集合且不在白名单 → 拒绝。
    """
    allowed = FEATURE_MATRIX.get(tier, set())
    if "*" in allowed:
        return True
    return feature in allowed or feature not in RESTRICTED_FEATURES


def require_feature(feature: str) -> Callable[[Request], None]:
    """FastAPI 依赖工厂:要求当前 tier 具备某功能,否则 403。admin 全能放行。

    用法::

        router = APIRouter(dependencies=[Depends(require_feature("arbitrage"))])

    或挂在单条端点::

        @router.get("/x", dependencies=[Depends(require_feature("rebate"))])

    - admin(``role == "admin"``)直接 return(上帝视角)。
    - 从 ``scope["state"]["tier"]`` 取 tier(中间件注入,默认 ``"free"``)。
    - 不满足 → 403 ``feature '<feature>' requires higher tier``。
    """
    def dep(request: Request) -> None:
        state = request.scope.get("state", {})
        if state.get("role") == "admin":
            return  # admin 全能
        tier = state.get("tier") or "free"
        if not feature_allowed(tier, feature):
            raise HTTPException(
                status_code=403,
                detail=f"feature '{feature}' requires higher tier",
            )

    return dep


# ═══════════════════════════════════════════════════════════════
# 维度 B: 数据可见度(按 tier 裁剪响应字段)
# ═══════════════════════════════════════════════════════════════

# free / pro 都隐藏的敏感字段(api_key/secret/private_key)与高阶分析明细
# (decision_detail / factor_detail —— 仅 vip 可见完整 AI 决策路径)。
# 阶段4 简化:pro 与 free 共用同一份隐藏集(spec 明确"pro 见更多,简化:与 free
# 同隐藏集");后续可按需给 pro 单独开放部分字段。
HIDDEN_FOR_NON_VIP: set[str] = {
    "api_key",
    "secret",
    "private_key",
    "decision_detail",
    "factor_detail",
}


def visible_fields(tier: str, fields: List[str]) -> List[str]:
    """按 tier 裁剪可见字段。vip 全见,free/pro 隐藏敏感字段。

    用法(端点内)::

        all_fields = ["symbol", "api_key", "decision_detail"]
        return {f: value[f] for f in visible_fields(tier, all_fields)}

    - vip → 原样返回。
    - 其它 tier → 剔除 ``HIDDEN_FOR_NON_VIP`` 中的字段。
    - 保持入参顺序(便于稳定的前端渲染)。
    """
    if tier == "vip":
        return list(fields)
    return [f for f in fields if f not in HIDDEN_FOR_NON_VIP]


# ═══════════════════════════════════════════════════════════════
# 维度 C: 使用配额(计数 / 上限)
# ═══════════════════════════════════════════════════════════════

# 阶段4 起步:内存计数器。进程重启即清零(够 demo / 防误用)。
# 规格明确后续迁 Redis(spec §6.2),届时只换 ``_quota_store`` 后端即可,API 不变。
_quota_store: defaultdict[tuple[int, str], int] = defaultdict(int)
_quota_lock = Lock()

# tier → {resource → limit}。未列出的 resource 视为无限制。
QUOTA_LIMITS: dict[str, dict[str, int]] = {
    "free": {"strategy_count": 3, "llm_call_per_day": 20},
    "pro": {"strategy_count": 20, "llm_call_per_day": 200},
    "vip": {"strategy_count": 100, "llm_call_per_day": 1000},
}


def check_quota(tenant_id: int, resource: str, tier: str) -> bool:
    """查询 ``(tenant_id, resource)`` 当前用量是否仍在 tier 的配额内。

    - 该 resource 在 tier 的 ``QUOTA_LIMITS`` 中无记录 → 无限制,返回 True。
    - 否则比较内存计数器与上限(严格小于)。
    - 线程安全(``_quota_lock``)。
    """
    limit = QUOTA_LIMITS.get(tier, {}).get(resource)
    if limit is None:
        return True  # 无限制
    with _quota_lock:
        return _quota_store[(tenant_id, resource)] < limit


def incr_quota(tenant_id: int, resource: str) -> None:
    """对 ``(tenant_id, resource)`` 的用量 +1。在"确实发生消耗"后调用。

    典型流程:先 ``check_quota`` → 放行 → 执行 → ``incr_quota``。
    线程安全。
    """
    with _quota_lock:
        _quota_store[(tenant_id, resource)] += 1


def reset_quota(tenant_id: int, resource: str) -> None:
    """清零 ``(tenant_id, resource)`` 计数。

    供测试与定时重置(如 llm_call_per_day 的日重置)使用。
    """
    with _quota_lock:
        _quota_store[(tenant_id, resource)] = 0


def require_quota(resource: str) -> Callable[[Request], None]:
    """FastAPI 依赖工厂:检查某 resource 配额,超限 → 429。admin 全能放行。

    用法::

        @router.post("/strategies", dependencies=[Depends(require_quota("strategy_count"))])

    - admin 直接 return。
    - 从 ``scope["state"]`` 取 ``tenant_id`` / ``tier``(中间件注入)。
    - ``tenant_id`` 缺失(未登录 / 运维通道) → 放行(由中间件层负责身份)。
    - 超限 → 429 ``quota '<resource>' exceeded for tier <tier>``。

    注意:本依赖只"读检",不"写增"。实际消耗发生在路由成功后,由调用方显式
    ``incr_quota`` —— 避免依赖执行了但路由体抛错导致"空增配额"。
    """
    def dep(request: Request) -> None:
        state = request.scope.get("state", {})
        if state.get("role") == "admin":
            return
        tenant_id = state.get("tenant_id")
        tier = state.get("tier") or "free"
        if not tenant_id:
            return  # 无身份(中间件层负责),不在此处拦
        if not check_quota(int(tenant_id), resource, tier):
            raise HTTPException(
                status_code=429,
                detail=f"quota '{resource}' exceeded for tier {tier}",
            )

    return dep


# ═══════════════════════════════════════════════════════════════
# 路由层-身份: require_admin(原阶段4 Task 4.3,保留)
# ═══════════════════════════════════════════════════════════════


def require_admin(request: Request) -> int:
    """路由级依赖:要求当前请求由 admin 发起。返回 admin 的 user_id。

    - 从 ``request.scope["state"]`` 读 ``role`` / ``user_id``(由
      ``JWTAuthMiddleware`` 在解码 access token 后写入)。
    - ``role != "admin"`` 或无 ``user_id`` → 403 ``admin required``。
    - 返回 ``int(user_id)`` 供路由记录"是谁在操作"(写审计日志)。

    用作整个 ``/api/admin`` 路由组的 ``dependencies=[Depends(require_admin)]``,
    组内所有端点都受保护;也可单独挂在某条端点上做更细粒度控制。
    """
    state = request.scope.get("state", {})
    role = state.get("role")
    user_id = state.get("user_id")
    if role != "admin" or not user_id:
        raise HTTPException(status_code=403, detail="admin required")
    return int(user_id)
