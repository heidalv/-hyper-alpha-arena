# -*- coding: utf-8 -*-
"""[h894 2026-10-07] 高频趋势概率方向:推理侧(trend_prob)+ 训练侧(hft_trend_prob_train)。

核心断言:
  · 分桶/推理数学正确(合成模型手算对照);
  · 模型缺失/过期 ⇒ None(fail-closed 回退旧方向路径);
  · 训练管线在「可学习结构」的合成数据上 OOS AUC 显著 > 0.5(防"假训练");
  · predict_side 的门槛语义(不够偏 ⇒ 无方向)。
"""
from __future__ import annotations

import json
import time

import numpy as np
import pytest

from backend.services.evolution.hft_trend_prob_train import train_model
from backend.services.market_maker import trend_prob as tp


# ──────────────────────────────────────────────────────────────────────
# 1. 分桶与推理数学(合成模型,手算对照)
# ──────────────────────────────────────────────────────────────────────
def _synthetic_model() -> dict:
    """ofi 一个特征定胜负:ofi 高 ⇒ up logit 大。其余特征权重 0。"""
    edges = [-0.5, -0.1, 0.1, 0.5]
    zero = {"edges": edges, "w": 0.0,
            "ll_up": [0, 0, 0, 0, 0], "ll_dn": [0, 0, 0, 0, 0]}
    tables = {f: dict(zero) for f in tp.FEATURES}
    tables["ofi"] = {"edges": edges, "w": 1.0,
                     "ll_up": [-2.0, -0.5, 0.0, 0.5, 2.0],
                     "ll_dn": [2.0, 0.5, 0.0, -0.5, -2.0]}
    return {
        "version": 1, "trained_at": time.time(), "horizon_sec": 30,
        "label_thr_bp": 3.0, "features": list(tp.FEATURES),
        "tables": tables,
        "prior": {"up": 0.0, "dn": 0.0},
        "platt": {"up": [1.0, 0.0], "dn": [1.0, 0.0]},
        "gate": {"p_min_up": 0.6, "p_min_dn": 0.6, "margin": 0.08},
        "metrics": {},
    }


def test_bucketize():
    edges = [-1.0, 0.0, 1.0]
    assert tp.bucketize(edges, -5.0) == 0
    assert tp.bucketize(edges, -0.5) == 1
    assert tp.bucketize(edges, 0.5) == 2
    assert tp.bucketize(edges, 5.0) == 3
    assert tp.bucketize(edges, None) == 1        # 缺失 ⇒ 中间桶(中性)


def test_predict_proba_monotonic():
    m = _synthetic_model()
    p_up_hi, p_dn_hi = tp.predict_proba(m, {"ofi": 0.9})
    p_up_lo, p_dn_lo = tp.predict_proba(m, {"ofi": -0.9})
    # ofi=0.9 ⇒ 第 4 桶 ll_up=2.0 ⇒ p_up = sigmoid(2.0) ≈ 0.881
    assert p_up_hi == pytest.approx(0.8808, abs=1e-3)
    assert p_dn_hi < 0.2
    assert p_up_lo < 0.2
    assert p_dn_lo == pytest.approx(0.8808, abs=1e-3)


def test_predict_side_gate():
    m = _synthetic_model()
    assert tp.predict_side("", {"ofi": 0.9}, model=m) == "buy"
    assert tp.predict_side("", {"ofi": -0.9}, model=m) == "sell"
    # ofi 中性 ⇒ sigmoid(0)=0.5 < p_min 0.6 ⇒ 无方向
    assert tp.predict_side("", {"ofi": 0.0}, model=m) == ""


def test_predict_side_edge_and_margin():
    """[h895] 激进模式:margin 覆盖 + edge 语义(供仓位随信心放大)。"""
    m = _synthetic_model()
    m["metrics"] = {"base_up": 0.18, "base_dn": 0.18}
    # ofi=0.2 ⇒ 第 3 桶 ll_up=0.5 ⇒ p_up = sigmoid(0.5) ≈ 0.6225
    # 默认门槛(模型文件 gate:p_min 0.6) ⇒ buy,edge = 0.6225 − 0.6
    r = tp.predict_side_edge("", {"ofi": 0.2}, model=m)
    assert r is not None and r[0] == "buy" and r[1] == pytest.approx(0.6225 - 0.6, abs=1e-3)
    # margin 覆盖:base 0.18 + 0.03 = 0.21 ⇒ 同样的特征 edge 更大
    r2 = tp.predict_side_edge("", {"ofi": 0.2}, model=m, margin=0.03)
    assert r2[0] == "buy" and r2[1] == pytest.approx(0.6225 - 0.21, abs=1e-3)
    assert r2[1] > r[1]                       # 激进档 ⇒ edge 更大 ⇒ 仓位更大
    # 弱信号在激进档下仍不够 ⇒ ("", 0.0)
    r3 = tp.predict_side_edge("", {"ofi": 0.05}, model=m, margin=0.03)
    assert r3 == ("", 0.0)
    # 模型不可用(不存在的根目录) ⇒ None
    assert tp.predict_side_edge("Z:\\nonexistent_h894", {"ofi": 1.0},
                                model=None) is None
    # predict_side 兼容旧语义
    assert tp.predict_side("", {"ofi": 0.55}, model=m) == "buy"


