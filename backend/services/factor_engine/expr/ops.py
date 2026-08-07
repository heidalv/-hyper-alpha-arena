"""
因子表达式 DSL — 算子库（P1.1）。

设计目标（方案 §2.1）：
    所有因子必须是表达式树（WorldQuant/AlphaGen 算子集），禁止自由 Python 因子类。
    理由：可静态审计 look-ahead、可快速重算（ExpressionCache）、可版本化追溯。

本模块：
    - 复用现有 formula_ops.py 的算子（已验证语义），并补齐 AlphaGen 算子集的缺失项。
    - 全部纯 numpy、1D 等长数组、窗口不足填 nan、不引入未来信息（t 时刻只用 ≤t 数据）。
    - protected 运算（div/sqrt/log）避免 NaN 传染。

算子分类（与方案 §2.1 一致）：
    Unary:    abs, sign, log, cs_rank, sqrt
    Binary:   add, sub, mul, div(protected), pow, greater, less
    Rolling:  ref, mean, sum, std, var, max, min, rank(ts), delta, wma, ema,
              decay_linear, ts_argmax, ts_argmin
    PairRoll: corr, cov
    Fields:   open, high, low, close, vwap, volume, returns, funding, oi, basis
              （fields 由 parser 绑定，不在本模块）
"""
from __future__ import annotations

import numpy as np

# 复用已验证的现有算子（避免语义漂移）
from backend.services.factor_engine.formula_ops import (  # noqa: F401
    decay_linear,
    delay,
    delta,
    scale,
    sign,
    ts_argmax,
    ts_argmin,
    ts_corr,
    ts_max,
    ts_mean,
    ts_min,
    ts_rank,
    ts_std,
    ts_sum,
)

# 内部辅助
_a1d = lambda x: np.asarray(x, dtype=float).reshape(-1)


def _rolling(a, w, fn):
    """滚动窗口应用（复用 formula_ops._rolling 语义，保持一致）。"""
    a = _a1d(a)
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


# ==================== Unary ====================

def abs_(x):
    return np.abs(_a1d(x))


def log(x):
    """自然对数，保护非正数 → nan。"""
    a = _a1d(x)
    out = np.full_like(a, np.nan)
    m = a > 0
    out[m] = np.log(a[m])
    return out


def sqrt(x):
    """平方根，保护负数 → nan。"""
    a = _a1d(x)
    out = np.full_like(a, np.nan)
    m = a >= 0
    out[m] = np.sqrt(a[m])
    return out


def cs_rank(x):
    """横截面排名（当前实现为序列内排名百分位 0..1）。

    注：真正的横截面 rank 需在 FactorCompute 层跨品种计算（需多品种对齐）。
    单序列内退化为当前值在全序列的分位，作为占位；横截面版由 FactorCompute 注入覆盖。
    """
    a = _a1d(x)
    valid = np.isfinite(a)
    out = np.full_like(a, np.nan)
    if valid.sum() < 2:
        return out
    argsort = np.argsort(a[valid])
    ranks = np.empty_like(argsort, dtype=float)
    ranks[argsort] = np.arange(len(argsort))
    out[valid] = ranks / max(1, (len(ranks) - 1))
    return out


# ==================== Binary ====================

def add(x, y):
    return _a1d(x) + _a1d(y)


def sub(x, y):
    return _a1d(x) - _a1d(y)


def mul(x, y):
    return _a1d(x) * _a1d(y)


def div(x, y):
    """protected 除法：|y|<1e-12 → 0（避免 inf/NaN 传染）。"""
    a, b = _a1d(x), _a1d(y)
    # 广播到等长（处理标量常量参数，如 div(close, 0) 中的 0）
    n = max(len(a), len(b))
    a = np.broadcast_to(a, (n,)).astype(float)
    b = np.broadcast_to(b, (n,)).astype(float)
    out = np.zeros(n)
    m = np.abs(b) > 1e-12
    out[m] = a[m] / b[m]
    return out


def pow_(x, y):
    """protected 幂：结果非有限处填 nan。"""
    a, b = _a1d(x), _a1d(y)
    with np.errstate(invalid="ignore", over="ignore"):
        out = np.power(np.abs(a), b) * np.sign(a)
    out[~np.isfinite(out)] = np.nan
    return out


