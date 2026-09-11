# -*- coding: utf-8 -*-
"""[P3.2 2026-09-03] 中性化 β 训练窗拟合、验证窗套用。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import numpy as np
import pytest

from backend.services.factor_engine import neutralization as neu


def _make_panel(rng, S=5, B=600, beta_shift=False):
    """r_t = b_t·mkt_t + ε_t；beta_shift=True 时后 30% 时间 β 从 3 变为 1（β 漂移）。"""
    mkt = rng.normal(0, 1e-3, B)
    eps = rng.normal(0, 1e-4, (S, B))
    b = np.full(B, 3.0)
    if beta_shift:
        b[int(B * 0.7):] = 1.0
    panels = {}
    ts0 = 1_700_000_000
    for s in range(S):
        r = b * mkt + eps[s]
        close = 100 * np.cumprod(1.0 + r)
        ts = (ts0 + np.arange(B) * 3600).astype(np.float64)
        panels[f"S{s}"] = (ts, close)
    return panels, mkt, eps


class TestTrainRowMask:
    def test_ratio_one_is_full_window(self):
        ts = np.repeat(np.arange(100), 3)
        m, cut = neu._train_row_mask(ts, fwd=2, ratio=1.0)
        assert m.all() and cut is None

    def test_train_window_purges_fwd_bars(self):
        ts = np.repeat(np.arange(100), 3)  # 100 个时间戳 × 3 币
        m, cut = neu._train_row_mask(ts, fwd=2, ratio=0.7)
        # 前 70 个时间戳 → 下标 0..69，再 purge 2 → 训练窗到下标 67
        assert cut == 67
        assert m.sum() == 68 * 3
        assert not m[ts > 67].any()

    def test_too_short_gives_empty_mask(self):
        ts = np.arange(3)
        m, cut = neu._train_row_mask(ts, fwd=5, ratio=0.7)
        assert not m.any() and cut is None


class TestBuildNeutralized:
    def test_default_fits_on_train_window_only(self):
        rng = np.random.default_rng(0)
        panels, _, _ = _make_panel(rng)
        out = neu.build_neutralized_returns(panels, 2, beta_train_ratio=0.7)
        assert len(out) == len(panels)
        info = neu.last_fit_info
        assert info["fit_scope"] == "train"
        assert 0 < info["n_train"] < info["n_total"]
        assert info["n_train"] <= int(info["n_total"] * 0.7) + len(panels)
        # 验证段（训练截止之后）也有残差（β 套用到全窗）
        ts0 = panels["S0"][0]
        later = ts0.astype("int64") * 10**9 > int(info["cut_ts"])
        assert np.isfinite(out["S0"][later][:-2]).mean() > 0.9

    def test_ratio_one_reproduces_legacy_full_window(self):
        rng = np.random.default_rng(1)
        panels, _, _ = _make_panel(rng)
        full = neu.build_neutralized_returns(panels, 2, beta_train_ratio=1.0)
        assert neu.last_fit_info["fit_scope"] == "full"
        assert neu.last_fit_info["n_train"] == neu.last_fit_info["n_total"]
        # 与旧实现等价：全窗口 OLS 残差（用同一 frame 重算对照）
        frame, _ = neu._panel_frames(panels, 2)
        frame["mkt"] = frame.groupby("ts")["fwd_ret"].transform("mean")
        reg = frame[["ts", "sym", "fwd_ret", "mkt", "mom", "vol"]].dropna()
        X = np.column_stack([np.ones(len(reg)), reg["mkt"], reg["mom"], reg["vol"]])
        y = reg["fwd_ret"].to_numpy()
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ beta
        resid -= resid.mean()
        got = np.concatenate([v for v in full.values()])
        got = got[np.isfinite(got)]
        assert len(got) == len(resid)
        assert np.std(got) == pytest.approx(np.std(resid), rel=1e-6)
        assert np.mean(got) == pytest.approx(0.0, abs=1e-12)

    def test_validation_labels_do_not_move_beta(self):
        """β 只由训练窗决定：改动验证段的收益不应改变拟合出的 β。"""
        rng = np.random.default_rng(2)
        panels, _, _ = _make_panel(rng)
        neu.build_neutralized_returns(panels, 2, beta_train_ratio=0.7)
        beta_a = list(neu.last_fit_info["beta"])
        cut_ts = int(neu.last_fit_info["cut_ts"])
        # 篡改所有币验证段（cut_ts 之后再空 25 根）的 close：加一段强趋势
        tampered = {}
        for s, (ts, close) in panels.items():
            ts_ns = ts.astype("int64") * 10**9
            start = int(np.searchsorted(ts_ns, cut_ts)) + 25
            c = close.copy()
            c[start:] = c[start:] * np.cumprod(np.full(len(c) - start, 1.002))
            tampered[s] = (ts, c)
        neu.build_neutralized_returns(tampered, 2, beta_train_ratio=0.7)
        beta_b = list(neu.last_fit_info["beta"])
        assert beta_a == pytest.approx(beta_b, rel=1e-6, abs=1e-9)

    def test_full_window_beta_is_moved_by_validation_labels(self):
        """对照：旧全窗口口径下，验证段篡改会改变 β（这正是要修的泄漏）。"""
        rng = np.random.default_rng(3)
        panels, _, _ = _make_panel(rng)
        neu.build_neutralized_returns(panels, 2, beta_train_ratio=1.0)
        beta_a = list(neu.last_fit_info["beta"])
        tampered = {}
        for s, (ts, close) in panels.items():
            c = close.copy()
            start = int(len(c) * 0.75)
            c[start:] = c[start:] * np.cumprod(np.full(len(c) - start, 1.002))
            tampered[s] = (ts, c)
        neu.build_neutralized_returns(tampered, 2, beta_train_ratio=1.0)
        beta_b = list(neu.last_fit_info["beta"])
        assert beta_a != pytest.approx(beta_b, rel=1e-6, abs=1e-9)

    def test_short_train_window_falls_back(self):
        rng = np.random.default_rng(4)
        panels, _, _ = _make_panel(rng, S=2, B=80)
        neu.build_neutralized_returns(panels, 2, beta_train_ratio=0.3)
        assert neu.last_fit_info.get("fit_scope") in ("full_fallback", "train")
        if neu.last_fit_info.get("fit_scope") == "full_fallback":
            assert neu.last_fit_info["n_train"] == neu.last_fit_info["n_total"]

    def test_settings_default_and_override(self, monkeypatch):
        from backend.config import settings as _s
        assert hasattr(_s, "FACTOR_NEUTRALIZE_BETA_TRAIN_RATIO")
        monkeypatch.setattr(_s, "FACTOR_NEUTRALIZE_BETA_TRAIN_RATIO", 0.5, raising=False)
        assert neu._beta_train_ratio() == 0.5
        assert neu._beta_train_ratio(1.0) == 1.0
        assert neu._beta_train_ratio(-1) == 0.7
        assert neu._beta_train_ratio(5.0) == 1.0

    def test_beta_neutralization_still_kills_market_factor(self):
        """功能不回退：训练窗 β 套用全窗后，纯 beta 代理因子的 IC 仍应大幅下降。"""
        from backend.services.factor_engine.factor_evaluator import FactorEvaluator
        import pandas as pd
        rng = np.random.default_rng(5)
        panels, mkt, _ = _make_panel(rng, S=6, B=600)
        neutral = neu.build_neutralized_returns(panels, 2, beta_train_ratio=0.7)
        ev = FactorEvaluator(forward_period=2)
        beta_factor = np.concatenate([mkt[1:-1] + mkt[2:], [0.0, 0.0]])
        raw_ics, neu_ics = [], []
        for s, (ts, close) in panels.items():
            n = len(close)
            fv = pd.Series(beta_factor[:n], index=np.arange(n))
            rep_raw = ev.evaluate_factor(s, fv, pd.Series(close, index=np.arange(n)), forward_period=2, neutral_returns=None)
            rep_neu = ev.evaluate_factor(s, fv, pd.Series(close, index=np.arange(n)), forward_period=2,
                                         neutral_returns=pd.Series(neutral[s], index=np.arange(n)))
            raw_ics.append(abs(rep_raw.ic_mean))
            neu_ics.append(abs(rep_neu.ic_mean))
        assert np.mean(raw_ics) > 0.2
        assert np.mean(neu_ics) < np.mean(raw_ics) * 0.5
