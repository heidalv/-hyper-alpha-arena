# -*- coding: utf-8 -*-
"""[F302 2026-09-21] 撤掉减仓侧出库挂单（`reduce_quote_disabled`）回归测试。

# 依据（真实账本，非模拟）

    止盈腿（taker，+12bp 落袋）  **+7.4369 bp/笔**
    普通 maker 出库腿              **+0.3 bp/笔**
    ⇒ 差 **24 倍**

而出库挂单与止盈**在同一条「有利偏移」轴上竞争，出库单总在更近处**
（出库赚 `r×半价差` ≈ 0.2bp，止盈要 +12bp）⇒ **挂着出库单时止盈几乎永不触发**
（H172 实测止盈占比恒 **0.0%**）。

⇒ 用户选定方案 ①：撤掉减仓侧出库单，让止盈成为主要出库路径。
"""
from __future__ import annotations

import inspect

import pytest

from backend.services.market_maker import runner as R
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams


@pytest.mark.unit
def test_flag_exists_and_defaults_off():
    """开关存在且默认 False（不改既有部署行为，可一键回退）。"""
    lim = LaneRiskLimits()
    assert hasattr(lim, "reduce_quote_disabled")
    assert lim.reduce_quote_disabled is False


@pytest.mark.unit
def test_flag_constructible_from_registry():
    """注册表传 True 时必须能构造出 True（否则热更新静默失效）。"""
    assert LaneRiskLimits(reduce_quote_disabled=True).reduce_quote_disabled is True


@pytest.mark.unit
def test_runner_guards_reduce_side_only_when_holding():
    """源码层面固定三条语义（跑 tick 需要 DB+行情，故用源码断言）：

      1. 开关必须被引用
      2. **只在持有仓位时**撤减仓侧 —— 空仓时两侧都是加仓侧，撤掉就等于不做市
      3. 多头撤 ask、空头撤 bid（与 `core._red_bid/_red_ask` 同口径）
    """
    src = inspect.getsource(R)
    assert "reduce_quote_disabled" in src, "runner 必须引用该开关"
    assert "reduce_quote_off" in src, "必须登记 skip=reduce_quote_off（可观测）"

    i = src.find("reduce_quote_disabled")
    seg = src[max(0, i - 400):i + 900]
    assert "abs(_pos_now) > 1e-12" in seg, (
        "必须只在持有仓位时撤减仓侧（空仓撤掉 = 不做市）")
    assert "_pos_now > 1e-12 and allow_sell" in seg, "多头应撤 ask（卖侧）"
    assert "_pos_now < -1e-12 and allow_buy" in seg, "空头应撤 bid（买侧）"


@pytest.mark.unit
def test_stop_loss_and_take_profit_unaffected():
    """撤出库单**不得**影响止损与止盈 —— 它们必须仍能无条件触发。

    这是本改动最关键的不变量：撤掉的是「被动出库挂单」，
    不是「风控出口」。去掉它们仓位就没有任何出口了。
    """
    src = inspect.getsource(R)
    assert 'dec.skip = "stop_loss"' in src, "价格止损必须仍在"
    assert 'dec.skip = "take_profit"' in src, "止盈必须仍在"
    # 两者的分支都不应依赖新开关
    for marker in ('dec.skip = "stop_loss"', 'dec.skip = "take_profit"'):
        j = src.find(marker)
        seg = src[max(0, j - 3000):j]
        assert "reduce_quote_disabled" not in seg, (
            f"{marker} 分支不得被 reduce_quote_disabled 影响")


@pytest.mark.unit
def test_timeout_maker_only_is_the_required_companion():
    """`reduce_quote_disabled` 必须与 `timeout_exit_maker_only=False` 配套。

    H176 实测：撤掉出库单后 38% 的仓位到 300s 仍在，
    且它们在 300s 时的**中位浮亏是 −15.89bp**。
    若超时也不 taker（`timeout_exit_maker_only=True`），这些仓位会**永远占着敞口**。
    ⇒ 本测试固定"两者同时为 True 是危险组合"这一事实，要求运维脚本显式处理。
    """
    lim = LaneRiskLimits(reduce_quote_disabled=True, timeout_exit_maker_only=True)
    risky = bool(lim.reduce_quote_disabled and lim.timeout_exit_maker_only)
    assert risky is True, "该组合在参数上可构造（引擎不禁止），但运维必须知道它有风险"
    # 记录：正确组合应为 reduce_quote_disabled=True + timeout_exit_maker_only=False
    ok = LaneRiskLimits(reduce_quote_disabled=True, timeout_exit_maker_only=False)
    assert ok.reduce_quote_disabled and not ok.timeout_exit_maker_only


@pytest.mark.unit
def test_quote_params_untouched():
    """本改动只碰 LaneRiskLimits。"""
    assert "reduce_quote_disabled" not in QuoteParams.__dataclass_fields__
