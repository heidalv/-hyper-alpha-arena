"""
Auto Optimizer - 自动参数优化器

提供策略参数自动优化功能：
1. 贝叶斯优化
2. 网格搜索
3. 遗传算法
4. 参数验证与边界检查

Author: Hyper-Alpha-Arena
"""

import numpy as np
from typing import Dict, List, Optional, Any, Tuple, Callable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
import logging
import json
import random
from collections import deque

logger = logging.getLogger(__name__)


class OptimizationMethod(str, Enum):
    """优化方法"""
    GRID_SEARCH = "grid_search"
    RANDOM_SEARCH = "random_search"
    BAYESIAN = "bayesian"
    GENETIC = "genetic"
    GRADIENT_FREE = "gradient_free"


class ParameterType(str, Enum):
    """参数类型"""
    CONTINUOUS = "continuous"
    DISCRETE = "discrete"
    CATEGORICAL = "categorical"
    BOOLEAN = "boolean"


@dataclass
class ParameterSpace:
    """参数空间定义"""
    name: str
    param_type: ParameterType
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    step: Optional[float] = None
    choices: Optional[List[Any]] = None
    default: Optional[Any] = None
    
    def sample(self) -> Any:
        """从参数空间随机采样"""
        if self.param_type == ParameterType.CONTINUOUS:
            return random.uniform(self.min_value or 0, self.max_value or 1)
        elif self.param_type == ParameterType.DISCRETE:
            step = self.step or 1
            options = np.arange(self.min_value or 0, (self.max_value or 10) + step, step)
            return float(random.choice(options))
        elif self.param_type == ParameterType.CATEGORICAL:
            return random.choice(self.choices or [self.default])
        elif self.param_type == ParameterType.BOOLEAN:
            return random.choice([True, False])
        return self.default
    
    def validate(self, value: Any) -> bool:
        """验证参数值是否在有效范围内"""
        if self.param_type == ParameterType.CONTINUOUS:
            return (self.min_value or float('-inf')) <= value <= (self.max_value or float('inf'))
        elif self.param_type == ParameterType.DISCRETE:
            return (self.min_value or float('-inf')) <= value <= (self.max_value or float('inf'))
        elif self.param_type == ParameterType.CATEGORICAL:
            return value in (self.choices or [])
        elif self.param_type == ParameterType.BOOLEAN:
            return isinstance(value, bool)
        return True


@dataclass
class OptimizationConfig:
    """优化配置"""
    method: OptimizationMethod = OptimizationMethod.BAYESIAN
    max_iterations: int = 100
    convergence_threshold: float = 0.001
    early_stopping_rounds: int = 10
    population_size: int = 50  # 用于遗传算法
    mutation_rate: float = 0.1
    crossover_rate: float = 0.7
    exploration_rate: float = 0.3  # 用于贝叶斯优化
    n_random_starts: int = 10


@dataclass
class OptimizationResult:
    """优化结果"""
    best_params: Dict[str, Any]
    best_score: float
    all_trials: List[Dict[str, Any]]
    convergence_history: List[float]
    optimization_time: float
    iterations_used: int
    method: OptimizationMethod
    timestamp: datetime = field(default_factory=datetime.now)


class ObjectiveFunction:
    """目标函数包装器"""
    
    def __init__(
        self,
        evaluate_fn: Callable[[Dict[str, Any]], float],
        parameter_spaces: List[ParameterSpace],
        maximize: bool = True
    ):
        self.evaluate_fn = evaluate_fn
        self.parameter_spaces = {p.name: p for p in parameter_spaces}
        self.maximize = maximize
        self.evaluation_history: List[Tuple[Dict[str, Any], float]] = []
    
    def evaluate(self, params: Dict[str, Any]) -> float:
        """评估参数组合"""
        # 验证参数
        for name, space in self.parameter_spaces.items():
            if name in params:
                if not space.validate(params[name]):
                    logger.warning(f"Invalid parameter value for {name}: {params[name]}")
                    params[name] = space.default
        
        try:
            score = self.evaluate_fn(params)
            self.evaluation_history.append((params.copy(), score))
            return score
        except Exception as e:
            logger.error(f"Evaluation failed: {e}")
            return float('-inf') if self.maximize else float('inf')
    
    def get_best(self) -> Tuple[Dict[str, Any], float]:
        """获取历史最佳"""
        if not self.evaluation_history:
            return {}, 0.0
        
        if self.maximize:
            return max(self.evaluation_history, key=lambda x: x[1])
        else:
            return min(self.evaluation_history, key=lambda x: x[1])


