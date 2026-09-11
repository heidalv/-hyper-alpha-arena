# -*- coding: utf-8 -*-
"""[2026-09-02] 因子 icir 断链修复单测。

背景（数据库实证）：factor_active_set 里 7 个 PAPER 种子因子的 icir 自 08-02
建行起恒为 bootstrap 占位值 0.05，而 last_net_ic / evaluated_cycles 一直在正常
更新到 09-02 11:09。根因是三处叠加：
  1. _review_active_factors 每轮只写 last_net_ic/turnover/capacity_usd/
     evaluated_cycles，**不写 icir**；
  2. _advance_shadow_states 里唯一的 f["icir"]=... 只在状态跃迁分支执行，状态
     长期不变的因子永远刷不到；
  3. 同处的 icir 实际算的是 np.mean(ics)（跨币 IC 均值），与 ICIR(mean/std)
     量纲不同。
而 combo_weights 的 icir 模式正是用这个字段定因子权重 → 实盘/模拟盘长期按一个
08-02 的常数分配因子话语权。

本文件锁定：icir 每轮刷新、口径与晋升评估同源、算不出时保持原值不写 0，以及
time_series_ic 的 step 参数语义（含向后兼容）。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from backend.services.factor_engine.evaluation import time_series_ic


# ───────────────────────── time_series_ic step 语义 ─────────────────────────

def _legacy_overlap_ic(fs: pd.Series, rs: pd.Series) -> np.ndarray:
    """step 参数引入前的历史实现（步长 1 重叠滑窗），用于兼容性比对。"""
    df = pd.DataFrame({"f": fs, "r": rs}).dropna()
    window = min(20, len(df) // 3)
    out = []
    for i in range(window, len(df)):
        a = df["f"].iloc[i - window:i].values
        b = df["r"].iloc[i - window:i].values
        m = np.isfinite(a) & np.isfinite(b)
        if m.sum() < 5:
            out.append(0.0)
            continue
        r, _ = stats.spearmanr(a[m], b[m])
        out.append(float(r) if np.isfinite(r) else 0.0)
    return np.array(out)


def test_step1_is_byte_for_byte_backward_compatible():
    """默认 step=1 必须与历史重叠滑窗实现逐元素一致。

    晋升门禁阈值是按重叠口径调出来的，默认值改变会静默平移所有 ICIR 判定。
    """
    rng = np.random.default_rng(11)
    fs = pd.Series(rng.normal(size=600))
    rs = pd.Series(rng.normal(size=600))

    new = time_series_ic(fs, rs, method="spearman", step=1)
    old = _legacy_overlap_ic(fs, rs)

    assert len(new) == len(old)
    assert np.allclose(new, old)
    # 不传 step 时也应等价于 step=1
    assert np.allclose(time_series_ic(fs, rs, method="spearman"), old)


def test_step0_yields_non_overlapping_windows():
    """step=0 → 非重叠窗口：序列长度约为 n/window，远短于重叠口径。"""
    rng = np.random.default_rng(12)
    n = 600
    fs = pd.Series(rng.normal(size=n))
    rs = pd.Series(rng.normal(size=n))

    overlap = time_series_ic(fs, rs, method="spearman", step=1)
    non_overlap = time_series_ic(fs, rs, method="spearman", step=0)

    window = min(20, n // 3)
    assert len(non_overlap) == pytest.approx(n // window, abs=1)
    # 非重叠必须显著更短（这是 22x 性能差的来源）
    assert len(non_overlap) < len(overlap) / 10


def test_step_edge_cases_do_not_raise():
    """短序列与 window<5 分支在新参数下行为不变。"""
    rng = np.random.default_rng(13)
    # n<10 → 空数组
    assert len(time_series_ic(pd.Series(rng.normal(size=8)),
                              pd.Series(rng.normal(size=8)), step=0)) == 0
    # window<5（n//3<5，即 n<15）→ 整体单点
    assert len(time_series_ic(pd.Series(rng.normal(size=13)),
                              pd.Series(rng.normal(size=13)), step=0)) == 1


# ───────────────────────── _trailing_net_ic 产出 icir ─────────────────────────

class _FakeExpr:
    """按收盘价动量产出因子值的假表达式（保证 IC 非零、可复现）。"""

    def evaluate(self, fields):
        close = np.asarray(fields["close"], dtype=float)
        out = np.zeros_like(close)
        out[5:] = close[5:] - close[:-5]
        return out


def _make_kline(n: int = 400, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="4h")
    close = 100.0 + np.cumsum(rng.normal(0, 1, size=n))
    return pd.DataFrame({
        "open": close + rng.normal(0, 0.1, size=n),
        "high": close + np.abs(rng.normal(0, 0.5, size=n)),
        "low": close - np.abs(rng.normal(0, 0.5, size=n)),
        "close": close,
        "volume": np.abs(rng.normal(1000, 100, size=n)),
    }, index=idx)


def test_trailing_net_ic_returns_icir_field():
    """_trailing_net_ic 必须同时产出 icir（此前只有 ic_mean/turnover/net_ic）。"""
    from backend.services.evolution.factor_evolution_loop import _trailing_net_ic

    dfs = {"BTC": _make_kline(seed=1), "ETH": _make_kline(seed=2)}
    res = _trailing_net_ic(_FakeExpr(), dfs)

    assert res is not None
    assert "icir" in res, "icir 字段缺失，复评就无法刷新该字段"
    assert res["icir"] is None or np.isfinite(res["icir"])
    # 原有字段不能被破坏
    for k in ("ic_mean", "turnover", "net_ic", "n_symbols"):
        assert k in res


def test_trailing_net_ic_icir_is_mean_over_std_not_mean():
    """icir 必须是 IC 序列的 mean/std，不能退化成 IC 均值本身。

    这正是 _advance_shadow_states 修复前的错误口径（np.mean(ics)）。
    """
    from backend.services.evolution.factor_evolution_loop import _trailing_net_ic

    dfs = {"BTC": _make_kline(seed=3), "ETH": _make_kline(seed=4)}
    res = _trailing_net_ic(_FakeExpr(), dfs)

    assert res is not None and res["icir"] is not None
    # mean/std 与 mean 只在 std==1 时相等；随机行情下二者应明显不同
    assert abs(float(res["icir"]) - float(res["ic_mean"])) > 1e-6


# ──────────────────── _review_active_factors 每轮刷新 icir ────────────────────

def _patch_review_side_effects(monkeypatch, mod, trailing_result):
    """屏蔽复评的落库副作用，只保留字段计算逻辑。"""
    monkeypatch.setattr(mod, "_trailing_net_ic", lambda expr, dfs: trailing_result)
    monkeypatch.setattr(mod, "_log_evolution", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_deactivate_factor", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_estimate_volume_usd", lambda df: 1e6)
    # 阈值压到极低，确保因子走"保留"分支而非退化分支
    monkeypatch.setattr(mod, "_min_net_ic_threshold", lambda: -9.9)


def test_review_refreshes_icir_off_placeholder(monkeypatch):
    """复评应把 bootstrap 占位值 0.05 刷成实际算出的 icir。"""
    from backend.services.evolution import factor_evolution_loop as mod

    _patch_review_side_effects(monkeypatch, mod, {
        "ic_mean": 0.02, "turnover": 0.3, "net_ic": 0.015,
        "n_symbols": 2, "icir": 0.4321,
    })

    factor = {"factor_id": "seed_mom5", "expr": _FakeExpr(), "icir": 0.05,
              "state": "PAPER"}
    kept, degraded = mod._review_active_factors([factor], {"BTC": _make_kline()})

    assert not degraded and len(kept) == 1
    assert factor["icir"] == pytest.approx(0.4321), (
        "icir 未被刷新——种子因子会继续用 08-02 的占位值参与 combo_weights 定权"
    )
    # 既有字段仍需照常更新。注意 last_net_ic 由复评用 net_ic(ic_mean, turnover)
    # 重算，不是直接取 _trailing_net_ic 返回的 net_ic。
    from backend.services.evolution.factor_labels import net_ic as _nic
    assert factor["last_net_ic"] == pytest.approx(round(_nic(0.02, 0.3), 6))
    assert factor["turnover"] == pytest.approx(0.3)
    assert factor["evaluated_cycles"] == 1


def test_review_keeps_old_icir_when_unavailable(monkeypatch):
    """样本不足（icir=None）时必须保持原值，不能写 0。

    写 0 会让因子在 min_icir 门禁下被误判退化，这是 07-23 批量误杀 100 个
    因子的同类机制。
    """
    from backend.services.evolution import factor_evolution_loop as mod

    _patch_review_side_effects(monkeypatch, mod, {
        "ic_mean": 0.01, "turnover": 0.2, "net_ic": 0.008,
        "n_symbols": 1, "icir": None,
    })

    factor = {"factor_id": "d6f82d364676127e", "expr": _FakeExpr(), "icir": 1.31,
              "state": "ACTIVE"}
    mod._review_active_factors([factor], {"BTC": _make_kline()})

    assert factor["icir"] == pytest.approx(1.31)


def test_review_ignores_non_finite_icir(monkeypatch):
    """icir 为 nan/inf 时同样保持原值。"""
    from backend.services.evolution import factor_evolution_loop as mod

    for bad in (float("nan"), float("inf")):
        _patch_review_side_effects(monkeypatch, mod, {
            "ic_mean": 0.01, "turnover": 0.2, "net_ic": 0.008,
            "n_symbols": 1, "icir": bad,
        })
        factor = {"factor_id": "f_bad", "expr": _FakeExpr(), "icir": 0.77,
                  "state": "ACTIVE"}
        mod._review_active_factors([factor], {"BTC": _make_kline()})
        assert factor["icir"] == pytest.approx(0.77)
