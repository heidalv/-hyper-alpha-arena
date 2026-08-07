"""
遗传算法策略参数优化器 — GeneticOptimizer

基于 strategy_evolver.py 的算法逻辑改造，标准化为独立模块。
（方案§3 新增模块 + §13 核心地位）

核心配置（方案§修复④）：
  - 周进化：30代 × 20个体
  - 紧急进化：10代 × 10个体
  - 早停耐心：5代
  - 晋升门槛 Sharpe >= 1.0

算法：简化遗传算法（无需 DEAP 外部依赖，纯 Python 实现）
"""

import logging
import random
import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class Individual:
    """遗传算法个体：一套策略参数"""
    genome: Dict[str, Any]       # 参数字典
    fitness: float = 0.0         # 适应度（Sharpe Ratio）
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    total_trades: int = 0
    generation: int = 0


@dataclass
class EvolutionResult:
    """进化结果"""
    best_genome: Dict[str, Any]
    best_fitness: float
    best_sharpe: float
    generations_run: int
    population_size: int
    early_stopped: bool = False
    early_stop_reason: str = ""
    history: List[Dict] = field(default_factory=list)  # 每代最优分数历史


class GeneticOptimizer:
    """
    遗传算法策略参数优化器。

    用法：
        optimizer = GeneticOptimizer()
        result = optimizer.evolve(
            template_id="tpl_001",
            param_ranges={
                "stop_loss_pct": (0.01, 0.08),
                "take_profit_pct": (0.02, 0.20),
                "leverage": (1, 5),
            },
            fitness_fn=my_backtest_fn,  # fn(genome) -> Individual
            generations=30,
            population_size=20,
        )
    """

    # 默认进化配置（方案§修复④）
    DEFAULT_GENERATIONS: int = 30
    DEFAULT_POPULATION_SIZE: int = 20
    EMERGENCY_GENERATIONS: int = 10
    EMERGENCY_POPULATION_SIZE: int = 10
    EARLY_STOP_PATIENCE: int = 5      # 连续5代无提升则早停
    PROMOTION_MIN_SHARPE: float = 1.0  # 晋升门槛

    # 遗传算法参数
    CROSSOVER_RATE: float = 0.7
    MUTATION_RATE: float = 0.2
    MUTATION_SIGMA: float = 0.15   # 变异幅度（参数范围的15%）
    TOURNAMENT_SIZE: int = 3       # 锦标赛选择大小
    ELITE_SIZE: int = 2            # 精英保留数量

    def evolve(
        self,
        template_id: str,
        param_ranges: Dict[str, Tuple[float, float]],
        fitness_fn: Callable[[Dict], Individual],
        generations: int = None,
        population_size: int = None,
        is_emergency: bool = False,
        seed_genome: Optional[Dict] = None,   # 从现有最优基因组开始
        on_generation_complete: Optional[Callable[[int, Individual], None]] = None,
    ) -> EvolutionResult:
        """
        执行遗传算法优化。

        Args:
            template_id: 策略模板ID（日志用）
            param_ranges: 参数搜索范围 {param_name: (min, max)}
            fitness_fn: 适应度函数 fn(genome_dict) -> Individual（内部调用回测）
            generations: 进化代数（None则用默认配置）
            population_size: 种群大小
            is_emergency: 是否紧急进化（使用更小规模）
            seed_genome: 初始种子基因组（可从现有最优解开始）
        """
        if is_emergency:
            gen_count = generations or self.EMERGENCY_GENERATIONS
            pop_size = population_size or self.EMERGENCY_POPULATION_SIZE
        else:
            gen_count = generations or self.DEFAULT_GENERATIONS
            pop_size = population_size or self.DEFAULT_POPULATION_SIZE

        logger.info(
            f"[GeneticOptimizer] 开始进化 template={template_id} "
            f"gen={gen_count} pop={pop_size} emergency={is_emergency}"
        )

        # 1. 初始化种群
        population = self._init_population(param_ranges, pop_size, seed_genome)

        # 2. 评估初始种群
        population = self._evaluate_population(population, fitness_fn, template_id)

        best = max(population, key=lambda x: x.fitness)
        history = [{"gen": 0, "best_fitness": best.fitness, "best_sharpe": best.sharpe}]
        no_improve_count = 0

        # 3. 进化主循环
        for gen in range(1, gen_count + 1):
            # 选择
            parents = self._select_parents(population)
            # 交叉 + 变异 → 新种群
            offspring = self._crossover_and_mutate(parents, param_ranges)
            # 精英保留
            elites = sorted(population, key=lambda x: x.fitness, reverse=True)[:self.ELITE_SIZE]
            # 评估后代
            offspring = self._evaluate_population(offspring, fitness_fn, template_id)
            # 合并精英与后代，取最优 pop_size 个
            combined = elites + offspring
            combined.sort(key=lambda x: x.fitness, reverse=True)
            population = combined[:pop_size]

            for ind in population:
                ind.generation = gen

            gen_best = population[0]
            history.append({
                "gen": gen,
                "best_fitness": gen_best.fitness,
                "best_sharpe": gen_best.sharpe,
                "best_genome": copy.deepcopy(gen_best.genome),
            })

            # 早停检查
            if gen_best.fitness > best.fitness + 0.01:
                best = gen_best
                no_improve_count = 0
            else:
                no_improve_count += 1

            logger.info(
                f"[GeneticOptimizer] Gen {gen}/{gen_count} "
                f"best_sharpe={gen_best.sharpe:.3f} no_improve={no_improve_count}"
            )

            # 每代完成回调（可选，用于 QAA PerformanceTracker 喂数据）
            if on_generation_complete:
                try:
                    on_generation_complete(gen, gen_best)
                except Exception as _cb_err:
                    logger.debug(f"[GeneticOptimizer] on_generation_complete error: {_cb_err}")

            if no_improve_count >= self.EARLY_STOP_PATIENCE:
                logger.info(f"[GeneticOptimizer] 早停：连续 {no_improve_count} 代无提升")
                return EvolutionResult(
                    best_genome=best.genome,
                    best_fitness=best.fitness,
                    best_sharpe=best.sharpe,
                    generations_run=gen,
                    population_size=pop_size,
                    early_stopped=True,
                    early_stop_reason=f"连续{no_improve_count}代无提升",
                    history=history,
                )

        logger.info(
            f"[GeneticOptimizer] 进化完成 template={template_id} "
            f"best_sharpe={best.sharpe:.3f} promote={'是' if best.sharpe >= self.PROMOTION_MIN_SHARPE else '否'}"
        )

        return EvolutionResult(
            best_genome=best.genome,
            best_fitness=best.fitness,
            best_sharpe=best.sharpe,
            generations_run=gen_count,
            population_size=pop_size,
            history=history,
        )

    def should_promote(self, result: EvolutionResult) -> bool:
        """判断进化结果是否达到晋升门槛（Sharpe >= 1.0）"""
        return result.best_sharpe >= self.PROMOTION_MIN_SHARPE

    # ── 遗传算子 ──

    def _init_population(
        self,
        param_ranges: Dict,
        size: int,
        seed: Optional[Dict],
    ) -> List[Individual]:
        """初始化种群"""
        pop = []
        # 若有种子基因组，加入种群（轻微扰动）
        if seed:
            pop.append(Individual(genome=copy.deepcopy(seed)))
        while len(pop) < size:
            genome = {
                k: random.uniform(v[0], v[1])
                if isinstance(v[0], float) else random.randint(int(v[0]), int(v[1]))
                for k, v in param_ranges.items()
            }
            pop.append(Individual(genome=genome))
        return pop

    def _evaluate_population(
        self,
        population: List[Individual],
        fitness_fn: Callable,
        template_id: str,
    ) -> List[Individual]:
        """批量评估适应度"""
        for ind in population:
            if ind.fitness != 0:
                continue
            try:
                result = fitness_fn(ind.genome)
                if isinstance(result, Individual):
                    ind.fitness = result.fitness
                    ind.sharpe = result.sharpe
                    ind.max_drawdown = result.max_drawdown
                    ind.total_trades = result.total_trades
                elif isinstance(result, (int, float)):
                    ind.fitness = float(result)
                    ind.sharpe = float(result)
            except Exception as e:
                logger.debug(f"[GeneticOptimizer] 适应度评估失败 {template_id}: {e}")
                ind.fitness = -99.0
        return population

    def _select_parents(self, population: List[Individual]) -> List[Individual]:
        """锦标赛选择"""
        parents = []
        for _ in range(len(population)):
            tournament = random.sample(population, min(self.TOURNAMENT_SIZE, len(population)))
            winner = max(tournament, key=lambda x: x.fitness)
            parents.append(winner)
        return parents

    def _crossover_and_mutate(
        self,
        parents: List[Individual],
        param_ranges: Dict,
    ) -> List[Individual]:
        """交叉 + 变异生成后代（支持轨迹级交叉）"""
        offspring = []
        random.shuffle(parents)

        use_trajectory = False
        try:
            from backend.services.strategy_genome import crossover_genomes, trajectory_mutate, FLAT_RANGES
            use_trajectory = len(param_ranges) > 5
        except ImportError:
            pass

        for i in range(0, len(parents) - 1, 2):
            p1, p2 = parents[i], parents[i + 1]

            if use_trajectory and random.random() < self.CROSSOVER_RATE:
                child_genome = crossover_genomes(p1.genome, p2.genome)
            elif random.random() < self.CROSSOVER_RATE:
                child_genome = {
                    k: p1.genome[k] if random.random() < 0.5 else p2.genome[k]
                    for k in p1.genome
                }
            else:
                child_genome = copy.deepcopy(p1.genome)

            if use_trajectory:
                child_genome = trajectory_mutate(child_genome, mutation_rate=self.MUTATION_RATE)
            else:
                for k, v_range in param_ranges.items():
                    if random.random() < self.MUTATION_RATE:
                        span = v_range[1] - v_range[0]
                        delta = random.gauss(0, span * self.MUTATION_SIGMA)
                        new_val = child_genome[k] + delta
                        new_val = max(v_range[0], min(v_range[1], new_val))
                        if isinstance(v_range[0], int):
                            new_val = int(round(new_val))
                        child_genome[k] = new_val

            offspring.append(Individual(genome=child_genome))

        if len(parents) % 2 == 1:
            offspring.append(Individual(genome=copy.deepcopy(parents[-1].genome)))
        return offspring


