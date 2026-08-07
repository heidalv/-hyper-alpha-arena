"""
因子评估指标（P1.2 的评估层，对标 §2.1.5）。

提供因子评估所需的全部指标计算：
    - IC / Rank IC（因子值与远期收益的相关）
    - ICIR（IC 的稳定性 = mean/std）
    - 单调性（分位组合超额收益的单调性 p 值）
    - Turnover（换手）
    - 半衰期（IC 衰减到峰值一半的 horizon）
    - 增量相关性（对活跃池）
    - DSR（Deflated Sharpe Ratio）
    - PBO（Probability of Backtest Overfitting）
    - 分位组合回测（5 分位净值/多头超额/多空对冲）
    - IC 显著性单边 t 检验
    - 滚动窗口 IC 与衰退检验
    - 复杂度惩罚（parsimony）
    - admission_gate（WorldQuant BRAIN 式提交门槛）

这些是 P1.2 清洗管线和 FactorLifecycle（P1.3）的共同基础。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from scipy import stats


@dataclass
class FactorEvalResult:
    """单因子评估结果。"""
    factor_id: str
    ic_mean: float = 0.0
    ic_std: float = 0.0
    rank_ic_mean: float = 0.0
    icir: float = 0.0           # mean(IC) / std(IC)
    monotonicity_p: float = 1.0
    turnover: float = 1.0
    halflife_bars: int = 0
    n_samples: int = 0


def information_coefficient(
    factor_values: np.ndarray,
    forward_returns: np.ndarray,
    *,
    method: str = "pearson",
) -> float:
    """单期 IC（因子值与远期收益的相关）。"""
    a = np.asarray(factor_values, dtype=float)
    b = np.asarray(forward_returns, dtype=float)
    mask = np.isfinite(a) & np.isfinite(b)
    if mask.sum() < 5:
        return 0.0
    if method == "spearman":
        r, _ = stats.spearmanr(a[mask], b[mask])
        return float(r) if np.isfinite(r) else 0.0
    r, _ = stats.pearsonr(a[mask], b[mask])
    return float(r) if np.isfinite(r) else 0.0


def time_series_ic(
    factor_series: pd.Series,
    return_series: pd.Series,
    *,
    method: str = "pearson",
) -> np.ndarray:
    """
    时序 IC 序列：逐期算因子值与下期收益的相关（滚动非重叠）。
    输入为对齐的时间序列，输出 IC 数组（每期一个）。
    """
    # 对齐
    factor_values = factor_series
    df = pd.DataFrame({"f": factor_values, "r": return_series}).dropna()
    if len(df) < 10:
        return np.array([])
    # 简化：整体 IC 作为单点（横截面场景逐期算需多品种面板）
    # 这里返回滑动窗口 IC 序列
    window = min(20, len(df) // 3)
    if window < 5:
        return np.array([information_coefficient(df["f"].values, df["r"].values, method=method)])
    ics = []
    for i in range(window, len(df)):
        seg_f = df["f"].iloc[i - window:i].values
        seg_r = df["r"].iloc[i - window:i].values
        ics.append(information_coefficient(seg_f, seg_r, method=method))
    return np.array(ics)


def compute_icir(ic_series: np.ndarray) -> float:
    """ICIR = mean(IC) / std(IC)。"""
    ic = np.asarray(ic_series, dtype=float)
    ic = ic[np.isfinite(ic)]
    if len(ic) < 2:
        return 0.0
    s = np.std(ic)
    if s < 1e-9:
        return 0.0
    return float(np.mean(ic) / s)


def compute_monotonicity(
    factor_values: np.ndarray,
    forward_returns: np.ndarray,
    *,
    n_quantiles: int = 5,
) -> float:
    """
    单调性检验：把因子值分 n_quantiles 档，看各档远期收益是否单调。
    返回 p 值（Spearman 相关的 p，越小越单调显著）。
    """
    a = np.asarray(factor_values, dtype=float)
    b = np.asarray(forward_returns, dtype=float)
    mask = np.isfinite(a) & np.isfinite(b)
    a, b = a[mask], b[mask]
    if len(a) < n_quantiles * 3:
        return 1.0
    # 分档平均收益
    quantile_edges = np.quantile(a, np.linspace(0, 1, n_quantiles + 1))
    quantile_idx = np.digitize(a, quantile_edges[1:-1])
    mean_ret_by_q = []
    for q in range(n_quantiles):
        sel = quantile_idx == q
        if sel.sum() > 0:
            mean_ret_by_q.append(float(np.mean(b[sel])))
    if len(mean_ret_by_q) < 3:
        return 1.0
    # Spearman 相关：档位序号 vs 平均收益
    r, p = stats.spearmanr(np.arange(len(mean_ret_by_q)), mean_ret_by_q)
    return float(p) if np.isfinite(p) else 1.0


def compute_turnover(factor_series: pd.Series) -> float:
    """换手：相邻两期仓位变化绝对值的平均。"""
    s = np.asarray(factor_series, dtype=float)
    s = s[np.isfinite(s)]
    if len(s) < 2:
        return 1.0
    # 用因子符号变化近似换手
    signs = np.sign(s)
    changes = np.abs(np.diff(signs))
    return float(np.mean(changes > 0))


def compute_halflife(ic_series: np.ndarray) -> int:
    """半衰期：IC 衰减到峰值一半的 horizon（简化：用 IC 序列的自相关衰减估）。"""
    ic = np.asarray(ic_series, dtype=float)
    ic = ic[np.isfinite(ic)]
    if len(ic) < 4:
        return 0
    peak = np.max(np.abs(ic))
    if peak < 1e-9:
        return 0
    half = peak / 2
    # 找从峰值后降到一半的步数
    peak_idx = int(np.argmax(np.abs(ic)))
    for j in range(peak_idx + 1, len(ic)):
        if abs(ic[j]) <= half:
            return j - peak_idx
    return len(ic) - peak_idx


def evaluate_factor(
    factor_id: str,
    factor_series: pd.Series,
    return_series: pd.Series,
    *,
    method: str = "spearman",
) -> FactorEvalResult:
    """完整评估单个因子，返回 FactorEvalResult。"""
    ic_series = time_series_ic(factor_series, return_series, method=method)
    rank_ic = information_coefficient(
        factor_series.values, return_series.values, method="spearman"
    )
    ic_mean = float(np.mean(ic_series)) if len(ic_series) else 0.0
    icir = compute_icir(ic_series)
    mono_p = compute_monotonicity(factor_series.values, return_series.values)
    turnover = compute_turnover(factor_series)
    halflife = compute_halflife(ic_series)
    return FactorEvalResult(
        factor_id=factor_id,
        ic_mean=ic_mean,
        ic_std=float(np.std(ic_series)) if len(ic_series) else 0.0,
        rank_ic_mean=rank_ic,
        icir=icir,
        monotonicity_p=mono_p,
        turnover=turnover,
        halflife_bars=halflife,
        n_samples=int(factor_series.notna().sum()),
    )


# ---------------------------------------------------------------------------
# 5.3.3 评估器升级：分层回测 / IC 显著性 / 滚动衰退 / 复杂度惩罚 / admission_gate
# ---------------------------------------------------------------------------


def _periods_per_year_default(period: str = "4h") -> float:
    """按周期估算一年期数（用于年化口径）。"""
    period = (period or "4h").lower()
    if period.endswith("m"):
        return 365.0 * 24 * 60 / float(period[:-1])
    if period.endswith("h"):
        return 365.0 * 24 / float(period[:-1])
    if period.endswith("d"):
        return 365.0 / float(period[:-1])
    return 365.0 * 24 / 4.0


@dataclass
class QuantileBacktestResult:
    """分位组合回测结果（5.3.3 quantile_backtest 的输出）。"""
    factor_id: str
    n_quantiles: int
    quantile_nav: np.ndarray           # (n_quantiles, n_obs) 每档累计净值
    quantile_annual_ret: np.ndarray    # 每档年化收益
    quantile_sharpe: np.ndarray        # 每档夏普（年化，无风险 0）
    long_short_cum: np.ndarray         # 多空对冲累计收益（top - bottom 逐期叠加）
    long_short_sharpe: float
    top_excess_cum: np.ndarray         # 多头组（top 档）累计超额 vs 基准
    top_excess_annual: float           # 多头组年化超额
    top_max_drawdown: float            # 多头组最大回撤
    monotonic_r: float                 # 档位-平均收益的 Spearman 相关
    n_obs: int = 0


@dataclass
class AdmissionGateResult:
    """admission_gate 判定结果（对齐 WorldQuant BRAIN 提交门槛）。"""
    passed: bool
    reasons: List[str] = field(default_factory=list)   # 未通过的项（passed 时为空）
    details: Dict[str, float] = field(default_factory=dict)  # 各项实测值


DEFAULT_GATE_CONFIG: Dict[str, float] = {
    # 全部门槛可配置（crypto 波动率缩放），默认值对标 WorldQuant BRAIN 并放宽到 crypto 口径
    "min_top_quantile_sharpe": 0.3,    # 多头组最小年化夏普
    "benchmark_coeff": 1.0,            # 多头夏普须 > benchmark_coeff × 基准夏普（无基准时忽略）
    "min_fitness": 1.0,                # Fitness > 1.0
    "min_turnover": 0.0,               # Turnover 下限（WorldQuant 1%）
    "max_turnover": 0.7,               # Turnover 上限（WorldQuant 70%）
    "max_pool_corr": 0.7,              # 与池内最大相关 |ρ| < 0.7
    "min_halflife_bars": 4,            # IC 半衰期 ≥ 4 期
    "ic_p_threshold": 0.05,            # IC 单边 t 检验 p < 0.05
    "min_ic_mean": 0.0,                # IC 均值 > 0
}


def quantile_backtest(
    factor_series: pd.Series,
    return_series: pd.Series,
    *,
    factor_id: str = "",
    n_quantiles: int = 5,
    benchmark_returns: Optional[pd.Series] = None,
    period: str = "4h",
) -> QuantileBacktestResult:
    """
    分位组合回测（5.3.3）：
    按因子值分 n_quantiles 档，逐档构建等权组合，输出累计净值 / 年化收益 / 夏普；
    多空对冲（top - bottom 逐期收益差累计）、多头组超额（vs 基准或全样本均值）、最大回撤、档位单调性。
    """
    df = pd.DataFrame({"f": factor_series, "r": return_series}).dropna()
    if len(df) < n_quantiles * 3:
        return QuantileBacktestResult(
            factor_id=factor_id,
            n_quantiles=n_quantiles,
            quantile_nav=np.full((n_quantiles, 0), np.nan),
            quantile_annual_ret=np.zeros(n_quantiles),
            quantile_sharpe=np.zeros(n_quantiles),
            long_short_cum=np.zeros(0),
            long_short_sharpe=0.0,
            top_excess_cum=np.zeros(0),
            top_excess_annual=0.0,
            top_max_drawdown=0.0,
            monotonic_r=0.0,
            n_obs=0,
        )
    f = df["f"].values.astype(float)
    r = df["r"].values.astype(float)
    ppy = _periods_per_year_default(period)

    edges = np.quantile(f, np.linspace(0, 1, n_quantiles + 1))
    # 边缘重复时（极端分布）用唯一值分组，避免空档
    q_idx = np.digitize(f, edges[1:-1])
    q_idx = np.clip(q_idx, 0, n_quantiles - 1)

    navs, annual_rets, sharpes = [], [], []
    for q in range(n_quantiles):
        r_q = r[q_idx == q]
        if len(r_q) == 0:
            navs.append(np.array([1.0]))
            annual_rets.append(0.0)
            sharpes.append(0.0)
            continue
        nav = np.cumprod(1.0 + r_q)
        navs.append(nav)
        ann_ret = nav[-1] ** (ppy / len(r_q)) - 1.0 if nav[-1] > 0 else -1.0
        vol = float(np.std(r_q)) * np.sqrt(ppy)
        annual_rets.append(float(ann_ret))
        sharpes.append(float(ann_ret / vol) if vol > 1e-9 else 0.0)

    # 多空对冲：逐期收益差（按时间序对齐前 n 期）
    top_mask = q_idx == (n_quantiles - 1)
    bot_mask = q_idx == 0
    n_pair = int(min(top_mask.sum(), bot_mask.sum()))
    ls = np.zeros(0)
    if n_pair > 0:
        top_r = r[top_mask][:n_pair]
        bot_r = r[bot_mask][:n_pair]
        ls = np.cumprod(1.0 + (top_r - bot_r))
    ls_sharpe = 0.0
    if len(ls) > 1:
        ls_vol = float(np.std(np.diff(np.append([1.0], ls))))
        if ls_vol > 1e-9:
            ls_sharpe = float(np.mean(ls - 1.0)) / ls_vol * np.sqrt(ppy) * 0.5

    # 多头超额：top 档 vs 基准（无基准则用全样本均值收益）
    top_nav = navs[-1]
    if benchmark_returns is not None:
        b = pd.Series(benchmark_returns).reindex(df.index).dropna()
        if len(b) >= len(top_nav):
            b_nav = np.cumprod(1.0 + b.values[: len(top_nav)])
        else:
            b_nav = np.cumprod(1.0 + b.values)
    else:
        b_nav = np.cumprod(1.0 + np.full(len(top_nav), np.mean(r)))
    excess = top_nav[: len(b_nav)] / b_nav[: len(top_nav)]
    excess_cum = excess
    top_excess_annual = float(excess[-1] ** (ppy / len(excess)) - 1.0) if excess[-1] > 0 else -1.0
    top_dd = 0.0
    if len(top_nav) > 0:
        peak = np.maximum.accumulate(top_nav)
        top_dd = float(np.max(peak / top_nav - 1.0)) if len(top_nav) else 0.0

    # 档位单调性（Spearman r，复用单调性检验思想）
    mean_ret_by_q = [float(np.mean(r[q_idx == q])) if (q_idx == q).sum() else np.nan for q in range(n_quantiles)]
    valid_q = [(i, v) for i, v in enumerate(mean_ret_by_q) if np.isfinite(v)]
    mono_r = 0.0
    if len(valid_q) >= 3:
        rr, _ = stats.spearmanr([i for i, _ in valid_q], [v for _, v in valid_q])
        mono_r = float(rr) if np.isfinite(rr) else 0.0

    max_nav_len = max((len(n) for n in navs), default=0)
    nav_matrix = np.full((n_quantiles, max_nav_len), np.nan)
    for _q, _nav in enumerate(navs):
        nav_matrix[_q, : len(_nav)] = _nav
    return QuantileBacktestResult(
        factor_id=factor_id,
        n_quantiles=n_quantiles,
        quantile_nav=nav_matrix,
        quantile_annual_ret=np.array(annual_rets),
        quantile_sharpe=np.array(sharpes),
        long_short_cum=ls,
        long_short_sharpe=ls_sharpe,
        top_excess_cum=excess_cum,
        top_excess_annual=top_excess_annual,
        top_max_drawdown=top_dd,
        monotonic_r=mono_r,
        n_obs=len(df),
    )


def ic_significance(ic_series: np.ndarray) -> float:
    """IC 序列单边 t 检验（H0: IC ≤ 0），返回 p 值（越小越显著为正）。"""
    ic = np.asarray(ic_series, dtype=float)
    ic = ic[np.isfinite(ic)]
    if len(ic) < 3:
        return 1.0
    t, p = stats.ttest_1samp(ic, 0.0)
    if not np.isfinite(t):
        return 1.0
    p_one = p / 2.0 if t > 0 else 1.0 - p / 2.0
    return float(np.clip(p_one, 0.0, 1.0))


def rolling_decay(
    factor_series: pd.Series,
    return_series: pd.Series,
    *,
    window: int = 30,
    step: int = 7,
    method: str = "spearman",
) -> Dict[str, object]:
    """
    滚动窗口 IC 与衰退检验（5.3.3 rolling_decay）：
    每 step 期滚动计算 window 期 IC，输出 IC 序列、线性趋势斜率、
    前后半段均值对比（衰退判定）、连续非正窗口数（neg_streak，供 5.5 自动降权触发）。
    """
    df = pd.DataFrame({"f": factor_series, "r": return_series}).dropna()
    if len(df) < window + step:
        return {"window_ics": np.array([]), "trend_slope": 0.0, "decay_p": 1.0,
                "first_half_mean_ic": 0.0, "second_half_mean_ic": 0.0,
                "neg_streak": 0, "decayed": False}
    f = df["f"].values
    r = df["r"].values
    ics = []
    i = 0
    while i + window <= len(df):
        ics.append(information_coefficient(f[i:i + window], r[i:i + window], method=method))
        i += step
    window_ics = np.array(ics)
    if len(window_ics) < 2:
        return {"window_ics": window_ics, "trend_slope": 0.0, "decay_p": 1.0,
                "first_half_mean_ic": float(np.mean(window_ics)) if len(window_ics) else 0.0,
                "second_half_mean_ic": 0.0, "neg_streak": 0, "decayed": False}
    # 线性趋势（slope < 0 表示 IC 在衰减）
    x = np.arange(len(window_ics))
    slope, _, _, p_val, _ = stats.linregress(x, window_ics)
    # 前后半段对比
    mid = len(window_ics) // 2
    first = float(np.mean(window_ics[:mid])) if mid else 0.0
    second = float(np.mean(window_ics[mid:])) if len(window_ics) - mid else 0.0
    # 连续非正窗口数（由尾部向前数）
    neg_streak = 0
    for v in window_ics[::-1]:
        if v <= 0:
            neg_streak += 1
        else:
            break
    decayed = bool(slope < 0 and second < first and neg_streak >= 2)
    return {
        "window_ics": window_ics,
        "trend_slope": float(slope),
        "decay_p": float(p_val) if np.isfinite(p_val) else 1.0,
        "first_half_mean_ic": first,
        "second_half_mean_ic": second,
        "neg_streak": int(neg_streak),
        "decayed": decayed,
    }


def parsimony_penalty(node_count: int, *, lambda_: float = 1e-3) -> float:
    """复杂度惩罚（5.3.3 / 5.2 公式膨胀控制）：λ × 节点数。供 GP 适应度使用。"""
    return lambda_ * max(0, int(node_count))


def admission_gate(
    *,
    factor_id: str = "",
    top_quantile_sharpe: float,
    benchmark_sharpe: Optional[float] = None,
    fitness: float,
    turnover: float,
    max_pool_corr: float,
    ic_halflife_bars: int,
    ic_mean: float,
    ic_p: float,
    config: Optional[Dict[str, float]] = None,
) -> AdmissionGateResult:
    """
    admission_gate（5.3.3，对齐 WorldQuant BRAIN 提交门槛，全配置化）：
    - 多头组 Sharpe > 基准×系数（无基准时用 min_top_quantile_sharpe）
    - Fitness > 1.0
    - Turnover 区间（默认 0-70%）
    - 与池内最大相关 |ρ| < 0.7
    - IC 半衰期 ≥ 阈值
    - IC 单边 t 检验 p < 阈值 且 IC 均值 > 0
    """
    cfg = {**DEFAULT_GATE_CONFIG, **(config or {})}
    reasons: List[str] = []
    details = {
        "top_quantile_sharpe": float(top_quantile_sharpe),
        "benchmark_sharpe": float(benchmark_sharpe) if benchmark_sharpe is not None else None,
        "fitness": float(fitness),
        "turnover": float(turnover),
        "max_pool_corr": float(max_pool_corr),
        "ic_halflife_bars": int(ic_halflife_bars),
        "ic_mean": float(ic_mean),
        "ic_p": float(ic_p),
    }
    # 1) 多头组夏普
    if benchmark_sharpe is not None and benchmark_sharpe > 0:
        min_sharpe = cfg["benchmark_coeff"] * benchmark_sharpe
    else:
        min_sharpe = cfg["min_top_quantile_sharpe"]
    if top_quantile_sharpe < min_sharpe:
        reasons.append(f"top_quantile_sharpe {top_quantile_sharpe:.3f} < {min_sharpe:.3f}")
    # 2) Fitness
    if fitness <= cfg["min_fitness"]:
        reasons.append(f"fitness {fitness:.3f} <= {cfg['min_fitness']:.3f}")
    # 3) Turnover 区间
    if turnover < cfg["min_turnover"] or turnover > cfg["max_turnover"]:
        reasons.append(f"turnover {turnover:.3f} 不在 [{cfg['min_turnover']}, {cfg['max_turnover']}]")
    # 4) 池内相关性
    if abs(max_pool_corr) >= cfg["max_pool_corr"]:
        reasons.append(f"max_pool_corr {max_pool_corr:.3f} >= {cfg['max_pool_corr']:.3f}")
    # 5) IC 半衰期
    if ic_halflife_bars < cfg["min_halflife_bars"]:
        reasons.append(f"ic_halflife_bars {ic_halflife_bars} < {cfg['min_halflife_bars']}")
    # 6) IC 显著为正
    if ic_mean <= cfg["min_ic_mean"]:
        reasons.append(f"ic_mean {ic_mean:.4f} <= {cfg['min_ic_mean']}")
    if ic_p >= cfg["ic_p_threshold"]:
        reasons.append(f"ic_p {ic_p:.4f} >= {cfg['ic_p_threshold']}")
    return AdmissionGateResult(passed=not reasons, reasons=reasons, details=details)
