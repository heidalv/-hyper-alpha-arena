# -*- coding: utf-8 -*-
"""[h667] 模拟按真实交易所条件——venue_filters 纯函数测试(用真实拉到的过滤器)。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import venue_filters as V  # noqa: E402


def test_filters_loaded_from_real_exchange_info():
    f = V.filters_for("UNI")
    assert f.get("step_size") == 1.0      # 真实值:UNI 必须整币
    assert f.get("min_notional") == 5.0
    b = V.filters_for("BNB")
    assert b.get("step_size") == 0.01
    assert b.get("pct_mult_up") == 1.02   # BNB ±2% 带


def test_round_qty_uni_integer():
    # UNI stepSize=1.0:2.003 会被真实交易所拒(对齐后 2.0,不再是 2.003)
    q = V.round_qty("UNI", 2.003)
    assert q == 2.0
    # 小于 minQty(1.0)⇒ 0(拒单)
    assert V.round_qty("UNI", 0.4) == 0.0


def test_round_qty_bnb_step():
    q = V.round_qty("BNB", 0.017)
    assert q == pytest.approx(0.02, abs=1e-9)   # 0.01 步进


def test_round_px_tick():
    assert V.round_px("BNB", 771.885) == 771.89    # tickSize 0.01
    assert V.round_px("ENA", 0.275085) == 0.27509  # tickSize 1e-5


def test_passes_min_notional_and_band():
    ok, why = V.passes("UNI", 8.9, 1.0, mark_px=9.0)
    assert ok, why
    # 名义 $4.45 < $5 ⇒ 拒(减仓豁免)
    ok2, _ = V.passes("UNI", 8.9, 0.5, mark_px=9.0)
    assert not ok2
    ok3, _ = V.passes("UNI", 8.9, 0.5, mark_px=9.0, reduce_only=True)
    assert ok3
    # 价格带:BNB 挂单超过 mark +2% ⇒ 拒
    ok4, why4 = V.passes("BNB", 800.0, 0.1, mark_px=771.0)
    assert not ok4 and "pct_band" in why4


def test_order_rate_limit():
    rl = V.OrderRateLimit(max_per_min=5)
    t0 = 1_000_000.0
    for i in range(5):
        assert rl.allow(1, t0 + i)
    assert not rl.allow(1, t0 + 5)      # 第 6 单:模拟 429
    assert rl.allow(1, t0 + 61)         # 60s 滑窗后恢复


def test_quote_ops_ignore_a_look_with_no_order_change():
    assert V.quote_ops(0, 0, 0, 0) == 0
    assert V.quote_ops(1.2, 0, 1.2, 0) == 0
    assert V.quote_ops(0, 0, 1.2, 0) == 1
    assert V.quote_ops(1.2, 0, 0, 0) == 1
    assert V.quote_ops(1.2, 0, 1.3, 0) == 2
