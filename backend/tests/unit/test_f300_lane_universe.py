# -*- coding: utf-8 -*-
"""[F300 2026-09-16] L1 车道标的切换的契约测试。

背景：事件研究（9,379 个被动成交事件）筛出新宇宙 ZEC/ASTER/SOL/DOGE/TAO，
替换旧的 LINK/ADA。本测试锁定三件事，防止将来有人改宇宙时漏掉配套项：

  1. `SYMBOL_STEP` 必须覆盖兜底宇宙的每一个标的 —— 否则 F278 步长闸对该币
     **fail-open**（等于没有保护）。ZEC/ASTER/TAO 就是本次因此补进表的。
  2. 表内数值必须与交易所规格一致（stepSize / minQty / minNotional）。
  3. 单腿名义（影子 $300 × compound_ratio 0.1 = $30）必须真的可下单 ——
     BTC 就是被这一条排除的（0.001 × $75,818 = $75.82 > $30）。
"""
from __future__ import annotations

import pytest

from backend.services.market_maker.core import SYMBOL_STEP, leg_qty_compliant
from backend.services.market_maker.runner import DEFAULT_SYMBOLS

# 交易所实测（fapi.asterdex.com/fapi/v1/exchangeInfo，2026-09-16）
EXPECTED_SPECS = {
    "ZEC":   {"step": 0.001, "min_qty": 0.001, "min_notional": 5.0},
    "ASTER": {"step": 0.01,  "min_qty": 0.01,  "min_notional": 5.0},
    "TAO":   {"step": 0.001, "min_qty": 0.001, "min_notional": 5.0},
    "SOL":   {"step": 0.01,  "min_qty": 0.01,  "min_notional": 5.0},
    "DOGE":  {"step": 1.0,   "min_qty": 1.0,   "min_notional": 5.0},
}
# 参考价（仅用于合规性检查，取实测附近的量级）
REF_PRICE = {"ZEC": 1184.0, "ASTER": 0.676, "TAO": 216.9, "SOL": 97.0, "DOGE": 0.0799}


def test_fallback_universe_is_the_event_study_selection():
    assert DEFAULT_SYMBOLS == ["ZEC", "ASTER", "SOL", "DOGE", "TAO"]


def test_every_universe_symbol_is_in_the_step_table():
    missing = [s for s in DEFAULT_SYMBOLS if s not in SYMBOL_STEP]
    assert not missing, (
        "这些标的缺 SYMBOL_STEP 条目 ⇒ F278 步长闸会 fail-open（无保护）：%s" % missing)


@pytest.mark.parametrize("sym", sorted(EXPECTED_SPECS))
def test_step_table_matches_exchange_spec(sym):
    assert sym in SYMBOL_STEP
    got = SYMBOL_STEP[sym]
    exp = EXPECTED_SPECS[sym]
    for k, v in exp.items():
        assert abs(float(got[k]) - v) < 1e-12, "%s.%s = %s, 期望 %s" % (sym, k, got[k], v)


@pytest.mark.parametrize("sym", sorted(EXPECTED_SPECS))
def test_universe_symbol_is_tradeable_at_the_lane_leg_size(sym):
    """影子单腿 = 权益 300 × compound_ratio 0.1 = $30。必须真的可下单。"""
    ok, qty, why = leg_qty_compliant(sym, 30.0, REF_PRICE[sym])
    assert ok, "%s 在 $30 单腿下不可下单：%s" % (sym, why)
    assert qty > 0


def test_btc_is_excluded_because_of_contract_specs_not_taste():
    """BTC 边际为正（+3.26bp）但 min leg = 0.001 × ~$75,800 = $75.82 > $30 ⇒
    规格上不可下单。锁定这条，避免有人凭收益把它加回宇宙。"""
    ok, qty, why = leg_qty_compliant("BTC", 30.0, 75818.0)
    assert not ok, "BTC 在 $30 单腿下竟然通过了闸门？规格复核"
    assert "below_step" in why
    assert qty == 0.0


def test_no_symbol_in_the_universe_is_dropped_by_the_gate_at_real_notional():
    """以【表内币种】的合规检查为契约：宇宙内任何币在 $30 腿下都不得被拒。"""
    bad = []
    for s in DEFAULT_SYMBOLS:
        ok, qty, why = leg_qty_compliant(s, 30.0, REF_PRICE[s])
        if not ok:
            bad.append("%s: %s" % (s, why))
    assert not bad, "宇宙内有不可下单的标的：%s" % bad
