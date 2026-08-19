# -*- coding: utf-8 -*-
"""M5: BOCPD 变点检测命中 + Wyckoff 四相位规则。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import numpy as np
import pandas as pd

from backend.services.trend_inception import bocpd_change_prob, inception_check
from backend.services.wyckoff_phase import classify_phase


def test_bocpd_detects_mean_shift():
    """合成序列：前 400 根均值 0，后 400 根均值 2 → 变点概率在 ~400 处显著。"""
    rng = np.random.default_rng(0)
    x = np.concatenate([rng.normal(0.0, 0.5, 400), rng.normal(2.0, 0.5, 400)])
    cp = bocpd_change_prob(x)
    # 变点之后的最大概率应出现在后半段（>= 380）
    assert float(cp[380:].max()) > 0.3, f"变点概率应显著, max={cp[380:].max():.3f}"
    # 变点前（前 200 根）概率应较低
    assert float(cp[:200].max()) < float(cp[380:].max())


def test_inception_check_fields():
    """inception_check 输出含全部腿字段。"""
    n = 400
    rng = np.random.default_rng(1)
    close = pd.Series(100 * np.cumprod(1 + rng.normal(0.001, 0.02, n)))
    df = pd.DataFrame({"open": close * 0.99, "high": close * 1.02,
                       "low": close * 0.98, "close": close})
    out = inception_check(df)
    assert set(out.keys()) >= {"change_prob", "leg1_l1_flip", "leg2_bocpd",
                               "leg3_micro", "confirmed", "score"}


def test_wyckoff_markup():
    """上升趋势 + OBV 上行 → markup。"""
    n = 200
    rng = np.random.default_rng(2)
    close = pd.Series(100 * np.cumprod(1 + rng.normal(0.004, 0.01, n)))
    volume = pd.Series(1000 * np.ones(n) * (1 + 0.5 * (close.pct_change().clip(0, None))))
    df = pd.DataFrame({"open": close * 0.99, "high": close * 1.02,
                       "low": close * 0.98, "close": close, "volume": volume})
    out = classify_phase(df)
    assert out["phase"] in ("markup", "transition"), out


def test_wyckoff_markdown():
    """下降趋势 + OBV 下行 → markdown。"""
    n = 200
    rng = np.random.default_rng(3)
    close = pd.Series(100 * np.cumprod(1 - rng.normal(0.004, 0.01, n)))
    volume = pd.Series(1000 * np.ones(n))
    df = pd.DataFrame({"open": close * 1.01, "high": close * 1.02,
                       "low": close * 0.98, "close": close, "volume": volume})
    out = classify_phase(df)
    assert out["phase"] in ("markdown", "transition"), out
