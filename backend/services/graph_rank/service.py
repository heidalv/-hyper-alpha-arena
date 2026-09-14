# -*- coding: utf-8 -*-
"""graph_rank —— RTGNN 迁移 P1b：影子训练服务。

职责：
- train_shadow_report()：面板 → walk-forward 训练（时间切分、早停于验证 Rank IC）
  → 测试段 Rank IC/ICIR 报告（影子对照，不进实盘）
- GraphRankService：模型加载/预测/缓存；graph_score_for_symbols() 给 CoinRank /
  AutoCoin V3 提供 0~1 图分数（训练模型优先，零训练试点信号兜底）
- 全链路 fail-closed：模型未就绪/数据不足 → 空 dict，调用方降级旧行为
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch

from backend.services.graph_rank.dataset import (
    PanelData,
    PanelStandardizer,
    add_labels,
    build_panel,
    time_split,
)
from backend.services.graph_rank.model import (
    RTGNNConfig,
    RTGNNModel,
    rank_ic_series,
    rtgnn_loss,
)

logger = logging.getLogger(__name__)


def _cfg() -> Dict[str, Any]:
    import os as _os

    defaults: Dict[str, Any] = {
        "enabled": _os.getenv("GRAPH_RANK_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on"),
        "period": _os.getenv("GRAPH_RANK_PERIOD", "1h"),
        "count": int(_os.getenv("GRAPH_RANK_COUNT", "720") or "720"),
        "lookback": int(_os.getenv("GRAPH_RANK_LOOKBACK", "24") or "24"),
        "horizon": int(_os.getenv("GRAPH_RANK_HORIZON", "6") or "6"),
        "epochs": int(_os.getenv("GRAPH_RANK_EPOCHS", "20") or "20"),
        "batch_size": int(_os.getenv("GRAPH_RANK_BATCH_SIZE", "16") or "16"),
        "lr": float(_os.getenv("GRAPH_RANK_LR", "1e-3") or "1e-3"),
        "patience": int(_os.getenv("GRAPH_RANK_PATIENCE", "5") or "5"),
        "lambda_rank": float(_os.getenv("GRAPH_RANK_LAMBDA_RANK", "0.5") or "0.5"),
        "model_path": _os.getenv("GRAPH_RANK_MODEL_PATH", "data/graph_rank_model.pt"),
        "score_ttl_sec": int(_os.getenv("GRAPH_RANK_SCORE_TTL_SEC", "600") or "600"),
        "min_assets": int(_os.getenv("GRAPH_RANK_MIN_ASSETS", "12") or "12"),
        "device": _os.getenv("GRAPH_RANK_DEVICE", "auto"),
    }
    try:
        from backend.config import settings as s

        for key in list(defaults):
            attr = "GRAPH_RANK_" + key.upper()
            if hasattr(s, attr):
                defaults[key] = getattr(s, attr)
    except Exception:
        pass
    return defaults


def graph_rank_enabled() -> bool:
    return bool(_cfg()["enabled"])


def _resolve_device() -> torch.device:
    c = _cfg()
    if c["device"] != "auto":
        d = torch.device(c["device"])
        return d
    if torch.cuda.is_available():
        try:
            free_mb, _ = torch.cuda.mem_get_info(0)
            if free_mb >= 2048:
                return torch.device("cuda")
        except Exception:
            pass
    return torch.device("cpu")


# ─────────────────────────────────────────────────────────────
# 数据装配
# ─────────────────────────────────────────────────────────────
def _load_panel_with_labels(
    symbols: List[str],
    period: str,
    count: int,
    horizon: int,
    sector_map: Optional[Dict[str, str]] = None,
) -> Tuple[PanelData, Dict[str, np.ndarray]]:
    from backend.services.data_center import data_center

    want = [str(s).upper() for s in symbols if s]
    close_by: Dict[str, np.ndarray] = {}
    panel = build_panel(want, period=period, count=count, sector_map=sector_map)
    if panel.n_ts == 0 or panel.n_assets < 3:
        return panel, close_by
    # 标签需要 close 序列：与 panel.ts 对齐
    ts = panel.ts
    for sym in panel.symbols:
        try:
            df = data_center.get_klines(sym, period, count=count, purpose="research").to_dataframe()
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df = df.reindex(pd_to_datetime(ts))
        closes = df["close"].astype(float).to_numpy()
        if np.isfinite(closes).sum() >= 10:
            close_by[sym] = closes
    add_labels(panel, close_by_symbol=close_by, horizon=horizon, cost_bps=10.0,
               demean=True, btc_beta=True)
    return panel, close_by


def pd_to_datetime(ts: np.ndarray):
    import pandas as pd

    return pd.to_datetime(np.asarray(ts, dtype=np.int64), unit="s", utc=True)


# ─────────────────────────────────────────────────────────────
# 领先-滞后关系矩阵（P4 关系输入；服务层计算，模型消费）
# ─────────────────────────────────────────────────────────────
def lead_lag_relation_matrix(panel: PanelData, max_lag: int = 6) -> Optional[np.ndarray]:
    """基于面板 ret1 列的滞后互相关：A_lead[i,j] = i 领先 j 的强度（非对称）。

    方向约定：i 的过去收益预测 j 的未来收益 → corr(ret_i[t], ret_j[t+ℓ])，ℓ∈[1..max_lag]。
    """
    T, N = panel.n_ts, panel.n_assets
    if T < max_lag + 30 or N < 2:
        return None
    ret = panel.X[:, :, 0].T  # (N,T)
    A = np.zeros((N, N), dtype=np.float32)
    for i in range(N):
        ri = ret[i]
        if not np.isfinite(ri).any():
            continue
        si = (ri - np.nanmean(ri)) / (np.nanstd(ri) + 1e-12)
        for j in range(N):
            if i == j:
                continue
            rj = ret[j]
            sj = (rj - np.nanmean(rj)) / (np.nanstd(rj) + 1e-12)
            best = 0.0
            for lag in range(1, max_lag + 1):
                # i 的过去 vs j 的未来（i 领先 j）
                x, y = si[:-lag], sj[lag:]
                ok = np.isfinite(x) & np.isfinite(y)
                if ok.sum() < 30:
                    continue
                c = float(np.dot(x[ok], y[ok]) / ok.sum())
                best = max(best, c)
            c0 = float(np.dot(si[max_lag:-max_lag], sj[max_lag:-max_lag]) / max(1, T - 2 * max_lag))
            A[i, j] = max(0.0, best - c0)
    # 稀疏化：每行 top-k
    k = max(2, min(6, N - 1))
    out = np.zeros_like(A)
    for i in range(N):
        idx = np.argsort(A[i])[::-1][:k]
        out[i, idx] = A[i, idx]
    return out


def sector_relation_matrix(panel: PanelData) -> Optional[np.ndarray]:
    if panel.sector_ids is None:
        return None
    ids = np.asarray(panel.sector_ids)
    return (ids[:, None] == ids[None, :]).astype(np.float32)


# ─────────────────────────────────────────────────────────────
# 影子训练
# ─────────────────────────────────────────────────────────────
@dataclass
class ShadowReport:
    train_ic: float = 0.0
    val_ic: float = 0.0
    val_icir: float = 0.0
    test_ic: float = 0.0
    test_icir: float = 0.0
    epochs_used: int = 0
    n_train_samples: int = 0
    n_val_samples: int = 0
    n_test_samples: int = 0
    symbols: List[str] = field(default_factory=list)
    per_t_test_ics: List[float] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "train_ic": round(self.train_ic, 4),
            "val_ic": round(self.val_ic, 4),
            "val_icir": round(self.val_icir, 4),
            "test_ic": round(self.test_ic, 4),
            "test_icir": round(self.test_icir, 4),
            "epochs_used": self.epochs_used,
            "n_train_samples": self.n_train_samples,
            "n_val_samples": self.n_val_samples,
            "n_test_samples": self.n_test_samples,
            "symbols": self.symbols,
            "note": self.note,
        }


def _valid_ts_range(panel: PanelData, lookback: int) -> slice:
    """标签有效且回看窗口足够的范围。"""
    T = panel.n_ts
    lo = lookback
    hi = T
    if panel.labels is not None:
        ok_t = np.where(panel.valid_mask() & np.isfinite(panel.labels))[0]
        if len(ok_t):
            lo = max(lo, int(ok_t.min()))
            hi = min(hi, int(ok_t.max()) + 1)
    return slice(lo, hi)


def _sample_indices(rng: np.random.Generator, lo: int, hi: int, n: int, weights: Optional[np.ndarray] = None) -> np.ndarray:
    if weights is not None:
        w = weights[lo:hi]
        w = np.clip(w, 0, None) + 1e-9
        w = w / w.sum()
        return rng.choice(np.arange(lo, hi), size=n, replace=True, p=w)
    return rng.integers(lo, hi, size=n)


def train_shadow_report(
    symbols: List[str],
    *,
    period: Optional[str] = None,
    count: Optional[int] = None,
    horizon: Optional[int] = None,
    lookback: Optional[int] = None,
    epochs: Optional[int] = None,
    batch_size: Optional[int] = None,
    lr: Optional[float] = None,
    patience: Optional[int] = None,
    lambda_rank: Optional[float] = None,
    sector_map: Optional[Dict[str, str]] = None,
    config: Optional[RTGNNConfig] = None,
    seed: int = 42,
    save_path: Optional[str] = None,
) -> Tuple[Optional[RTGNNModel], ShadowReport, Optional[PanelStandardizer]]:
    """walk-forward 影子训练。返回 (model, report, standardizer)。

    - 时间切分（60/20/20），标准化只用训练段统计
    - 早停指标：验证段逐截面 Rank IC
    - 样本权重：近期样本指数加权（加密漂移快，半衰期 2~4 周 ≈ 168~672 根 1h）
    """
    c = _cfg()
    period = period or str(c["period"])
    count = int(count or c["count"])
    horizon = int(horizon if horizon is not None else c["horizon"])
    lookback = int(lookback or c["lookback"])
    epochs = int(epochs or c["epochs"])
    batch_size = int(batch_size or c["batch_size"])
    lr = float(lr or c["lr"])
    patience = int(patience if patience is not None else c["patience"])
    lambda_rank = float(lambda_rank if lambda_rank is not None else c["lambda_rank"])
    min_assets = int(c["min_assets"])

    panel, _ = _load_panel_with_labels(symbols, period, count, horizon, sector_map)
    if panel.n_ts < lookback + horizon + 20 or panel.n_assets < min_assets:
        rep = ShadowReport(symbols=panel.symbols,
                           note=f"数据不足: T={panel.n_ts} N={panel.n_assets}（需 T≥{lookback + horizon + 20}, N≥{min_assets}）")
        return None, rep, None

    s_train, s_val, s_test = time_split(panel.n_ts, 0.6, 0.2)
    rng = np.random.default_rng(seed)
    device = _resolve_device()
    torch.manual_seed(seed)

    # 标准化（训练段统计）
    std = PanelStandardizer.fit(panel, s_train)
    Xs = std.apply(panel.X)
    labels = panel.labels
    mask = panel.valid_mask()

    cfg = config or RTGNNConfig(lambda_rank=lambda_rank)
    cfg.relations_enabled = bool(cfg.relations_enabled)
    model = RTGNNModel(panel.n_features, panel.n_assets, cfg).to(device)
    if panel.btci is not None:
        model.btci = panel.btci

    # 关系矩阵（P4）
    a_lead = lead_lag_relation_matrix(panel) if cfg.relations_enabled else None
    a_sector = sector_relation_matrix(panel) if cfg.relations_enabled else None
    a_lead_t = torch.from_numpy(a_lead).to(device) if a_lead is not None else None
    a_sector_t = torch.from_numpy(a_sector).to(device) if a_sector is not None else None

    # 采样窗口与近期权重（半衰期 336 根 1h ≈ 2 周）
    rng_tr = _valid_ts_range(panel, lookback)
    train_lo, train_hi = max(s_train.start, rng_tr.start), min(s_train.stop, rng_tr.stop)
    half_life = 336.0
    decay = np.exp(-np.log(2.0) * np.maximum(0.0, np.arange(panel.n_ts)[::-1]) / half_life)

    def batch_loss(model: RTGNNModel) -> torch.Tensor:
        t_idx = _sample_indices(rng, train_lo, max(train_lo + 1, train_hi), batch_size, weights=decay)
        total = torch.zeros((), device=device)
        n_valid = 0
        for t in t_idx:
            x = torch.from_numpy(Xs[t - lookback + 1:t + 1]).to(device)          # (L,N,F)
            x = x.permute(1, 0, 2)                                               # (N,L,F)
            m = torch.from_numpy(mask[t - lookback + 1:t + 1]).to(device).permute(1, 0)  # (N,L)
            y = torch.from_numpy(labels[t]).to(device)                           # (N,)
            valid = torch.from_numpy(mask[t] & np.isfinite(labels[t])).to(device)
            scores, _ = model(x, mask=m, a_lead=a_lead_t, a_sector=a_sector_t)
            loss = rtgnn_loss(scores, y, valid, cfg.lambda_rank, cfg.rank_margin, cfg.use_huber)
            if torch.isfinite(loss):
                total = total + loss
                n_valid += 1
        return total / max(1, n_valid)

    def eval_scores(rng_s: slice) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        lo, hi = max(rng_s.start, rng_tr.start), min(rng_s.stop, rng_tr.stop)
        s_out, y_out, m_out = [], [], []
        with torch.no_grad():
            for t in range(lo, hi):
                x = torch.from_numpy(Xs[t - lookback + 1:t + 1]).to(device).permute(1, 0, 2)
                m = torch.from_numpy(mask[t - lookback + 1:t + 1]).to(device).permute(1, 0)
                sc, _ = model(x, mask=m, a_lead=a_lead_t, a_sector=a_sector_t)
                s_out.append(sc.cpu())
                y_out.append(torch.from_numpy(labels[t]))
                m_out.append(torch.from_numpy(mask[t] & np.isfinite(labels[t])))
        return torch.stack(s_out), torch.stack(y_out), torch.stack(m_out)

    opt = torch.optim.Adam(model.parameters(), lr=lr)
    best_val_ic = -1.0
    best_state = None
    bad = 0
    epochs_used = 0
    for ep in range(epochs):
        model.train()
        opt.zero_grad()
        loss = batch_loss(model)
        if torch.isfinite(loss) and loss.item() > 0:
            loss.backward()
            opt.step()
        model.eval()
        vs, vy, vm = eval_scores(s_val)
        val_ic, val_icir, _ = rank_ic_series(vs, vy, vm)
        if val_ic > best_val_ic + 1e-4:
            best_val_ic = val_ic
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            bad = 0
        else:
            bad += 1
            if bad >= patience:
                break
        epochs_used = ep + 1

    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()

    vs, vy, vm = eval_scores(s_val)
    val_ic, val_icir, _ = rank_ic_series(vs, vy, vm)
    ts, ty, tm = eval_scores(s_test)
    test_ic, test_icir, per_t = rank_ic_series(ts, ty, tm)
    trs, try_, trm = eval_scores(slice(train_lo, train_hi))
    train_ic, _, _ = rank_ic_series(trs, try_, trm)

    report = ShadowReport(
        train_ic=train_ic, val_ic=val_ic, val_icir=val_icir,
        test_ic=test_ic, test_icir=test_icir, epochs_used=epochs_used,
        n_train_samples=max(0, train_hi - train_lo), n_val_samples=len(vy),
        n_test_samples=len(ty), symbols=panel.symbols, per_t_test_ics=per_t,
        note=f"period={period} count={count} horizon={horizon} lookback={lookback} device={device}",
    )
    if save_path:
        _save_model(model, std, cfg, save_path, panel.symbols)
    return model, report, std


def _save_model(model: RTGNNModel, std: PanelStandardizer, cfg: RTGNNConfig, path: str, symbols: List[str]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    torch.save(
        {
            "state_dict": model.state_dict(),
            "mu": std.mu,
            "sd": std.sd,
            "config": cfg,
            "symbols": symbols,
            "n_features": model.n_features,
            "n_assets": model.n_assets,
            "btci": model.btci,
        },
        path,
    )


# ─────────────────────────────────────────────────────────────
# 推理服务（fail-closed）
# ─────────────────────────────────────────────────────────────
class GraphRankService:
    """模型加载 + 截面分数预测（0~1 百分位）。未就绪 → 空 dict。"""

    def __init__(self) -> None:
        self._model: Optional[RTGNNModel] = None
        self._std: Optional[PanelStandardizer] = None
        self._cfg: Optional[RTGNNConfig] = None
        self._symbols: List[str] = []
        self._score_cache: Dict[str, Tuple[float, Dict[str, float]]] = {}
        self._load_attempted = False

    def _load(self) -> bool:
        if self._load_attempted:
            return self._model is not None
        self._load_attempted = True
        path = _cfg()["model_path"]
        if not os.path.exists(path):
            return False
        try:
            data = torch.load(path, map_location="cpu", weights_only=False)
            cfg = data.get("config") or RTGNNConfig()
            self._model = RTGNNModel(data["n_features"], data["n_assets"], cfg)
            self._model.load_state_dict(data["state_dict"])
            self._model.btci = data.get("btci")
            self._model.eval()
            self._std = PanelStandardizer(data["mu"], data["sd"])
            self._cfg = cfg
            self._symbols = list(data.get("symbols") or [])
            logger.info("[GraphRank] 模型已加载: %s（N=%d）", path, data["n_assets"])
            return True
        except Exception as e:
            logger.warning("[GraphRank] 模型加载失败: %s", e)
            return False

    def is_ready(self) -> bool:
        return self._load()

    def predict_scores(self, symbols: List[str], period: Optional[str] = None, count: Optional[int] = None) -> Dict[str, float]:
        """{SYM: 0~1 百分位分}；失败/未就绪 → {}。"""
        c = _cfg()
        now = time.time()
        key = tuple(sorted(set(str(s).upper() for s in symbols)))
        hit = self._score_cache.get(key)
        if hit and hit[0] > now:
            return dict(hit[1])
        if not self._load():
            return {}
        period = period or str(c["period"])
        count = int(count or c["count"])
        lookback = int(c["lookback"])  # 回看窗口是服务级配置（模型可变长时序，无需对齐训练窗口）

        model_syms = self._symbols or [str(s).upper() for s in symbols]
        panel = build_panel(model_syms, period=period, count=max(count, lookback + 20))
        if panel.n_ts < lookback or panel.n_assets < 2:
            return {}
        Xs = self._std.apply(panel.X)
        with torch.no_grad():
            x = torch.from_numpy(Xs[-lookback:]).permute(1, 0, 2)
            mask = None
            if panel.mask is not None:
                mask = torch.from_numpy(panel.mask[-lookback:]).permute(1, 0)
            scores, _ = self._model(x, mask=mask)
        vals = scores.cpu().numpy()
        # 截面百分位 → [0,1]
        order = np.argsort(np.argsort(vals))
        n = max(1, len(order) - 1)
        pct = order / n
        out = {sym: float(pct[i]) for i, sym in enumerate(panel.symbols)}
        wanted = {str(s).upper() for s in symbols}
        out = {s: v for s, v in out.items() if s in wanted}
        self._score_cache[key] = (now + float(c["score_ttl_sec"]), out)
        return out


_graph_rank_service = GraphRankService()


def graph_score_for_symbols(symbols: List[str]) -> Dict[str, float]:
    """给 CoinRank / AutoCoin V3 的图分数提供方（0~1）。

    优先已训练的 RTGNN-lite；模型不可用 → 零训练试点信号（lead/dm 融合）兜底；
    两者都不可用 → {}（调用方降级旧行为，零影响）。
    """
    if not symbols:
        return {}
    if graph_rank_enabled():
        try:
            scores = _graph_rank_service.predict_scores(list(symbols))
            if scores:
                return scores
        except Exception as e:
            logger.debug("[GraphRank] 模型预测失败，回退零训练试点: %s", e)
    try:
        from backend.services.coin_rank.graph_signal import compute_graph_signals

        merged = compute_graph_signals(list(symbols))
        lead_w = 0.6
        try:
            from backend.config.settings import COIN_RANK_GRAPH_LEAD_WEIGHT

            lead_w = float(COIN_RANK_GRAPH_LEAD_WEIGHT)
        except Exception:
            pass
        return {
            sym: max(0.0, min(1.0, lead_w * float(g.get("lead", 0.0)) + (1.0 - lead_w) * float(g.get("dm", 0.5))))
            for sym, g in merged.items()
        }
    except Exception as e:
        logger.debug("[GraphRank] 零训练试点兜底失败: %s", e)
        return {}


graph_rank_service = _graph_rank_service


# ─────────────────────────────────────────────────────────────
# 周度重训入口（main.py 定时任务调用；模拟盘 DC 持续供数 → 模型随数据滚动刷新）
# ─────────────────────────────────────────────────────────────
def run_graph_rank_retrain() -> Dict[str, Any]:
    """周度全量重训 + 保存模型；失败不抛（调度器日志兜底）。"""
    if not graph_rank_enabled():
        return {"status": "disabled"}
    t0 = time.time()
    try:
        symbols = list(_cfg().get("symbols") or [])
        if not symbols:
            # 缺省宇宙：数据中心的流动性 TopN（P0 数据管道持续积累）
            from backend.services.data_center import data_center

            symbols = list(data_center.get_top_liquid_symbols(n=40, min_volume_usd=2_000_000) or [])
        if not symbols:
            return {"status": "no_symbols"}
        cfg = RTGNNConfig(d_model=32, n_heads=4, gru_hidden=32, gat_heads=4,
                          dropout=0.1, lambda_rank=0.5, top_k=8, ema_alpha=0.2)
        c = _cfg()
        model, report, std = train_shadow_report(
            symbols, period=str(c["period"]), count=int(c["count"]),
            horizon=int(c["horizon"]), lookback=int(c["lookback"]),
            epochs=int(c["epochs"]), batch_size=int(c["batch_size"]),
            lr=float(c["lr"]), patience=int(c["patience"]),
            lambda_rank=float(c["lambda_rank"]), config=cfg, seed=42,
            save_path=str(c["model_path"]),
        )
        if model is None:
            return {"status": "insufficient_data", "note": report.note if report else "?"}
        # 重训后清推理缓存，下一次选币循环用新模型
        _graph_rank_service._score_cache.clear()
        _graph_rank_service._load_attempted = False
        _graph_rank_service._model = None
        logger.info("[GraphRank] 周度重训完成: val_ic=%.4f test_ic=%.4f 耗时%.1fs",
                    report.val_ic, report.test_ic, time.time() - t0)
        return {"status": "ok", **report.to_dict(), "elapsed_s": round(time.time() - t0, 1)}
    except Exception as e:
        logger.warning("[GraphRank] 周度重训失败（非致命）: %s", e)
        return {"status": "error", "error": str(e)[:200]}
