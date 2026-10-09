# -*- coding: utf-8 -*-
"""特征工程 —— 通道A的横截面特征（formula_ops 同源算子）。

防前视纪律（设计 §3.1）：
    - 全部特征在 t 时刻只使用 ≤t 的 OHLCV（滚动算子窗口不足填 NaN，与 DSL 契约一致）；
    - 标签 fwd_ret = t+1 收盘 / t 收盘 - 1（横截面十分位 0..9 作 lambdarank label_gain）；
    - build_panel 输出的每一行 (symbol, date) 均满足：特征信息集 ⊂ {≤t}，标签 ∈ {t+1}。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from backend.services.hybrid_scoring import kpanel

logger = logging.getLogger(__name__)

_EPS = 1e-12

# 特征清单（顺序即模型特征顺序，落盘进 model_meta.json）
# [2026-09-17 实测] 1d 横截面呈弱反转（动量特征 IC≈0/负），补 rev_1/rev_3 让
# IC 加权兜底有正 IC 特征可用。
FEATURES: List[str] = [
    "mom_5",       # 5日均值收益（短动量）
    "mom_20",      # 20日均值收益（中动量）
    "rev_1",       # 1日反转（-ret_1）
    "rev_3",       # 3日反转（-mean(ret,3)）
    "vol_20",      # 20日收益波动
    "range_14",    # 14日平均振幅（high-low)/close
    "wobble_5",    # 5日收益波动（微观噪声）
    "rsi_pct_14",  # 14日收益百分位（趋势强度代理）
    "volz_20",     # 成交量 20日 z 分数（量能异动）
    "dd_20",       # 距 20日高点的回撤
    "close_z_60",  # 收盘价 60日 z 分数（横截面价格位置）
    "turnover_20", # 20日平均成交额（对数）
]


def _feature_row(df: pd.DataFrame) -> Optional[Dict[str, float]]:
    """从单币 OHLCV（时间升序）算最新一根的特征。窗口不足 → None（不硬造）。"""
    from backend.services.factor_engine.formula_ops import ts_max, ts_mean, ts_rank, ts_std

    if df is None or len(df) < 60:  # 最长窗口 60
        return None
    try:
        close = df["close"].to_numpy(dtype=float)
        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        volume = df["volume"].to_numpy(dtype=float)
        rets = np.diff(close, prepend=close[0]) / (close + _EPS)

        def _last(a: np.ndarray, w: int) -> float:
            v = a[-w] if len(a) >= w else np.nan
            return float(v) if np.isfinite(v) else np.nan

        rng = (high - low) / (close + _EPS)
        vol_usd = volume * close
        row = {
            "mom_5": _last(ts_mean(rets, 5), 1),
            "mom_20": _last(ts_mean(rets, 20), 1),
            "rev_1": float(-rets[-1]) if np.isfinite(rets[-1]) else np.nan,
            "rev_3": float(-np.nanmean(rets[-3:])),
            "vol_20": _last(ts_std(rets, 20), 1),
            "range_14": _last(ts_mean(rng, 14), 1),
            "wobble_5": _last(ts_std(rets, 5), 1),
            "rsi_pct_14": _last(ts_rank(rets, 14), 1),
            "volz_20": float((volume[-1] - np.nanmean(volume[-20:])) / (np.nanstd(volume[-20:]) + _EPS)),
            "dd_20": float(close[-1] / (np.nanmax(ts_max(close, 20)[-1:]) + _EPS) - 1.0),
            "close_z_60": float((close[-1] - np.nanmean(close[-60:])) / (np.nanstd(close[-60:]) + _EPS)),
            "turnover_20": float(np.log(np.nanmean(vol_usd[-20:]) + 1.0)),
        }
    except Exception as e:  # noqa: BLE001
        logger.debug("[HybridScore.features] 计算失败: %s", str(e)[:120])
        return None
    if not any(np.isfinite(v) for v in row.values()):
        return None
    return row


def _series_features(df: pd.DataFrame) -> pd.DataFrame:
    """单币逐日特征表（index=date，列为 FEATURES）。供训练面板使用。"""
    from backend.services.factor_engine.formula_ops import ts_max, ts_mean, ts_rank, ts_std

    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    volume = df["volume"].to_numpy(dtype=float)
    rets = np.diff(close, prepend=close[0]) / (close + _EPS)
    rng = (high - low) / (close + _EPS)
    vol_usd = volume * close

    def _roll(a, w, fn):
        out = fn(a, w)
        return np.asarray(out, dtype=float)

    out = pd.DataFrame(index=df.index)
    out["mom_5"] = _roll(rets, 5, ts_mean)
    out["mom_20"] = _roll(rets, 20, ts_mean)
    out["rev_1"] = -pd.Series(rets).to_numpy()
    out["rev_3"] = -_roll(rets, 3, ts_mean)
    out["vol_20"] = _roll(rets, 20, ts_std)
    out["range_14"] = _roll(rng, 14, ts_mean)
    out["wobble_5"] = _roll(rets, 5, ts_std)
    out["rsi_pct_14"] = _roll(rets, 14, ts_rank)
    # 逐日 z 分数（只用 ≤t 窗口）
    def _zday(a: np.ndarray, w: int) -> np.ndarray:
        s = pd.Series(a)
        m = s.rolling(w, min_periods=w).mean()
        sd = s.rolling(w, min_periods=w).std()
        return ((s - m) / (sd + _EPS)).to_numpy(dtype=float)

    out["volz_20"] = _zday(volume, 20)
    out["dd_20"] = close / (np.asarray(ts_max(close, 20), dtype=float) + _EPS) - 1.0
    out["close_z_60"] = _zday(close, 60)
    out["turnover_20"] = np.log(pd.Series(vol_usd).rolling(20, min_periods=20).mean().to_numpy() + 1.0)
    out["fwd_ret"] = pd.Series(close).shift(-1).to_numpy() / (close + _EPS) - 1.0  # t+1（标签专用）
    return out


def _cross_sectional_z(panel: pd.DataFrame) -> pd.DataFrame:
    """逐日横截面 z 分数（时序信息 + 横截面位置，均在 ≤t 信息集内）。"""
    for c in FEATURES:
        panel[c] = panel.groupby(level="date")[c].transform(
            lambda s: (s - s.mean()) / (s.std(ddof=1) + _EPS)
        )
    return panel


def build_panel(symbols: List[str], period: str = "1d", bars: int = 400) -> pd.DataFrame:
    """构建训练面板：MultiIndex(date, symbol) × FEATURES + fwd_ret。

    横截面宽度 < 5 的日期丢弃（RankIC 无意义）；尾部无标签行丢弃。
    """
    frames = []
    for sym in symbols:
        df = kpanel.load_klines(sym, period=period, bars=bars)
        if df is None or len(df) < 60:
            continue
        f = _series_features(df)
        f = f.assign(symbol=kpanel.norm_sym(sym))
        f = f.rename_axis("date").reset_index()
        frames.append(f)
    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames, ignore_index=True)
    panel = panel.dropna(subset=["fwd_ret"])                       # 只留有标签行
    panel["n_cs"] = panel.groupby("date")["symbol"].transform("count")
    panel = panel[panel["n_cs"] >= 5].drop(columns=["n_cs"])       # 横截面宽度门
    panel = panel.dropna(subset=FEATURES, how="all")
    panel = panel.set_index(["date", "symbol"]).sort_index()
    panel = _cross_sectional_z(panel)
    # label_gain：fwd_ret 的逐日十分位（0..9）——lambdarank 的整数标签
    panel["label_gain"] = panel.groupby(level="date")["fwd_ret"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 10, labels=False, duplicates="drop")
    )
    panel["label_gain"] = panel["label_gain"].fillna(0).astype(int)
    return panel


def live_rows(symbols: List[str], period: str = "1d") -> Dict[str, Dict[str, float]]:
    """部署侧：每币最新一根的特征（未经横截面 z；打分前由调用方做同行横截面 z）。"""
    rows: Dict[str, Dict[str, float]] = {}
    for sym in symbols:
        df = kpanel.load_klines(sym, period=period, bars=120)
        r = _feature_row(df) if df is not None and len(df) else None
        if r:
            rows[kpanel.norm_sym(sym)] = r
    return rows


def cross_z_rows(rows: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    """对 live_rows 做一次横截面 z（与训练同口径）。样本 <5 时不变换（原值直用）。"""
    if len(rows) < 5:
        return rows
    cols = FEATURES
    mat = pd.DataFrame([r for r in rows.values()])[cols].astype(float)
    z = (mat - mat.mean()) / (mat.std(ddof=1) + _EPS)
    out = {}
    for (sym, _), (_, zr) in zip(rows.items(), z.iterrows()):
        out[sym] = {c: (float(zr[c]) if np.isfinite(zr[c]) else 0.0) for c in cols}
    return out
