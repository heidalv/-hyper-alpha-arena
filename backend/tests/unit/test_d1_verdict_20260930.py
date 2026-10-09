# -*- coding: utf-8 -*-
"""[h650/D1] d1_verdict 纯函数测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import importlib.util  # noqa: E402
import pytest  # noqa: E402

# [h651] 归因已迁移到共享模块:测试直接测 attribution 的纯函数
from backend.services.market_maker import attribution as dv  # noqa: E402

pair_roundtrips = dv.pair_roundtrips_chrono
mid_at = dv.mid_at
trend_bp_at = dv.trend_bp_at
bucket_side = dv.bucket_side
welch_two_sided = dv.welch_two_sided


def _leg(sym="BTC", pid="mm:BTC:1", ts=1000.0, side="buy", ep="",
         notional=100.0, net_bp=1.0, quote_ts=0.0, qty=1.0):
    return {"symbol": sym, "position_id": pid, "ts_epoch": ts, "side": side,
            "exit_path": ep, "notional": notional, "net_bp": net_bp,
            "quote_ts": quote_ts, "qty": qty}


def test_pair_chrono_matches_open_and_close():
    legs = [
        _leg(ts=1000.0, side="buy", net_bp=2.0, notional=100.0, qty=1.0),
        _leg(ts=1060.0, side="sell", net_bp=-3.0, notional=100.0, qty=1.0),
    ]
    trips = pair_roundtrips(legs, since_epoch=900.0)
    assert len(trips) == 1
    t = trips[0]
    assert t["side"] == "buy" and t["n_legs"] == 2
    assert t["net_bp"] == pytest.approx((2.0 * 100 - 3.0 * 100) / 200.0)
    assert t["open_ts"] == 1000.0


def test_pair_chrono_adds_and_flip():
    legs = [
        _leg(ts=1000.0, side="buy", qty=1.0, net_bp=1.0),
        _leg(ts=1010.0, side="buy", qty=0.5, net_bp=1.0),
        _leg(ts=1020.0, side="sell", qty=2.0, net_bp=-2.0),   # 平 1.5 反手 0.5
    ]
    trips = pair_roundtrips(legs, since_epoch=900.0)
    assert len(trips) == 1          # 反手后的新往返窗口内未平,不产出
    assert trips[0]["side"] == "buy"
    assert trips[0]["n_legs"] == 3


def test_pair_chrono_window_edge_pre_window_open_dropped():
    legs = [
        _leg(ts=800.0, side="buy", qty=1.0, net_bp=1.0),   # 开在窗前
        _leg(ts=1100.0, side="sell", qty=1.0, net_bp=-1.0),  # 平在窗内
    ]
    assert pair_roundtrips(legs, since_epoch=1000.0) == []


def test_pair_chrono_skips_bad_side_and_no_qty():
    legs = [
        _leg(ts=1000.0, side="hold", qty=1.0),
        _leg(ts=1000.0, side="buy", qty=0.0),
    ]
    assert pair_roundtrips(legs, since_epoch=900.0) == []


def test_mid_at_nearest_within_tol():
    st = [100.0, 115.0, 130.0]
    sm = [10.0, 11.0, 12.0]
    assert mid_at(st, sm, 116.0) == 11.0
    assert mid_at(st, sm, 115.0) == 11.0
    assert mid_at(st, sm, 200.0) is None
    assert mid_at(st, sm, 0.0) is None


def test_trend_bp_at():
    st = [100.0, 400.0, 700.0, 1000.0]
    sm = [100.0, 101.0, 103.0, 104.0]
    # quote_ts=1000:mid=104;quote−300=700:mid=103 ⇒ (104−103)/103×1e4≈97.09bp
    assert trend_bp_at(st, sm, 1000.0) == pytest.approx(1.0 / 103.0 * 1e4)
    assert trend_bp_at(st, sm, 50.0) is None      # 300s 前无数据
    assert trend_bp_at(st, sm, 0.0) is None


def test_bucket_side():
    assert bucket_side("buy", 5.0) == "with"
    assert bucket_side("buy", -5.0) == "against"
    assert bucket_side("sell", 5.0) == "against"
    assert bucket_side("sell", -5.0) == "with"
    assert bucket_side("buy", None) == "unknown"


def test_welch_identical_series():
    a = [1.0, 2.0, 3.0, 4.0, 5.0]
    b = [1.0, 2.0, 3.0, 4.0, 5.0]
    r = welch_two_sided(a, b)
    assert r["p"] == pytest.approx(1.0, abs=1e-9)


def test_welch_thin_series():
    r = welch_two_sided([1.0], [2.0])
    assert r["p"] is None and r["note"] == "样本不足"
