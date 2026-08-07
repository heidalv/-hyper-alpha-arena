"""
ATAS V2 参数优化器

支持多种优化算法：网格搜索、贝叶斯优化、遗传算法
"""
import logging
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Tuple, Optional, Callable
from dataclasses import dataclass, asdict
from enum import Enum
from datetime import datetime
import itertools

logger = logging.getLogger(__name__)


class OptimizationMethod(str, Enum):
    """优化方法"""
    GRID_SEARCH = "grid_search"
    BAYESIAN = "bayesian"
    GENETIC = "genetic"
    RANDOM_SEARCH = "random_search"


@dataclass
class ParameterSpace:
    """参数空间定义"""
    name: str
    param_type: str  # 'int', 'float', 'categorical'
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    values: Optional[List[Any]] = None  # 用于categorical
    step: Optional[float] = None  # 用于grid search
    
    def validate(self):
        """验证参数空间定义"""
        if self.param_type == 'categorical':
            if not self.values:
                raise ValueError(f"Categorical parameter {self.name} requires 'values'")
        else:
            if self.min_value is None or self.max_value is None:
                raise ValueError(f"Numeric parameter {self.name} requires 'min_value' and 'max_value'")
    
    def sample_random(self) -> Any:
        """随机采样"""
        if self.param_type == 'categorical':
            return np.random.choice(self.values)
        elif self.param_type == 'int':
            return np.random.randint(self.min_value, self.max_value + 1)
        else:  # float
            return np.random.uniform(self.min_value, self.max_value)
    
    def get_grid_values(self) -> List[Any]:
        """获取网格值"""
        if self.param_type == 'categorical':
            return self.values
        elif self.param_type == 'int':
            step = self.step or 1
            return list(range(int(self.min_value), int(self.max_value) + 1, int(step)))
        else:  # float
            step = self.step or (self.max_value - self.min_value) / 10
            values = []
            current = self.min_value
            while current <= self.max_value:
                values.append(current)
                current += step
            return values


@dataclass
class OptimizationResult:
    """优化结果"""
    method: str
    best_params: Dict[str, Any]
    best_score: float
    all_results: List[Dict[str, Any]]
    optimization_time: float
    total_evaluations: int
    convergence_history: List[float]
    
    # 统计信息
    mean_score: float
    std_score: float
    min_score: float
    max_score: float
    
    def to_dict(self) -> dict:
        return asdict(self)


