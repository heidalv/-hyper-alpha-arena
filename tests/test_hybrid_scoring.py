# -*- coding: utf-8 -*-
"""混合打分中心（hybrid_scoring）单元测试 —— 全合成数据，无 LLM、无真实 DB、无网络。

覆盖：特征防前视 / LTR 训练与兜底链 / 通道B解析与失败缺席 / 融合收缩 / 影子全链路 /
命中回填 / 周报 / 钩子节流 / fusion_blend 模式纪律。
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
import pytest

from backend.services.hybrid_scoring import channel_b, config, fusion, ltr
from backend.services.hybrid_scoring import features as feats
from backend.services.hybrid_scoring import service


# ---------------- 合成数据 ----------------

def _synth_klines(days: int = 200, seed: int = 0, ar: float = 0.35) -> pd.DataFrame:
    """AR(1) 收益序列 → 动量特征对次日收益有可学的正相关。"""
    rng = np.random.default_rng(seed)
    r = np.zeros(days)
    for t in range(1, days):
        r[t] = ar * r[t - 1] + rng.normal(0, 0.02)
    close = 100.0 * np.cumprod(1.0 + r)
    high = close * (1 + np.abs(rng.normal(0, 0.008, days)))
    low = close * (1 - np.abs(rng.normal(0, 0.008, days)))
    volume = np.abs(rng.normal(1e6, 2e5, days)) * (1 + 2 * np.abs(r))
    idx = pd.date_range("2025-01-01", periods=days, freq="D", name="datetime")
    return pd.DataFrame({"open": close, "high": high, "low": low,
                         "close": close, "volume": volume}, index=idx)


SYMS = [f"S{i:02d}" for i in range(12)]


@pytest.fixture()
def patched_kpanel(monkeypatch):
    store = {s: _synth_klines(seed=hash(s) % 10000) for s in SYMS}

    def _loader(sym, period="1d", bars=400, exchange=None):
        if period == "1h":  # 命中评估用：截至当前的小时线
            rng = np.random.default_rng(hash(sym) % 999 + 1)
            n = 350
            idx = pd.date_range(end=pd.Timestamp.utcnow().floor("h"), periods=n, freq="h", name="datetime")
            close = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, n))
            return pd.DataFrame({"open": close, "high": close * 1.005, "low": close * 0.995,
                                 "close": close, "volume": np.abs(rng.normal(1e3, 1e2, n))}, index=idx)
        return store.get(sym, pd.DataFrame())

    monkeypatch.setattr("backend.services.hybrid_scoring.kpanel.load_klines", _loader)
    monkeypatch.setattr("backend.services.hybrid_scoring.kpanel.top_liquid_symbols",
                        lambda limit=30, days=30, period="1d": SYMS[:limit])
    return store


@pytest.fixture()
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_DATA_DIR", tmp_path)
    config.data_dir()
    return tmp_path


# ---------------- 特征与面板 ----------------

def test_feature_row_no_lookahead():
    """t 时刻特征只依赖 ≤t 数据：截断后重算 t 的特征应一致。"""
    df = _synth_klines(days=150, seed=7)
    full = feats._series_features(df)
    cut = feats._series_features(df.iloc[:-5])
    t = cut.index[-1]
    for c in feats.FEATURES:
        a, b = float(full.loc[t, c]), float(cut.loc[t, c])
        assert (np.isnan(a) and np.isnan(b)) or abs(a - b) < 1e-9, f"{c} 泄漏未来数据"
    # 标签是 t+1：截断后最后一行无标签
    assert pd.isna(cut["fwd_ret"].iloc[-1]) or cut.index[-1] == full.index[-1]
    assert pd.isna(full["fwd_ret"].iloc[-1])


def test_build_panel_shape_and_cs(patched_kpanel):
    panel = feats.build_panel(SYMS, period="1d", bars=200)
    assert len(panel) >= 500
    assert panel.index.get_level_values("date").nunique() >= 30
    assert set(feats.FEATURES) <= set(panel.columns)
    # 横截面 z 后逐日均值 ≈ 0
    day_mean = panel.groupby(level="date")["mom_5"].mean().abs().max()
    assert day_mean < 1e-6
    # label_gain 整数且 ≥0
    assert panel["label_gain"].between(0, 9).all()


def test_live_rows_and_cross_z(patched_kpanel):
    rows = feats.live_rows(SYMS, period="1d")
    assert len(rows) == len(SYMS)
    z = feats.cross_z_rows(rows)
    vals = np.array([r["mom_5"] for r in z.values()])
    assert abs(vals.mean()) < 1e-6 and 0.1 < vals.std() < 3.0


# ---------------- LTR 训练与兜底 ----------------

def test_ltr_train_and_score(patched_kpanel, tmp_data):
    panel = feats.build_panel(SYMS, period="1d", bars=200)
    metrics = ltr.train(panel)
    assert metrics["valid_rank_ic"] > 0.10, "植入信号未学到"
    assert metrics["test_rank_ic"] > 0.0
    assert config.model_path().exists()
    meta = json.loads(config.model_meta_path().read_text(encoding="utf-8"))
    assert meta["features"] == feats.FEATURES
    assert "ic_weights" in meta
    rows = feats.cross_z_rows(feats.live_rows(SYMS))
    scored = ltr.score(rows)
    assert len(scored) == len(SYMS)
    assert all(0.0 <= r["score"] <= 1.0 for r in scored.values())
    assert scored[SYMS[0]]["backend"] in ("ltr", "ic_weight")


def test_ltr_fallback_chain(patched_kpanel, tmp_data, monkeypatch):
    rows = feats.cross_z_rows(feats.live_rows(SYMS))
    # 无模型无meta → uniform
    scored = ltr.score(rows)
    assert all(r["backend"] == "uniform" for r in scored.values())
    # 只有meta(ic_weights) → ic_weight
    meta = {"features": feats.FEATURES,
            "ic_weights": {c: 1.0 / len(feats.FEATURES) for c in feats.FEATURES}}
    config.model_meta_path().write_text(json.dumps(meta), encoding="utf-8")
    scored = ltr.score(rows)
    assert all(r["backend"] == "ic_weight" for r in scored.values())


def test_ltr_train_rejects_small_panel():
    with pytest.raises(ValueError):
        ltr.train(pd.DataFrame())


# ---------------- 通道B ----------------

def test_channel_b_parse_scores():
    raw = json.dumps({"scores": [
        {"symbol": "BTC", "score": 8.5, "direction": "long", "confidence": 0.8, "rationale": "动量强"},
        {"symbol": "eth", "score": 12.0, "direction": "long"},  # 越界截断
    ]})
    out = channel_b.parse_scores(raw)
    assert out["BTC"]["score"] == pytest.approx(0.85)
    assert out["ETH"]["score"] == 1.0
    # code fence
    fenced = f"```json\n{raw}\n```"
    assert channel_b.parse_scores(fenced)["BTC"]["score"] == pytest.approx(0.85)
    with pytest.raises(ValueError):
        channel_b.parse_scores("not json at all")
    with pytest.raises(ValueError):
        channel_b.parse_scores(json.dumps({"scores": [{"symbol": "X", "score": "abc"}]}))


def test_channel_b_run_no_config_returns_none(monkeypatch):
    monkeypatch.setattr(channel_b, "_load_llm_config", lambda: None)
    packs = {"BTC": {"symbol": "BTC"}}
    assert channel_b.run(packs) is None  # 缺席而非假中性


# ---------------- 融合 ----------------

def test_fusion_weights_prior_only_and_shrunk():
    w = fusion.channel_weights(None, regime="range")
    assert w["a"] == pytest.approx(0.5) and w["lambda"] == 1.0
    w = fusion.channel_weights({"days": 5, "ic_a": 0.2, "ic_b": 0.1})
    assert w["source"] == "prior_only"
    # 30天、A强B弱、低方差 → w_a > 0.5 且未全收缩
    w = fusion.channel_weights({"days": 30, "ic_a": 0.10, "ic_b": 0.01, "std_diff": 0.05})
    assert w["a"] > 0.55 and w["lambda"] < 1.0
    # 高方差 → 接近先验
    w = fusion.channel_weights({"days": 30, "ic_a": 0.10, "ic_b": 0.01, "std_diff": 0.5})
    assert abs(w["a"] - 0.5) < 0.15
    # 趋势态先验偏A
    w = fusion.channel_weights(None, regime="trend_up")
    assert w["a"] == pytest.approx(0.6)


def test_fuse_arms_and_rank():
    a = {"B": {"score": 0.9, "rank": 1, "backend": "ltr", "features": {}},
         "A": {"score": 0.2, "rank": 2, "backend": "ltr", "features": {}}}
    b = {"A": {"score": 0.9, "confidence": 0.7, "direction": "long", "rationale": "x"},
         "B": {"score": 0.1, "confidence": 0.6, "direction": "short", "rationale": "y"}}
    out = fusion.fuse(a, None, None)
    assert all(v["arm"] == "A0" and v["b_pct"] is None for v in out.values())
    out = fusion.fuse(a, b, None)
    assert all(v["arm"] == "A3" for v in out.values())
    assert set(out["A"]["llm"].keys()) >= {"score", "confidence", "rationale"}
    ranks = sorted(v["rank"] for v in out.values())
    assert ranks == [1, 2]


# ---------------- 影子全链路 ----------------

def test_run_cycle_shadow_end_to_end(patched_kpanel, tmp_data, monkeypatch):
    monkeypatch.setattr("backend.services.hybrid_scoring.service._load_ic_stats",
                        lambda: {})
    res = service.run_cycle(symbols=SYMS, force=True, llm_enabled=False)
    assert res["ok"] is True and res["n_symbols"] == len(SYMS)
    latest = json.loads(config.latest_path().read_text(encoding="utf-8"))
    assert latest["fusion"]["channel_b_present"] is False
    assert len(latest["fused"]) == len(SYMS)
    lines = config.score_log_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(SYMS)
    e = json.loads(lines[0])
    assert e["arm_fields"]["A0"] is not None and e["arm_fields"]["A3"] is not None
    assert e["outcome"] is None


def test_evaluate_hits_backfills(patched_kpanel, tmp_data, monkeypatch):
    # 伪造一条 25h 前的日志 + 1h 合成K线（ret 可算）
    monkeypatch.setattr("backend.services.hybrid_scoring.service._load_ic_stats", lambda: {})
    service.run_cycle(symbols=SYMS, force=True, llm_enabled=False)
    lines = config.score_log_path().read_text(encoding="utf-8").splitlines()
    old = json.loads(lines[0])
    old["ts"] = time.time() - 25 * 3600
    old["iso"] = pd.Timestamp.utcnow().isoformat()
    lines[0] = json.dumps(old, ensure_ascii=False)
    config.score_log_path().write_text("\n".join(lines) + "\n", encoding="utf-8")
    res = service.evaluate_hits()
    assert res["ok"] is True and res["evaluated"] >= 1
    after = [json.loads(l) for l in config.score_log_path().read_text(encoding="utf-8").splitlines()]
    done = [e for e in after if e["outcome"]]
    assert done and "ret_24h" in done[0]["outcome"]
    stats = json.loads(config.ic_stats_path().read_text(encoding="utf-8"))
    assert stats["days"] >= 1


def test_weekly_report(patched_kpanel, tmp_data, monkeypatch):
    monkeypatch.setattr("backend.services.hybrid_scoring.service._load_ic_stats", lambda: {})
    service.run_cycle(symbols=SYMS, force=True, llm_enabled=False)
    from backend.services.hybrid_scoring import report
    lines = config.score_log_path().read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(lines):
        e = json.loads(l)
        e["ts"] = time.time() - 30 * 86400
        e["iso"] = (pd.Timestamp.utcnow() - pd.Timedelta(days=30)).isoformat()
        e["outcome"] = {"ret_24h": float(np.random.default_rng(i).normal(0, 0.02))}
        if i % 3 == 0:
            e["thesis"] = {"present": True, "tier": "mid"}
        lines[i] = json.dumps(e, ensure_ascii=False)
    config.score_log_path().write_text("\n".join(lines) + "\n", encoding="utf-8")
    rep = report.weekly_report()
    assert rep["ok"] is True and rep["rows"]
    arms = {r["arm"] for r in rep["rows"]}
    assert "A0" in arms and "A3" in arms


def test_hook_throttle_and_mode_off(monkeypatch, tmp_data):
    calls = []
    monkeypatch.setattr(service, "_safe_cycle", lambda syms: calls.append(syms))
    assert service.maybe_shadow_hook(["BTC"]) is True
    assert service.maybe_shadow_hook(["BTC"]) is False  # 节流
    monkeypatch.setattr(config, "mode", lambda: "off")
    assert service.maybe_shadow_hook(["BTC"]) is False


def test_fusion_blend_mode_discipline(patched_kpanel, tmp_data, monkeypatch):
    monkeypatch.setattr(config, "mode", lambda: "shadow")
    assert service.fusion_blend("BTC", 0.7) == pytest.approx(0.7)  # shadow 不消费
    monkeypatch.setattr(config, "mode", lambda: "fusion")
    # 无 latest / IC 不达标 → 原样返回
    assert service.fusion_blend("BTC", 0.7) == pytest.approx(0.7)
    latest = {"ts": time.time(), "fused": {"BTC": {"hybrid": 1.0}}}
    config.latest_path().write_text(json.dumps(latest), encoding="utf-8")
    monkeypatch.setattr(service, "_load_ic_stats", lambda: {"ic_fused": 0.06})
    v = service.fusion_blend("BTC", 0.0)
    assert v == pytest.approx(config.fusion_weight_cap())  # 0*(1-w)+1*w
    monkeypatch.setattr(service, "_load_ic_stats", lambda: {"ic_fused": 0.01})
    assert service.fusion_blend("BTC", 0.0) == pytest.approx(0.0)  # IC 未达标不消费
