# -*- coding: utf-8 -*-
"""[F303 2026-09-16] 库存敞口护栏契约测试。

背景（本轮真实过程）：我曾据"峰值净敞口 93 倍权益"判定车道有裸露的方向风险，
**那个结论是错的**——它把 7 天账本（旧配置，`fill_notional=2000`，单腿高达 $11,661）
与今天才生效的配置（单腿 $30）混算了。按配置时代切分后，当前时代峰值只有 **0.10x**。

正确的结论是：**护栏已经存在且工作正常，不需要加对冲**。本测试把这条护栏
锁死，使"它悄悄失效"变成一次测试失败，而不是一个几周后才被发现的事故。

锁定的四件事：
  1. 上限参数存在且方向正确（>0 才会生效）；
  2. `runner.tick()` 必须把**共享账本**传给 `plan_tick`（否则闸门看到空账本）；
  3. `shared_book` 必须在标的循环**之前**预填充，且纳入孤儿持仓（F90）；
  4. `check_side_allowed`：超限时拦**加仓**侧、放**减仓**侧。
"""
from __future__ import annotations

import inspect

import pytest

EQUITY = 300.0


# ── 1. 参数在位且方向正确 ──────────────────────────────────────────────
def test_exposure_limits_exist_and_are_positive():
    from backend.services.market_maker.core import LaneRiskLimits

    lim = LaneRiskLimits()
    assert lim.max_net_directional_ratio > 0, "单币方向上限必须 >0 才生效"
    assert lim.max_net_exposure_ratio > 0, "组合净敞口上限必须 >0 才生效"


def test_gross_ratio_zero_means_disabled_not_broken():
    """gross=0 是"关闭"的合法取值（旧行为逐字一致），不能被误当成配置错误。"""
    from backend.services.market_maker.core import LaneRiskLimits

    lim = LaneRiskLimits(max_gross_notional_ratio=0.0)
    assert lim.max_gross_notional_ratio == 0.0


# ── 2. tick 必须传共享账本 ────────────────────────────────────────────
def test_tick_passes_shared_book_to_plan_tick():
    from backend.services.market_maker import runner as R

    src = inspect.getsource(R.ShadowRunner.tick)
    assert "shared_book" in src, "tick 必须构建共享账本"
    i = src.index("plan_tick(")
    call = src[i:i + 900]
    assert "book=shared_book" in call, (
        "plan_tick 必须收到共享账本 —— 否则 check_side_allowed 看到的是空账本，"
        "闸门恒放行（这正是本轮排查的怀疑点）")


def test_gross_paths_into_check_side_allowed():
    from backend.services.market_maker import runner as R

    src = inspect.getsource(R.plan_tick)
    assert "check_side_allowed" in src
    assert "book=local_book" in src


# ── 3. 共享账本必须预填充、且含孤儿 ────────────────────────────────────
def test_book_prefilled_before_symbol_loop():
    """共享账本要在 `for s in self.symbols` 之前建好，否则组合净敞口不完整。"""
    from backend.services.market_maker import runner as R

    src = inspect.getsource(R.ShadowRunner.tick)
    i_book = src.index("shared_book = InventoryBook()")
    i_loop = src.index("for s in self.symbols:")
    assert i_book < i_loop, "共享账本必须在标的循环之前构建"


def test_book_includes_orphans():
    """[F90] 不在宇宙里但有持仓的币也必须计入 —— 风险不可见是最危险的。"""
    from backend.services.market_maker import runner as R

    src = inspect.getsource(R.ShadowRunner.tick)
    assert "risk_symbols()" in src, "共享账本必须遍历 risk_symbols（含孤儿）"
    rs = inspect.getsource(R.ShadowRunner.risk_symbols)
    assert "orphan" in rs.lower()


# ── 4. 闸门行为：拦加仓、放减仓 ────────────────────────────────────────
def _book(qty):
    from backend.services.market_maker.core import InventoryBook, Position
    b = InventoryBook()
    b.positions["BTC"] = Position(qty=qty, avg_px=78000.0, avg_mid=78000.0,
                                  opened_ts=0.0)
    return b


def test_gate_blocks_risk_increasing_side():
    from backend.services.market_maker.core import LaneRiskLimits, check_side_allowed

    lim = LaneRiskLimits(max_net_directional_ratio=0.3, max_net_exposure_ratio=0.6,
                         max_gross_notional_ratio=1.0, vol_pause_sigma=0.0)
    ok, why = check_side_allowed(symbol="BTC", side="buy", book=_book(8000 / 78000),
                                 marks={"BTC": 78000.0}, equity=EQUITY,
                                 add_notional=30.0, limits=lim, now_ts=0.0)
    assert not ok, "超限多头继续加仓必须被拦"
    assert "symbol_exposure" in why or "net_exposure" in why or "gross" in why


def test_gate_always_allows_risk_reducing_side():
    from backend.services.market_maker.core import LaneRiskLimits, check_side_allowed

    lim = LaneRiskLimits(max_net_directional_ratio=0.3, max_net_exposure_ratio=0.6,
                         max_gross_notional_ratio=1.0, vol_pause_sigma=0.0)
    ok, why = check_side_allowed(symbol="BTC", side="sell", book=_book(8000 / 78000),
                                 marks={"BTC": 78000.0}, equity=EQUITY,
                                 add_notional=30.0, limits=lim, now_ts=0.0)
    assert ok, "减仓侧永远必须放行 —— 否则库存到顶后永久卡死，只能等超时砸单"
    assert why == "reduce"


def test_gate_lets_small_positions_through():
    """上限不能把正常的小仓也拦掉（收紧过度 = 误杀成交）。"""
    from backend.services.market_maker.core import LaneRiskLimits, check_side_allowed

    lim = LaneRiskLimits(max_net_directional_ratio=0.3, max_net_exposure_ratio=0.6,
                         max_gross_notional_ratio=1.0, vol_pause_sigma=0.0)
    ok, why = check_side_allowed(symbol="BTC", side="buy", book=_book(30.0 / 78000),
                                 marks={"BTC": 78000.0}, equity=EQUITY,
                                 add_notional=30.0, limits=lim, now_ts=0.0)
    assert ok, "正常 $30 腿必须放行（why=%s）" % why


# ── 5. 方法论文档：上限只能同时代评判 ──────────────────────────────────
def test_era_split_helper_documented_in_analysis():
    """把"上限只能在同一配置时代内评判"这条教训留在可执行的地方。"""
    import io
    import os
    p = r"D:\001Alpha\research_l1\analyze\direction_by_era.py"
    if not os.path.exists(p):
        pytest.skip("分析脚本不在位")
    txt = io.open(p, encoding="utf-8").read()
    assert "CORRECTION OF AN EARLIER ERROR" in txt
    assert "fill_notional" in txt, "必须记录旧配置的单腿口径，避免再次混算"
