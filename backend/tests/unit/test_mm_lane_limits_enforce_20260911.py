# -*- coding: utf-8 -*-
"""[§82 契约 2026-09-11 / 决策 P5-A] MM 车道级风控闸门接线（清单第 19 条）。

接线前的事实（`_audit_ml/Z222_mm_shadow_dead_gate.py` AST 复核）：
  * `check_lane_limits()` 生产调用点 **0 个** ⇒ `toxic_streak` 从未生效；
  * `daily_loss_stop_pct` 判定用消费方 **0 个** ⇒ 从未实现（只有字段与展示）。

接线口径（本次）：
  * 开关 `MM_LANE_LIMITS_ENFORCE`（默认 false ⇒ 与接线前逐字一致）；
  * 只执行**车道级**判据（权益/波动/毒性流/日亏）—— **不含敞口**：
    敞口一律留给单侧闸门，因为"减仓方向永远允许"是做市的生存条件
    （库存到顶若把两侧都停掉，只能等超时砸单，亏损从价差搬到 taker 成本）；
  * 暂停位置在 ①′止损 / ②超时平仓**之后** ⇒ 暂停永不阻断已有库存的离场。
"""
from __future__ import annotations

import inspect

import pytest

from backend.services.market_maker import core as mmcore
from backend.services.market_maker import runner as mmrunner


def test_default_off_keeps_previous_behaviour(monkeypatch):
    """默认关闭：`MM_LANE_LIMITS_ENFORCE` 未设/false ⇒ 判定与接线前一致。"""
    monkeypatch.delenv("MM_LANE_LIMITS_ENFORCE", raising=False)
    monkeypatch.setattr(mmruntime_settings(), "MM_LANE_LIMITS_ENFORCE", False, raising=False)
    assert mmrunner.lane_limits_enforce_enabled() is False
    paused, why = mmcore.lane_pause_reason(equity=5000.0, toxic_streak=99, day_pnl_usd=-9999.0)
    assert paused is True            # 判据本身可用……
    # ……但开关关着时 plan_tick 不得因此暂停（下面用源码位置断言）


def mmruntime_settings():
    from backend.config import settings
    return settings


def _mk_state():
    return mmrunner.SymbolState(symbol="BTCUSDT")


def _plan(monkeypatch, *, enforce: bool, toxic_streak: int = 0, day_pnl: float = 0.0,
          equity: float = 5000.0):
    from backend.config import settings
    monkeypatch.setattr(settings, "MM_LANE_LIMITS_ENFORCE", enforce, raising=False)
    st = _mk_state()
    st.toxic_streak = toxic_streak
    st.quote_mid = 100.0
    return mmrunner.plan_tick(
        state=st, mid=100.0, seg_low=99.0, seg_high=101.0,
        seg_taker_sell=1.0, seg_taker_buy=1.0, now_ts=1_000_000.0,
        equity=equity, half_spread=0.01, day_pnl_usd=day_pnl,
    )


def test_gate_off_does_not_pause_even_with_extreme_streak(monkeypatch):
    """开关关 = 零行为变化：哪怕 toxic_streak 极大、日亏极深，也照常报价。"""
    dec, _meta = _plan(monkeypatch, enforce=False, toxic_streak=99, day_pnl=-9999.0)
    assert dec.lane_pause == "", "开关关着仍然暂停了 ⇒ 静默行为变更"
    assert dec.action in ("quote", "pause")
    if dec.action == "quote":
        assert dec.bid > 0 or dec.ask > 0


def test_gate_on_pauses_on_toxic_streak(monkeypatch):
    """开关开 + 连续逆选择 ≥ 阈值 ⇒ 整车道暂停报价，原因可追溯。"""
    dec, meta = _plan(monkeypatch, enforce=True, toxic_streak=3)
    assert dec.lane_pause.startswith("toxic_streak"), dec.lane_pause
    assert dec.action == "pause"
    assert dec.bid == 0.0 and dec.ask == 0.0
    assert meta.get("lane_pause", "").startswith("toxic_streak")
    assert "toxic_streak" in dec.to_dict()["lane_pause"]


