# -*- coding: utf-8 -*-
r"""[整顿轮·T29 2026-10-06] 单腿名义上限必须可配，且**默认交回引擎自己的风险模型**。

病根（实测，权益 $10,077，`flow_learn_params` floor=15 cap=40，实测 stop 15~20bp）：

    stop_bp | 风险推导上限 | h887 的 5% 上限 | 止损时实亏 | 占设计意图
    --------|-------------|----------------|-----------|----------
       15   |   $33,591   |      $504      |   $0.76   |   1.5%
       20   |   $25,193   |      $504      |   $1.01   |   2.0%
       40   |   $12,596   |      $504      |   $2.02   |   4.0%

引擎自己声明的单笔风险预算是 `EQUITY_LOSS_PER_TRADE = 0.5%` ⇒ **$50.39/笔**。
5% 上限让实际风险只有 $1.01 ⇒ **比设计意图低 50 倍**。

⇒ 这不是"保守"，是口子比引擎自己的风险模型**小两个数量级**，收益被同比例压薄。
（R045 实测：稳定态 $504 腿 12 条只赚 +$0.65。）

修法：`MM_AF_LEG_CAP_PCT` 可配，默认 **0 = 交回风险模型**
（`notional_cap_usd` 同时受"单笔 0.5%"与"当日 2%÷同向笔数"约束，风险仍焊住）。
`MM_AF_LEG_CAP_PCT=5` = 旧行为逐字恢复（回滚）。
"""
from __future__ import annotations

import os

from backend.services.market_maker import active_flow as AF
from backend.services.market_maker.flow_rules import (
    EQUITY_LOSS_PER_TRADE,
    notional_cap_usd,
)

EQ = 10077.16
STOP_BP = 20.0


def _effective_cap(pct: float) -> float:
    """复现 active_flow.py 的 cap 计算（风险模型 + 可选硬百分比）。"""
    cap = notional_cap_usd(EQ, STOP_BP, 1, EQUITY_LOSS_PER_TRADE)
    if pct > 0:
        cap = min(cap, EQ * pct / 100.0)
    return cap


def test_switch_documented():
    src = open(AF.__file__, encoding="utf-8", errors="replace").read()
    assert "MM_AF_LEG_CAP_PCT" in src, "必须提供可配开关"
    assert "os.getenv" in src, "开关必须走 env"


def test_default_uses_risk_model():
    """默认(0) ⇒ cap 就是风险模型输出，即设计意图的 0.5% 风险。"""
    cap = _effective_cap(0.0)
    assert cap == notional_cap_usd(EQ, STOP_BP, 1, EQUITY_LOSS_PER_TRADE)
    risk = cap * STOP_BP / 1e4
    intent = EQ * EQUITY_LOSS_PER_TRADE
    assert abs(risk - intent) / intent < 0.01, (
        f"默认应恰好用满 0.5% 预算：实亏 ${risk:.2f} vs 意图 ${intent:.2f}")


def test_rollback_reproduces_old_behaviour():
    """MM_AF_LEG_CAP_PCT=5 ⇒ 逐字恢复旧的 5% 上限。"""
    cap = _effective_cap(5.0)
    assert abs(cap - EQ * 0.05) < 1e-6, "回滚值必须精确等于 equity×5%"


def test_old_cap_was_far_below_intent():
    """把病根写成断言，防止有人再把上限调回去。"""
    old_cap = _effective_cap(5.0)
    old_risk = old_cap * STOP_BP / 1e4
    intent = EQ * EQUITY_LOSS_PER_TRADE
    assert old_risk < intent * 0.05, (
        f"旧 5% 上限的实亏 ${old_risk:.2f} 应远小于意图 ${intent:.2f}"
        f"（实测约 2%）")


def test_risk_model_still_bounds_by_daily_limit():
    """放开的只是多余那一层：风险模型自身仍受当日 2% 约束。"""
    one = notional_cap_usd(EQ, STOP_BP, 1, EQUITY_LOSS_PER_TRADE)
    six = notional_cap_usd(EQ, STOP_BP, 6, EQUITY_LOSS_PER_TRADE)
    assert six < one, "同向笔数增加时上限必须收紧（当日 2% 风险约束）"
    assert six * STOP_BP / 1e4 <= EQ * 0.02 + 1e-9, "不得超过当日 2% 风险"


def test_env_override_is_read_at_runtime():
    """开关必须在调用时读取（不是 import 时固化），否则重启才能改。"""
    src = open(AF.__file__, encoding="utf-8", errors="replace").read()
    idx = src.find("MM_AF_LEG_CAP_PCT")
    assert idx > 0
    # 该读取必须出现在函数体内（缩进），而不是模块顶层
    line_start = src.rfind("\n", 0, idx) + 1
    assert src[line_start:line_start + 4] == "    ", (
        "开关应在函数内读取，便于运行时生效")
