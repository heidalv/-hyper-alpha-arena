"""
持续学习防遗忘（整改#17）—— 对标 EWC(PNAS 2017) + EVCL(arXiv 2406.15972)。

问题：RL policy / 因子学习模型重训时会灾难性遗忘旧 regime 的知识。本模块提供两条
互补防线，均为模型无关的纯数值实现（作用于"参数字典"与"样本缓冲"）：

  1. EWC：在旧任务数据上估计每个参数的 Fisher 信息（重要性），重训时对重要参数施加
     二次惩罚 λ·Σ Fᵢ(θᵢ−θ*ᵢ)²，使其几乎不动，不重要参数自由适应新数据。
  2. Replay：新 batch 掺入按 regime 分层采样的旧代表性样本，配合 EWC 双重防遗忘。

零风险：
  - 默认关（EWC_ENABLED=false）→ 惩罚恒为 0、mix_batch 返回原 batch。
  - 纯 numpy，无重后端依赖；对 LightGBM 用 feature-importance 近似 Fisher，对线性/GRU
    用 empirical Fisher（梯度平方均值）。
  - 不改任何现有训练流程；由 rl_core / 因子学习层在开关开启时显式包一层。
"""
from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    return os.environ.get("EWC_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def ewc_lambda_default() -> float:
    try:
        return float(os.environ.get("EWC_LAMBDA", "400"))
    except ValueError:
        return 400.0


def replay_ratio_default() -> float:
    try:
        return float(os.environ.get("REPLAY_RATIO", "0.3"))
    except ValueError:
        return 0.3


@dataclass
class FisherInformation:
    """Fisher 信息矩阵（对角近似）+ 旧任务最优参数锚点。"""
    fisher_diag: Dict[str, np.ndarray]     # {param_name: importance_vector}
    theta_star: Dict[str, np.ndarray]      # {param_name: 旧任务最优参数}
    task_tag: str = ""                     # 该快照对应的任务/regime 标签

    def normalized(self) -> "FisherInformation":
        """把每个参数的 Fisher 归一到均值=1，避免不同尺度参数惩罚失衡。"""
        norm = {}
        for k, f in self.fisher_diag.items():
            m = float(np.mean(np.abs(f))) if f.size else 0.0
            norm[k] = f / m if m > 1e-12 else f
        return FisherInformation(norm, self.theta_star, self.task_tag)


def _as_array(v: Any) -> np.ndarray:
    return np.atleast_1d(np.asarray(v, dtype=float))


def compute_empirical_fisher(
    theta_star: Dict[str, Any],
    grad_fn: Callable[[Dict[str, np.ndarray], Any], Dict[str, np.ndarray]],
    data_samples: Sequence[Any],
    n_samples: int = 500,
    task_tag: str = "",
) -> FisherInformation:
    """Empirical Fisher：Fᵢ = E[(∂log p/∂θᵢ)²]，用旧任务样本上的梯度平方均值估计。

    grad_fn(params, sample) → {param_name: gradient_vector}。适用线性 Q / GRU（PyTorch
    可用 backward 得梯度）。返回对角 Fisher。
    """
    theta = {k: _as_array(v) for k, v in theta_star.items()}
    accum = {k: np.zeros_like(v) for k, v in theta.items()}
    samples = list(data_samples)[:n_samples]
    n = 0
    for s in samples:
        try:
            grads = grad_fn(theta, s)
        except Exception as e:  # noqa: BLE001
            logger.debug("[EWC#17] grad_fn 失败，跳过样本: %s", e)
            continue
        for k, g in grads.items():
            if k in accum:
                accum[k] += _as_array(g) ** 2
        n += 1
    if n > 0:
        for k in accum:
            accum[k] /= n
    return FisherInformation(accum, theta, task_tag)


def compute_fisher_from_importance(
    theta_star: Dict[str, Any],
    importances: Dict[str, Any],
    task_tag: str = "",
) -> FisherInformation:
    """LightGBM/树模型近似 Fisher：用 feature importance 作参数重要性代理。"""
    theta = {k: _as_array(v) for k, v in theta_star.items()}
    fisher = {k: _as_array(importances.get(k, np.zeros_like(v))) for k, v in theta.items()}
    return FisherInformation(fisher, theta, task_tag)


class EWCTrainer:
    """带 EWC 惩罚的训练器（多任务 Fisher 栈）。"""

    def __init__(self, ewc_lambda: Optional[float] = None, max_tasks: int = 5):
        self.ewc_lambda = ewc_lambda if ewc_lambda is not None else ewc_lambda_default()
        self.fisher_history: List[FisherInformation] = []
        self.max_tasks = max_tasks

    def consolidate(self, fisher_info: FisherInformation, normalize: bool = True) -> None:
        """完成一个旧任务后固化其 Fisher 快照，加入历史栈。"""
        self.fisher_history.append(fisher_info.normalized() if normalize else fisher_info)
        if len(self.fisher_history) > self.max_tasks:
            # 丢弃最旧任务（保留近端 regime 的重要性）
            self.fisher_history.pop(0)

    def penalty(self, current_params: Dict[str, Any]) -> float:
        """Σ_tasks Σ_i Fᵢ(θᵢ−θ*ᵢ)²。开关关时恒 0。"""
        if not is_enabled() or not self.fisher_history:
            return 0.0
        total = 0.0
        cur = {k: _as_array(v) for k, v in current_params.items()}
        for fi in self.fisher_history:
            for name, fisher in fi.fisher_diag.items():
                if name not in cur or name not in fi.theta_star:
                    continue
                diff = cur[name] - fi.theta_star[name]
                m = min(fisher.size, diff.size)
                total += float(np.sum(fisher[:m] * (diff[:m] ** 2)))
        return total

    def penalized_loss(self, new_loss: float, current_params: Dict[str, Any]) -> float:
        """total_loss = new_loss + λ · penalty。"""
        return float(new_loss) + self.ewc_lambda * self.penalty(current_params)


class RegimeReplayBuffer:
    """按 regime 标签分层存储旧代表性样本。"""

    def __init__(self, capacity_per_regime: int = 1000, seed: int = 42):
        self.capacity = capacity_per_regime
        self._buf: Dict[str, List[Any]] = {}
        self._rng = random.Random(seed)

    def add(self, sample: Any, regime: str = "default") -> None:
        bucket = self._buf.setdefault(regime, [])
        if len(bucket) < self.capacity:
            bucket.append(sample)
        else:  # 蓄水池替换，维持代表性
            j = self._rng.randint(0, len(bucket))
            if j < self.capacity:
                bucket[j] = sample

    def sample_stratified(self, n: int) -> List[Any]:
        """跨 regime 均衡分层采样 n 个旧样本。"""
        regimes = [r for r, b in self._buf.items() if b]
        if not regimes or n <= 0:
            return []
        per = max(1, n // len(regimes))
        out: List[Any] = []
        for r in regimes:
            bucket = self._buf[r]
            k = min(per, len(bucket))
            out.extend(self._rng.sample(bucket, k))
        self._rng.shuffle(out)
        return out[:n]

    def total(self) -> int:
        return sum(len(b) for b in self._buf.values())


class ReplayAugmentedTrainer:
    """旧 regime 代表性样本回放 — 对标 EVCL。"""

    def __init__(self, replay_ratio: Optional[float] = None):
        self.replay_ratio = replay_ratio if replay_ratio is not None else replay_ratio_default()

    def mix_batch(self, new_batch: List[Any], old_regime_buffer: RegimeReplayBuffer) -> List[Any]:
        """新 batch 掺 replay_ratio 比例的旧 regime 分层样本。开关关时返回原 batch。"""
        if not is_enabled() or not new_batch or old_regime_buffer.total() == 0:
            return list(new_batch)
        n_replay = int(len(new_batch) * self.replay_ratio)
        if n_replay <= 0:
            return list(new_batch)
        replay = old_regime_buffer.sample_stratified(n_replay)
        return list(new_batch) + replay