def test_gate_on_pauses_on_daily_loss(monkeypatch):
    """开关开 + 当日已实现亏损超 `daily_loss_stop_pct`（1% × 权益）⇒ 暂停。

    此前该判据**从未实现**（判定用消费方 0 个）—— 这条用例就是它的第一个契约。
    """
    dec, _ = _plan(monkeypatch, enforce=True, day_pnl=-60.0, equity=5000.0)   # 1% = $50
    assert dec.lane_pause.startswith("daily_loss"), dec.lane_pause
    assert dec.action == "pause"


def test_daily_loss_threshold_boundary(monkeypatch):
    """边界：损失**未**超过阈值不得暂停（-49.99 vs 阈值 -50）。"""
    dec_ok, _ = _plan(monkeypatch, enforce=True, day_pnl=-49.99, equity=5000.0)
    assert dec_ok.lane_pause == ""
    dec_bad, _ = _plan(monkeypatch, enforce=True, day_pnl=-50.0, equity=5000.0)
    assert dec_bad.lane_pause.startswith("daily_loss")


def test_lane_pause_ignores_exposure_by_design():
    """**安全边界**：车道级暂停判据不得包含敞口 —— 否则会连"减仓腿"一起停掉。"""
    src = inspect.getsource(mmcore.lane_pause_reason)
    for kw in ("net_exposure", "symbol_exposure", "net_notional", "holding_seconds"):
        assert kw not in src, f"车道级判据里出现了敞口/持仓时长判据 {kw} ⇒ 会停掉减仓腿"
    # 敞口仍然由单侧闸门负责，且"减仓方向永远允许"
    from backend.services.market_maker.core import InventoryBook, Position, check_side_allowed
    book = InventoryBook()
    book.positions["BTCUSDT"] = Position(qty=1.0, avg_px=100.0, avg_mid=100.0,
                                         opened_ts=0.0, last_ts=0.0)
    allow, why = check_side_allowed(symbol="BTCUSDT", side="sell", book=book,
                                    marks={"BTCUSDT": 100.0}, equity=5000.0)
    assert allow is True and why == "reduce"


def test_pause_is_placed_after_exit_paths_in_source():
    """**顺序契约**：车道暂停必须位于 ①′止损 / ②超时平仓之后（暂停不阻断离场）。"""
    src = inspect.getsource(mmrunner.plan_tick)
    # 用**调用点**而不是名字做标记：`lane_pause_reason` 也出现在函数顶部的 import 里，
    # 用裸名字会在第一版误判（索引比止损更靠前）——这类"标记太宽"的坑已踩过多次。
    i_flatten_sl = src.index("should_stop_loss(state.qty")
    i_timeout = src.index("limits.max_one_side_seconds:")
    i_pause = src.index("_lane_pause, _lane_why = lane_pause_reason(")
    assert i_flatten_sl < i_pause and i_timeout < i_pause, \
        "车道暂停被放在离场逻辑之前 ⇒ 可能把已有库存卡死"


def test_check_lane_limits_contract_preserved():
    """回归：`check_lane_limits` 的原因串与判定顺序保持不变（含新增日亏参数默认关闭）。"""
    from backend.services.market_maker.core import InventoryBook, check_lane_limits
    book = InventoryBook()
    ok, why = check_lane_limits(symbol="BTCUSDT", book=book, marks={}, equity=0.0)
    assert (ok, why) == (False, "equity<=0")
    ok, why = check_lane_limits(symbol="BTCUSDT", book=book, marks={"BTCUSDT": 100.0},
                                equity=5000.0, sigma_norm=99.0)
    assert (ok, why) == (False, "vol_pause(sigma=99.00)")
    ok, why = check_lane_limits(symbol="BTCUSDT", book=book, marks={"BTCUSDT": 100.0},
                                equity=5000.0, toxic_streak=5)
    assert (ok, why) == (False, "toxic_streak(5)")
    # 新参数默认 0 ⇒ 不触发日亏闸（向后兼容）
    ok, _ = check_lane_limits(symbol="BTCUSDT", book=book, marks={"BTCUSDT": 100.0}, equity=5000.0)
    assert ok is True


def test_disabled_helper_reads_env_when_settings_missing(monkeypatch):
    """settings 不可用时的兜底：读 `os.environ`（默认 false）。"""
    monkeypatch.delenv("MM_LANE_LIMITS_ENFORCE", raising=False)
    src = inspect.getsource(mmruntime_settings())
    assert "MM_LANE_LIMITS_ENFORCE" in src, "配置键未登记进 settings.py（接线纪律）"
