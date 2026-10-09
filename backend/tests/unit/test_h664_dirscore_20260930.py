# -*- coding: utf-8 -*-
"""[h664] 方向分数纯函数测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import dirscore as D  # noqa: E402


def test_direction_score_bounds_and_signs():
    assert D.direction_score(0.0, 0.0, 0.0) == pytest.approx(0.0, abs=1e-9)
    # 全看涨分量 ⇒ 正且 ≤1
    d = D.direction_score(mp_skew_bp=2.0, ofi=1.0, trend_bp=20.0)
    assert 0.99 <= d <= 1.0
    # 全看跌 ⇒ 负且 ≥−1
    d2 = D.direction_score(mp_skew_bp=-2.0, ofi=-1.0, trend_bp=-20.0)
    assert -1.0 <= d2 <= -0.99


def test_direction_score_weight_dominance():
    # 流权重最高(0.40):流负、其它零 ⇒ d = −0.40
    d = D.direction_score(0.0, -1.0, 0.0)
    assert d == pytest.approx(-0.4, abs=1e-9)


def test_direction_score_toxicity_veto():
    # 毒性 1.0 ⇒ 完全否决(乘法归零)
    assert D.direction_score(2.0, 1.0, 20.0, toxicity=1.0) == pytest.approx(0.0, abs=1e-9)
    # 毒性 0.5 ⇒ 折半
    d = D.direction_score(2.0, 1.0, 20.0, toxicity=0.5)
    assert d == pytest.approx(0.5, abs=0.01)


def test_direction_score_saturation():
    # 饱和:超饱和输入不越界
    d = D.direction_score(mp_skew_bp=50.0, ofi=3.0, trend_bp=200.0)
    assert -1.0 <= d <= 1.0