# ──────────────────────────────────────────────────────────────────────
# 2. fail-closed:模型缺失/过期/坏 ⇒ None(回退旧路径)
# ──────────────────────────────────────────────────────────────────────
def test_load_model_missing(tmp_path):
    assert tp.load_model(str(tmp_path)) is None


def test_load_model_stale(tmp_path):
    d = tmp_path / "data" / "hft_trend_prob"
    d.mkdir(parents=True)
    m = _synthetic_model()
    m["trained_at"] = time.time() - 48 * 3600      # 48h 前 ⇒ 过期
    (d / "model.json").write_text(json.dumps(m), encoding="utf-8")
    tp._CACHE.clear()
    assert tp.load_model(str(tmp_path)) is None
    # 新鲜模型 ⇒ 可用
    m["trained_at"] = time.time()
    (d / "model.json").write_text(json.dumps(m), encoding="utf-8")
    tp._CACHE.clear()
    assert tp.load_model(str(tmp_path)) is not None


def test_load_model_broken_json(tmp_path):
    d = tmp_path / "data" / "hft_trend_prob"
    d.mkdir(parents=True)
    (d / "model.json").write_text("{broken", encoding="utf-8")
    tp._CACHE.clear()
    assert tp.load_model(str(tmp_path)) is None


def test_held_edge_bp():
    """[h899d] 持仓剩余优势:持多 ⇒ edge=p_up−p_dn;持空 ⇒ p_dn−p_up;×scale。"""
    m = _synthetic_model()
    # ofi=0.9 ⇒ p_up=sigmoid(2)=0.881, p_dn=sigmoid(−2)=0.119
    e_long = tp.held_edge_bp("", {"ofi": 0.9}, held_side="buy", model=m, scale=20.0)
    assert e_long == pytest.approx((0.8808 - 0.1192) * 20.0, abs=0.1)
    # 持空在强多特征下 ⇒ edge 为负(该平)
    e_short = tp.held_edge_bp("", {"ofi": 0.9}, held_side="sell", model=m, scale=20.0)
    assert e_short is not None and e_short < 0
    # 模型不可用 ⇒ None(回退兜底)
    assert tp.held_edge_bp("Z:\\nonexistent_h899", {"ofi": 0.5},
                           held_side="buy", model=None) is None


# ──────────────────────────────────────────────────────────────────────
# 3. 训练管线:合成数据带可学习结构 ⇒ OOS AUC 必须显著 > 0.5
# ──────────────────────────────────────────────────────────────────────
def test_train_model_learns_synthetic():
    rng = np.random.default_rng(3)
    n = 4000
    ofi = rng.normal(0, 0.5, n)
    noise = rng.normal(0, 2.0, n)
    fwd = 6.0 * np.tanh(3 * ofi) + noise           # ofi 强预测 fwd
    obs = np.zeros((n, len(tp.FEATURES)), np.float32)
    obs[:, 1] = ofi                                # ofi 列
    obs[:, 0] = rng.normal(0, 1, n)                # 其余列 = 噪声
    obs[:, 2] = rng.normal(0, 5, n)
    obs[:, 3] = rng.normal(0, 5, n)
    obs[:, 4] = rng.normal(0, 5, n)
    obs[:, 5] = np.abs(rng.normal(3, 1, n))
    rows = {"SYN": {"obs": obs, "fwd": fwd.astype(np.float32)}}
    model = train_model(rows, label_thr_bp=2.0, margin=0.08)
    auc_up = model["metrics"]["up"]["auc"]
    assert auc_up is not None and auc_up > 0.65, f"AUC 过低: {auc_up}"
    # ofi 必须是高权重特征之一(8 个特征里有噪声列,不苛求它是唯一最高,
    # 但它必须排进前列 —— 真正的学习证明看上面的 AUC 和下面的端到端)
    w = {f: model["tables"][f]["w"] for f in model["features"]}
    assert w["ofi"] >= 0.5, f"ofi 权重过低: {w['ofi']}"
    # 端到端:强 ofi ⇒ predict_side 给方向
    tp._CACHE.clear()
    side = tp.predict_side("", {"ofi": 3.0}, model=model)
    assert side == "buy"
