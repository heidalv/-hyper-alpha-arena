# -*- coding: utf-8 -*-
"""[2026-09-15 F176] 「延迟一档判定」契约：拿 L 个桶之前那张单，判它当时真正存续的分片。

背景（我自己的修复引入的回归）：F171 把成交桶改成"按成交自身时间戳分桶、**桶结束后
才落库**"✓（覆盖率 47.5%→93.3% ✓），代价是桶比 tick **晚 15~30s** 可见 ✗。
实盘若仍用"当前状态里的挂单"判定，晚到的桶会被 `quote_ts` 过滤器排除 ✗：
实测**空分片率 47%→85.7%** ✗✗、成交 **184/h→27.5/h** ✗、与模型速率比掉到 **0.18×** ✗✗。
修法：判定对象改为"标签 ≤ 分片上界的**最近一张挂单**"（`_lagged_quote`），
分片上界取 `snap − L×15s`；L 由 `MM_JUDGE_LAG_BUCKETS` 控制（0 = 旧行为，可即时回退 ✓）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.core import InventoryBook, LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.runner import SymbolState, plan_tick  # noqa: E402


def _state(**kw):
    st = SymbolState(symbol="BTC")
    st.mid_hist = [100.0] * 60
    st.vol_baseline_bp = 1.5
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def _run(judged_quote, st=None, seg_low=99.0, seg_high=101.0):
    now = time.time()
    st = st or _state()
    dec, _meta = plan_tick(
        state=st, mid=100.0, seg_low=seg_low, seg_high=seg_high,
        seg_taker_sell=5.0, seg_taker_buy=5.0, now_ts=now,
        params=QuoteParams(w_base_bp=4.0, k_inv=0.5), limits=LaneRiskLimits(),
        equity=300.0, fill_notional=300.0, taker_fee_bp=4.0, maker_fee_bp=0.0,
        half_spread=0.01, sigma_norm=0.0, book=InventoryBook(),
        marks={"BTC": 100.0}, ofi=0.0, day_pnl_usd=0.0,
        pending={"up": 0.0, "down": 0.0, "gross": 0.0},
        lane_limits_enforce=True, judged_quote=judged_quote)
    return dec


def test_judged_quote_drives_the_fill_not_the_live_state():
    """① 显式传入的"延迟挂单"才是判定对象（状态里的挂单无关）。"""
    st = _state(quote_bid=0.0, quote_ask=0.0, quote_mid=0.0, quote_ts=time.time())
    dec = _run((99.5, 100.5, 100.0), st=st)      # 区间 low=99 < 99.5 ⇒ 买单成交
    assert dec.fills, "显式挂单被穿越时必须成交"
    f = dec.fills[0]
    assert abs(f.px - 99.5) < 1e-9, "成交价必须是**被判定挂单**的买价"


def test_no_fill_when_judged_quote_is_empty():
    """② 没有可用的延迟挂单时传 (0,0,0) ⇒ 本 tick 不判成交（宁可漏判不误判 ✓）。"""
    st = _state(quote_bid=99.5, quote_ask=100.5, quote_mid=100.0, quote_ts=time.time())
    dec = _run((0.0, 0.0, 0.0), st=st)
    assert not dec.fills, "判定对象为空时不得用状态里的挂单成交（否则是错时误判 ✗）"


def test_backward_compatible_default_is_live_state_quote():
    """③ 不传 judged_quote ⇒ 与旧行为逐字一致（回放/旧路径不受影响 ✓）。"""
    st = _state(quote_bid=99.5, quote_ask=100.5, quote_mid=100.0, quote_ts=time.time())
    dec = _run(None, st=st)
    assert dec.fills and abs(dec.fills[0].px - 99.5) < 1e-9


def test_lagged_quote_helper_picks_nearest_not_newer():
    """④ `_lagged_quote` 必须取"标签 ≤ 上界"的**最近一张**，不能取更新的单。"""
    from backend.services.market_maker.runner import ShadowRunner
    r = ShadowRunner(lane_id="t", venue="x", symbols=["BTC"])
    r._quote_hist["BTC"] = [
        {"basis": 1000.0, "bid": 1.0, "ask": 2.0, "mid": 1.5, "ts": 10.0},
        {"basis": 2000.0, "bid": 3.0, "ask": 4.0, "mid": 3.5, "ts": 20.0},
        {"basis": 3000.0, "bid": 5.0, "ask": 6.0, "mid": 5.5, "ts": 30.0},
    ]
    q = r._lagged_quote("BTC", 2500)
    assert q is not None and q["basis"] == 2000.0, "必须取 ≤ 上界的最近一张（不能取 3000）"
    assert r._lagged_quote("BTC", 500) is None, "上界早于所有挂单 ⇒ None（本 tick 不判）"


def test_runner_exposes_lag_gate_with_env_rollback():
    """⑤ L 必须可配置且 0 = 旧行为（出问题能即时回退，不用改代码 ✓）。"""
    import inspect
    from backend.services.market_maker.runner import ShadowRunner
    r = ShadowRunner(lane_id="t", venue="x", symbols=["BTC"])
    assert isinstance(r.judge_lag_buckets, int) and r.judge_lag_buckets >= 0
    src = inspect.getsource(ShadowRunner)
    assert "MM_JUDGE_LAG_BUCKETS" in src, "必须有环境变量回退开关"
    fetch = inspect.getsource(ShadowRunner.fetch_market)
    assert "_lagged_quote(" in fetch and "judge_lag_buckets" in fetch, \
        "取数窗口必须使用延迟上界与延迟挂单"
    tick = inspect.getsource(ShadowRunner.tick)
    assert "judged_quote=" in tick and "_quote_hist" in tick, \
        "tick 必须传延迟挂单并维护挂单历史"
