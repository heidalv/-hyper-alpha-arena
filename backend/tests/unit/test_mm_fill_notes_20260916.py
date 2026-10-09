# -*- coding: utf-8 -*-
"""[F256 2026-09-16] 成交备注环契约：每笔成交必须带判定上下文
（ts/symbol/side/qty/px/mid/spread_bp/price_bp/flatten/skip/action/sigma_norm/quote_age_s），
且环形缓冲上限 60 条。live↔model 差距（时代窗口 16~46×）靠事后回放已无法分辨，
下一段快盘由成交备注自解释。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.runner import ShadowRunner  # noqa: E402


def _make_runner():
    r = ShadowRunner(
        lane_id="t_f256", venue="x", symbols=["BTC"],
        equity=300.0, params=QuoteParams(
            side_mode="both", w_base_bp=10.0, k_inv=0.0, k_vol=0.0,
            frozen_width_bp=None),
        limits=LaneRiskLimits(
            max_net_directional_ratio=0.3, max_net_exposure_ratio=0.6,
            max_gross_notional_ratio=1.0, vol_pause_sigma=0.0,
            trend_pause_bp=0.0, stop_loss_bp=0.0, max_one_side_seconds=3600.0,
            ofi_block_threshold=0.0),
        fill_notional=30.0)
    r.judge_lag_buckets = 1
    r.compound_ratio = 0.0
    r.account_id = None
    r.save_states = lambda: None
    r._record_fills = lambda dec: None
    r._refresh_registry = lambda *a, **k: None
    return r


def _fake_market(r, clock):
    def fetch(since_ms):
        clock["t"] += 15.0
        snap_ms = int(clock["t"] * 1000)
        out = {}
        for s in r.symbols:
            hi_use = snap_ms - r.judge_lag_buckets * 15000
            jq = r._lagged_quote(s, hi_use)
            jq_t = ((float((jq or {}).get("bid") or 0.0),
                     float((jq or {}).get("ask") or 0.0),
                     float((jq or {}).get("mid") or 0.0)) if jq else (0.0, 0.0, 0.0))
            out[s] = {
                "ts_ms": snap_ms, "mid": 100.0,
                "half_spread": 0.05, "rel_spread": 0.001,
                "seg_low": 99.0, "seg_high": 101.0,
                "seg_sell": 1.0, "seg_buy": 1.0, "ofi": 0.0,
                "seg_lo_ms": hi_use - 15000, "seg_hi_ms": hi_use,
                "judged_quote": jq_t, "taker_sell_vol": 1.0, "taker_buy_vol": 1.0,
            }
        return out
    return fetch


def test_fill_notes_recorded_with_context(monkeypatch):
    r = _make_runner()
    clock = {"t": time.time()}
    monkeypatch.setattr(r, "fetch_market", _fake_market(r, clock))
    for _ in range(3):
        r.tick()
    assert len(r.fill_notes) > 0, "有成交就必须留下备注"
    f = r.fill_notes[-1]
    for k in ("ts", "symbol", "side", "qty", "px", "mid", "spread_bp",
              "price_bp", "flatten", "skip", "action", "sigma_norm", "quote_age_s"):
        assert k in f, f"备注缺字段 {k}: {f}"
    assert f["symbol"] == "BTC" and f["flatten"] is False


def test_fill_notes_capped_at_60(monkeypatch):
    r = _make_runner()
    r.fill_notes = [{"ts": 0.0} for _ in range(70)]
    # 环形上限由 append 路径维护；这里直接断言状态快照字段截断
    rep = r.status()
    assert len(rep["fill_notes"]) <= 60
