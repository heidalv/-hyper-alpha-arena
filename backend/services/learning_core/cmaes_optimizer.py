"""
CMA-ES 连续参数精调（整改#20）—— 对标 Hansen CMA-ES / Optuna CMAESSampler。

替换 QAA AutoOptimizer 的 "-10% 均匀扰动" 朴素启发式：在连续参数空间上用协方差自适应
优化，学习参数间相关性（如 stop_loss 与 leverage 联动）。

零风险：
  - 默认 QAA_OPTIMIZER=naive_minus_10（保持旧行为），显式设 cmaes 才启用。
  - optuna 缺失时自动回退到"带精英保留的随机搜索"，保证离线可跑、可测。
  - 本模块不改动现有 QAA optimizer；由 qaa_evolution_bridge 在开关开启时选用。
"""
from __future__ import annotations

import logging
import os
import random
from typing import Callable, Dict, Tuple

logger = logging.getLogger(__name__)

ParamSpace = Dict[str, Tuple[float, float]]     # {name: (low, high)}
ObjectiveFn = Callable[[Dict[str, float]], float]   # genome -> fitness（越大越好）


def selected_optimizer() -> str:
    return os.environ.get("QAA_OPTIMIZER", "naive_minus_10").strip().lower()


def default_trials() -> int:
    try:
        return int(os.environ.get("CMAES_TRIALS", "100"))
    except ValueError:
        return 100


class CMAESOptimizer:
    """Optuna CMA-ES 包装；optuna 缺失时回退随机搜索。"""

    def __init__(self, seed: int = 42):
        self.seed = seed

    def _optuna_available(self) -> bool:
        import importlib.util
        return importlib.util.find_spec("optuna") is not None

    def optimize(self, objective_fn: ObjectiveFn, param_space: ParamSpace,
                 n_trials: int = 100) -> Tuple[Dict[str, float], float]:
        if not param_space:
            return ({}, float("-inf"))
        if self._optuna_available():
            try:
                return self._optuna_cmaes(objective_fn, param_space, n_trials)
            except Exception as e:  # noqa: BLE001
                logger.warning("[CMA-ES] optuna 优化失败，回退随机搜索: %s", e)
        return self._random_search(objective_fn, param_space, n_trials)

    def _optuna_cmaes(self, objective_fn, param_space, n_trials):
        import optuna

        optuna.logging.set_verbosity(optuna.logging.WARNING)

        def _obj(trial):
            params = {k: trial.suggest_float(k, lo, hi) for k, (lo, hi) in param_space.items()}
            return objective_fn(params)

        # optuna 各版本命名不一：新版 CmaEsSampler / 旧文档 CMAESSampler，兼容取用
        sampler_cls = (getattr(optuna.samplers, "CmaEsSampler", None)
                       or getattr(optuna.samplers, "CMAESSampler", None))
        if sampler_cls is None:
            raise RuntimeError("optuna 无 CMA-ES sampler")
        sampler = sampler_cls(seed=self.seed)
        study = optuna.create_study(direction="maximize", sampler=sampler)
        study.optimize(_obj, n_trials=n_trials)
        return study.best_params, study.best_value

    def _random_search(self, objective_fn, param_space, n_trials):
        rng = random.Random(self.seed)
        best_params: Dict[str, float] = {k: (lo + hi) / 2 for k, (lo, hi) in param_space.items()}
        best_val = objective_fn(dict(best_params))
        for _ in range(max(1, n_trials)):
            cand = {k: rng.uniform(lo, hi) for k, (lo, hi) in param_space.items()}
            val = objective_fn(cand)
            if val > best_val:
                best_val, best_params = val, cand
        return best_params, best_val


class RealAutoOptimizer:
    """替换 QAA AutoOptimizer 的 -10% 逻辑为 CMA-ES（协方差感知）。"""

    def __init__(self, n_trials: int = None, span: float = 0.3):
        self.n_trials = n_trials or default_trials()
        self.span = span          # 以当前值为中心，±span 相对范围构造搜索空间

    def tune_config(self, current_config: Dict[str, float], eval_fn: ObjectiveFn
                    ) -> Dict[str, float]:
        """在当前配置附近构造连续空间并用 CMA-ES 精调；只调数值参数。"""
        param_space: ParamSpace = {}
        for k, v in current_config.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            v = float(v)
            lo, hi = (v * (1 - self.span), v * (1 + self.span)) if v >= 0 else \
                     (v * (1 + self.span), v * (1 - self.span))
            if lo == hi:
                lo, hi = lo - 1e-6, hi + 1e-6
            param_space[k] = (lo, hi)
        if not param_space:
            return dict(current_config)
        best, score = CMAESOptimizer().optimize(eval_fn, param_space, n_trials=self.n_trials)
        # 合并回原 config（非数值项保持不变）
        out = dict(current_config)
        out.update(best)
        logger.info("[CMA-ES] 精调完成 fitness=%.4f params=%s", score, best)
        return out