# ════════════════════════════════════════════════════════
#  Phase 5: NSGA-II 多目标优化
# ════════════════════════════════════════════════════════

@dataclass
class MultiObjectiveIndividual(Individual):
    """多目标个体 — 在 Individual 基础上增加多目标字段"""
    objectives: Dict[str, float] = field(default_factory=dict)
    rank: int = 0
    crowding_distance: float = 0.0


@dataclass
class ParetoFront:
    """Pareto前沿"""
    individuals: List[MultiObjectiveIndividual]
    generation: int

    def get_best_compromise(self) -> Optional[MultiObjectiveIndividual]:
        """获取折中最优解（距理想点最近）"""
        if not self.individuals:
            return None
        objs = [ind.objectives for ind in self.individuals]
        keys = list(objs[0].keys())
        mins = {k: min(o.get(k, 0) for o in objs) for k in keys}
        maxs = {k: max(o.get(k, 0) for o in objs) for k in keys}

        best = None
        best_dist = float('inf')
        for ind in self.individuals:
            dist = sum(
                (1 - (ind.objectives.get(k, 0) - mins[k]) /
                 (maxs[k] - mins[k] + 1e-10)) ** 2
                for k in keys
            ) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best = ind
        return best


class NSGAIIOptimizer(GeneticOptimizer):
    """
    NSGA-II 多目标遗传优化器

    继承 GeneticOptimizer 的遗传算子，新增:
    - 非支配排序 (fast non-dominated sort)
    - 拥挤度距离 (crowding distance)
    - 多目标进化 (evolve_multi_objective)

    目标函数: sharpe (最大化), max_drawdown (最小化), win_rate (最大化)
    """

    OBJECTIVE_NAMES = ['sharpe', 'max_drawdown', 'win_rate']
    MAXIMIZE = {'sharpe': True, 'max_drawdown': False, 'win_rate': True}

    def evolve_multi_objective(
        self,
        template_id: str,
        param_ranges: Dict[str, Tuple[float, float]],
        fitness_fn: Callable[[Dict], MultiObjectiveIndividual],
        generations: int = 30,
        population_size: int = 40,
    ) -> ParetoFront:
        """
        多目标进化

        Args:
            template_id: 策略模板ID（日志用）
            param_ranges: 参数搜索范围
            fitness_fn: fn(genome) -> MultiObjectiveIndividual
            generations: 进化代数
            population_size: 种群大小（NSGA-II推荐更大种群）

        Returns:
            ParetoFront 包含 rank=0 的非支配解集
        """
        population = self._init_mo_population(param_ranges, population_size)
        population = self._evaluate_mo_population(population, fitness_fn)

        for gen in range(1, generations + 1):
            fronts = self._non_dominated_sort(population)
            self._assign_crowding_distance(fronts)

            parents = self._tournament_select_mo(population)
            offspring = self._crossover_and_mutate_mo(parents, param_ranges)
            offspring = self._evaluate_mo_population(
                [MultiObjectiveIndividual(genome=o.genome) for o in offspring],
                fitness_fn,
            )

            combined = population + offspring
            fronts = self._non_dominated_sort(combined)
            self._assign_crowding_distance(fronts)

            population = []
            for front in fronts:
                if len(population) + len(front) <= population_size:
                    population.extend(front)
                else:
                    front.sort(key=lambda x: x.crowding_distance, reverse=True)
                    population.extend(front[:population_size - len(population)])
                    break

            logger.info(
                f"[NSGA-II] Gen {gen}/{generations} "
                f"front_0_size={len(fronts[0]) if fronts else 0}"
            )

        return ParetoFront(
            individuals=[ind for ind in population if ind.rank == 0],
            generation=generations,
        )

    # ── NSGA-II 核心算法 ──

    def _init_mo_population(self, param_ranges: Dict, size: int) -> List[MultiObjectiveIndividual]:
        """初始化多目标种群"""
        pop = []
        while len(pop) < size:
            genome = {
                k: random.uniform(v[0], v[1])
                if isinstance(v[0], float) else random.randint(int(v[0]), int(v[1]))
                for k, v in param_ranges.items()
            }
            pop.append(MultiObjectiveIndividual(genome=genome))
        return pop

    def _evaluate_mo_population(
        self,
        population: List[MultiObjectiveIndividual],
        fitness_fn: Callable,
    ) -> List[MultiObjectiveIndividual]:
        """评估多目标种群"""
        for ind in population:
            try:
                result = fitness_fn(ind.genome)
                if isinstance(result, MultiObjectiveIndividual):
                    ind.objectives = result.objectives
                    ind.fitness = result.fitness
                    ind.sharpe = result.sharpe
                    ind.max_drawdown = result.max_drawdown
                elif isinstance(result, Individual):
                    ind.fitness = result.fitness
                    ind.sharpe = result.sharpe
                    ind.max_drawdown = result.max_drawdown
                    ind.objectives = {
                        'sharpe': result.sharpe,
                        'max_drawdown': result.max_drawdown,
                        'win_rate': 0.0,
                    }
            except Exception as e:
                logger.debug(f"[NSGA-II] fitness eval failed: {e}")
                ind.fitness = -99.0
                ind.objectives = {k: -99.0 for k in self.OBJECTIVE_NAMES}
        return population

    def _non_dominated_sort(self, population: List) -> List[List]:
        """快速非支配排序"""
        n = len(population)
        domination_count = [0] * n
        dominated_set = [[] for _ in range(n)]
        fronts = [[]]

        for i in range(n):
            for j in range(i + 1, n):
                if self._dominates(population[i], population[j]):
                    dominated_set[i].append(j)
                    domination_count[j] += 1
                elif self._dominates(population[j], population[i]):
                    dominated_set[j].append(i)
                    domination_count[i] += 1

            if domination_count[i] == 0:
                population[i].rank = 0
                fronts[0].append(population[i])

        k = 0
        while fronts[k]:
            next_front = []
            for ind in fronts[k]:
                idx = population.index(ind)
                for j in dominated_set[idx]:
                    domination_count[j] -= 1
                    if domination_count[j] == 0:
                        population[j].rank = k + 1
                        next_front.append(population[j])
            k += 1
            fronts.append(next_front)

        return [f for f in fronts if f]

    def _dominates(self, a: MultiObjectiveIndividual, b: MultiObjectiveIndividual) -> bool:
        """判断 a 是否支配 b"""
        dominated = False
        for obj in self.OBJECTIVE_NAMES:
            va = a.objectives.get(obj, 0)
            vb = b.objectives.get(obj, 0)
            if self.MAXIMIZE.get(obj, True):
                if va < vb:
                    return False
                if va > vb:
                    dominated = True
            else:
                if va > vb:
                    return False
                if va < vb:
                    dominated = True
        return dominated

    def _assign_crowding_distance(self, fronts: List[List]):
        """计算拥挤度距离"""
        for front in fronts:
            n = len(front)
            if n <= 2:
                for ind in front:
                    ind.crowding_distance = float('inf')
                continue

            for ind in front:
                ind.crowding_distance = 0

            for obj in self.OBJECTIVE_NAMES:
                front.sort(key=lambda x: x.objectives.get(obj, 0))
                front[0].crowding_distance = float('inf')
                front[-1].crowding_distance = float('inf')
                obj_range = (
                    front[-1].objectives.get(obj, 0) -
                    front[0].objectives.get(obj, 0) + 1e-10
                )
                for i in range(1, n - 1):
                    front[i].crowding_distance += (
                        front[i + 1].objectives.get(obj, 0) -
                        front[i - 1].objectives.get(obj, 0)
                    ) / obj_range

    def _tournament_select_mo(self, population: List[MultiObjectiveIndividual]) -> List[MultiObjectiveIndividual]:
        """NSGA-II 锦标赛选择（基于 rank + crowding_distance）"""
        parents = []
        for _ in range(len(population)):
            tournament = random.sample(population, min(self.TOURNAMENT_SIZE, len(population)))
            winner = min(tournament, key=lambda x: (x.rank, -x.crowding_distance))
            parents.append(winner)
        return parents

    def _crossover_and_mutate_mo(
        self,
        parents: List[MultiObjectiveIndividual],
        param_ranges: Dict,
    ) -> List[MultiObjectiveIndividual]:
        """交叉 + 变异生成多目标后代"""
        offspring = []
        random.shuffle(parents)

        for i in range(0, len(parents) - 1, 2):
            p1, p2 = parents[i], parents[i + 1]

            if random.random() < self.CROSSOVER_RATE:
                child_genome = {
                    k: p1.genome[k] if random.random() < 0.5 else p2.genome[k]
                    for k in p1.genome
                }
            else:
                child_genome = copy.deepcopy(p1.genome)

            for k, v_range in param_ranges.items():
                if random.random() < self.MUTATION_RATE:
                    span = v_range[1] - v_range[0]
                    delta = random.gauss(0, span * self.MUTATION_SIGMA)
                    new_val = child_genome[k] + delta
                    new_val = max(v_range[0], min(v_range[1], new_val))
                    if isinstance(v_range[0], int):
                        new_val = int(round(new_val))
                    child_genome[k] = new_val

            offspring.append(MultiObjectiveIndividual(genome=child_genome))

        if len(parents) % 2 == 1:
            offspring.append(MultiObjectiveIndividual(
                genome=copy.deepcopy(parents[-1].genome)
            ))
        return offspring


# 模块级单例
genetic_optimizer = GeneticOptimizer()
