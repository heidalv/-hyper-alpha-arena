# -*- coding: utf-8 -*-
"""M6: 宏观顺逆风滞后相关 + 减半周期相位。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import numpy as np
import pandas as pd

from backend.services.macro_tailwind import _lag_corr
from backend.services.halving_phase import compute_halving_phase


def test_lag_corr_detects_lead():
    rng = np.random.default_rng(0)
    n = 300
    dxy_ret = rng.normal(0, 0.01, n)
    noise = rng.normal(0, 0.005, n)
    btc_ret = -0.6 * np.roll(dxy_ret, 5) + noise
    btc_ret[:5] = noise[:5]
    k, c = _lag_corr(dxy_ret, btc_ret)
    assert k == 5, f"should detect lead 5, got k={k}"
    assert c < -0.2, f"should be negative corr, got {c:.3f}"


def test_halving_phase_current_cycle():
    now = pd.Timestamp("2026-08-19").timestamp()
    out = compute_halving_phase(now_ts=now)
    assert out["phase"] in ("初涨", "主升"), out
    assert out["days_since_halving"] is not None and out["days_since_halving"] > 700
    assert 0.5 <= out["position_mult"] <= 1.0
