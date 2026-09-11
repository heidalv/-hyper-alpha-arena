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
    # [2026-08-27 挖掘根治] 窗口上限 200 根：变异产生的超大窗口(数百~上千)会让
    # 本 O(n×w) 循环在 5400 样本面板上耗时数分钟级（MCTS 挂死根因，实测 ts_argmin
    # 卡死在 _rolling）。4h 因子 200 根=33 天，语义上无损失。
    w = min(w, 120)
    out = np.full(n, np.nan)
    if w > n:
        return out
    for i in range(w - 1, n):
        win = a[i - w + 1: i + 1]
        if np.isfinite(win).sum() < max(2, w // 2):
            continue
        out[i] = fn(win[np.isfinite(win)])
    return out


def _pd_rolling(x, w: int, kind: str) -> np.ndarray:
    """[2026-08-27 挖掘根治] 常用滚动算子走 pandas 向量化 C 实现。

    Python 版 _rolling 在 5400 样本面板上 O(n×w) 每窗口函数调用（w≤200 时
    单次求值 5-30s）——MCTS 批量评估挂死的根因。pandas rolling 亚毫秒级。
    """
    import pandas as _pd
    a = _as1d(x)
    w = min(max(1, int(w)), 120)
    # min_periods 对齐旧 Python 实现语义: 窗口内有效值 >= max(2, w//2) 才算
    s = _pd.Series(a)
    _mp = max(2, w // 2)
    if kind == "sum":
        out = s.rolling(w, min_periods=_mp).sum().to_numpy()
    elif kind == "mean":
        out = s.rolling(w, min_periods=_mp).mean().to_numpy()
    elif kind == "std":
        # ddof=0 对齐旧实现 np.std 语义(pandas 默认 ddof=1)
        out = s.rolling(w, min_periods=_mp).std(ddof=0).to_numpy()
    elif kind == "max":
        out = s.rolling(w, min_periods=_mp).max().to_numpy()
    elif kind == "min":
        out = s.rolling(w, min_periods=_mp).min().to_numpy()
    else:
        return _rolling(a, w, np.mean)
    return _mask_head(out, w)


def _mask_head(out: np.ndarray, w: int) -> np.ndarray:
    """[2026-09-02 GPU/CPU 等价修复] 头部 w-1 根强制 NaN。

    pandas rolling(w, min_periods=mp) 在 i < w-1 的**不满窗口**只要够 mp 个有效值
    就出值；而旧 Python _rolling、模块契约（"窗口不足处填 nan"）和 GPU 镜像
    （gpu_batch_eval._pos_gate）都是 i ≥ w-1 才出值。08-27 向量化时漏掉这一点，
    导致 std(ts_rank(v,5),30) 头部多出 11 个值 → 下游 ts_argmin 窗口有效值计数
    两侧不同 → GPU/CPU 等价验收 pearson 从 1.000000 跌到 0.988655。

    [2026-09-02 只读修复] 必须拿可写副本再原地改：pandas `.to_numpy()` 在 Copy-on-Write
    生效时返回只读视图，`out[:w-1] = nan` 抛 "assignment destination is read-only"
    （全量测试中某用例开启 CoW 后，ts_rank/ts_std 等 13 个用例连锁失败；生产中任一处
    开 CoW 也会让整条公式求值崩掉）。np.array(copy=True) 一律可写，代价是一次 O(n) 拷贝。
    """
    out = np.array(out, dtype=float, copy=True)
    if w > 1 and len(out) > 0:
        out[: min(w - 1, len(out))] = np.nan
    return out


def ts_sum(x, w: int = 5) -> np.ndarray:
    return _pd_rolling(x, w, "sum")


def ts_mean(x, w: int = 5) -> np.ndarray:
    return _pd_rolling(x, w, "mean")


def ts_std(x, w: int = 5) -> np.ndarray:
    return _pd_rolling(x, w, "std")


def ts_max(x, w: int = 5) -> np.ndarray:
    return _pd_rolling(x, w, "max")


def ts_min(x, w: int = 5) -> np.ndarray:
    return _pd_rolling(x, w, "min")


def ts_rank(x, w: int = 5) -> np.ndarray:
    """滚动排名：当前值在窗口内的百分位 (0..1)。

    [2026-08-27 性能] rolling().rank(pct=True) 是 C 实现，比逐窗 Python lambda
    快 100 倍+（原实现是 MCTS 批量评估慢的主热路径之一）。

    [2026-09-02 GPU/CPU 等价修复] 原始语义（ed92085，GPU 镜像 _roll_ts_rank 的基准）是
    `count(v <= last) / len(v)`，v 为窗口内剔除 NaN 后的值，且 ≥ max(2, w//2) 个才算。
    08-27 的 `rolling(w).rank(pct=True)` 有两处偏离：
      - 默认 min_periods=w → 窗口内任一 NaN 即 NaN（GPU 是剔除后凑够就算）；
      - 默认 method="average" → 并列时取平均名次（GPU/原实现是 ≤ 计数，即 "max"）。
        ts_rank(ts_rank(...)) 这类离散输入并列极常见，差异会被放大。
    现显式 min_periods + method="max"，并强制头部 w-1 根 NaN（同 _mask_head）。
    第三处偏离：a[i] 本身为 NaN 时 pandas 直接输出 NaN，而原实现/GPU 对窗口内
    "最后一个有效值"排名。真实因子链的 NaN 几乎只在连续头部（此时窗口也不够数），
    该情形极少，只对这些位置做逐点回填，向量化主路径不受影响。
    """
    import pandas as _pd
    a = _as1d(x)
    w = min(max(2, int(w)), 120)
    _mp = max(2, w // 2)
    out = _pd.Series(a).rolling(w, min_periods=_mp).rank(method="max", pct=True).to_numpy()
    out = _mask_head(out, w)
    nan_idx = np.flatnonzero(~np.isfinite(a))
    for i in nan_idx[nan_idx >= w - 1]:
        win = a[i - w + 1: i + 1]
        v = win[np.isfinite(win)]
        if len(v) >= _mp:
            out[i] = float((v <= v[-1]).sum()) / float(len(v))
    return out


def _argext_tolerant_first(v: np.ndarray, is_max: bool) -> int:
    """[算力刀 2026-08-21] 容差内取**首个**极值位置（CPU/GPU 跨设备确定性）。

    并列极值（量化输入常见：ts_rank/std 产出离散级）在跨设备求和顺序的
    ~1e-15 差异下会翻转 argmin/argmax 位置。容差 |极值|×1e-9+1e-12 内取
    首个出现，两侧同规则 → 选择确定（gpu_batch_eval._roll_argmaxmin 镜像）。
    """
    if is_max:
        m = float(np.max(v))
        return int(np.argmax(v >= m - (abs(m) * 1e-9 + 1e-12)))
    m = float(np.min(v))
    return int(np.argmax(v <= m + (abs(m) * 1e-9 + 1e-12)))


def ts_argmax(x, w: int = 5) -> np.ndarray:
    """窗口内最大值距当前的位置（0=当前, w-1=最早），归一化到 0..1。"""
    def _am(v):
        return float(len(v) - 1 - _argext_tolerant_first(v, True)) / float(max(1, len(v) - 1))
    return _rolling(x, w, _am)


def ts_argmin(x, w: int = 5) -> np.ndarray:
    def _am(v):
        return float(len(v) - 1 - _argext_tolerant_first(v, False)) / float(max(1, len(v) - 1))
    return _rolling(x, w, _am)


def ts_corr(x, y, w: int = 5) -> np.ndarray:
    """滚动皮尔逊相关。[2026-08-27] pandas 向量化 + 窗口上限 120（性能护栏）。"""
    import pandas as _pd
    a = _as1d(x)
    b = _as1d(y)
    w = min(max(2, int(w)), 120)
    n = min(len(a), len(b))
    out = np.full(n, np.nan)
    # [2026-08-27] pandas rolling corr 向量化（C 实现，原 Python 循环每窗
    # np.corrcoef 是 MCTS 挂死的主要热路径之一）。
    try:
        return _pd.Series(a[:n]).rolling(w, min_periods=max(2, w // 2)).corr(
            _pd.Series(b[:n])
        ).to_numpy()
    except Exception:
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