def greater(x, y):
    return (_a1d(x) > _a1d(y)).astype(float)


def less(x, y):
    return (_a1d(x) < _a1d(y)).astype(float)


# ==================== Rolling（补充） ====================

def ref(x, d=1):
    """Ref(d): 取 d 期前的值（= delay）。"""
    return delay(x, d)


def mean(x, w=5):
    return ts_mean(x, w)


def sum_(x, w=5):
    return ts_sum(x, w)


def std(x, w=5):
    return ts_std(x, w)


def var(x, w=5):
    """滚动方差。"""
    return _rolling(x, w, lambda v: float(np.var(v)))


def max_(x, w=5):
    return ts_max(x, w)


def min_(x, w=5):
    return ts_min(x, w)


def rank_ts(x, w=20):
    """滚动时间序列排名（与 formula_ops.rank 一致）。"""
    return ts_rank(x, w)


def wma(x, w=5):
    """加权移动平均（线性权重，与 decay_linear 一致）。"""
    return decay_linear(x, w)


def ema(x, w=5):
    """指数移动平均，alpha = 2/(w+1)。"""
    a = _a1d(x)
    if len(a) == 0:
        return a.copy()
    alpha = 2.0 / (max(1, int(w)) + 1.0)
    out = np.full_like(a, np.nan)
    # 找第一个 finite
    idx = np.where(np.isfinite(a))[0]
    if len(idx) == 0:
        return out
    out[idx[0]] = a[idx[0]]
    prev = a[idx[0]]
    for i in range(int(idx[0]) + 1, len(a)):
        if np.isfinite(a[i]):
            prev = alpha * a[i] + (1 - alpha) * prev
            out[i] = prev
        else:
            out[i] = prev  # 用上次值前填，保持等长
    return out


# ==================== PairRoll ====================

def corr(x, y, w=5):
    return ts_corr(x, y, w)


def cov(x, y, w=5):
    """滚动协方差。"""
    a, b = _a1d(x), _a1d(y)
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
        out[i] = float(np.cov(va, vb)[0, 1])
    return out


# ==================== 算子注册表 ====================
# 键 = AST 中的 op 名；值 = 实现函数。
# arity 标注用于 audit 期参数数量校验。

OP_REGISTRY: dict[str, tuple[int, callable]] = {
    # Unary (arity=1)
    "abs": (1, abs_),
    "sign": (1, sign),
    "log": (1, log),
    "sqrt": (1, sqrt),
    "cs_rank": (1, cs_rank),
    # Binary (arity=2)
    "add": (2, add),
    "sub": (2, sub),
    "mul": (2, mul),
    "div": (2, div),
    "pow": (2, pow_),
    "greater": (2, greater),
    "less": (2, less),
    # Rolling (arity=2: x, window)  window 作为常量参数
    "ref": (2, ref),
    "mean": (2, mean),
    "sum": (2, sum_),
    "std": (2, std),
    "var": (2, var),
    "max": (2, max_),
    "min": (2, min_),
    "rank": (1, cs_rank),  # 横截面排名（Alpha101 风格，arity=1）
    "ts_rank": (2, ts_rank),  # 滚动时间序列排名（arity=2: x, w）
    "delta": (2, delta),
    "wma": (2, wma),
    "ema": (2, ema),
    "decay_linear": (2, decay_linear),
    "ts_argmax": (2, ts_argmax),
    "ts_argmin": (2, ts_argmin),
    "ts_corr": (3, ts_corr),  # x, y, w
    # PairRoll
    "corr": (3, corr),
    "cov": (3, cov),
    # scale (arity=2: x, k)
    "scale": (2, scale),
}

# 允许的字段名（parser 绑定时校验）
ALLOWED_FIELDS: frozenset[str] = frozenset({
    "open", "high", "low", "close", "vwap", "volume", "returns",
    "funding", "oi", "basis",  # 永续特化（P1.7）
    "amount", "turnover", "liquidation",  # 衍生品/清算（P1.7）
})
