# -*- coding: utf-8 -*-
"""M7: 博弈确认（费率回归/清算企稳）+ 叙事标签骨架 + B1 三选二确认。"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import numpy as np
import pandas as pd

from backend.services.microstructure_gauge import funding_mean_reversion, gauge
from backend.services.narrative_tag import suggest_narrative_tags, sector_perf_table
from backend.services.trend_inception import inception_check


def test_funding_mean_reversion():
    s = [0.001, 0.0008, 0.0006, 0.0004, 0.0002, 0.00015, 0.0001, 0.00005]
    assert funding_mean_reversion(s) is True
    s2 = [0.00002, 0.00003, 0.00002, 0.00003, 0.00002, 0.00003, 0.00002, 0.00003]
    assert funding_mean_reversion(s2) is False  # 从未极端
    s3 = [0.001, 0.001, 0.001, 0.001, 0.001, 0.001, 0.001, 0.001]
    assert funding_mean_reversion(s3) is False  # 极端未回归


def test_gauge_fields():
    db = MagicMock()
    with patch("backend.services.microstructure_gauge._funding_series", return_value=None), \
         patch("backend.services.microstructure_gauge.liquidation_stabilize", return_value=False):
        out = gauge(db, "BTC")
    assert out["vpin"] is None and out["confirmed"] is False


def test_narrative_tags_disabled_default():
    out = suggest_narrative_tags()
    assert out == {"tags": [], "llm": False}  # 默认关，不调 LLM


def test_inception_three_legs():
    """腿3 就绪后三选二确认。"""
    n = 400
    rng = np.random.default_rng(4)
    close = pd.Series(100 * np.cumprod(1 + rng.normal(0.001, 0.02, n)))
    df = pd.DataFrame({"open": close * 0.99, "high": close * 1.02,
                       "low": close * 0.98, "close": close})
    # 腿1 恒 False（无翻多），腿2 未知 → 腿3=True 也只能得 1/3 票 → 不确认
    out = inception_check(df, l1_states=pd.Series(["sideways"] * n).values, leg3=True)
    assert out["confirmed"] is False
    assert out["leg3_micro"] is True
    assert out["score"] <= 0.34
