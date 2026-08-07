"""formula_ops — 公式因子可用的时间序列算子库（供受限 eval 命名空间注入）。

背景
====
`custom_factor_store.make_formula_compute` 与 `factor_backtest_scorer._eval_formula`
的受限命名空间此前只暴露 `np` + OHLCV 数组，无法表达 Alpha101 那类需要
delay/delta/滚动均值方差/滚动相关/滚动排名 的公式。本模块提供一组**纯 numpy、
作用于 1D 数组、返回等长数组**的时间序列算子，注入到两处 eval 命名空间后，
公式即可写成 Alpha101 风格（单表达式、向量化、无副作用）。

所有算子：
- 输入/输出均为 1D np.ndarray（等长），窗口不足处填 nan（下游用 isfinite 掩码）。
- 不引入未来信息：t 时刻只用 ≤t 的数据。
- 安全：无 IO、无 import、无属性访问，配合 `{"__builtins__": {}}` 的受限 eval。
"""
from __future__ import annotations

import numpy as np


def _as1d(x) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    return a.reshape(-1)


def delay(x, d: int = 1) -> np.ndarray:
    """滞后 d 期：t 取 t-d 的值，前 d 个填 nan。"""
    a = _as1d(x)
    d = int(d)
    out = np.full_like(a, np.nan)
    if d <= 0:
        return a.copy()
    if d < len(a):
        out[d:] = a[:-d]
    return out


def delta(x, d: int = 1) -> np.ndarray:
    """差分：x - delay(x, d)。"""
    a = _as1d(x)
    return a - delay(a, d)


def _rolling(a: np.ndarray, w: int, fn) -> np.ndarray:
    a = _as1d(a)
    w = max(1, int(w))
    n = len(a)
    out = np.full(n, np.nan)
    if w > n:
        return out
    for i in range(w - 1, n):
        win = a[i - w + 1: i + 1]
        if np.isfinite(win).sum() < max(2, w // 2):
            continue
        out[i] = fn(win[np.isfinite(win)])
    return out


def ts_sum(x, w: int = 5) -> np.ndarray:
    return _rolling(x, w, np.sum)


def ts_mean(x, w: int = 5) -> np.ndarray:
    return _rolling(x, w, np.mean)


def ts_std(x, w: int = 5) -> np.ndarray:
    return _rolling(x, w, lambda v: np.std(v))


def ts_max(x, w: int = 5) -> np.ndarray:
    return _rolling(x, w, np.max)


def ts_min(x, w: int = 5) -> np.ndarray:
    return _rolling(x, w, np.min)


def ts_rank(x, w: int = 5) -> np.ndarray:
    """滚动排名：窗口内最后一个值的百分位 (0..1)。"""
    def _rank_last(v):
        last = v[-1]
        return float((v <= last).sum()) / float(len(v))
    return _rolling(x, w, _rank_last)


def ts_argmax(x, w: int = 5) -> np.ndarray:
    """窗口内最大值距当前的位置（0=当前, w-1=最早），归一化到 0..1。"""
    def _am(v):
        return float(len(v) - 1 - int(np.argmax(v))) / float(max(1, len(v) - 1))
    return _rolling(x, w, _am)


def ts_argmin(x, w: int = 5) -> np.ndarray:
    def _am(v):
        return float(len(v) - 1 - int(np.argmin(v))) / float(max(1, len(v) - 1))
    return _rolling(x, w, _am)


def ts_corr(x, y, w: int = 5) -> np.ndarray:
    """滚动皮尔逊相关。"""
    a = _as1d(x)
    b = _as1d(y)
    w = max(2, int(w))
    n = min(len(a), len(b))
    out = np.full(n, np.nan)
    for i in range(w - 1, n):
        va = a[i - w + 1: i + 1]
        vb = b[i - w + 1: i + 1]
        m = np.isfinite(va) & np.isfinite(vb)
        if m.sum() < max(2, w // 2):
            continue
        va, vb = va[m], vb[m]
        if np.std(va) < 1e-12 or np.std(vb) < 1e-12:
            out[i] = 0.0
            continue
        out[i] = float(np.corrcoef(va, vb)[0, 1])
    return out


def scale(x, k: float = 1.0) -> np.ndarray:
    """缩放：使 sum(|x|)=k（逐点，用全序列范数近似）。"""
    a = _as1d(x)
    s = np.nansum(np.abs(a))
    if s < 1e-12:
        return a.copy()
    return a * (float(k) / s)


def sign(x) -> np.ndarray:
    return np.sign(_as1d(x))


def rank(x, w: int = 20) -> np.ndarray:
    """单序列无横截面 rank，退化为滚动时间序列排名（默认窗口 20）。"""
    return ts_rank(x, w)


def decay_linear(x, w: int = 5) -> np.ndarray:
    """线性加权移动平均（越近权重越大）。"""
    a = _as1d(x)
    w = max(1, int(w))
    weights = np.arange(1, w + 1, dtype=float)
    weights /= weights.sum()

    def _wm(v):
        vv = v[-w:] if len(v) >= w else v
        ww = weights[-len(vv):]
        ww = ww / ww.sum()
        return float(np.dot(vv, ww))
    return _rolling(a, w, _wm)


# 注入到受限 eval 命名空间的算子表（键即公式中可用的函数名）
FORMULA_OPS = {
    "delay": delay,
    "delta": delta,
    "ts_sum": ts_sum,
    "ts_mean": ts_mean,
    "ts_std": ts_std,
    "ts_max": ts_max,
    "ts_min": ts_min,
    "ts_rank": ts_rank,
    "ts_argmax": ts_argmax,
    "ts_argmin": ts_argmin,
    "ts_corr": ts_corr,
    "scale": scale,
    "sign": sign,
    "rank": rank,
    "decay_linear": decay_linear,
}
