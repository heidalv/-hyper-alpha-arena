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
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_MOM_WINDOW = 20
_VOL_WINDOW = 20


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
            t = pd.to_datetime(ts[:n], unit="ms", errors="coerce").astype("int64")
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


def build_neutralized_returns(
    panels: Dict[str, Tuple[np.ndarray, np.ndarray]],
    fwd: int,
) -> Dict[str, np.ndarray]:
    """池化中性化：返回 {sym: 残差 fwd_return（与原 close 等长、NaN 表示不可用）}。"""
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
    try:
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    except Exception as e:  # noqa: BLE001
        logger.warning("[Neutralize] 回归失败: %s", e)
        return out
    resid_all = y - X @ beta
    # 残差整体均值归零（截距项残差应无系统偏移，浮点噪声归零更稳）
    resid_all = resid_all - resid_all.mean()
    reg["resid"] = resid_all
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
            t = pd.to_datetime(ts_a[:n], unit="ms", errors="coerce").astype("int64")
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
