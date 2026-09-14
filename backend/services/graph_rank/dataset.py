# -*- coding: utf-8 -*-
"""graph_rank —— RTGNN 迁移 P0：多币对齐面板数据集。

把 data_center 的多币 K 线装配成 (T, N, F) 面板：
- 特征：ret1/ret3/ret6/ret12（多周期收益）、rv（实现波动）、vol_z（量 z-score）、
  hi_lo（振幅）、close_d（收盘偏离 vwap）
- 标签：成本调整前瞻收益 → 截面 demean（一阶市场中性，MDGNN benchmark 减法同思路）
- 时间切分与标准化统计量只来自训练段（防前视）
- point-in-time：宇宙由调用方显式传入（不做"今天 top50 回填历史"）

另含 synthetic_panel：测试用合成面板（BTC 锚 + 不同滞后跟随币 + 动量加速信号），
保证可学习性测试不依赖生产数据。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 特征名顺序即 X 的第 3 维顺序
PANEL_FEATURES: List[str] = ["ret1", "ret3", "ret6", "ret12", "rv", "vol_z", "hi_lo", "close_d"]
_RET_IDX = {"ret1": 0, "ret3": 1, "ret6": 2, "ret12": 3}

DEFAULT_COST_BPS = 10.0  # 双边手续费+滑点估计（流动性分级可外部覆盖）


@dataclass
class PanelData:
    """(T, N, F) 面板。ts 为对齐后的 unix 秒。"""

    ts: np.ndarray
    X: np.ndarray
    symbols: List[str]
    feature_names: List[str] = field(default_factory=lambda: list(PANEL_FEATURES))
    mask: Optional[np.ndarray] = None  # (T,N) 有效位；None = 全有效
    btci: Optional[int] = None         # BTC 在 symbols 中的下标（市场锚）
    sector_ids: Optional[np.ndarray] = None  # (N,) int 板块 id（可选，P4 板块关系用）
    labels: Optional[np.ndarray] = None      # (T,N) 成本调整+截面中性化前瞻收益
    source: str = ""

    @property
    def n_ts(self) -> int:
        return int(self.X.shape[0])

    @property
    def n_assets(self) -> int:
        return int(self.X.shape[1])

    @property
    def n_features(self) -> int:
        return int(self.X.shape[2])

    def valid_mask(self) -> np.ndarray:
        return self.mask if self.mask is not None else np.ones((self.n_ts, self.n_assets), dtype=bool)


# ─────────────────────────────────────────────────────────────
# 单币特征
# ─────────────────────────────────────────────────────────────
def _features_from_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)
    amount = df["amount"].astype(float) if "amount" in df.columns else pd.Series(np.nan, index=df.index)

    out = pd.DataFrame(index=df.index)
    out["ret1"] = close.pct_change()
    out["ret3"] = close.pct_change(3)
    out["ret6"] = close.pct_change(6)
    out["ret12"] = close.pct_change(12)
    rv = out["ret1"].rolling(12).std()
    out["rv"] = rv
    vmean = volume.rolling(24).mean()
    vstd = volume.rolling(24).std()
    out["vol_z"] = (volume - vmean) / (vstd + 1e-9)
    out["hi_lo"] = (high - low) / close.replace(0, np.nan)
    if amount.notna().sum() > 10:
        vwap = (amount / volume.replace(0, np.nan)).rolling(12).mean()
    else:
        vwap = ((high + low + close) / 3.0).rolling(12).mean()
    out["close_d"] = close / vwap - 1.0
    out = out.replace([np.inf, -np.inf], np.nan)
    return out


# ─────────────────────────────────────────────────────────────
# 面板构建
# ─────────────────────────────────────────────────────────────
def build_panel(
    symbols: List[str],
    period: str = "1h",
    count: Optional[int] = None,
    exchange: Optional[str] = None,
    min_bars_per_symbol: int = 30,
    sector_map: Optional[Dict[str, str]] = None,
) -> PanelData:
    """多币 K 线 → 对齐面板。数据不足的币直接剔除并记录日志。"""
    from backend.services.data_center import data_center

    want = [str(s).upper() for s in symbols if s]
    count = int(count or 500)
    batch = data_center.get_klines_batch(want, period, count=count, exchange=exchange, purpose="research")

    feats: Dict[str, pd.DataFrame] = {}
    for sym, result in (batch or {}).items():
        try:
            df = result.to_dataframe()
        except Exception as e:
            logger.debug("[GraphRank] %s 取数失败: %s", sym, e)
            continue
        if df is None or len(df) < min_bars_per_symbol:
            continue
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert("UTC")
        f = _features_from_ohlcv(df)
        if f.notna().sum().sum() >= min_bars_per_symbol:
            feats[str(sym).upper()] = f

    if not feats:
        logger.warning("[GraphRank] build_panel: 无可用币")
        return PanelData(ts=np.asarray([], dtype=np.int64), X=np.zeros((0, 0, 0)), symbols=[])

    # 公共时间轴：各币时间戳交集（排序去重）
    common = None
    for f in feats.values():
        idx = np.asarray(f.index.astype("int64") // 10**9, dtype=np.int64)
        common = idx if common is None else np.intersect1d(common, idx)
    if common is None or len(common) < min_bars_per_symbol:
        logger.warning("[GraphRank] build_panel: 公共时间轴不足 %d", min_bars_per_symbol)
        return PanelData(ts=np.asarray([], dtype=np.int64), X=np.zeros((0, 0, 0)), symbols=[])

    ordered = [s for s in want if s in feats]
    ts = np.sort(common)
    T, N, F = len(ts), len(ordered), len(PANEL_FEATURES)
    X = np.full((T, N, F), np.nan, dtype=np.float32)
    mask = np.zeros((T, N), dtype=bool)
    for j, sym in enumerate(ordered):
        f = feats[sym].reindex(pd.to_datetime(ts, unit="s", utc=True))
        vals = f[PANEL_FEATURES].to_numpy(dtype=np.float32)
        X[:, j, :] = vals
        mask[:, j] = np.isfinite(vals).all(axis=1)

    btci = ordered.index("BTC") if "BTC" in ordered else None
    sector_ids = None
    if sector_map:
        ids: Dict[str, int] = {}
        arr = np.zeros(N, dtype=np.int64)
        for j, sym in enumerate(ordered):
            sec = sector_map.get(sym, "other")
            if sec not in ids:
                ids[sec] = len(ids)
            arr[j] = ids[sec]
        sector_ids = arr

    return PanelData(
        ts=ts, X=X, symbols=ordered, mask=mask, btci=btci, sector_ids=sector_ids,
        source=f"{period}@{exchange or 'default'}",
    )


# ─────────────────────────────────────────────────────────────
# 标签
# ─────────────────────────────────────────────────────────────
def add_labels(
    panel: PanelData,
    close_by_symbol: Optional[Dict[str, np.ndarray]] = None,
    horizon: int = 6,
    cost_bps: float = DEFAULT_COST_BPS,
    demean: bool = True,
    btc_beta: bool = True,
    beta_window: int = 96,
) -> PanelData:
    """追加前瞻收益标签（成本调整 + 截面 demean + 可选 BTC-beta 残差）。

    注意：标签需要 close 价格序列，单独传入 close_by_symbol {SYM: closes[T]}（与
    panel.ts 对齐）；调用方（service）从同批 K 线构造，避免面板只存特征丢信息。
    beta 用 trailing 窗口（β_window 根）估计——只用历史，无前视。
    """
    T, N = panel.n_ts, panel.n_assets
    if not close_by_symbol:
        raise ValueError("add_labels 需要 close_by_symbol {SYM: closes[]}（与 panel.ts 对齐）")
    fwd = np.full((T, N), np.nan, dtype=np.float32)
    for j, sym in enumerate(panel.symbols):
        closes = np.asarray(close_by_symbol.get(sym, []), dtype=float)
        if len(closes) < T:
            continue
        c = closes[:T]
        with np.errstate(divide="ignore", invalid="ignore"):
            # 前瞻收益必须存在「起点行 t」：fwd[t] = close[t+h]/close[t] − 1
            # （此前误存到终点行 t+h，标签变成反向收益 = 模型预测过去，IC 虚高）
            fwd[:-horizon, j] = (c[horizon:] / c[:-horizon]) - 1.0
        fwd[:, j] -= cost_bps / 1e4  # 成本调整：扣费后收益（与实盘对齐）
    valid = np.isfinite(fwd) & panel.valid_mask()
    y = fwd.copy()

    if demean:
        # 截面 demean：一阶市场中性（MDGNN 减 benchmark 的等权版）
        mu = np.zeros(T, dtype=np.float32)
        for t in range(T):
            row = y[t][valid[t]]
            mu[t] = float(row.mean()) if len(row) else np.nan
        y = y - mu[:, None]

    if btc_beta and panel.btci is not None and panel.btci >= 0:
        btc = y[:, panel.btci].copy()
        for j in range(N):
            if j == panel.btci:
                continue
            for t in range(beta_window, T):
                if not valid[t, j]:
                    continue
                w = slice(t - beta_window, t)
                yj = fwd[w, j]
                yb = fwd[w, panel.btci]
                ok = np.isfinite(yj) & np.isfinite(yb)
                if ok.sum() < 24:
                    continue
                beta = float(np.cov(yj[ok], yb[ok])[0, 1] / (np.var(yb[ok]) + 1e-12))
                y[t, j] = fwd[t, j] - beta * btc[t]
    y[~valid] = np.nan
    panel.labels = y.astype(np.float32)
    return panel


# ─────────────────────────────────────────────────────────────
# 切分与标准化
# ─────────────────────────────────────────────────────────────
def time_split(T: int, train_ratio: float = 0.6, val_ratio: float = 0.2) -> Tuple[slice, slice, slice]:
    """按时间顺序切 train/val/test（严禁随机切）。"""
    n_train = max(1, int(T * train_ratio))
    n_val = max(1, int(T * val_ratio))
    return slice(0, n_train), slice(n_train, n_train + n_val), slice(n_train + n_val, T)


@dataclass
class PanelStandardizer:
    """训练段统计量 → 全局标准化（防前视）。"""

    mu: np.ndarray
    sd: np.ndarray

    @classmethod
    def fit(cls, panel: PanelData, train_slice: slice) -> "PanelStandardizer":
        X = panel.X[train_slice].reshape(-1, panel.n_features)
        ok = np.isfinite(X).all(axis=1)
        mu = np.nanmean(X[ok], axis=0) if ok.any() else np.zeros(panel.n_features, dtype=np.float32)
        sd = np.nanstd(X[ok], axis=0) if ok.any() else np.ones(panel.n_features, dtype=np.float32)
        sd = np.where(sd < 1e-8, 1.0, sd)
        return cls(mu.astype(np.float32), sd.astype(np.float32))

    def apply(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mu) / self.sd


# ─────────────────────────────────────────────────────────────
# 合成面板（测试用）
# ─────────────────────────────────────────────────────────────
def synthetic_panel(
    T: int = 260,
    N: int = 12,
    seed: int = 0,
    lead_strength: float = 0.55,
    mom_strength: float = 0.40,
    anchor_std: float = 0.02,
) -> PanelData:
    """BTC 锚 + 滞后跟随币 + 动量加速信号的合成面板。

    结构（保证可学习）：
    - 币 0 = BTC 锚，随机游走
    - 币 i 的收益 = lead_strength·btc_ret[t−ℓ_i] + 噪声，ℓ_i = i % 4（部分币被锚领先）
    - 最后 1/3 段：给"加速"币（偶数 i）额外正漂移、给"减速"币（奇数 i）负漂移
      —— 模型应能从 ret3/ret6/ret12 特征学到加速排名
    """
    rng = np.random.default_rng(seed)
    btc_ret = rng.normal(0, anchor_std, size=T)
    closes = np.zeros((T, N), dtype=np.float64)
    volumes = np.zeros((T, N), dtype=np.float64)
    highs = np.zeros((T, N), dtype=np.float64)
    lows = np.zeros((T, N), dtype=np.float64)
    closes[0] = 100.0
    rets = np.zeros((T, N), dtype=np.float64)
    split_t = int(T * 2 / 3)
    for i in range(N):
        lag = i % 4
        noise = rng.normal(0, anchor_std * 1.4, size=T)
        base = np.zeros(T)
        base[lag:] = btc_ret[:-lag] if lag > 0 else btc_ret
        drift = np.zeros(T)
        drift[split_t:] = (mom_strength * anchor_std * 0.5) * (1.0 if i % 2 == 0 else -1.0)
        r = lead_strength * base + noise + drift
        rets[:, i] = r
        c = 100.0 * np.exp(np.cumsum(r))
        closes[:, i] = c
        highs[:, i] = c * (1.0 + np.abs(r))
        lows[:, i] = c * (1.0 - 0.5 * np.abs(r))
        volumes[:, i] = rng.lognormal(0.0, 0.5, size=T) * 1000.0 * (1.0 + 2.0 * np.abs(r))

    rows: Dict[str, pd.DataFrame] = {}
    close_by_symbol: Dict[str, np.ndarray] = {}
    idx = pd.date_range("2026-01-01", periods=T, freq="1h", tz="UTC")
    for i in range(N):
        sym = "BTC" if i == 0 else f"S{i}"
        df = pd.DataFrame(
            {
                "open": np.concatenate([[100.0], closes[:-1, i]]),
                "high": highs[:, i],
                "low": lows[:, i],
                "close": closes[:, i],
                "volume": volumes[:, i],
            },
            index=idx,
        )
        rows[sym] = df
        close_by_symbol[sym] = closes[:, i]

    # 复用特征管道
    feats: Dict[str, pd.DataFrame] = {}
    for sym, df in rows.items():
        feats[sym] = _features_from_ohlcv(df)
    ts = np.asarray(idx.astype("int64") // 10**9, dtype=np.int64)
    X = np.stack([feats[sym][PANEL_FEATURES].to_numpy(dtype=np.float32) for sym in feats], axis=1)
    mask = np.isfinite(X).all(axis=2)
    symbols = ["BTC"] + [f"S{i}" for i in range(1, N)]
    panel = PanelData(
        ts=ts, X=X, symbols=symbols, mask=mask, btci=0,
        source=f"synthetic(T={T},N={N},seed={seed})",
    )
    panel = add_labels(panel, close_by_symbol=close_by_symbol, horizon=6, cost_bps=10.0,
                       demean=True, btc_beta=False)
    return panel