class ParameterOptimizer:
    """
    参数优化器
    
    支持多种优化算法，用于策略参数调优
    """
    
    def __init__(
        self,
        objective_function: Callable[[Dict[str, Any]], float],
        parameter_space: List[ParameterSpace],
        maximize: bool = True
    ):
        """
        初始化优化器
        
        Args:
            objective_function: 目标函数，接收参数字典，返回评分
            parameter_space: 参数空间列表
            maximize: 是否最大化目标（True）或最小化（False）
        """
        self.objective_function = objective_function
        self.parameter_space = {p.name: p for p in parameter_space}
        self.maximize = maximize
        
        # 验证参数空间
        for param in parameter_space:
            param.validate()
        
        # 优化历史
        self.evaluation_history: List[Dict[str, Any]] = []
        
        logger.info(f"ParameterOptimizer initialized with {len(parameter_space)} parameters")
    
    def optimize(
        self,
        method: OptimizationMethod = OptimizationMethod.GRID_SEARCH,
        max_evaluations: int = 100,
        n_jobs: int = 1,
        **kwargs
    ) -> OptimizationResult:
        """
        执行参数优化
        
        Args:
            method: 优化方法
            max_evaluations: 最大评估次数
            n_jobs: 并行任务数
            **kwargs: 方法特定参数
            
        Returns:
            优化结果
        """
        start_time = datetime.now()
        self.evaluation_history = []
        
        logger.info(f"Starting optimization with method: {method}")
        
        if method == OptimizationMethod.GRID_SEARCH:
            result = self._grid_search(max_evaluations)
        elif method == OptimizationMethod.RANDOM_SEARCH:
            result = self._random_search(max_evaluations)
        elif method == OptimizationMethod.BAYESIAN:
            result = self._bayesian_optimization(max_evaluations, **kwargs)
        elif method == OptimizationMethod.GENETIC:
            result = self._genetic_algorithm(max_evaluations, **kwargs)
        else:
            raise ValueError(f"Unsupported optimization method: {method}")
        
        # 计算统计信息
        scores = [r['score'] for r in self.evaluation_history]
        
        optimization_time = (datetime.now() - start_time).total_seconds()
        
        return OptimizationResult(
            method=method.value,
            best_params=result['params'],
            best_score=result['score'],
            all_results=self.evaluation_history,
            optimization_time=optimization_time,
            total_evaluations=len(self.evaluation_history),
            convergence_history=[r['score'] for r in self.evaluation_history],
            mean_score=np.mean(scores),
            std_score=np.std(scores),
            min_score=np.min(scores),
            max_score=np.max(scores)
        )
    
    def _grid_search(self, max_evaluations: int) -> Dict[str, Any]:
        """
        网格搜索
        
        Args:
            max_evaluations: 最大评估次数
            
        Returns:
            最佳参数和评分
        """
        logger.info("Running grid search")
        
        # 生成所有参数组合
        param_names = list(self.parameter_space.keys())
        param_grids = [self.parameter_space[name].get_grid_values() for name in param_names]
        
        all_combinations = list(itertools.product(*param_grids))
        
        # 限制评估次数
        if len(all_combinations) > max_evaluations:
            logger.warning(f"Grid has {len(all_combinations)} combinations, sampling {max_evaluations}")
            indices = np.random.choice(len(all_combinations), max_evaluations, replace=False)
            all_combinations = [all_combinations[i] for i in indices]
        
        best_score = float('-inf') if self.maximize else float('inf')
        best_params = None
        
        for combination in all_combinations:
            params = dict(zip(param_names, combination))
            score = self._evaluate(params)
            
            if (self.maximize and score > best_score) or (not self.maximize and score < best_score):
                best_score = score
                best_params = params
        
        return {'params': best_params, 'score': best_score}
    
    def _random_search(self, max_evaluations: int) -> Dict[str, Any]:
        """
        随机搜索
        
        Args:
            max_evaluations: 最大评估次数
            
        Returns:
            最佳参数和评分
        """
        logger.info("Running random search")
        
        best_score = float('-inf') if self.maximize else float('inf')
        best_params = None
        
        for _ in range(max_evaluations):
            params = {
                name: param_space.sample_random()
                for name, param_space in self.parameter_space.items()
            }
            
            score = self._evaluate(params)
            
            if (self.maximize and score > best_score) or (not self.maximize and score < best_score):
                best_score = score
                best_params = params
        
        return {'params': best_params, 'score': best_score}
    
    def _bayesian_optimization(
        self,
        max_evaluations: int,
        n_initial_points: int = 10,
        acquisition_function: str = 'ei'  # 'ei', 'ucb', 'poi'
    ) -> Dict[str, Any]:
        """
        贝叶斯优化
        
        使用高斯过程进行参数优化
        
        Args:
            max_evaluations: 最大评估次数
            n_initial_points: 初始随机采样点数
            acquisition_function: 获取函数类型
            
        Returns:
            最佳参数和评分
        """
        logger.info("Running Bayesian optimization")
        
        try:
            from sklearn.gaussian_process import GaussianProcessRegressor
            from sklearn.gaussian_process.kernels import RBF, ConstantKernel
        except ImportError:
            logger.error("sklearn not installed, falling back to random search")
            return self._random_search(max_evaluations)
        
        # 初始随机采样
        X_observed = []
        y_observed = []
        
        for _ in range(min(n_initial_points, max_evaluations)):
            params = {
                name: param_space.sample_random()
                for name, param_space in self.parameter_space.items()
            }
            score = self._evaluate(params)
            
            X_observed.append(self._params_to_array(params))
            y_observed.append(score if self.maximize else -score)
        
        # 高斯过程回归
        kernel = ConstantKernel(1.0) * RBF(length_scale=1.0)
        gp = GaussianProcessRegressor(kernel=kernel, n_restarts_optimizer=10)
        
        best_score = max(y_observed) if self.maximize else -min(y_observed)
        best_params = None
        
        # 迭代优化
        for iteration in range(max_evaluations - n_initial_points):
            # 拟合高斯过程
            gp.fit(np.array(X_observed), np.array(y_observed))
            
            # 寻找最佳获取点
            next_params = self._optimize_acquisition(gp, acquisition_function)
            next_score = self._evaluate(next_params)
            
            X_observed.append(self._params_to_array(next_params))
            y_observed.append(next_score if self.maximize else -next_score)
            
            if (self.maximize and next_score > best_score) or (not self.maximize and next_score < best_score):
                best_score = next_score
                best_params = next_params
        
        return {'params': best_params, 'score': best_score}
    
    def _genetic_algorithm(
        self,
        max_evaluations: int,
        population_size: int = 50,
        mutation_rate: float = 0.1,
        crossover_rate: float = 0.8,
        elite_size: int = 5
    ) -> Dict[str, Any]:
        """
        遗传算法
        
        Args:
            max_evaluations: 最大评估次数
            population_size: 种群大小
            mutation_rate: 突变率
            crossover_rate: 交叉率
            elite_size: 精英保留数量
            
        Returns:
            最佳参数和评分
        """
        logger.info("Running genetic algorithm")
        
        # 初始化种群
        population = [
            {name: param_space.sample_random() for name, param_space in self.parameter_space.items()}
            for _ in range(population_size)
        ]
        
        best_score = float('-inf') if self.maximize else float('inf')
        best_params = None
        
        n_generations = max_evaluations // population_size
        
        for generation in range(n_generations):
            # 评估种群
            scores = [self._evaluate(params) for params in population]
            
            # 更新最佳
            gen_best_idx = np.argmax(scores) if self.maximize else np.argmin(scores)
            if (self.maximize and scores[gen_best_idx] > best_score) or \
               (not self.maximize and scores[gen_best_idx] < best_score):
                best_score = scores[gen_best_idx]
                best_params = population[gen_best_idx].copy()
            
            # 选择
            population = self._selection(population, scores, elite_size)
            
            # 交叉
            offspring = []
            for i in range(0, len(population) - elite_size, 2):
                if np.random.random() < crossover_rate and i + 1 < len(population):
                    child1, child2 = self._crossover(population[i], population[i + 1])
                    offspring.extend([child1, child2])
                else:
                    offspring.extend([population[i], population[i + 1] if i + 1 < len(population) else population[i]])
            
            # 突变
            for i in range(len(offspring)):
                if np.random.random() < mutation_rate:
                    offspring[i] = self._mutate(offspring[i])
            
            # 新一代种群（精英 + 后代）
            population = population[:elite_size] + offspring[:population_size - elite_size]
        
        return {'params': best_params, 'score': best_score}
    
    def _evaluate(self, params: Dict[str, Any]) -> float:
        """
        评估参数组合
        
        Args:
            params: 参数字典
            
        Returns:
            评分
        """
        try:
            score = self.objective_function(params)
            
            self.evaluation_history.append({
                'params': params.copy(),
                'score': score,
                'timestamp': datetime.now().isoformat()
            })
            
            return score
            
        except Exception as e:
            logger.error(f"Evaluation failed for params {params}: {e}")
            return float('-inf') if self.maximize else float('inf')
    
    def _params_to_array(self, params: Dict[str, Any]) -> np.ndarray:
        """将参数字典转换为数组（用于高斯过程）"""
        arr = []
        for name in sorted(self.parameter_space.keys()):
            param_space = self.parameter_space[name]
            value = params[name]
            
            if param_space.param_type == 'categorical':
                # One-hot编码
                idx = param_space.values.index(value)
                arr.append(idx)
            else:
                # 归一化到[0, 1]
                normalized = (value - param_space.min_value) / (param_space.max_value - param_space.min_value)
                arr.append(normalized)
        
        return np.array(arr)
    
    def _optimize_acquisition(self, gp, acquisition_function: str, n_samples: int = 1000) -> Dict[str, Any]:
        """优化获取函数以找到下一个采样点"""
        best_acquisition = float('-inf')
        best_params = None
        
        # 随机采样候选点
        for _ in range(n_samples):
            params = {
                name: param_space.sample_random()
                for name, param_space in self.parameter_space.items()
            }
            
            X = self._params_to_array(params).reshape(1, -1)
            mean, std = gp.predict(X, return_std=True)
            
            # 计算获取函数值
            if acquisition_function == 'ei':  # Expected Improvement
                best_observed = max([r['score'] for r in self.evaluation_history])
                z = (mean[0] - best_observed) / (std[0] + 1e-9)
                from scipy.stats import norm
                acquisition = (mean[0] - best_observed) * norm.cdf(z) + std[0] * norm.pdf(z)
            elif acquisition_function == 'ucb':  # Upper Confidence Bound
                kappa = 2.0
                acquisition = mean[0] + kappa * std[0]
            else:  # POI - Probability of Improvement
                best_observed = max([r['score'] for r in self.evaluation_history])
                z = (mean[0] - best_observed) / (std[0] + 1e-9)
                from scipy.stats import norm
                acquisition = norm.cdf(z)
            
            if acquisition > best_acquisition:
                best_acquisition = acquisition
                best_params = params
        
        return best_params
    
    def _selection(self, population: List[Dict], scores: List[float], elite_size: int) -> List[Dict]:
        """选择操作（锦标赛选择）"""
        # 保留精英
        sorted_indices = np.argsort(scores)
        if not self.maximize:
            sorted_indices = sorted_indices[::-1]
        
        elite = [population[i] for i in sorted_indices[:elite_size]]
        
        # 锦标赛选择其余个体
        selected = elite.copy()
        tournament_size = 3
        
        while len(selected) < len(population):
            tournament_indices = np.random.choice(len(population), tournament_size, replace=False)
            tournament_scores = [scores[i] for i in tournament_indices]
            winner_idx = tournament_indices[np.argmax(tournament_scores) if self.maximize else np.argmin(tournament_scores)]
            selected.append(population[winner_idx].copy())
        
        return selected
    
    def _crossover(self, parent1: Dict, parent2: Dict) -> Tuple[Dict, Dict]:
        """交叉操作"""
        child1 = parent1.copy()
        child2 = parent2.copy()
        
        for name in self.parameter_space.keys():
            if np.random.random() < 0.5:
                child1[name], child2[name] = child2[name], child1[name]
        
        return child1, child2
    
    def _mutate(self, individual: Dict) -> Dict:
        """突变操作"""
        mutated = individual.copy()
        
        for name, param_space in self.parameter_space.items():
            if np.random.random() < 1.0 / len(self.parameter_space):
                mutated[name] = param_space.sample_random()
        
        return mutated


# 便捷函数
def quick_optimize(
    objective_function: Callable,
    parameter_space: List[ParameterSpace],
    method: OptimizationMethod = OptimizationMethod.RANDOM_SEARCH,
    max_evaluations: int = 50,
    maximize: bool = True
) -> OptimizationResult:
    """
    快速优化便捷函数
    
    Args:
        objective_function: 目标函数
        parameter_space: 参数空间
        method: 优化方法
        max_evaluations: 最大评估次数
        maximize: 是否最大化
        
    Returns:
        优化结果
    """
    optimizer = ParameterOptimizer(objective_function, parameter_space, maximize)
    return optimizer.optimize(method, max_evaluations)
