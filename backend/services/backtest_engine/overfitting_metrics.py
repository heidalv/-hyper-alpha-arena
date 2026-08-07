"""
研究级过拟合诊断指标。

对标：López de Prado *Advances in Financial Machine Learning* Ch.11-15，
      Bailey & López de Prado (2014) *The Deflated Sharpe Ratio*，
      Bailey & López de Prado (2012) *The Sharpe Ratio Efficient Frontier*。

提供：
  - compute_pbo_cscv       组合对称交叉验证（CSCV）估计回测过拟合概率 PBO
  - probabilistic_sharpe_ratio (PSR)   观察 Sharpe 超过基准的概率（考虑偏度/峰度）
  - deflated_sharpe_ratio (DSR)        对多重检验做通胀校正后的 PSR
  - min_backtest_length (MinTRL)       使 Sharpe 可信所需的最小回测长度

依赖：numpy、scipy.stats（均为既有依赖）。纯函数、无副作用、可独立单测。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations
from typing import List, Optional, Tuple

import numpy as np

try:
    from scipy.stats import norm

    def _phi(x: float) -> float:
        return float(norm.cdf(x))

    def _phi_inv(p: float) -> float:
        return float(norm.ppf(p))
except Exception:  # noqa: BLE001 —— scipy 缺失时用误差函数近似，保证可用
    def _phi(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    def _phi_inv(p: float) -> float:
        # Acklam 近似逆正态；仅在 scipy 缺失时兜底
        p = min(max(p, 1e-12), 1 - 1e-12)
        a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
             1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
        b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
             6.680131188771972e+01, -1.328068155288572e+01]
        c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
             -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
        d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
             3.754408661907416e+00]
        plow, phigh = 0.02425, 1 - 0.02425
        if p < plow:
            q = math.sqrt(-2 * math.log(p))
            return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                   ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
        if p > phigh:
            q = math.sqrt(-2 * math.log(1 - p))
            return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                    ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5]) * q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


_EULER_GAMMA = 0.5772156649015329


@dataclass
class CSCVResult:
    """Combinatorially Symmetric Cross-Validation 结果。"""
    pbo: float                          # Probability of Backtest Overfitting ∈ [0,1]
    logit_pbo: float                    # logit(rank) 均值；<0 偏过拟合
    stochastic_dominance: np.ndarray    # 各组合中 best-IS 策略的 OOS 相对秩分布
    n_combinations: int = 0
    verdict: str = ""                   # 'robust'|'borderline'|'overfit'

    @staticmethod
    def classify(pbo: float) -> str:
        if pbo < 0.1:
            return "robust"
        if pbo <= 0.5:
            return "borderline"
        return "overfit"


def _sharpe(returns: np.ndarray) -> float:
    """非年化 Sharpe（均值/标准差）。std≈0 时返回 0。"""
    if returns.size < 2:
        return 0.0
    mu = float(np.mean(returns))
    sd = float(np.std(returns, ddof=1))
    if sd < 1e-12:
        return 0.0
    return mu / sd


def compute_pbo_cscv(
    is_returns: np.ndarray,
    oos_returns: np.ndarray,
    n_blocks: int = 16,
) -> CSCVResult:
    """用 CSCV 估计回测过拟合概率（PBO）。

    Args:
        is_returns:  形状 (n_strategies, n_is_periods) 的样本内收益矩阵。
        oos_returns: 形状 (n_strategies, n_oos_periods) 的样本外收益矩阵。
        n_blocks:    CSCV 分块数（偶数）；López de Prado 建议 16 → C(16,8)=12870 组合。

    Returns:
        CSCVResult(pbo, logit_pbo, stochastic_dominance, n_combinations, verdict)

    做法：把 IS+OOS 沿时间拼成性能矩阵 M(T, N)，等分 n_blocks；枚举所有
    "半块作 IS、半块作 OOS" 的对称组合；对每个组合选出 IS 表现最优策略，
    考察其在 OOS 的相对秩 ω；PBO = ω ≤ 0.5（OOS 落到中位数以下）的组合占比。
    """
    is_returns = np.atleast_2d(np.asarray(is_returns, dtype=float))
    oos_returns = np.atleast_2d(np.asarray(oos_returns, dtype=float))
    n_strategies = is_returns.shape[0]
    if n_strategies < 2:
        raise ValueError("PBO/CSCV 至少需要 2 个策略（配置）参与比较")
    if n_blocks % 2 != 0:
        n_blocks += 1

    # 沿时间拼接：M 形状 (T, N)
    M = np.vstack([is_returns.T, oos_returns.T])  # (n_is+n_oos, N)
    T = M.shape[0]
    if T < n_blocks:
        n_blocks = max(2, (T // 2) * 2)

    # 切成 n_blocks 个（近似等长）时间块
    block_bounds = np.array_split(np.arange(T), n_blocks)
    blocks = [b for b in block_bounds if len(b) > 0]
    n_blocks = len(blocks)
    if n_blocks < 2:
        raise ValueError("数据长度不足以进行 CSCV 分块")

    half = n_blocks // 2
    logits: List[float] = []
    omegas: List[float] = []
    n_comb = 0
    for is_combo in combinations(range(n_blocks), half):
        is_set = set(is_combo)
        is_idx = np.concatenate([blocks[i] for i in range(n_blocks) if i in is_set])
        oos_idx = np.concatenate([blocks[i] for i in range(n_blocks) if i not in is_set])

        is_perf = np.array([_sharpe(M[is_idx, n]) for n in range(n_strategies)])
        oos_perf = np.array([_sharpe(M[oos_idx, n]) for n in range(n_strategies)])

        n_star = int(np.argmax(is_perf))
        # best-IS 策略在 OOS 的秩（1=最差 … N=最好），相对秩 ω=rank/(N+1)
        order = np.argsort(oos_perf)  # 升序
        rank = int(np.where(order == n_star)[0][0]) + 1
        omega = rank / (n_strategies + 1)
        omega = min(max(omega, 1e-6), 1 - 1e-6)
        logits.append(math.log(omega / (1 - omega)))
        omegas.append(omega)
        n_comb += 1

    omegas_arr = np.asarray(omegas)
    pbo = float(np.mean(omegas_arr <= 0.5)) if n_comb else 1.0
    logit_pbo = float(np.mean(logits)) if logits else 0.0
    return CSCVResult(
        pbo=pbo,
        logit_pbo=logit_pbo,
        stochastic_dominance=omegas_arr,
        n_combinations=n_comb,
        verdict=CSCVResult.classify(pbo),
    )


def probabilistic_sharpe_ratio(
    sharpe: float,
    n: int,
    benchmark_sharpe: float = 0.0,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> float:
    """PSR：观察 Sharpe 超过基准 Sharpe 的概率（考虑偏度/峰度），返回 [0,1]。

    PSR = Φ( (SR - SR_b)·√(n-1) / √(1 - γ3·SR + (γ4-1)/4·SR²) )
    其中 γ3=skew，γ4=kurt（正态=3）。sharpe 与 skew/kurt/n 须同频率。
    """
    if n <= 1:
        return 0.5
    denom = 1.0 - skew * sharpe + ((kurt - 1.0) / 4.0) * (sharpe ** 2)
    denom = max(denom, 1e-12)
    z = (sharpe - benchmark_sharpe) * math.sqrt(n - 1) / math.sqrt(denom)
    return _phi(z)


def expected_max_sharpe(n_trials: int, sharpe_variance: float) -> float:
    """N 次独立试验下、零真实技能时期望的最大 Sharpe（DSR 的通胀基准 SR0）。

    SR0 = √V · [ (1-γ)·Φ⁻¹(1 - 1/N) + γ·Φ⁻¹(1 - 1/(N·e)) ]，γ=Euler-Mascheroni。
    """
    n_trials = max(int(n_trials), 1)
    if n_trials == 1 or sharpe_variance <= 0:
        return 0.0
    z1 = _phi_inv(1.0 - 1.0 / n_trials)
    z2 = _phi_inv(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(sharpe_variance) * ((1 - _EULER_GAMMA) * z1 + _EULER_GAMMA * z2)


def deflated_sharpe_ratio(
    observed_sharpe: float,
    n_trials: int,
    returns: np.ndarray,
    skew: Optional[float] = None,
    kurt: Optional[float] = None,
    sharpe_variance: Optional[float] = None,
    risk_free: float = 0.0,
) -> Tuple[float, float]:
    """Deflated Sharpe Ratio。

    对观察到的 Sharpe 做多重检验（n_trials）通胀校正：把 PSR 的基准从 0 抬到
    "N 次试验下零技能期望的最大 Sharpe" SR0。

    Args:
        observed_sharpe: 观察 Sharpe（与 returns 同频率的非年化 SR；若与 returns 计算不符以 returns 为准）。
        n_trials:        搜索/测试过的策略/参数组合数（多重检验校正强度）。
        returns:         策略收益序列（用于估计 skew/kurt/n 及 Sharpe 估计方差）。
        skew/kurt:       缺省则从 returns 估计（kurt 为完整峰度，正态=3）。
        sharpe_variance: 试验间 Sharpe 的方差 V；缺省用 Sharpe 估计量方差近似。

    Returns:
        (dsr, p_value)：dsr∈[0,1] 为"真实技能"的概率；p_value=1-dsr。
    """
    returns = np.asarray(returns, dtype=float)
    returns = returns[np.isfinite(returns)]
    n = returns.size
    if n > 1:
        sr = _sharpe(returns - risk_free)
        if skew is None:
            skew = float(_safe_skew(returns))
        if kurt is None:
            kurt = float(_safe_kurt(returns))
    else:
        sr = observed_sharpe
        skew = skew if skew is not None else 0.0
        kurt = kurt if kurt is not None else 3.0
        n = max(n, 2)

    if sharpe_variance is None:
        # 用 Sharpe 估计量方差作为试验间方差的保守近似
        sharpe_variance = (1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr ** 2) / max(n - 1, 1)
        sharpe_variance = max(sharpe_variance, 1e-12)

    sr0 = expected_max_sharpe(n_trials, sharpe_variance)
    dsr = probabilistic_sharpe_ratio(sr, n=n, benchmark_sharpe=sr0, skew=skew, kurt=kurt)
    return float(dsr), float(1.0 - dsr)


def min_backtest_length(
    sharpe: float,
    skew: float = 0.0,
    kurt: float = 3.0,
    conf_level: float = 0.95,
    periods_per_year: float = 252.0,
) -> float:
    """使 Sharpe 估计可信（PSR(0)>conf）所需的最小回测长度（年）。

    MinTRL(obs) = 1 + (1 - γ3·SR + (γ4-1)/4·SR²) · (Z_conf / SR)²
    返回 MinTRL / periods_per_year（年）。sharpe 须为 per-period（与 periods_per_year 匹配）。
    """
    if abs(sharpe) < 1e-9:
        return float("inf")
    z = _phi_inv(conf_level)
    var_factor = 1.0 - skew * sharpe + ((kurt - 1.0) / 4.0) * (sharpe ** 2)
    min_obs = 1.0 + max(var_factor, 1e-12) * (z / sharpe) ** 2
    return float(min_obs / periods_per_year)


def _safe_skew(x: np.ndarray) -> float:
    n = x.size
    if n < 3:
        return 0.0
    m = np.mean(x)
    sd = np.std(x, ddof=0)
    if sd < 1e-12:
        return 0.0
    return float(np.mean(((x - m) / sd) ** 3))


def _safe_kurt(x: np.ndarray) -> float:
    n = x.size
    if n < 4:
        return 3.0
    m = np.mean(x)
    sd = np.std(x, ddof=0)
    if sd < 1e-12:
        return 3.0
    return float(np.mean(((x - m) / sd) ** 4))  # 完整峰度（正态=3）
