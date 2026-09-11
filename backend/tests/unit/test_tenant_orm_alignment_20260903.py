"""[2026-09-03 M1-1d] ORM 与 RLS 租户列对齐守卫。

原病症：0004 给 23 张 account 作用域表加了 ``tenant_id NOT NULL DEFAULT 1``，0005 FORCE RLS；
但其中 17 个 ORM 模型一直没声明这一列。``connection._auto_fill_tenant_id`` 只对"模型声明了
tenant_id"的对象生效，于是这些模型的新行全落 DEFAULT 1：
  - 属主 326/327 的行对属主与后台身份都不可见（实盘账户 188 的风控配置即在其中）；
  - 请求上下文内写入被 WITH CHECK 拒（API 新建账户的初始快照 / 默认风控配置从未建成）。

本测试锁两件事：
  1. 0004 A1 组的每个已映射模型都声明了 tenant_id（再漏一个立刻红）；
  2. AccountStrategyConfig 的构造器不接受 trigger_mode（该列不存在），而 account_routes
     不再传它——否则默认策略配置永远建不出来（TypeError 被 try 吞掉）。
"""
from __future__ import annotations

import inspect
import re

import pytest

from backend.database import models as m

# 与 alembic 0004/0005/0020 中的 A1 组一致
A1_TABLES = (
    "positions", "orders", "trades", "account_asset_snapshots",
    "account_strategy_configs", "account_prompt_bindings", "ai_strategies",
    "hyperliquid_wallets", "hyperliquid_account_snapshots", "hyperliquid_positions",
    "hyperliquid_exchange_actions", "risk_control_configs", "paper_balances",
    "paper_positions", "paper_orders", "paper_funding_ledger", "position_exit_events",
    "trade_memory_records", "trader_mental_states", "trader_personalities",
    "signal_trade_feedback", "full_auto_sessions", "arbitrage_profiles",
)


def _mapped_models_by_table() -> dict:
    out: dict = {}
    for mapper in m.Base.registry.mappers:
        out.setdefault(mapper.local_table.name, []).append(mapper)
    return out


@pytest.mark.parametrize("table", A1_TABLES)
def test_a1_model_declares_tenant_id(table):
    mappers = _mapped_models_by_table().get(table)
    if not mappers:
        pytest.skip(f"{table} 没有 ORM 映射（raw SQL 管理）")
    for mp in mappers:
        assert "tenant_id" in mp.column_attrs.keys(), (
            f"{mp.class_.__name__}（{table}）未声明 tenant_id → before_flush 自动填充失效，"
            f"新行落 DEFAULT 1，FORCE RLS 下属主不可见 / 请求内写入被拒"
        )


def test_auto_fill_hook_sees_declared_column():
    """钩子依据 mapper.column_attrs 判定；这里用 RiskControlConfig 直接验证判定为真。"""
    from sqlalchemy import inspect as sa_inspect
    mp = sa_inspect(m.RiskControlConfig)
    assert "tenant_id" in mp.column_attrs.keys()
    obj = m.RiskControlConfig(account_id=1)
    assert getattr(obj, "tenant_id", None) is None  # 待钩子填充


def test_account_strategy_config_has_no_trigger_mode_kwarg():
    """trigger_mode 只存在于返回前端的 schema，ORM 与 DB 都没有；传它必抛。"""
    with pytest.raises(TypeError):
        m.AccountStrategyConfig(account_id=1, trigger_mode="unified")
    # 正常构造可用
    ok = m.AccountStrategyConfig(account_id=1, trigger_interval=300, enabled="true")
    assert ok.trigger_interval == 300


def test_account_routes_no_longer_pass_trigger_mode_to_orm():
    """account_routes 里两处 AccountStrategyConfig(...) 构造不得再带 trigger_mode。"""
    import backend.api.account_routes as ar
    src = inspect.getsource(ar)
    # 抓所有 AccountStrategyConfig( ... ) 构造块（非 query），检查块内是否有 trigger_mode
    bad = []
    for mt in re.finditer(r"AccountStrategyConfig\(\s*\n(?P<body>(?:.*\n)*?)\s*\)", src):
        body = mt.group("body")
        if "trigger_mode" in body:
            bad.append(body.strip()[:80])
    assert not bad, f"仍有 ORM 构造传 trigger_mode（会 TypeError 被吞）: {bad}"
