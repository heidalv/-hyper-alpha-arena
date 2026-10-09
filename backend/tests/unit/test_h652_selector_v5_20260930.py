# -*- coding: utf-8 -*-
"""[h652] h329_selector_v5 纯函数测试。"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "h329v5", ROOT / "scripts" / "h329_selector_v5.py")
v5 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v5)  # type: ignore[union-attr]

SEED = {
    "p1": {"NEAR": {"edge": 1.10, "freq": 54.0}, "BNB": {"edge": -0.32, "freq": None}},
    "p5": {"UNI": {"edge": 2.17, "freq": 55.9}, "BNB": {"edge": 0.54, "freq": None}},
    "p4": {"SOL": {"edge": 1.0, "freq": 27.9}},
    "negative_seed": ["XMR"],
    "vr60_seed": {},
}


def test_spread_ok_share():
    assert v5.spread_ok_share([0.6, 0.4, 0.5], thr=0.5) == pytest.approx(2 / 3)
    assert v5.spread_ok_share([], thr=0.5) == 0.0
    assert v5.spread_ok_share([0.1, 0.2]) == 0.0


def test_vr60_trending_above_one():
    # 线性上涨序列:60s 方差比远大于 1(动量)
    mids = [100.0 + i * 0.1 for i in range(120)]
    vr = v5.vr60_from_mids(mids)
    assert vr is not None and vr > 1.0


def test_vr60_thin_returns_none():
    assert v5.vr60_from_mids([100.0, 101.0]) is None
    assert v5.vr60_from_mids([]) is None


def test_family_threshold():
    assert v5.family(1.29) == "reversal"
    assert v5.family(1.39) == "momentum"
    assert v5.family(None) == "unknown"


def test_score_coin_bnb_momentum_only():
    # BNB vr=1.39:h367 反转族失效 ⇒ 即使 p1 在榜,patterns 也不该含 p1
    sc = v5.score_coin(SEED, "BNB", 1.39)
    assert "p1" not in sc["patterns"]
    assert "p5" in sc["patterns"]
    assert sc["max_edge"] == pytest.approx(0.54)


def test_score_coin_neg_seed_flag():
    sc = v5.score_coin(SEED, "XMR", 1.0)
    assert sc["neg_seed"] is True


def test_choose_slots_respects_max_new_and_families():
    scored = [
        {"symbol": "UNI", "family": "momentum", "max_edge": 2.17, "patterns": ["p5"]},
        {"symbol": "NEAR", "family": "reversal", "max_edge": 1.10, "patterns": ["p1"]},
        {"symbol": "SOL", "family": "reversal", "max_edge": 1.00, "patterns": ["p4"]},
    ]
    uni, dropped = v5.choose_slots(scored, slots=2, rev_slots=1, max_new=2,
                                   current=[])
    assert len(uni) == 2
    # 动量槽 1 个:应含 UNI(动量族最高)
    assert "UNI" in uni
    # 反转槽 1 个:NEAR
    assert "NEAR" in uni


def test_choose_slots_keeps_current_when_qualified():
    scored = [
        {"symbol": "UNI", "family": "momentum", "max_edge": 2.17, "patterns": ["p5"]},
        {"symbol": "NEAR", "family": "reversal", "max_edge": 1.10, "patterns": ["p1"]},
        {"symbol": "SOL", "family": "reversal", "max_edge": 1.00, "patterns": ["p4"]},
    ]
    # 现役 SOL/UNI 都合格且已满槽 ⇒ 不强行换人(轮换只发生在现役币失去资格时,v5.1)
    uni, dropped = v5.choose_slots(scored, slots=2, rev_slots=1, max_new=1,
                                   current=["SOL", "UNI"])
    assert uni == ["SOL", "UNI"] and dropped == []


def test_choose_slots_rotates_unqualified_current():
    scored = [
        {"symbol": "UNI", "family": "momentum", "max_edge": 2.17, "patterns": ["p5"]},
        {"symbol": "NEAR", "family": "reversal", "max_edge": 1.10, "patterns": ["p1"]},
    ]
    # SOL 失去资格(L1 衰减,不在 scored)⇒ 被换出,NEAR 顶替
    uni, dropped = v5.choose_slots(scored, slots=2, rev_slots=1, max_new=2,
                                   current=["SOL", "UNI"])
    assert set(uni) == {"UNI", "NEAR"}
    assert dropped == ["SOL"]


def test_build_pattern_matrix_contexts():
    scored = [
        {"symbol": "NEAR", "family": "reversal", "patterns": ["p1"], "max_edge": 1.1},
        {"symbol": "UNI", "family": "momentum", "patterns": ["p5", "p4"], "max_edge": 2.17},
    ]
    pm = v5.build_pattern_matrix(["NEAR", "UNI"], scored)
    assert pm["NEAR"] == ["P1"]
    assert pm["UNI"] == ["P45"]
