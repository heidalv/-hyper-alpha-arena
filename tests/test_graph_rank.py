# -*- coding: utf-8 -*-
"""graph_rank 数据集 + 模型 + 服务 单元测试（纯合成数据，不依赖生产库）。"""
import numpy as np
import pandas as pd
import pytest
import torch

from backend.services.graph_rank.dataset import (
    PanelStandardizer,
    add_labels,
    synthetic_panel,
    time_split,
)
from backend.services.graph_rank.model import (
    RTGNNConfig,
    RTGNNModel,
    rank_ic_series,
    rtgnn_loss,
    spearman,
)
from backend.services.graph_rank.service import (
    ShadowReport,
    graph_score_for_symbols,
    lead_lag_relation_matrix,
    sector_relation_matrix,
    train_shadow_report,
)


# ─────────────────────────────────────────────────────────────
# 合成 data_center 假件（rows 带 timestamp 秒 + OHLCV）
# ─────────────────────────────────────────────────────────────
class _FakeDC:
    def __init__(self, panel):
        self.panel = panel

    def get_klines_batch(self, symbols, period, count=500, exchange=None, purpose="trade"):
        from backend.services.data_center import KlineResult

        out = {}
        ts = self.panel.ts
        for i, sym in enumerate(panel.symbols):
            closes = 100 * np.exp(np.cumsum(np.random.default_rng(i).normal(0, 0.01, len(ts))))
            rows = [
                {
                    "timestamp": int(t),
                    "open": float(o),
                    "high": float(o * 1.01),
                    "low": float(o * 0.99),
                    "close": float(c),
                    "volume": 1000.0,
                }
                for t, o, c in zip(ts, np.concatenate([[100.0], closes[:-1]]), closes)
            ]
            out[sym] = KlineResult(sym, period, "", rows)
        return out

    def get_klines(self, symbol, period, count=500, exchange=None, purpose="trade"):
        return self.get_klines_batch([symbol], period, count, exchange, purpose)[symbol]


panel = synthetic_panel(T=260, N=12, seed=11, lead_strength=0.5, mom_strength=0.5)


# ─────────────────────────────────────────────────────────────
# dataset
# ─────────────────────────────────────────────────────────────
def test_synthetic_panel_shape_and_labels():
    p = synthetic_panel(T=220, N=10, seed=0)
    assert p.X.shape == (220, 10, 8)
    assert p.symbols[0] == "BTC"
    assert p.btci == 0
    assert p.labels is not None and p.labels.shape == (220, 10)
    # 标签截面 demean 后每时间戳均值≈0（只看有有效标签的行）
    valid = np.isfinite(p.labels)
    ok_rows = valid.any(axis=1)
    row_means = np.nanmean(p.labels[ok_rows], axis=1)
    assert np.nanmax(np.abs(row_means)) < 1e-4