class GridSearchOptimizer:
    """网格搜索优化器"""
    
    def __init__(self, config: OptimizationConfig):
        self.config = config
    
    def optimize(self, objective: ObjectiveFunction) -> OptimizationResult:
        """执行网格搜索"""
        start_time = datetime.now()
        
        # 生成网格
        grid = self._generate_grid(objective.parameter_spaces)
        
        all_trials = []
        best_score = float('-inf') if objective.maximize else float('inf')
        best_params = {}
        convergence_history = []
        
        for i, params in enumerate(grid):
            if i >= self.config.max_iterations:
                break
            
            score = objective.evaluate(params)
            all_trials.append({"params": params, "score": score})
            
            if (objective.maximize and score > best_score) or \
               (not objective.maximize and score < best_score):
                best_score = score
                best_params = params.copy()
            
            convergence_history.append(best_score)
        
        return OptimizationResult(
            best_params=best_params,
            best_score=best_score,
            all_trials=all_trials,
            convergence_history=convergence_history,
            optimization_time=(datetime.now() - start_time).total_seconds(),
            iterations_used=len(all_trials),
            method=OptimizationMethod.GRID_SEARCH
        )
    
    def _generate_grid(self, parameter_spaces: Dict[str, ParameterSpace]) -> List[Dict[str, Any]]:
        """生成参数网格"""
        grid = [{}]
        
        for name, space in parameter_spaces.items():
            new_grid = []
            values = self._get_grid_values(space)
            
            for params in grid:
                for value in values:
                    new_params = params.copy()
                    new_params[name] = value
                    new_grid.append(new_params)
            
            grid = new_grid
        
        return grid
    
    def _get_grid_values(self, space: ParameterSpace, n_points: int = 5) -> List[Any]:
        """获取参数的网格值"""
        if space.param_type == ParameterType.CATEGORICAL:
            return space.choices or [space.default]
        elif space.param_type == ParameterType.BOOLEAN:
            return [True, False]
        elif space.param_type == ParameterType.DISCRETE:
            step = space.step or 1
            return list(np.arange(space.min_value or 0, (space.max_value or 10) + step, step))
        else:  # CONTINUOUS
            return list(np.linspace(space.min_value or 0, space.max_value or 1, n_points))


class RandomSearchOptimizer:
    """随机搜索优化器"""
    
    def __init__(self, config: OptimizationConfig):
        self.config = config
    
    def optimize(self, objective: ObjectiveFunction) -> OptimizationResult:
        """执行随机搜索"""
        start_time = datetime.now()
        
        all_trials = []
        best_score = float('-inf') if objective.maximize else float('inf')
        best_params = {}
        convergence_history = []
        no_improvement_count = 0
        
        for i in range(self.config.max_iterations):
            # 随机采样
            params = {
                name: space.sample()
                for name, space in objective.parameter_spaces.items()
            }
            
            score = objective.evaluate(params)
            all_trials.append({"params": params, "score": score})
            
            if (objective.maximize and score > best_score) or \
               (not objective.maximize and score < best_score):
                best_score = score
                best_params = params.copy()
                no_improvement_count = 0
            else:
                no_improvement_count += 1
            
            convergence_history.append(best_score)
            
            # 早停
            if no_improvement_count >= self.config.early_stopping_rounds:
                logger.info(f"Early stopping at iteration {i}")
                break
        
        return OptimizationResult(
            best_params=best_params,
            best_score=best_score,
            all_trials=all_trials,
            convergence_history=convergence_history,
            optimization_time=(datetime.now() - start_time).total_seconds(),
            iterations_used=len(all_trials),
            method=OptimizationMethod.RANDOM_SEARCH
        )


