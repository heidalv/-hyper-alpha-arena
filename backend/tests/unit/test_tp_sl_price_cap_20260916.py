# -*- coding: utf-8 -*-
"""[2026-09-16 调研轮7] TP/SL **价格层**止损距离封顶契约测试。

背景：`compute_initial_tp_sl_prices` 是挂单 TP/SL 价格的权威计算点，其
`_vol_mult = ATR/1%`（上限 3.0）会把 tier 默认 SL 放大 0.7~3.0 倍：
实测 mid 3.5% × 1.33 = **4.67%**、long **6.5%**，而赢家最大逆行 MAE 仅 1.20%（n=18）
⇒ avg_loss(-17.10) > avg_win(+13.90)、期望为负。入场执行层（midlong_helpers）
的同款上限**覆盖不到**这里的放大，故必须在本层封顶（现场：ETH 4686 开仓即 4.67%）。

回滚：MIDLONG_MAX_SL_PCT_MID / _LONG = 0。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.config import settings  # noqa: E402
from backend.services.full_auto.tp_sl_prices import compute_initial_tp_sl_prices as f  # noqa: E402


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(settings, "MIDLONG_MAX_SL_PCT_MID", 0.02, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_MAX_SL_PCT_LONG", 0.03, raising=False)
    yield


def test_mid_stop_capped_at_2pct():
    """复现 ETH 4686 现场（ATR 1.33% → 旧口径 4.67%），新口径必须 ≤2%。"""
    _tp, sl, src = f("mid", "buy", 100.0, atr_pct=0.0133, sym="ETH", atr_1d_pct=0.031)
    dist = abs(sl - 100.0) / 100.0
    assert dist <= 0.02 + 1e-9, f"mid 止损距离 {dist:.4%} 未封顶 (src={src})"
    assert "sl_capped" in src


def test_long_stop_capped_at_3pct():
    _tp, sl, src = f("long", "buy", 100.0, atr_pct=0.0133, sym="BTC", atr_1d_pct=0.031)
    dist = abs(sl - 100.0) / 100.0
    assert dist <= 0.03 + 1e-9, f"long 止损距离 {dist:.4%} 未封顶 (src={src})"


def test_short_lane_untouched():
    """scalp/short 车道不改（其止损口径独立）。"""
    _tp, sl, _src = f("short", "buy", 100.0, atr_pct=0.02, sym="ETH")
    assert abs(sl - 100.0) / 100.0 > 0.02


def test_sell_side_symmetric():
    _tp, sl, _src = f("mid", "sell", 100.0, atr_pct=0.0133, sym="XRP", atr_1d_pct=0.031)
    assert sl > 100.0 and abs(sl - 100.0) / 100.0 <= 0.02 + 1e-9


def test_rollback_zero_disables_cap(monkeypatch):
    monkeypatch.setattr(settings, "MIDLONG_MAX_SL_PCT_MID", 0.0, raising=False)
    _tp, sl, _src = f("mid", "buy", 100.0, atr_pct=0.0133, sym="ETH", atr_1d_pct=0.031)
    assert abs(sl - 100.0) / 100.0 > 0.02, "cap=0 必须回到旧口径"
