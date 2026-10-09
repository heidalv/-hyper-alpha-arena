# -*- coding: utf-8 -*-
"""[2026-09-14 F98] 车道级闸门语义契约（σ 开关方向 / 毒性暂停自解除 / 可被实验覆盖）。

三处此前只在「打开 MM_LANE_LIMITS_ENFORCE」时才会暴露的问题：
  ① `lane_pause_reason` 的 σ 判据缺 `>0` 守卫，而线上 `vol_pause_sigma=0.0`
     （F82 约定 = 显式禁用）⇒ 一旦打开开关，σ>0 几乎恒成立 ⇒ **整车道永久停摆**。
     同一参数在两处含义相反 = bug。
  ② 毒性暂停会清空挂单，而 `toxic_streak` 只在成交判定里更新 ⇒ 暂停后不再有成交
     ⇒ 计数卡死 ⇒ **永久停摆**（谁打开开关谁中招）。
  ③ 闸门调用被环境变量硬门控，回放**永远测不到** daily_loss_stop_pct / toxic_streak
     ⇒ 配置里的两个数字从未被任何实验覆盖。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402
from backend.services.market_maker.core import (  # noqa: E402
    LaneRiskLimits,
    QuoteParams,
    lane_pause_reason,
)


def test_sigma_gate_zero_means_disabled():
    """σ 闸：0 = 显式禁用（与 check_side_allowed 同口径），不得把车道停掉。"""
    lim = LaneRiskLimits(vol_pause_sigma=0.0)
    paused, why = lane_pause_reason(equity=300.0, limits=lim, sigma_norm=9.9)
    assert not paused, f"vol_pause_sigma=0 必须视为关闭，却被判暂停: {why}"


def test_sigma_gate_positive_still_works():
    """σ 闸：>0 时才生效（保持原语义）。"""
    lim = LaneRiskLimits(vol_pause_sigma=1.5)
    assert lane_pause_reason(equity=300.0, limits=lim, sigma_norm=1.0)[0] is False
    paused, why = lane_pause_reason(equity=300.0, limits=lim, sigma_norm=2.0)
    assert paused and why.startswith("vol_pause")


def test_daily_loss_gate_semantics():
    """日亏闸：<= -pct% 才暂停；pct=0 关闭。"""
    lim = LaneRiskLimits(daily_loss_stop_pct=10.0, vol_pause_sigma=0.0)
    assert lane_pause_reason(equity=300.0, limits=lim, day_pnl_usd=-29.0)[0] is False
    paused, why = lane_pause_reason(equity=300.0, limits=lim, day_pnl_usd=-30.5)
    assert paused and why.startswith("daily_loss")
    off = LaneRiskLimits(daily_loss_stop_pct=0.0, vol_pause_sigma=0.0)
    assert lane_pause_reason(equity=300.0, limits=off, day_pnl_usd=-999.0)[0] is False


def _st_with_streak(n: int):
    st = mmrunner.SymbolState(symbol="BTC")
    st.mid_hist = [100.0] * 60
    st.toxic_streak = n
    return st


def _gate_limits(**kw):
    """与线上一致的宽松敞口 + 指定车道闸门（避免默认 0.1× 单币上限误挡报价）。"""
    base = dict(toxic_streak=3, vol_pause_sigma=0.0, trend_pause_bp=0.0,
                ofi_block_threshold=0.0, max_quote_age_sec=0.0,
                max_one_side_seconds=10_000.0, max_symbol_notional_ratio=1.0,
                max_net_directional_ratio=1.0, max_net_exposure_ratio=3.0,
                stop_loss_bp=0.0)
    base.update(kw)
    return LaneRiskLimits(**base)


def test_toxic_pause_self_clears():
    """毒性暂停必须**自解除**：暂停时重置计数，否则永久停摆。"""
    lim = _gate_limits(toxic_streak=3)
    st = _st_with_streak(3)
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.0, seg_low=99.9, seg_high=100.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_700_000_000.0,
        params=QuoteParams(), limits=lim, equity=300.0, fill_notional=300.0,
        lane_limits_enforce=True)
    assert dec.lane_pause.startswith("toxic_streak"), dec.lane_pause
    assert st.toxic_streak == 0, "暂停后计数未重置 ⇒ 永久停摆"
    dec2, _ = mmrunner.plan_tick(
        state=st, mid=100.0, seg_low=99.9, seg_high=100.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_700_000_015.0,
        params=QuoteParams(), limits=lim, equity=300.0, fill_notional=300.0,
        lane_limits_enforce=True)
    assert not dec2.lane_pause, f"下一 tick 仍在暂停（永久停摆）: {dec2.lane_pause}"
    assert dec2.bid > 0 or dec2.ask > 0, "解除暂停后必须恢复报价"


def test_enforce_switch_can_be_overridden_for_experiments():
    """`lane_limits_enforce` 显式覆盖必须能强制开启（回放才测得到闸门）。"""
    lim = _gate_limits(toxic_streak=3)
    st = _st_with_streak(5)
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.0, seg_low=99.9, seg_high=100.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_700_000_000.0,
        params=QuoteParams(), limits=lim, equity=300.0, fill_notional=300.0,
        lane_limits_enforce=True)
    assert dec.lane_pause, "显式开启时必须执行闸门"


def test_replay_exposes_lane_gate_inputs():
    """回放必须能传入日亏并强制开启闸门（源码契约）。"""
    import inspect
    from backend.services.market_maker import portfolio_replay as pr
    src = inspect.getsource(pr.replay_portfolio)
    assert "enforce_lane_limits" in src
    assert "day_pnl_usd" in src and "lane_limits_enforce" in src
    assert "max_daily_loss_usd" in src and "max_toxic_streak" in src


def test_day_pnl_is_era_scoped():
    """[F98] 日亏闸输入必须按统计时代裁剪。

    实测：今日自然日车道净额 −$18.77，其中 −$13.73 来自「修复前」的旧配置时期
    （漏单/幻影成交），时代内只有 −$5.04。若不裁剪，日亏闸会用**已被修掉的缺陷**
    造成的亏损去停掉现在的车道（阈值 −$30 被占 63%），且与前端时代口径矛盾。
    """
    import inspect
    src = inspect.getsource(mmrunning_day_pnl())
    assert "stats_since" in src, "日亏闸读数未按时代裁剪"
    assert "since=_since" in src
    # 行为断言：两次读数应一致且为时代口径（不依赖具体数值）
    a = mmrunner.lane_day_pnl_usd("mm_asterdex")
    b = mmrunner.lane_day_pnl_usd("mm_asterdex")
    assert a == pytest.approx(b)


def mmrunning_day_pnl():
    return mmrunner.lane_day_pnl_usd


def test_status_exposes_live_quoted_width():
    """[F98] status 必须暴露实盘**实际挂宽**与 σ。

    为什么必要：k_vol>0 后挂宽随波动变化，「实盘成交比回放少」时首要嫌疑就是挂宽，
    但此前实盘完全看不到自己挂多宽（只能靠猜）。观测口径：只统计真正挂出去的那一侧。
    """
    r = mmrunner.ShadowRunner(lane_id="t", venue="x", symbols=["BTC"])
    st = r.status()
    assert "avg_width_bp" in st and "avg_sigma" in st and "quoted_decisions" in st
    assert st["quoted_decisions"] == 0 and st["avg_width_bp"]["bid"] is None
    # [F232] 挂宽均值改为**分侧计数**：bid/ask 各除各侧决策数（`_w_n_side`），
    # 不再除含单边决策的 `_w_n` —— 否则单边行情里未挂侧会把另一侧均值稀释
    # （实测 bid 读数 1.86bp < 任何可能的挂宽 ✗）。断言必须跟着口径走。
    assert st["avg_width_bp"]["ask"] is None
    # 累计口径：挂出去的一侧才计入
    r._w_sum["bid"] += 7.0
    r._w_sum["ask"] += 3.0
    r._sigma_sum += 0.5
    r._w_n = 1
    r._w_n_side["bid"] = 1
    r._w_n_side["ask"] = 1
    st2 = r.status()
    assert st2["avg_width_bp"] == {"bid": 7.0, "ask": 3.0}
    assert st2["avg_sigma"] == 0.5
    # 分侧口径的实质：只挂了一侧时，另一侧必须是 None 而不是 0 或被稀释
    r2 = mmrunner.ShadowRunner(lane_id="t2", venue="x", symbols=["BTC"])
    r2._w_sum["bid"] += 5.0
    r2._w_n_side["bid"] = 1
    r2._w_n = 1
    st3 = r2.status()
    assert st3["avg_width_bp"]["bid"] == 5.0
    assert st3["avg_width_bp"]["ask"] is None, (
        "未挂侧必须为 None —— 这是 F232 分侧计数的目的（不被对方稀释）")
