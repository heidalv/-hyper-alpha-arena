"""收益中性化（升级计划 v3.0 S1/M2 · P1）。

背景：IC 直接对原始前瞻收益计算，市场 beta / 动量 / 波动风格会伪装成 alpha
通过门禁（给 beta 放行）。本模块做风格残差化：把前瞻收益对风格暴露做
横截面-时间池化 OLS（pooled OLS）取残差，后续 IC/ICIR/衰减/PBO 全部对
残差收益计算（walk-forward 回测仍用原始收益——绩效按真实 P&L 计量）。

风格（设计 v3.0 §M2）：
  - market beta : 每个时间戳上全币 fwd_return 的截面均值
  - momentum    : 单币 trailing 20 根 close.pct_change(20)
  - volatility  : 单币 trailing 20 根收益 std
crypto 截面仅 9 币 → 时间池化保证自由度。

对齐：各币以时间戳对齐到公共时间轴（内连接）；不在公共轴上的行残差为 NaN
（下游 IC 掩码自然剔除）。

[P3.2 2026-09-03] β 改为**训练窗拟合、全窗套用**：此前 4 个池化系数用整个窗口
（含末尾验证/判决段）估计，验证段的标签参与了 β 的拟合——泄漏量级 O(1/N)、
对标签而非特征，实务上极小，但与 held-out / WFO "验证段对拟合不可见"的原则相悖。
现按时间轴前 FACTOR_NEUTRALIZE_BETA_TRAIN_RATIO（默认 0.7）的时间戳拟合 β，
并剔除训练窗末尾 fwd 根（标签向前看 fwd 根，避免跨到验证窗），再把 β 套用到
全部行取残差。ratio=1.0 回到旧的全窗口口径（可回滚）。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_MOM_WINDOW = 20
_VOL_WINDOW = 20
#: 训练窗拟合 β 至少需要的回归样本数（低于此回退全窗口拟合，并记 fit_scope=full_fallback）
_MIN_TRAIN_ROWS = 60
#: 最近一次拟合的诊断信息（测试/日志用；不参与计算）
last_fit_info: Dict[str, object] = {}


def _beta_train_ratio(override: Optional[float] = None) -> float:
    """β 训练窗比例：显式参数 > settings.FACTOR_NEUTRALIZE_BETA_TRAIN_RATIO > 0.7。"""
    v = override
    if v is None:
        try:
            from backend.config import settings as _s
            v = getattr(_s, "FACTOR_NEUTRALIZE_BETA_TRAIN_RATIO", 0.7)
        except Exception:
            v = 0.7
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 0.7
    if not np.isfinite(v) or v <= 0.0:
        return 0.7
    return min(v, 1.0)


def _panel_frames(
    panels: Dict[str, Tuple[np.ndarray, np.ndarray]], fwd: int,
) -> Tuple[pd.DataFrame, List[str]]:
    """panels: {sym: (ts[], close[])} → 长表 DataFrame(ts, sym, close, fwd_ret, mom, vol)。

    返回 (frame, syms)。ts 可能为 float/int epoch 或字符串——统一转 int64 纳秒。
    """
    frames = []
    syms: List[str] = []
    for sym, (ts, close) in panels.items():
        ts = np.asarray(ts)
        close = np.asarray(close, dtype=float).ravel()
        n = min(len(ts), len(close))
        if n < fwd + _VOL_WINDOW + 2:
            continue
        try:
            t = pd.to_datetime(ts[:n], unit="s", errors="coerce").astype("int64")
        except Exception:
            t = pd.to_numeric(pd.Series(ts[:n]), errors="coerce").astype("int64")
        f = pd.DataFrame({"ts": t.values, "sym": sym, "close": close[:n]})
        f = f.dropna(subset=["ts"])
        f["fwd_ret"] = f["close"].pct_change(fwd).shift(-fwd)
        f["mom"] = f["close"].pct_change(_MOM_WINDOW)
        f["vol"] = f["close"].pct_change().rolling(_VOL_WINDOW).std()
        frames.append(f)
        syms.append(sym)
    if not frames:
        return pd.DataFrame(), syms
    return pd.concat(frames, ignore_index=True), syms


def _train_row_mask(ts: np.ndarray, fwd: int, ratio: float) -> Tuple[np.ndarray, Optional[int]]:
    """按时间轴切训练窗：前 ratio 的**不同时间戳**为训练窗，再剔除末尾 fwd 个时间戳（purge）。

    返回 (mask, cut_ts)；ratio>=1 → 全行 True、cut_ts=None（旧口径）。
    """
    ts = np.asarray(ts)
    if ratio >= 1.0:
        return np.ones(len(ts), dtype=bool), None
    uniq = np.unique(ts)
    n_ts = len(uniq)
    cut_idx = int(np.floor(n_ts * ratio)) - 1 - max(0, int(fwd))
    if cut_idx < 0:
        return np.zeros(len(ts), dtype=bool), None
    cut_ts = uniq[cut_idx]
    return ts <= cut_ts, int(cut_ts)


def build_neutralized_returns(
    panels: Dict[str, Tuple[np.ndarray, np.ndarray]],
    fwd: int,
    *,
    beta_train_ratio: Optional[float] = None,
) -> Dict[str, np.ndarray]:
    """池化中性化：返回 {sym: 残差 fwd_return（与原 close 等长、NaN 表示不可用）}。

    β 在时间轴前 beta_train_ratio 的训练窗上拟合（默认取 settings，0.7），
    套用到全部行取残差；残差用**训练窗残差均值**归零（验证窗不参与任何估计）。
    """
    global last_fit_info
    out: Dict[str, np.ndarray] = {}
    frame, syms = _panel_frames(panels, fwd)
    if frame.empty or len(syms) < 2:
        return out
    # 市场 beta：每个 ts 上截面均值 fwd_ret
    mkt = frame.groupby("ts")["fwd_ret"].transform("mean")
    frame["mkt"] = mkt
    reg = frame[["ts", "sym", "fwd_ret", "mkt", "mom", "vol"]].dropna()
    if len(reg) < 60:
        logger.warning("[Neutralize] 有效回归样本不足 %d，跳过中性化（回退原始收益口径）", len(reg))
        return out
    X = np.column_stack([
        np.ones(len(reg)),
        reg["mkt"].to_numpy(),
        reg["mom"].to_numpy(),
        reg["vol"].to_numpy(),
    ])
    y = reg["fwd_ret"].to_numpy()
    # [P3.2] 训练窗拟合 β（时间轴前 ratio + purge fwd 根），验证窗只套用
    ratio = _beta_train_ratio(beta_train_ratio)
    train_mask, cut_ts = _train_row_mask(reg["ts"].to_numpy(), int(fwd), ratio)
    fit_scope = "train"
    if ratio >= 1.0:
        fit_scope = "full"
    elif int(train_mask.sum()) < _MIN_TRAIN_ROWS:
        logger.info(
            "[Neutralize] 训练窗样本不足 %d（<%d），β 回退全窗口拟合",
            int(train_mask.sum()), _MIN_TRAIN_ROWS,
        )
        train_mask = np.ones(len(reg), dtype=bool)
        fit_scope = "full_fallback"
    try:
        beta, *_ = np.linalg.lstsq(X[train_mask], y[train_mask], rcond=None)
    except Exception as e:  # noqa: BLE001
        logger.warning("[Neutralize] 回归失败: %s", e)
        return out
    resid_all = y - X @ beta
    # 残差归零只用训练窗均值（截距项残差应无系统偏移，浮点噪声归零更稳；
    # 验证窗的均值不参与，避免把验证段信息带进残差口径）
    resid_all = resid_all - float(np.mean(resid_all[train_mask]))
    reg["resid"] = resid_all
    last_fit_info = {
        "fit_scope": fit_scope,
        "ratio": float(ratio),
        "n_train": int(train_mask.sum()),
        "n_total": int(len(reg)),
        "cut_ts": cut_ts,
        "beta": [float(b) for b in beta],
    }
    res_map = dict(zip(zip(reg["ts"], reg["sym"]), reg["resid"]))
    # 按原顺序回填
    for sym, (ts, close) in panels.items():
        if sym not in syms:
            out[sym] = np.full(len(np.asarray(close).ravel()), np.nan)
            continue
        ts_a = np.asarray(ts)
        close_a = np.asarray(close, dtype=float).ravel()
        n = min(len(ts_a), len(close_a))
        try:
            t = pd.to_datetime(ts_a[:n], unit="s", errors="coerce").astype("int64")
        except Exception:
            t = pd.to_numeric(pd.Series(ts_a[:n]), errors="coerce").astype("int64")
        res = np.array([res_map.get((tv, sym), np.nan) for tv in t.values], dtype=float)
        out[sym] = res
    return out


def neutralize_ic_series(
    factor_vals: np.ndarray, neutral_returns: np.ndarray, window: int = 30,
) -> np.ndarray:
    """对给定（已中性化的）收益序列算滚动 IC 时序（复用 scorer 的口径）。"""
    f = np.asarray(factor_vals, dtype=float).ravel()
    r = np.asarray(neutral_returns, dtype=float).ravel()
    n = min(len(f), len(r))
    if n < window:
        return np.full(n, np.nan)
    f, r = f[:n], r[:n]
    ics = np.full(n, np.nan)
    for i in range(window, n):
        fs, rs = f[i - window:i], r[i - window:i]
        m = np.isfinite(fs) & np.isfinite(rs)
        if int(m.sum()) < 20:
            continue
        xs = fs[m] - np.mean(fs[m])
        ys = rs[m] - np.mean(rs[m])
        denom = float(np.sqrt(np.sum(xs * xs)) * np.sqrt(np.sum(ys * ys)))
        if denom < 1e-12:
            continue
        ic = float(np.sum(xs * ys) / denom)
        if np.isfinite(ic):
            ics[i] = ic
    return ics
