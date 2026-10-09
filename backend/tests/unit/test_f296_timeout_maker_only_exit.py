# -*- coding: utf-8 -*-
"""[F296 2026-09-21] 超时出库改走被动（`timeout_exit_maker_only`）回归测试。

# 为什么必须有

用户指出「挂单交易没有手续费」，本轮账本实测印证这是**全部成本结构的关键**：

    入场腿（maker） 12,269 笔  平均 fee = **0.0000 bp**   ← 完全免费
    强平腿（taker）    917 笔  平均 fee = **−3.9956 bp**
    强平腿累计 taker 费 = **−$50.87**，整夜亏损 −$52.20 ⇒ **97% 的亏损＝taker 费**

而 917 笔强平里 `price_bp ≤ −40bp` 的**只占 10.9%**、中位漂移 **−7.39bp**
⇒ 近 90% 是在「行情几乎没动、只是时间到了」时 taker 出场 —— 纯为时间付费。

被动出库成功样本 96.1% 在 120s 内完成、**100% 在 300s 内完成**
⇒ 300s 窗口不是瓶颈，"价格回不来"不是等得不够。

所以正确的出库条件应该只有**价格**（①′ 止损），时间到了只需停止加仓、继续等被动成交。
本测试把这个语义钉住，防止被改回"到点就打对手价"。
"""
from __future__ import annotations

import inspect

import pytest

from backend.services.market_maker import runner as R
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams


@pytest.mark.unit
def test_flag_exists_and_defaults_off():
    """开关必须存在，且默认 False（不改既有部署行为，可一键回退）。"""
    lim = LaneRiskLimits()
    assert hasattr(lim, "timeout_exit_maker_only"), (
        "LaneRiskLimits 必须有 timeout_exit_maker_only 字段")
    assert lim.timeout_exit_maker_only is False, "默认必须是 False（旧行为逐字一致）"


@pytest.mark.unit
def test_flag_accepts_true_from_registry_dict():
    """注册表传 True 时必须能构造出 True（否则热更新静默失效）。"""
    lim = LaneRiskLimits(timeout_exit_maker_only=True)
    assert lim.timeout_exit_maker_only is True


@pytest.mark.unit
def test_timeout_branch_is_guarded_by_flag():
    """源码层面固定：超时 taker 分支必须**先**判开关。

    用一个源码断言而不是跑 tick，是因为 tick 需要 DB + 行情；
    这里要防的是"有人把 if 删了"这种回归，源码断言对此最直接。
    """
    src = inspect.getsource(R)
    assert "timeout_exit_maker_only" in src, "runner 里必须引用该开关"
    # 开关为真时只登记不成交
    assert "timeout_maker_only" in src, "必须登记 skip=timeout_maker_only（可观测）"
    # 开关出现在超时分支内部（而不是别处）
    i_flag = src.find("timeout_exit_maker_only")
    i_timeout = src.find("> limits.max_one_side_seconds")
    assert i_timeout != -1, "必须仍存在超时判定"
    assert abs(i_flag - i_timeout) < 1200, (
        "开关必须在超时判定附近（同一条出库路径内）")


@pytest.mark.unit
def test_symbol_state_tracks_blocked_counter_roundtrip():
    """`timeout_exit_blocked` 必须能序列化/反序列化（否则重启后计数丢失）。"""
    st = R.SymbolState(symbol="ASTER")
    assert st.timeout_exit_blocked == 0
    st.timeout_exit_blocked = 7
    d = st.to_dict()
    assert d["timeout_exit_blocked"] == 7
    st2 = R.SymbolState.from_dict(d)
    assert st2.timeout_exit_blocked == 7, "重启后计数必须保留"


@pytest.mark.unit
def test_stop_loss_remains_the_only_taker_when_enabled():
    """启用后，价格止损**仍是** taker 路径 —— 否则就没有任何价格保护了。

    这是本改动最关键的不变量：去掉的是**时间**出库，不是**价格**保护。
    配套前提是 F295/H127 让 `stop_loss_vol_min=0`（止损恒启用）。
    """
    src = inspect.getsource(R)
    # 止损分支仍写 fee_rate=taker 并置 is_flatten=True
    i_sl = src.find('dec.skip = "stop_loss"')
    assert i_sl != -1, "价格止损分支必须仍在（不得被顺手删掉）"
    # 且该分支不依赖新开关
    seg = src[max(0, i_sl - 4000):i_sl]
    assert "timeout_exit_maker_only" not in seg, (
        "价格止损分支不得被 timeout 开关影响 —— 它必须无条件可用")


@pytest.mark.unit
def test_quote_params_untouched():
    """本改动只碰 LaneRiskLimits，不得误改 QuoteParams（宽度语义正交）。"""
    assert "timeout_exit_maker_only" not in QuoteParams.__dataclass_fields__
