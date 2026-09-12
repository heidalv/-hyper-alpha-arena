# -*- coding: utf-8 -*-
"""[2026-09-12 F75] L1 做市回放/实盘同口径契约。

现场：实盘每个穿越段全量吃 $100，回放只吃 F59_QUEUE_SHARE(0.30)×区间主动量——
库存摆动 ~3× 大、漂移亏损 ~3× 大，回放正收益配置在实盘变负的根因
（实盘 7 天：spread +5.9bp / price -8.0bp / net -3.28bp；同配置回放 +4.59bp）。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402


def _mk_state():
    return mmrunner.SymbolState(symbol="BTC")


def test_fill_qty_limited_by_queue_share():
    """区间主动量小 → 成交数量 = 主动量×QUEUE_SHARE，而非全额 $100。"""
    st = _mk_state()
    st.quote_bid = 99.0
    st.quote_mid = 100.0
    st.quote_ts = 1_000_000.0
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.0, seg_low=98.9, seg_high=100.1,
        seg_taker_sell=1.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0, params=QuoteParams(), limits=LaneRiskLimits(),
    )
    # leg_qty = 100/100 = 1.0；队列份额 = 1.0 × 0.30 = 0.3 → qty=0.3
    assert dec.fills, "穿越段应成交"
    assert dec.fills[0].qty == pytest.approx(0.3, rel=1e-6), dec.fills[0].qty


def test_micro_fill_below_min_notional_skipped():
    """队列份额后名义 < MIN_FILL_NOTIONAL($10) → 跳过成交（与回放一致）。"""
    st = _mk_state()
    st.quote_bid = 99.0
    st.quote_mid = 100.0
    st.quote_ts = 1_000_000.0
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.0, seg_low=98.9, seg_high=100.1,
        seg_taker_sell=0.2, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0, params=QuoteParams(), limits=LaneRiskLimits(),
    )
    # 0.2 × 0.30 = 0.06 qty × 99 ≈ $5.94 < $10 → 无成交
    assert not dec.fills, dec.fills


def test_plan_tick_uses_replay_queue_constants():
    """单一来源：队列份额/最小名义必须来自 replay 模块（避免两处常量分裂）。"""
    src = inspect.getsource(mmrunner.plan_tick)
    assert "QUEUE_SHARE" in src and "MIN_FILL_NOTIONAL" in src
    from backend.services.market_maker import replay as _rp
    assert src.find("market_maker.replay") > 0


def test_report_flattens_same_window_as_fills():
    """报表 flattens 必须与 fills 同窗口同源（此前进程内计数 vs 30 天账本分裂）。"""
    src = inspect.getsource(mmrunner.ShadowRunner.report)
    assert '"flattens": int((fstats or {}).get("flattens") or 0)' in src
    assert "flatten_stats" in src
