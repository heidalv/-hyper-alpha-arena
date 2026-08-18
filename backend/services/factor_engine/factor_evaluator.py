"""
因子质量评估框架 — IC / ICIR / 衰减分析 / 正交性检验

战略规划要求的三个评估维度:
1. 预测能力: IC均值、IC标准差、ICIR、IC衰减半衰期
2. 交易特性: 换手率预估、容量限制
3. 风险特征: 因子间相关性(正交性)、极端情景表现

使用方式:
    evaluator = FactorEvaluator()
    report = evaluator.evaluate_factor("rsi_14", data_dict)
    batch = evaluator.evaluate_all(data_dict, top_n=20)
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class FactorEvalReport:
    """单因子评估报告"""
    factor_id: str
    ic_mean: float = 0.0           # IC 均值 (rank correlation with forward returns)
    ic_std: float = 0.0            # IC 标准差
    icir: float = 0.0              # IC Information Ratio = ic_mean / ic_std
    ic_positive_pct: float = 0.0   # IC > 0 的比例
    ic_decay_halflife: int = 0     # IC 衰减半衰期 (bars)
    turnover: float = 0.0          # 因子换手率 (rank autocorrelation proxy)
    max_drawdown_ic: float = 0.0   # IC 最大回撤
    monotonicity: float = 0.0      # 分位收益单调性 (0-1)
    tail_risk: float = 0.0         # 极端因子值时的收益波动
    data_points: int = 0
    grade: str = "F"               # A/B/C/D/F


@dataclass
class OrthogonalityReport:
    """因子正交性检验报告"""
    factor_pairs: List[Tuple[str, str, float]] = field(default_factory=list)
    avg_correlation: float = 0.0
    max_correlation: float = 0.0
    redundant_pairs: List[Tuple[str, str, float]] = field(default_factory=list)


class FactorEvaluator:
    """因子质量评估引擎"""

    # IC 评分阈值
    IC_GRADE_A = 0.05   # IC > 5%
    IC_GRADE_B = 0.03   # IC > 3%
    IC_GRADE_C = 0.015  # IC > 1.5%
    IC_GRADE_D = 0.005  # IC > 0.5%
    REDUNDANCY_THRESHOLD = 0.7  # 兼容旧引用；[P0-B] 统一读 _redundancy_threshold()

    def _redundancy_threshold(self) -> float:
        """准入冗余阈值（|corr| 超过视为冗余）——统一读 FACTOR_SCORER_REDUNDANCY_CORR。"""
        try:
            from backend.config import settings as _s
            return float(getattr(_s, "FACTOR_SCORER_REDUNDANCY_CORR", 0.7) or 0.7)
        except Exception:
            return 0.7

    def __init__(self, forward_period: int = 5):
        """
        Args:
            forward_period: 前瞻收益的 bar 数 (用于计算 IC)
        """
        self.forward_period = forward_period

    # ── Public API ───────────────────────────────

    def evaluate_factor(
        self,
        factor_id: str,
        factor_values: pd.Series,
        close_prices: pd.Series,
        forward_period: Optional[int] = None,
    ) -> FactorEvalReport:
        """
        评估单个因子。

        Args:
            factor_id: 因子ID
            factor_values: 因子值 Series (index aligned with close)
            close_prices: 收盘价 Series
            forward_period: 前瞻期 (默认 self.forward_period)

        Returns:
            FactorEvalReport
        """
        fwd = forward_period or self.forward_period
        report = FactorEvalReport(factor_id=factor_id)

        # 对齐数据并去 NaN
        df = pd.DataFrame({"factor": factor_values, "close": close_prices}).dropna()
        if len(df) < fwd + 20:
            report.data_points = len(df)
            return report

        # 前瞻收益
        df["fwd_return"] = df["close"].pct_change(fwd).shift(-fwd)
        df = df.dropna()
        report.data_points = len(df)

        if len(df) < 30:
            return report

        # ── 1. Rolling IC (Spearman rank correlation) ──
        window = max(20, len(df) // 10)
        ic_series = self._rolling_ic(df["factor"], df["fwd_return"], window)
        valid_ic = ic_series.dropna()

        if len(valid_ic) < 5:
            return report

        report.ic_mean = float(valid_ic.mean())
        report.ic_std = float(valid_ic.std())
        report.icir = report.ic_mean / (report.ic_std + 1e-10)
        report.ic_positive_pct = float((valid_ic > 0).mean())

        # IC 最大回撤
        cum_ic = valid_ic.cumsum()
        roll_max = cum_ic.cummax()
        dd = cum_ic - roll_max
        report.max_drawdown_ic = float(dd.min())

        # ── 2. IC 衰减半衰期 ──
        report.ic_decay_halflife = self._compute_ic_decay(
            df["factor"], df["close"], max_lag=min(20, len(df) // 5)
        )

        # ── 3. 换手率 (rank autocorrelation) ──
        report.turnover = self._compute_turnover(df["factor"])

        # ── 4. 分位收益单调性 ──
        report.monotonicity = self._compute_monotonicity(df["factor"], df["fwd_return"])

        # ── 5. 尾部风险 ──
        report.tail_risk = self._compute_tail_risk(df["factor"], df["fwd_return"])

        # ── 6. 评级 ──
        abs_ic = abs(report.ic_mean)
        if abs_ic >= self.IC_GRADE_A and report.icir > 0.5:
            report.grade = "A"
        elif abs_ic >= self.IC_GRADE_B and report.icir > 0.3:
            report.grade = "B"
        elif abs_ic >= self.IC_GRADE_C:
            report.grade = "C"
        elif abs_ic >= self.IC_GRADE_D:
            report.grade = "D"
        else:
            report.grade = "F"

        return report

    def evaluate_batch(
        self,
        factor_dict: Dict[str, pd.Series],
        close_prices: pd.Series,
        top_n: int = 20,
    ) -> List[FactorEvalReport]:
        """
        批量评估多个因子，按 |IC| 排序。

        Args:
            factor_dict: {factor_id: factor_values_series}
            close_prices: 收盘价 Series
            top_n: 返回前 N 个

        Returns:
            List[FactorEvalReport] 按 |IC| 降序
        """
        reports: List[FactorEvalReport] = []
        for fid, vals in factor_dict.items():
            try:
                r = self.evaluate_factor(fid, vals, close_prices)
                reports.append(r)
            except Exception as e:
                logger.debug(f"[FactorEvaluator] {fid} 评估失败: {e}")

        reports.sort(key=lambda r: abs(r.ic_mean), reverse=True)
        return reports[:top_n]

    def check_orthogonality(
        self,
        factor_dict: Dict[str, pd.Series],
    ) -> OrthogonalityReport:
        """
        因子正交性检验 — 检测高相关因子对。

        Args:
            factor_dict: {factor_id: values}

        Returns:
            OrthogonalityReport
        """
        report = OrthogonalityReport()
        ids = list(factor_dict.keys())
        if len(ids) < 2:
            return report

        # 构建因子矩阵
        df = pd.DataFrame(factor_dict).dropna()
        if len(df) < 20:
            return report

        # Spearman 相关矩阵
        corr = df.rank().corr()

        pairs: List[Tuple[str, str, float]] = []
        redundant: List[Tuple[str, str, float]] = []
        total_corr = 0.0
        count = 0

        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                c = float(corr.iloc[i, j])
                pairs.append((ids[i], ids[j], c))
                total_corr += abs(c)
                count += 1
                if abs(c) > self._redundancy_threshold():
                    redundant.append((ids[i], ids[j], c))

        report.factor_pairs = sorted(pairs, key=lambda x: abs(x[2]), reverse=True)[:20]
        report.avg_correlation = total_corr / max(count, 1)
        report.max_correlation = max(abs(c) for _, _, c in pairs) if pairs else 0.0
        report.redundant_pairs = redundant

        return report

    def generate_summary(self, reports: List[FactorEvalReport]) -> Dict[str, Any]:
        """生成因子评估汇总。"""
        if not reports:
            return {"total": 0}

        grade_counts = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
        for r in reports:
            grade_counts[r.grade] = grade_counts.get(r.grade, 0) + 1

        return {
            "total": len(reports),
            "grade_distribution": grade_counts,
            "avg_ic": np.mean([r.ic_mean for r in reports]),
            "avg_icir": np.mean([r.icir for r in reports]),
            "top_5": [
                {"factor_id": r.factor_id, "ic": r.ic_mean, "icir": r.icir, "grade": r.grade}
                for r in sorted(reports, key=lambda x: abs(x.ic_mean), reverse=True)[:5]
            ],
            "usable_count": sum(1 for r in reports if r.grade in ("A", "B", "C")),
        }

    # ── Internal computations ────────────────────

    def _rolling_ic(self, factor: pd.Series, returns: pd.Series, window: int) -> pd.Series:
        """Rolling Spearman rank IC."""
        ic_values = []
        for i in range(window, len(factor)):
            f_slice = factor.iloc[i - window:i]
            r_slice = returns.iloc[i - window:i]
            if f_slice.std() < 1e-10 or r_slice.std() < 1e-10:
                ic_values.append(0.0)
                continue
            ic = f_slice.rank().corr(r_slice.rank())
            ic_values.append(ic if np.isfinite(ic) else 0.0)
        return pd.Series(ic_values, index=factor.index[window:])

    def _compute_ic_decay(self, factor: pd.Series, close: pd.Series, max_lag: int) -> int:
        """IC 随前瞻期增长的衰减半衰期。"""
        ics = []
        for lag in range(1, max_lag + 1):
            fwd = close.pct_change(lag).shift(-lag)
            valid = pd.DataFrame({"f": factor, "r": fwd}).dropna()
            if len(valid) < 20:
                break
            ic = valid["f"].rank().corr(valid["r"].rank())
            ics.append(abs(ic) if np.isfinite(ic) else 0.0)

        if not ics or ics[0] < 1e-10:
            return 0

        # 半衰期: IC 降到初始值 50% 的 lag
        half_val = ics[0] * 0.5
        for i, ic_val in enumerate(ics):
            if ic_val < half_val:
                return i + 1
        return len(ics)

    def _compute_turnover(self, factor: pd.Series) -> float:
        """因子换手率 proxy: 1 - rank_autocorrelation."""
        rank = factor.rank()
        rank_lag = rank.shift(1)
        valid = pd.DataFrame({"r": rank, "rl": rank_lag}).dropna()
        if len(valid) < 10:
            return 0.0
        autocorr = valid["r"].corr(valid["rl"])
        return float(1.0 - autocorr) if np.isfinite(autocorr) else 0.0

    def _compute_monotonicity(self, factor: pd.Series, returns: pd.Series, n_bins: int = 5) -> float:
        """分位收益单调性: 因子从低到高分组，收益是否单调递增/递减。"""
        try:
            df = pd.DataFrame({"f": factor, "r": returns}).dropna()
            if len(df) < n_bins * 5:
                return 0.0

            df["q"] = pd.qcut(df["f"], n_bins, labels=False, duplicates="drop")
            group_means = df.groupby("q")["r"].mean().values

            if len(group_means) < 3:
                return 0.0

            # 计算单调性: 相邻组收益方向一致的比例
            diffs = np.diff(group_means)
            positive = (diffs > 0).sum()
            negative = (diffs < 0).sum()
            total = len(diffs)
            return float(max(positive, negative) / total) if total > 0 else 0.0

        except Exception:
            return 0.0

    def _compute_tail_risk(self, factor: pd.Series, returns: pd.Series) -> float:
        """极端因子值 (top/bottom 5%) 时的收益波动。"""
        try:
            df = pd.DataFrame({"f": factor, "r": returns}).dropna()
            if len(df) < 50:
                return 0.0

            low_thresh = df["f"].quantile(0.05)
            high_thresh = df["f"].quantile(0.95)

            tail = df[(df["f"] <= low_thresh) | (df["f"] >= high_thresh)]
            if len(tail) < 5:
                return 0.0

            return float(tail["r"].std())
        except Exception:
            return 0.0


# Global singleton
_evaluator: Optional[FactorEvaluator] = None


def get_factor_evaluator(forward_period: int = 5) -> FactorEvaluator:
    global _evaluator
    if _evaluator is None:
        _evaluator = FactorEvaluator(forward_period=forward_period)
    else:
        # [2026-08-14 P1-A1 修复] 单例首次创建后 forward_period 不再更新的 bug：
        # 进程内第一个被评分因子的前瞻期会固化并污染后续所有周期（1h/4h/1d）
        # 的 IC/ICIR/衰减/单调性评级。改为每次调用同步更新（标量写，无锁安全）。
        _evaluator.forward_period = int(forward_period)
    return _evaluator
