# -*- coding: utf-8 -*-
"""P3 新模板单测：全部模板可 parse + evaluate 出有限值，且数量增加。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import numpy as np
import pandas as pd
import pytest

from backend.services.evolution import factor_evolution_loop as fe


def _mk_df(n=220, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
    return pd.DataFrame({
        "open": close * (1 + rng.normal(0, 0.002, n)),
        "high": close * (1 + np.abs(rng.normal(0.001, 0.003, n))),
        "low": close * (1 - np.abs(rng.normal(0.001, 0.003, n))),
        "close": close,
        "volume": np.abs(rng.normal(1000, 200, n)) + 1.0,
    })


def test_new_template_families_present_and_finite():
    dfs = {"AAA": _mk_df(seed=1), "BBB": _mk_df(seed=2)}
    candidates = fe._mine_candidates(dfs, period="4h", quick=True)
    names = [name for _, name in candidates]
    # 新族存在
    for fam in ("vwapdev20", "vwapdev50", "brk20", "brk50",
                "body10", "body20", "rngpos10", "rngpos20",
                "vshake10", "vshake20", "ema_gap12_26"):
        assert fam in names, f"缺少新模板 {fam}"
    # 候选总数应明显大于旧 14 个种子
    assert len(candidates) >= 24, f"候选数应≥24, got {len(candidates)}"
    # 全部可 evaluate 出有限值（至少非全 NaN）
    from backend.services.factor_engine.expr.parser import parse as _p  # noqa: F401
    for expr_obj, name in candidates:
        df = dfs["AAA"]
        fields = fe._kline_to_fields(df)
        vals = np.asarray(expr_obj.evaluate(fields), dtype=float)
        assert len(vals) == len(df), name
        finite = np.isfinite(vals).sum()
        assert finite >= len(df) * 0.5, f"{name} 有限值过少: {finite}/{len(df)}"