class BayesianOptimizer:
    """贝叶斯优化器（简化版）"""
    
    def __init__(self, config: OptimizationConfig):
        self.config = config
        self.observations: List[Tuple[Dict[str, Any], float]] = []
    
    def optimize(self, objective: ObjectiveFunction) -> OptimizationResult:
        """执行贝叶斯优化"""
        start_time = datetime.now()
        
        all_trials = []
        best_score = float('-inf') if objective.maximize else float('inf')
        best_params = {}
        convergence_history = []
        
        # 随机初始化
        for _ in range(self.config.n_random_starts):
            params = {
                name: space.sample()
                for name, space in objective.parameter_spaces.items()
            }
            score = objective.evaluate(params)
            self.observations.append((params, score))
            all_trials.append({"params": params, "score": score})
            
            if (objective.maximize and score > best_score) or \
               (not objective.maximize and score < best_score):
                best_score = score
                best_params = params.copy()
            
            convergence_history.append(best_score)
        
        # 贝叶斯优化迭代
        no_improvement_count = 0
        for i in range(self.config.max_iterations - self.config.n_random_starts):
            # 使用采集函数选择下一个点
            params = self._acquisition_sample(objective)
            
            score = objective.evaluate(params)
            self.observations.append((params, score))
            all_trials.append({"params": params, "score": score})
            
            if (objective.maximize and score > best_score) or \
               (not objective.maximize and score < best_score):
                best_score = score
                best_params = params.copy()
                no_improvement_count = 0
            else:
                no_improvement_count += 1
            
            convergence_history.append(best_score)
            
            if no_improvement_count >= self.config.early_stopping_rounds:
                logger.info(f"Early stopping at iteration {i + self.config.n_random_starts}")
                break
        
        return OptimizationResult(
            best_params=best_params,
            best_score=best_score,
            all_trials=all_trials,
            convergence_history=convergence_history,
            optimization_time=(datetime.now() - start_time).total_seconds(),
            iterations_used=len(all_trials),
            method=OptimizationMethod.BAYESIAN
        )
    
    def _acquisition_sample(self, objective: ObjectiveFunction) -> Dict[str, Any]:
        """使用采集函数采样"""
        # 简化的UCB采集函数
        if random.random() < self.config.exploration_rate:
            # 探索：随机采样
            return {
                name: space.sample()
                for name, space in objective.parameter_spaces.items()
            }
        else:
            # 利用：在最佳点附近采样
            best_params, _ = objective.get_best()
            perturbed = {}
            
            for name, space in objective.parameter_spaces.items():
                if space.param_type == ParameterType.CONTINUOUS:
                    # 在最佳值附近添加高斯噪声
                    base_val = best_params.get(name, (space.min_value + space.max_value) / 2)
                    range_val = (space.max_value - space.min_value) * 0.1
                    new_val = base_val + random.gauss(0, range_val)
                    new_val = max(space.min_value, min(space.max_value, new_val))
                    perturbed[name] = new_val
                else:
                    # 其他类型保持最佳值或随机
                    perturbed[name] = best_params.get(name) if random.random() > 0.2 else space.sample()
            
            return perturbed