def test_add_labels_forward_direction():
    """标签必须落在起点行 t：label[t] = close[t+h]/close[t] − 1（防反向标签回归）。"""
    from backend.services.graph_rank.dataset import PanelData

    T, N, h = 60, 3, 6
    idx = pd.date_range("2026-01-01", periods=T, freq="1h", tz="UTC")
    ts = np.asarray(idx.astype("int64") // 10**9, dtype=np.int64)
    X = np.zeros((T, N, 8), dtype=np.float32)
    mask = np.ones((T, N), dtype=bool)
    # 收盘单调递增：A 快涨、B 慢涨、C 下跌
    closes = {
        "A": np.linspace(100, 300, T),
        "B": np.linspace(100, 150, T),
        "C": np.linspace(200, 100, T),
    }
    panel = PanelData(ts=ts, X=X, symbols=["A", "B", "C"], mask=mask, btci=None)
    panel = add_labels(panel, close_by_symbol=closes, horizon=h, cost_bps=0.0,
                       demean=False, btc_beta=False)
    for j, sym in enumerate(["A", "B", "C"]):
        c = closes[sym]
        for t in range(T - h):
            expect = c[t + h] / c[t] - 1.0
            assert panel.labels[t, j] == pytest.approx(expect, rel=1e-4), \
                f"{sym}@{t}: {panel.labels[t, j]} != {expect}"
        assert np.isnan(panel.labels[T - h:, j]).all(), "末 h 根必须无标签"


def test_labels_do_not_leak_past_return_on_rw():
    """随机游走合成数据（无漂移/无领先）：纯历史特征 ret6 对前瞻标签 IC≈0。"""
    p = synthetic_panel(T=400, N=12, seed=31, lead_strength=0.0, mom_strength=0.0)
    ret6 = torch.from_numpy(p.X[:, :, 2])
    labels = torch.from_numpy(p.labels)
    m = torch.from_numpy(np.isfinite(p.labels) & p.valid_mask())
    ic, _, _ = rank_ic_series(ret6, labels, m)
    assert abs(ic) < 0.08, f"随机游走下 ret6 对前瞻标签 IC 应≈0，实际 {ic:+.4f}（若高=标签反向泄漏）"


def test_time_split_ordering():
    s_tr, s_val, s_te = time_split(100, 0.6, 0.2)
    assert s_tr.stop == 60 and s_val.stop == 80 and s_te.stop == 100


def test_standardizer_train_only_stats():
    p = synthetic_panel(T=200, N=8, seed=1)
    s_tr, _, _ = time_split(200)
    std = PanelStandardizer.fit(p, s_tr)
    Xs = std.apply(p.X)
    # 早期滚动窗口留 NaN 属预期（模型按 mask 置零）；有效位上必须有限
    ok = np.isfinite(p.X).all(axis=2) & p.valid_mask()
    assert np.isfinite(Xs[ok]).all()
    assert std.mu.shape == (8,) and std.sd.shape == (8,)
    assert np.isfinite(std.mu).all() and (std.sd > 0).all()


def test_lead_lag_relation_matrix_asymmetric():
    p = synthetic_panel(T=200, N=6, seed=2, lead_strength=0.6)
    A = lead_lag_relation_matrix(p, max_lag=4)
    assert A is not None and A.shape == (6, 6)
    # BTC 领先滞后跟随币：BTC→lag>0 跟随币的强度应高于反向
    assert A[0, 3] > A[3, 0] + 0.05, f"锚应领先滞后跟随币: A[0,3]={A[0,3]:.3f} A[3,0]={A[3,0]:.3f}"


def test_sector_relation_matrix():
    p = synthetic_panel(T=120, N=6, seed=3)
    p.sector_ids = np.array([0, 1, 0, 1, 0, 1])
    A = sector_relation_matrix(p)
    assert A is not None
    assert A[0, 2] == 1.0 and A[0, 1] == 0.0


# ─────────────────────────────────────────────────────────────
# model
# ─────────────────────────────────────────────────────────────
def test_model_forward_shapes_and_asymmetry():
    torch.manual_seed(0)
    cfg = RTGNNConfig(d_model=16, n_heads=2, gru_hidden=16, gat_heads=2, top_k=4, ema_alpha=0.0)
    model = RTGNNModel(8, 10, cfg)
    x = torch.randn(10, 24, 8)  # (N,L,F)
    scores, aux = model(x)
    assert scores.shape == (10,)
    A = aux["adjacency"]
    assert A.shape == (10, 10)
    # 非对称（动量差打破对称性）
    assert not torch.allclose(A, A.T), "邻接矩阵应对称性被打破"


def test_rtgnn_loss_and_spearman():
    torch.manual_seed(0)
    scores = torch.tensor([1.0, 0.5, 0.0, -0.5])
    labels = torch.tensor([0.3, 0.1, -0.1, -0.3])
    valid = torch.tensor([True, True, True, True])
    loss = rtgnn_loss(scores, labels, valid, lambda_rank=0.5)
    assert torch.isfinite(loss) and loss.item() > 0
    # 完美排序时排序损失≈0（只剩回归项）
    perf = rtgnn_loss(labels, labels, valid, lambda_rank=1.0)
    assert perf.item() < 0.2
    assert spearman(scores, labels) == pytest.approx(1.0, abs=1e-6)
    rev = torch.tensor([-0.3, -0.1, 0.1, 0.3])
    assert spearman(scores, rev) == pytest.approx(-1.0, abs=1e-6)


def test_model_relations_and_gate_run():
    cfg = RTGNNConfig(d_model=16, n_heads=2, gru_hidden=16, gat_heads=2,
                      relations_enabled=True, market_gate=True, top_k=4, ema_alpha=0.0)
    model = RTGNNModel(8, 8, cfg)
    model.btci = 0
    x = torch.randn(8, 24, 8)
    a_lead = torch.rand(8, 8)
    a_sector = (torch.arange(8)[:, None] == torch.arange(8)[None, :]).float()
    regime = torch.zeros(6)
    scores, aux = model(x, a_lead=a_lead, a_sector=a_sector, regime=regime)
    assert scores.shape == (8,)
    assert torch.isfinite(scores).all()


def test_rank_ic_series():
    torch.manual_seed(0)
    T, N = 40, 10
    scores = torch.randn(T, N)
    labels = scores + 0.2 * torch.randn(T, N)  # 高相关
    mask = torch.ones(T, N, dtype=torch.bool)
    mean_ic, icir, ics = rank_ic_series(scores, labels, mask)
    assert mean_ic > 0.5, f"同源信号 Rank IC 应显著为正: {mean_ic:.3f}"
    assert len(ics) == T


# ─────────────────────────────────────────────────────────────
# service
# ─────────────────────────────────────────────────────────────
def test_train_shadow_report_insufficient_data_fail_closed(monkeypatch):
    from backend.services.graph_rank import service as svc

    def empty(symbols, period, count, horizon, sector_map):
        from backend.services.graph_rank.dataset import PanelData

        return PanelData(ts=np.asarray([], dtype=np.int64), X=np.zeros((0, 0, 0)), symbols=[]), {}

    monkeypatch.setattr(svc, "_load_panel_with_labels", empty)
    model, report, std = train_shadow_report(["A", "B"], epochs=2)
    assert model is None
    assert "数据不足" in report.note


def test_graph_score_for_symbols_disabled_fallback(monkeypatch):
    """未开启 GRAPH_RANK 时走零训练试点兜底。"""
    monkeypatch.setattr("backend.services.graph_rank.service.graph_rank_enabled", lambda: False)
    monkeypatch.setattr("backend.services.coin_rank.graph_signal.compute_graph_signals",
                        lambda symbols: {"A": {"lead": 1.0, "dm": 0.8}})
    out = graph_score_for_symbols(["A", "B"])
    assert out == {"A": pytest.approx(0.6 * 1.0 + 0.4 * 0.8)}


def test_graph_score_for_symbols_total_fail_closed(monkeypatch):
    monkeypatch.setattr("backend.services.graph_rank.service.graph_rank_enabled", lambda: False)

    def boom(symbols):
        raise RuntimeError("db down")

    monkeypatch.setattr("backend.services.coin_rank.graph_signal.compute_graph_signals", boom)
    assert graph_score_for_symbols(["A"]) == {}


def test_train_and_roundtrip_with_synthetic_dc(tmp_path, monkeypatch):
    """FakeDC 上完整跑 walk-forward：可学习性 + 保存 + 加载 + 预测分数。"""
    from backend.services.graph_rank import service as svc

    monkeypatch.setattr("backend.services.data_center.data_center", _FakeDC(panel))
    model_path = str(tmp_path / "model.pt")
    cfg = RTGNNConfig(d_model=16, n_heads=2, gru_hidden=16, gat_heads=2, dropout=0.0,
                      lambda_rank=0.5, top_k=4, ema_alpha=0.0)
    model, report, std = train_shadow_report(
        panel.symbols, period="1h", count=260, horizon=6, lookback=24,
        epochs=6, batch_size=8, lr=2e-3, patience=3, config=cfg, seed=42,
        save_path=model_path,
    )
    assert model is not None
    assert isinstance(report, ShadowReport)
    assert report.val_ic > 0.0, f"合成结构应可学习: {report.to_dict()}"
    assert report.test_ic > 0.0, f"样本外应保持正 IC: {report.to_dict()}"
    assert report.epochs_used >= 1

    # 加载（settings 属性优先于 env，直接打模块属性）
    import backend.config.settings as _settings

    monkeypatch.setattr(_settings, "GRAPH_RANK_MODEL_PATH", model_path)
    svc._graph_rank_service._load_attempted = False
    svc._graph_rank_service._model = None
    svc._graph_rank_service._score_cache.clear()
    assert svc._graph_rank_service._load() is True
    scores = svc._graph_rank_service.predict_scores(panel.symbols)
    assert set(scores) == set(panel.symbols)
    assert all(0.0 <= v <= 1.0 for v in scores.values())


def test_train_with_relations_and_market_gate(tmp_path, monkeypatch):
    """P4：多关系图（lead-lag + 板块）+ BTC 市场门控在完整管线中可训练、可学习。"""
    monkeypatch.setattr("backend.services.data_center.data_center", _FakeDC(panel))
    sector_map = {sym: ("even" if i % 2 == 0 else "odd") for i, sym in enumerate(panel.symbols)}
    cfg = RTGNNConfig(d_model=16, n_heads=2, gru_hidden=16, gat_heads=2, dropout=0.0,
                      lambda_rank=0.5, top_k=4, ema_alpha=0.0,
                      relations_enabled=True, market_gate=True)
    model, report, std = train_shadow_report(
        panel.symbols, period="1h", count=260, horizon=6, lookback=24,
        epochs=5, batch_size=8, lr=2e-3, patience=3, config=cfg, seed=42,
        sector_map=sector_map,
    )
    assert model is not None
    assert report.val_ic > 0.0, f"关系+门控不应破坏可学习性: {report.to_dict()}"
    assert torch.isfinite(torch.tensor(report.test_ic))
