# -*- coding: utf-8 -*-
"""[调研轮8] 看板候选**流动性下限**契约测试（上游过滤）。

背景：catalog 白名单里仍混着本所几乎无法交易的标的（实测 CRCL liquidity=0.0026、
ALICE/AIO=0.25 都能被 AI approve 进看板），而下游 VIP跟投/中线路由按
「24h成交额 < 试仓下限 $500k」硬拒 ⇒ 看板 approve 大量是"下不了单"的名字，
AI 选币因此在实盘空转。修复：在候选白名单层按看板自算的 liquidity 分过滤。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services import coin_select_platform_service as csp  # noqa: E402


@pytest.fixture(autouse=True)
def _catalog(monkeypatch):
    # 让 catalog 白名单包含测试用的全部 symbol（隔离「非 catalog」这条既有过滤）
    monkeypatch.setattr(csp, "_LIQUID_PREF", ("BTC", "ETH", "FET", "CRCL", "ALICE", "NEWX", "DOGE"))
    import backend.services.kline_sync_meta as ksm

    monkeypatch.setattr(ksm, "list_catalog_symbols", lambda ex, status=None: [], raising=False)
    yield


def _cand(sym, liq=None, **kw):
    c = {"symbol": sym, "score": 0.5}
    if liq is not None:
        c["market_scores"] = {"liquidity": liq}
    c.update(kw)
    return c


def test_illiquid_candidates_dropped():
    out = csp._fail_closed_filter([
        _cand("BTC", 1.0), _cand("FET", 0.71), _cand("DOGE", 0.6),
        _cand("CRCL", 0.0026), _cand("ALICE", 0.25),
    ])
    syms = [c["symbol"] for c in out]
    assert syms == ["BTC", "FET", "DOGE"], syms


def test_missing_liquidity_is_fail_open():
    """无 liquidity 字段（老候选/未评分链路）不得被误杀。"""
    out = csp._fail_closed_filter([_cand("NEWX"), _cand("BTC", 1.0)])
    assert {c["symbol"] for c in out} == {"NEWX", "BTC"}


def test_floor_zero_disables(monkeypatch):
    monkeypatch.setenv("COIN_SELECT_PLATFORM_MIN_LIQUIDITY", "0")
    out = csp._fail_closed_filter([_cand("CRCL", 0.0026), _cand("BTC", 1.0)])
    assert {c["symbol"] for c in out} == {"CRCL", "BTC"}, "0 必须回滚为不过滤"


def test_liquidity_reader_handles_shapes():
    assert csp._candidate_liquidity({"market_scores": {"liquidity": 0.4}}) == pytest.approx(0.4)
    assert csp._candidate_liquidity({"liquidity": "0.7"}) == pytest.approx(0.7)
    assert csp._candidate_liquidity({}) is None
    assert csp._candidate_liquidity({"market_scores": {"liquidity": "bad"}}) is None