class GeneticOptimizer:
    """遗传算法优化器"""
    
    def __init__(self, config: OptimizationConfig):
        self.config = config
    
    def optimize(self, objective: ObjectiveFunction) -> OptimizationResult:
        """执行遗传算法优化"""
        start_time = datetime.now()
        
        # 初始化种群
        population = [
            {name: space.sample() for name, space in objective.parameter_spaces.items()}
            for _ in range(self.config.population_size)
        ]
        
        all_trials = []
        best_score = float('-inf') if objective.maximize else float('inf')
        best_params = {}
        convergence_history = []
        
        for generation in range(self.config.max_iterations // self.config.population_size):
            # 评估适应度
            fitness_scores = []
            for individual in population:
                score = objective.evaluate(individual)
                fitness_scores.append(score)
                all_trials.append({"params": individual, "score": score})
                
                if (objective.maximize and score > best_score) or \
                   (not objective.maximize and score < best_score):
                    best_score = score
                    best_params = individual.copy()
            
            convergence_history.append(best_score)
            
            # 选择
            selected = self._selection(population, fitness_scores, objective.maximize)
            
            # 交叉
            offspring = self._crossover(selected, objective.parameter_spaces)
            
            # 变异
            mutated = self._mutation(offspring, objective.parameter_spaces)
            
            # 更新种群
            population = mutated
        
        return OptimizationResult(
            best_params=best_params,
            best_score=best_score,
            all_trials=all_trials,
            convergence_history=convergence_history,
            optimization_time=(datetime.now() - start_time).total_seconds(),
            iterations_used=len(all_trials),
            method=OptimizationMethod.GENETIC
        )
    
    def _selection(
        self,
        population: List[Dict[str, Any]],
        fitness_scores: List[float],
        maximize: bool
    ) -> List[Dict[str, Any]]:
        """锦标赛选择"""
        selected = []
        tournament_size = 3
        
        for _ in range(len(population)):
            tournament = random.sample(list(zip(population, fitness_scores)), tournament_size)
            if maximize:
                winner = max(tournament, key=lambda x: x[1])[0]
            else:
                winner = min(tournament, key=lambda x: x[1])[0]
            selected.append(winner.copy())
        
        return selected
    
    def _crossover(
        self,
        population: List[Dict[str, Any]],
        parameter_spaces: Dict[str, ParameterSpace]
    ) -> List[Dict[str, Any]]:
        """均匀交叉"""
        offspring = []
        
        for i in range(0, len(population), 2):
            parent1 = population[i]
            parent2 = population[min(i + 1, len(population) - 1)]
            
            if random.random() < self.config.crossover_rate:
                child1, child2 = {}, {}
                for name in parameter_spaces:
                    if random.random() < 0.5:
                        child1[name] = parent1[name]
                        child2[name] = parent2[name]
                    else:
                        child1[name] = parent2[name]
                        child2[name] = parent1[name]
                offspring.extend([child1, child2])
            else:
                offspring.extend([parent1.copy(), parent2.copy()])
        
        return offspring[:len(population)]
    
    def _mutation(
        self,
        population: List[Dict[str, Any]],
        parameter_spaces: Dict[str, ParameterSpace]
    ) -> List[Dict[str, Any]]:
        """变异"""
        mutated = []
        
        for individual in population:
            new_individual = individual.copy()
            for name, space in parameter_spaces.items():
                if random.random() < self.config.mutation_rate:
                    new_individual[name] = space.sample()
            mutated.append(new_individual)
        
        return mutated


class AutoOptimizer:
    """自动参数优化器主类"""
    
    def __init__(self, config: Optional[OptimizationConfig] = None):
        self.config = config or OptimizationConfig()
        self.optimization_history: List[OptimizationResult] = []
        
        # 预定义的策略参数空间
        self.strategy_params = self._define_strategy_params()
    
    def _define_strategy_params(self) -> List[ParameterSpace]:
        """定义策略参数空间"""
        return [
            # 止盈止损参数
            ParameterSpace(
                name="sl_atr_multiple",
                param_type=ParameterType.CONTINUOUS,
                min_value=1.0,
                max_value=5.0,
                default=2.5
            ),
            ParameterSpace(
                name="tp_ratio",
                param_type=ParameterType.CONTINUOUS,
                min_value=1.0,
                max_value=5.0,
                default=2.0
            ),
            ParameterSpace(
                name="trailing_start_atr",
                param_type=ParameterType.CONTINUOUS,
                min_value=0.5,
                max_value=3.0,
                default=1.0
            ),
            # 仓位管理参数
            ParameterSpace(
                name="max_position_pct",
                param_type=ParameterType.CONTINUOUS,
                min_value=0.02,
                max_value=0.15,
                default=0.05
            ),
            ParameterSpace(
                name="kelly_fraction",
                param_type=ParameterType.CONTINUOUS,
                min_value=0.1,
                max_value=0.5,
                default=0.25
            ),
            # 信号阈值
            ParameterSpace(
                name="signal_threshold",
                param_type=ParameterType.CONTINUOUS,
                min_value=0.3,
                max_value=0.9,
                default=0.6
            ),
            ParameterSpace(
                name="min_confidence",
                param_type=ParameterType.CONTINUOUS,
                min_value=0.4,
                max_value=0.9,
                default=0.65
            ),
            # 因子权重
            ParameterSpace(
                name="momentum_weight",
                param_type=ParameterType.CONTINUOUS,
                min_value=0.0,
                max_value=0.5,
                default=0.25
            ),
            ParameterSpace(
                name="trend_weight",
                param_type=ParameterType.CONTINUOUS,
                min_value=0.0,
                max_value=0.5,
                default=0.25
            ),
            ParameterSpace(
                name="volatility_weight",
                param_type=ParameterType.CONTINUOUS,
                min_value=0.0,
                max_value=0.5,
                default=0.20
            ),
        ]
    
    def optimize(
        self,
        evaluate_fn: Callable[[Dict[str, Any]], float],
        parameter_spaces: Optional[List[ParameterSpace]] = None,
        method: Optional[OptimizationMethod] = None,
        maximize: bool = True
    ) -> OptimizationResult:
        """执行优化"""
        spaces = parameter_spaces or self.strategy_params
        method = method or self.config.method
        
        objective = ObjectiveFunction(evaluate_fn, spaces, maximize)
        
        # 选择优化器
        if method == OptimizationMethod.GRID_SEARCH:
            optimizer = GridSearchOptimizer(self.config)
        elif method == OptimizationMethod.RANDOM_SEARCH:
            optimizer = RandomSearchOptimizer(self.config)
        elif method == OptimizationMethod.BAYESIAN:
            optimizer = BayesianOptimizer(self.config)
        elif method == OptimizationMethod.GENETIC:
            optimizer = GeneticOptimizer(self.config)
        else:
            optimizer = BayesianOptimizer(self.config)
        
        logger.info(f"Starting optimization with method: {method}")
        result = optimizer.optimize(objective)
        
        self.optimization_history.append(result)
        logger.info(f"Optimization completed. Best score: {result.best_score}")
        
        return result
    
    def get_recommended_params(
        self,
        market_regime: str = "normal"
    ) -> Dict[str, Any]:
        """获取推荐参数"""
        # 基于市场状态的推荐参数
        regime_adjustments = {
            "breakout": {
                "sl_atr_multiple": 3.0,
                "tp_ratio": 2.5,
                "max_position_pct": 0.08,
                "momentum_weight": 0.35
            },
            "trending": {
                "sl_atr_multiple": 2.5,
                "tp_ratio": 3.0,
                "trailing_start_atr": 1.5,
                "trend_weight": 0.35
            },
            "ranging": {
                "sl_atr_multiple": 2.0,
                "tp_ratio": 1.5,
                "max_position_pct": 0.04,
                "volatility_weight": 0.30
            },
            "volatile": {
                "sl_atr_multiple": 3.5,
                "max_position_pct": 0.03,
                "volatility_weight": 0.35,
                "signal_threshold": 0.75
            }
        }
        
        # 基础参数
        base_params = {space.name: space.default for space in self.strategy_params}
        
        # 应用调整
        if market_regime in regime_adjustments:
            base_params.update(regime_adjustments[market_regime])
        
        return base_params
    
    def save_result(self, result: OptimizationResult, filepath: str) -> None:
        """保存优化结果"""
        data = {
            "best_params": result.best_params,
            "best_score": result.best_score,
            "method": result.method.value,
            "iterations_used": result.iterations_used,
            "optimization_time": result.optimization_time,
            "timestamp": result.timestamp.isoformat(),
            "convergence_history": result.convergence_history
        }
        
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
        
        logger.info(f"Optimization result saved to {filepath}")


# 全局实例
_auto_optimizer: Optional[AutoOptimizer] = None


def get_auto_optimizer() -> AutoOptimizer:
    """获取自动优化器实例"""
    global _auto_optimizer
    if _auto_optimizer is None:
        _auto_optimizer = AutoOptimizer()
    return _auto_optimizer


def optimize_parameters(
    evaluate_fn: Callable[[Dict[str, Any]], float],
    method: OptimizationMethod = OptimizationMethod.BAYESIAN,
    max_iterations: int = 100
) -> OptimizationResult:
    """优化参数"""
    optimizer = get_auto_optimizer()
    optimizer.config.max_iterations = max_iterations
    return optimizer.optimize(evaluate_fn, method=method)
