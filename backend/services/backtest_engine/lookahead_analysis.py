"""
前视（lookahead / 未来函数）检测工具 —— 对标 Freqtrade `lookahead-analysis`。

原理（蒙特卡洛块打乱）
----------------------
若策略/因子**真无前视**（严格因果），把 K 线的时间顺序打乱后，其赖以生存的
时序结构被破坏，回测收益应显著塌陷（趋近扣费后的随机水平）。

反之，若打乱后的收益仍与基线**难以区分**（大量打乱样本能复现甚至超过基线收益），
说明策略的"收益"并不依赖真实的时间因果，而很可能来自**偷看未来**（非因果的
pandas 操作、用了未来 bar 的标签、shift 方向写反等），即存在前视 bug。

判据
----
- baseline_return：原始时间顺序回测收益。
- shuffled_returns：n_trials 次块打乱后的收益分布。
- p_value = P(打乱收益 >= 基线收益)（+1 平滑）。p 越大越可疑。
- z_score = (baseline - mean_shuffled) / std_shuffled。z 越大越健康（基线远高于打乱均值）。
- suspected_lookahead：基线有显著收益，但打乱后依然能复现（p_value > 阈值）→ 判定可疑。

设计约束
--------
- **策略无关**：任意实现了 `generate_signals` / `on_bar` 的 Strategy 都能测。
- **零副作用**：每次 trial 用独立 BacktestEngine，可选 strategy_factory 重建有状态策略。
- **只读工具**：仅用于研究/CI 校验，不改动任何交易或回测主流程。

集成点
------
- 可作为 `walk_forward.py` 的可选校验步骤（新因子/策略上线前跑一次）。
- CI/CD 中对新因子/策略自动运行，suspected=True 时告警/阻断。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Union

import numpy as np
import pandas as pd

from .backtest_engine import BacktestEngine, BacktestConfig, BacktestMode, Strategy

StrategyOrFactory = Union[Strategy, Callable[[], Strategy]]


@dataclass
class LookaheadReport:
    """前视检测报告。"""
    baseline_return: float
    shuffled_returns: List[float]
    mean_shuffled: float
    std_shuffled: float
    p_value: float
    z_score: float
    suspected_lookahead: bool
    n_trials: int
    n_valid_trials: int
    block_size: int
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "baseline_return": self.baseline_return,
            "mean_shuffled": self.mean_shuffled,
            "std_shuffled": self.std_shuffled,
            "p_value": self.p_value,
            "z_score": self.z_score,
            "suspected_lookahead": self.suspected_lookahead,
            "n_trials": self.n_trials,
            "n_valid_trials": self.n_valid_trials,
            "block_size": self.block_size,
            "reason": self.reason,
        }

    def summary(self) -> str:
        flag = "[SUSPECTED] 疑似前视" if self.suspected_lookahead else "[OK] 未见前视"
        return (
            f"[LookaheadAnalyzer] {flag} | baseline={self.baseline_return:.4f} "
            f"shuffled_mean={self.mean_shuffled:.4f}±{self.std_shuffled:.4f} "
            f"p={self.p_value:.3f} z={self.z_score:.2f} "
            f"(valid {self.n_valid_trials}/{self.n_trials}, block={self.block_size}) | {self.reason}"
        )


class LookaheadAnalyzer:
    """蒙特卡洛式前视检测器。"""

    def __init__(
        self,
        config: Optional[BacktestConfig] = None,
        p_value_threshold: float = 0.10,
        min_baseline_abs: float = 0.005,
    ):
        """
        Args:
            config: 回测配置。默认用向量化模式（快，适合成百上千次蒙特卡洛）。
            p_value_threshold: p_value 超过此值判可疑（默认 0.10）。
            min_baseline_abs: 基线收益绝对值低于此阈值时视为"无显著收益"，
                              判定为 inconclusive（无边可查）。
        """
        self.config = config or BacktestConfig(mode=BacktestMode.VECTORIZED)
        self.p_value_threshold = p_value_threshold
        self.min_baseline_abs = min_baseline_abs

    def analyze(
        self,
        strategy: StrategyOrFactory,
        data: pd.DataFrame,
        n_trials: int = 100,
        block_size: int = 10,
        seed: Optional[int] = None,
    ) -> LookaheadReport:
        """
        1. 基线回测（原始顺序）。
        2. n_trials 次块打乱顺序回测。
        3. 比较：若打乱后收益与基线相近（高 p_value）→ 疑似前视。
        """
        if data is None or len(data) < max(2 * block_size, 20):
            raise ValueError("数据量过小，无法进行前视检测（至少需要 max(2*block_size,20) 根 K 线）")

        rng = np.random.default_rng(seed)

        baseline_return = self._run_one(strategy, data)

        shuffled: List[float] = []
        for _ in range(n_trials):
            shuffled_data = self._shuffle_blocks(data, block_size, rng)
            try:
                shuffled.append(self._run_one(strategy, shuffled_data))
            except Exception:
                # 单次 trial 失败（如打乱后指标 NaN）跳过，不影响整体统计
                continue

        arr = np.asarray(shuffled, dtype=float)
        arr = arr[np.isfinite(arr)]
        n_valid = int(arr.size)

        mean_s = float(np.mean(arr)) if n_valid else 0.0
        std_s = float(np.std(arr)) if n_valid else 0.0
        # p_value：打乱后收益 >= 基线的比例（+1 平滑，避免 0）
        ge = int(np.sum(arr >= baseline_return)) if n_valid else 0
        p_value = (ge + 1) / (n_valid + 1) if n_valid else 1.0
        z_score = (baseline_return - mean_s) / std_s if std_s > 1e-12 else 0.0

        # 判定
        if abs(baseline_return) < self.min_baseline_abs:
            suspected = False
            reason = (
                f"基线收益 {baseline_return:.4f} 低于显著阈值 {self.min_baseline_abs}，"
                f"无显著边可供判定（inconclusive）"
            )
        elif n_valid == 0:
            suspected = False
            reason = "所有打乱 trial 均失败，无法判定"
        elif p_value > self.p_value_threshold:
            suspected = True
            reason = (
                f"打乱后仍有 {p_value:.1%} 的样本复现/超过基线收益（> 阈值 "
                f"{self.p_value_threshold:.0%}），收益不依赖真实时间因果 → 疑似前视/未来函数"
            )
        else:
            suspected = False
            reason = (
                f"打乱后收益显著塌陷（p={p_value:.3f} ≤ 阈值，z={z_score:.2f}），"
                f"策略收益依赖真实时序结构，未见前视"
            )

        return LookaheadReport(
            baseline_return=baseline_return,
            shuffled_returns=arr.tolist(),
            mean_shuffled=mean_s,
            std_shuffled=std_s,
            p_value=p_value,
            z_score=z_score,
            suspected_lookahead=suspected,
            n_trials=n_trials,
            n_valid_trials=n_valid,
            block_size=block_size,
            reason=reason,
        )

    def _shuffle_blocks(self, data: pd.DataFrame, block_size: int, rng: np.random.Generator) -> pd.DataFrame:
        """块打乱：保留块内局部结构，仅打乱块之间的顺序。

        打乱后重新赋予原始的单调时间索引，避免回测引擎按时间排序时报错，
        同时保证 pct_change 等按行计算的逻辑在块边界处断裂——这正是检测点。
        """
        n = len(data)
        n_blocks = int(np.ceil(n / block_size))
        order = rng.permutation(n_blocks)
        parts = [data.iloc[b * block_size:(b + 1) * block_size] for b in order]
        shuffled = pd.concat(parts, axis=0)
        # 复用原始索引前 len(shuffled) 个，保持单调、长度一致
        shuffled = shuffled.iloc[: n].copy()
        shuffled.index = data.index[: len(shuffled)]
        return shuffled

    def _run_one(self, strategy: StrategyOrFactory, data: pd.DataFrame) -> float:
        """跑一次回测返回总收益率。有状态策略请传入 strategy_factory（可调用）。"""
        strat = strategy() if (callable(strategy) and not isinstance(strategy, Strategy)) else strategy
        engine = BacktestEngine(self.config)
        result = engine.run(strat, data)
        return float(result.total_return)


def run_lookahead_check(
    strategy: StrategyOrFactory,
    data: pd.DataFrame,
    n_trials: int = 100,
    block_size: int = 10,
    seed: Optional[int] = None,
    config: Optional[BacktestConfig] = None,
) -> LookaheadReport:
    """便捷入口：一次性跑前视检测并返回报告。"""
    return LookaheadAnalyzer(config=config).analyze(
        strategy, data, n_trials=n_trials, block_size=block_size, seed=seed
    )
