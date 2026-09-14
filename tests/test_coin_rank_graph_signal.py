# -*- coding: utf-8 -*-
"""coin_rank.graph_signal（RTGNN 迁移第一阶，零训练图信号试点）单元测试。

覆盖：
- 领先-滞后检测的方向性（锚领先 → lead>0；独立序列 → ≈0）
- lead/dm 分数归一化与排序一致性
- compute_graph_signals 的 TTL 缓存与 fail-open
- score_rows 的 graph_map 软融合（不破坏旧行为）
"""
import numpy as np
import pytest

from backend.services.coin_rank import graph_signal as gs
from backend.services.coin_rank.score import score_rows


# ─────────────────────────────────────────────────────────────
# 合成数据工具
# ─────────────────────────────────────────────────────────────
def _random_walk(n: int, seed: int, drift: float = 0.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    ret = rng.normal(drift, 0.02, size=n)
    closes = 100 * np.exp(np.cumsum(ret))
    return closes


def _closes_to_ret(closes: np.ndarray) -> np.ndarray:
    return np.diff(closes) / closes[:-1]


def _rows_from_ret(ret: np.ndarray) -> list:
    """收益序列 → K 线 rows（close 从 100 开始按 ret 累乘）。"""
    closes = 100 * np.exp(np.cumsum(np.asarray(ret, dtype=float)))
    return [{"close": float(c), "open": float(c), "high": float(c), "low": float(c)} for c in closes]


# ─────────────────────────────────────────────────────────────
# 信号 1：领先-滞后
# ─────────────────────────────────────────────────────────────
def test_lead_vs_anchor_detects_leadership():
    """锚领先 2 bar 时，lead 强度应显著为正。"""
    anchor = _random_walk(400, seed=1)
    ar = _closes_to_ret(anchor)
    rng = np.random.default_rng(7)
    follower = np.concatenate([np.zeros(2), ar[:-2]]) + rng.normal(0, 0.005, len(ar))
    score = gs._lead_vs_anchor(ar, follower, max_lag=6, min_bars=60)
    assert score > 0.10, f"锚领先应给出正 lead 强度，实际 {score:.4f}"


def test_lead_vs_anchor_no_structure():
    """相互独立的序列不应误报领先。"""
    a = _closes_to_ret(_random_walk(400, seed=2))
    b = _closes_to_ret(_random_walk(400, seed=3))
    score = gs._lead_vs_anchor(a, b, max_lag=6, min_bars=60)
    assert abs(score) < 0.10, f"独立序列 lead 应接近 0，实际 {score:.4f}"


def test_lead_lag_scores_normalized_and_directional(monkeypatch):
    """lead_lag_scores：更强跟随者得分更高，输出在 [0,1]。"""
    anchor = _closes_to_ret(_random_walk(800, seed=4))
    rng = np.random.default_rng(11)
    strong = np.concatenate([np.zeros(2), anchor[:-2]]) + rng.normal(0, 0.001, len(anchor))
    weak = np.concatenate([np.zeros(1), anchor[:-1]]) + rng.normal(0, 0.04, len(anchor))

    def fake_fetch(symbols, period, count):
        return {
            "BTC": anchor,
            "STRONG": strong,
            "WEAK": weak,
        }

    monkeypatch.setattr(gs, "_fetch_klines", fake_fetch)
    out = gs.lead_lag_scores(["BTC", "STRONG", "WEAK"], period="15m", count=288,
                              anchors=("BTC",), max_lag=6, min_bars=60)
    assert set(out) == {"STRONG", "WEAK"}
    assert 0.0 <= out["STRONG"] <= 1.0 and 0.0 <= out["WEAK"] <= 1.0
    assert out["STRONG"] > out["WEAK"], f"强跟随应更高: {out}"


# ─────────────────────────────────────────────────────────────
# 信号 2：动量加速度
# ─────────────────────────────────────────────────────────────
def test_momentum_delta_ranks_ordering(monkeypatch):
    """加速币的 dm 排名应高于减速币。"""
    def fake_fetch(symbols, period, count):
        # 60 根/段、低噪声：窗口均值 30σ 分离，抽样噪声无法翻转
        rng = np.random.default_rng(21)
        a_ret = np.concatenate([rng.normal(0.001, 0.005, 60), rng.normal(0.02, 0.005, 60)])
        b_ret = np.concatenate([rng.normal(0.02, 0.005, 60), rng.normal(0.001, 0.005, 60)])
        return {"A": a_ret, "B": b_ret}

    monkeypatch.setattr(gs, "_fetch_klines", fake_fetch)
    out = gs.momentum_delta_ranks(["A", "B"], period="4h", count=48)
    assert set(out) == {"A", "B"}
    assert 0.0 <= out["A"] <= 1.0 and 0.0 <= out["B"] <= 1.0
    assert out["A"] > out["B"], f"加速币 dm 应更高: {out}"


# ─────────────────────────────────────────────────────────────
# 统一入口：开关 / fail-open / 缓存
# ─────────────────────────────────────────────────────────────
def test_compute_graph_signals_disabled_returns_empty():
    """默认关闭（未开启开关）→ 空 dict，零开销。"""
    out = gs.compute_graph_signals(["BTC", "ETH"])
    assert out == {}


def test_compute_graph_signals_fail_open(monkeypatch):
    """取数爆炸 → 返回 {}，绝不抛错。"""
    monkeypatch.setattr(gs, "graph_signal_enabled", lambda: True)

    def boom(symbols, period, count):
        raise RuntimeError("db down")

    monkeypatch.setattr(gs, "_fetch_klines", boom)
    gs.clear_cache()
    out = gs.compute_graph_signals(["A", "B"])
    assert out == {}


def test_compute_graph_signals_cache(monkeypatch):
    """TTL 内重复调用只取数一次（每周期一次）。"""
    monkeypatch.setattr(gs, "graph_signal_enabled", lambda: True)
    anchor = _closes_to_ret(_random_walk(300, seed=5))
    rng = np.random.default_rng(13)
    follower = np.concatenate([np.zeros(2), anchor[:-2]]) + rng.normal(0, 0.005, len(anchor))
    calls = {"n": 0}

    def counting_fetch(symbols, period, count):
        calls["n"] += 1
        return {"BTC": anchor, "ALT": follower}

    monkeypatch.setattr(gs, "_fetch_klines", counting_fetch)
    gs.clear_cache()
    first = gs.compute_graph_signals(["BTC", "ALT"])
    n_after_first = calls["n"]
    assert n_after_first == 2  # lead(15m) + mom(4h) 各一次
    assert "ALT" in first and "lead" in first["ALT"] and "dm" in first["ALT"]

    second = gs.compute_graph_signals(["BTC", "ALT"])
    assert calls["n"] == n_after_first, "TTL 内不应重复取数"
    assert second == first


# ─────────────────────────────────────────────────────────────
# score_rows 融合
# ─────────────────────────────────────────────────────────────
def _dc_rows() -> dict:
    base = {
        "symbol": "X",
        "volume_24h": 50_000_000,
        "change_24h": 3.0,
        "change_1h": 1.0,
        "change_4h": 2.0,
        "price": 10.0,
        "sources": ["dc_ticker"],
    }
    return {
        "A": {**base, "symbol": "A"},
        "B": {**base, "symbol": "B"},
    }


def test_score_rows_graph_blend_orders_by_lead():
    """高 lead 币相对分差被图信号拉大；无 graph_map 时字段为空。"""
    rows = _dc_rows()
    plain = score_rows(rows, symbols=["A", "B"], apply_factor=False)
    plain_by = {r.symbol: r for r in plain}
    gap0 = plain_by["A"].composite - plain_by["B"].composite

    # 有图信号：A 高 lead/dm，B 低
    graph_map = {"A": {"lead": 1.0, "dm": 1.0}, "B": {"lead": 0.0, "dm": 0.0}}
    blended = score_rows(rows, symbols=["A", "B"], apply_factor=False, graph_map=graph_map)
    by = {r.symbol: r for r in blended}
    gap1 = by["A"].composite - by["B"].composite
    assert gap1 > gap0, f"图信号应扩大 A 相对分差: gap0={gap0:.4f} gap1={gap1:.4f}"
    assert by["A"].composite > plain_by["A"].composite
    assert by["B"].composite < plain_by["B"].composite
    assert by["A"].lead_score == 1.0 and by["A"].dm_score == 1.0
    assert by["B"].lead_score == 0.0
    d = by["A"].to_dict()
    assert d["lead_score"] == 1.0 and d["graph_score"] is not None


def test_score_rows_graph_map_missing_symbol_noop():
    """graph_map 缺币 / 空 dict → 与旧行为一致，不新增字段值。"""
    rows = _dc_rows()
    plain = score_rows(rows, symbols=["A", "B"], apply_factor=False)
    merged = score_rows(rows, symbols=["A", "B"], apply_factor=False, graph_map={})
    assert [r.composite for r in plain] == [r.composite for r in merged]
    assert all(r.lead_score is None for r in merged)


# ─────────────────────────────────────────────────────────────
# engine 接线
# ─────────────────────────────────────────────────────────────
def test_engine_graph_map_disabled_returns_none(monkeypatch):
    from backend.services.coin_rank import engine

    monkeypatch.setattr("backend.services.coin_rank.graph_signal.graph_signal_enabled", lambda: False)
    assert engine._graph_map_for(["A", "B"]) is None


def test_engine_graph_map_enabled_passthrough(monkeypatch):
    from backend.services.coin_rank import engine

    monkeypatch.setattr("backend.services.coin_rank.graph_signal.graph_signal_enabled", lambda: True)
    monkeypatch.setattr(
        "backend.services.coin_rank.graph_signal.compute_graph_signals",
        lambda symbols: {"A": {"lead": 1.0, "dm": 0.5}},
    )
    out = engine._graph_map_for(["A", "B"])
    assert out == {"A": {"lead": 1.0, "dm": 0.5}}


def test_engine_graph_map_empty_signals_returns_none(monkeypatch):
    from backend.services.coin_rank import engine

    monkeypatch.setattr("backend.services.coin_rank.graph_signal.graph_signal_enabled", lambda: True)
    monkeypatch.setattr(
        "backend.services.coin_rank.graph_signal.compute_graph_signals",
        lambda symbols: {},
    )
    assert engine._graph_map_for(["A", "B"]) is None


def test_log_graph_overlap_metrics(caplog):
    """图信号影响度指标：有差异 → 重叠<1 并记日志；全等 → 重叠=1。"""
    import logging

    from backend.services.coin_rank.engine import log_graph_overlap
    from backend.services.coin_rank.score import RankResult

    def make(syms, graph_vals):
        out = []
        for i, s in enumerate(syms):
            r = RankResult(symbol=s, composite=0.9 - 0.03 * i, decay_mult=1.0)
            r.graph_score = graph_vals.get(s)
            out.append(r)
        return out

    syms = [f"S{i}" for i in range(12)]
    # 有差异：graph 分与 composite 反向 → 重叠下降
    diff_vals = {s: (1.0 if i % 2 else 0.0) for i, s in enumerate(syms)}
    with caplog.at_level(logging.INFO, logger="backend.services.coin_rank.engine"):
        log_graph_overlap(make(syms, diff_vals), {s: {"lead": diff_vals[s], "dm": 0.5} for s in syms})
    assert any("graph_overlap top10=" in rec.message for rec in caplog.records)

    # 全等：graph 分与 composite 同序 → 重叠=1.0（日志 top10=1.00）
    caplog.clear()
    eq_vals = {s: 1.0 - 0.03 * i for i, s in enumerate(syms)}
    with caplog.at_level(logging.INFO, logger="backend.services.coin_rank.engine"):
        log_graph_overlap(make(syms, eq_vals), {s: {"lead": eq_vals[s], "dm": 0.5} for s in syms})
    assert any("graph_overlap top10=1.00" in rec.message for rec in caplog.records)
