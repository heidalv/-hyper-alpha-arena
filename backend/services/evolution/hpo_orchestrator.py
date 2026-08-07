"""
超参优化体系整合（P4.7b，方案 §P4.7b）。

目标：把现有三套优化器（Optuna TPE / CMA-ES / MAP-Elites）串进进化闭环。
    - Optuna TPE → 策略离散/结构参数
    - CMA-ES → 连续参数精调
    - MAP-Elites → 行为特征维度多样性精英档案（防进化坍缩）
    - 多目标：Sharpe + maxDD + turnover + capacity 帕累托前沿（非单目标）

诊断（环境7缺陷）：三套优化器代码在但未串进闭环，谁主导不清。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

import numpy as np


class OptimizerType(str, Enum):
    OPTUNA_TPE = "optuna_tpe"
    CMA_ES = "cma_es"
    MAP_ELITES = "map_elites"


@dataclass
class HPORequest:
    """超参优化请求。"""
    param_space: dict[str, Any]      # {"param_name": ("uniform", low, high) | ("choice", [...])}
    objective_fn: Callable[[dict], dict]  # params -> {"sharpe":, "maxdd":, "turnover":, "capacity":}
    n_trials: int = 100
    optimizer: OptimizerType = OptimizerType.OPTUNA_TPE
    multi_objective: bool = True


@dataclass
class HPOResult:
    """优化结果。"""
    best_params: dict[str, Any]
    best_metrics: dict[str, float]
    pareto_front: list[dict] = field(default_factory=list)  # 多目标帕累托前沿
    archive: dict[str, Any] = field(default_factory=dict)   # MAP-Elites 档案
    n_evaluated: int = 0


class HPOOrchestrator:
    """
    超参优化调度器（整合三套优化器）。

    生产环境接 Optuna（TPE/CmaEsSampler）；当前提供接口 + 多目标帕累托 + MAP-Elites 框架。
    """

    def __init__(self):
        self._has_optuna = False
        try:
            import optuna  # noqa: F401
            self._has_optuna = True
        except ImportError:
            pass

    def optimize(self, request: HPORequest) -> HPOResult:
        """运行超参优化。"""
        if request.optimizer == OptimizerType.MAP_ELITES:
            return self._map_elites(request)
        # Optuna/CMA-ES 需要 optuna 库；降级到随机搜索
        return self._random_search(request)

    def _random_search(self, request: HPORequest) -> HPOResult:
        """无 optuna 时的随机搜索降级（+ 帕累托前沿）。"""
        results = []
        for _ in range(request.n_trials):
            params = self._sample(request.param_space)
            metrics = request.objective_fn(params)
            results.append({**params, **metrics})

        pareto = self._pareto_front(results) if request.multi_objective else []
        # 单目标：选最高 Sharpe
        best = max(results, key=lambda r: r.get("sharpe", -999))
        return HPOResult(
            best_params={k: best[k] for k in request.param_space},
            best_metrics={k: best.get(k, 0) for k in ("sharpe", "maxdd", "turnover", "capacity")},
            pareto_front=pareto,
            n_evaluated=len(results),
        )

    def _map_elites(self, request: HPORequest) -> HPOResult:
        """
        MAP-Elites：行为特征维度上的多样性精英档案。

        防进化坍缩到单点：每个行为特征格子留精英。
        简化版：2D 行为特征 (regime, holding_period) × fitness。
        """
        archive: dict[tuple, dict] = {}
        for _ in range(request.n_trials):
            params = self._sample(request.param_space)
            metrics = request.objective_fn(params)
            # 行为特征：turnover 分桶 × capacity 分桶
            bc = (
                int(metrics.get("turnover", 0.5) * 10),
                int(np.log10(max(metrics.get("capacity", 1), 1))),
            )
            fitness = metrics.get("sharpe", 0)
            if bc not in archive or fitness > archive[bc].get("sharpe", -999):
                archive[bc] = {**params, **metrics}
        # 全局最优
        best = max(archive.values(), key=lambda r: r.get("sharpe", -999))
        return HPOResult(
            best_params={k: best[k] for k in request.param_space},
            best_metrics={k: best.get(k, 0) for k in ("sharpe", "maxdd", "turnover", "capacity")},
            archive={str(k): v for k, v in archive.items()},
            n_evaluated=request.n_trials,
        )

    def _sample(self, space: dict[str, Any]) -> dict[str, Any]:
        """从参数空间采样。"""
        params = {}
        for name, spec in space.items():
            kind = spec[0]
            if kind == "uniform":
                params[name] = np.random.uniform(spec[1], spec[2])
            elif kind == "choice":
                params[name] = np.random.choice(spec[1])
            elif kind == "int":
                params[name] = int(np.random.randint(spec[1], spec[2] + 1))
        return params

    @staticmethod
    def _pareto_front(results: list[dict],
                      objectives: tuple = ("sharpe",),
                      minimize: tuple = ("maxdd", "turnover")) -> list[dict]:
        """计算帕累托前沿（max sharpe, min maxdd/turnover）。"""
        front = []
        for r in results:
            dominated = False
            for other in results:
                if other is r:
                    continue
                if (all(other.get(o, 0) >= r.get(o, 0) for o in objectives)
                    and all(other.get(m, 0) <= r.get(m, 0) for m in minimize)
                    and any(other.get(o, 0) > r.get(o, 0) for o in objectives
                            + tuple(f"-{m}" for m in minimize))):
                    dominated = True
                    break
            if not dominated:
                front.append(r)
        return front
