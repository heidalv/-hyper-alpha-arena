"""
Distributed Evolution — 多进化岛架构 (F3-5)

每个岛独立进化（不同 symbol / 时间区间 / 随机种子），
每 migration_interval 代跨岛迁移 top 2 个体，
主岛聚合全部 champion。

设计原则：
- 与现有 StrategyEvolver 并行工作，不修改其内部逻辑
- ThreadPoolExecutor 实现岛屿并行
- 迁移通过 genome 注入实现（修改种群中的个体参数）
"""

import copy
import logging
import math
import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# 默认岛屿配置
DEFAULT_ISLAND_SYMBOLS = [
    ["BTC", "ETH"],
    ["BTC", "ETH", "SOL"],
    ["ETH", "SOL", "DOGE"],
    ["BTC", "SOL", "LINK"],
]

DEFAULT_TIME_RANGES = [365, 545, 730, 910]  # 1年, 1.5年, 2年, 2.5年

MIGRATION_INTERVAL = 5    # 每 N 代跨岛迁移
MIGRATION_TOP_K = 2       # 每岛迁出 top K 个个体


@dataclass
class IslandConfig:
    """单个进化岛的配置"""
    island_id: str = ""
    symbols: List[str] = field(default_factory=lambda: ["BTC", "ETH"])
    time_range_days: int = 365
    random_seed: int = 42
    generations: int = 12
    population_size: int = 16
    tier: str = "mid"


@dataclass
class IslandProgress:
    """岛屿进化进度"""
    island_id: str = ""
    current_generation: int = 0
    best_sharpe: float = -999.0
    best_win_rate: float = 0.0
    population: List[Dict[str, Any]] = field(default_factory=list)
    champion: Optional[Dict[str, Any]] = None
    generation_history: List[Dict[str, Any]] = field(default_factory=list)
    immigrants: List[Dict[str, Any]] = field(default_factory=list)
    emigrants: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "pending"  # pending | running | migrating | completed | failed
    error: str = ""


@dataclass
class DistributedEvolutionResult:
    """分布式进化结果"""
    template_id: str = ""
    islands: List[IslandProgress] = field(default_factory=list)
    aggregated_champion: Optional[Dict[str, Any]] = None
    migration_events: List[Dict[str, Any]] = field(default_factory=list)
    total_duration_s: float = 0.0
    started_at: str = ""
    completed_at: str = ""


class DistributedEvolutionOrchestrator:
    """
    多进化岛编排器 (F3-5)

    用法:
        orchestrator = DistributedEvolutionOrchestrator()
        result = orchestrator.run(
            db, template_id,
            num_islands=4,
            generations=12,
            population_size=16,
        )
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._islands: Dict[str, IslandProgress] = {}
        self._running = False
        self._migration_lock = threading.Lock()
        logger.info("[DistributedEvo] 分布式进化编排器初始化完成")

    # ══════════════════════════════════════════════════
    #  主入口
    # ══════════════════════════════════════════════════

    def run(
        self,
        db: Session,
        template_id: str,
        num_islands: int = 4,
        generations: int = 12,
        population_size: int = 16,
        tier: str = "mid",
        migration_interval: int = MIGRATION_INTERVAL,
        migration_top_k: int = MIGRATION_TOP_K,
    ) -> DistributedEvolutionResult:
        """运行分布式进化。

        为每个岛配置不同的 symbol 组合、时间范围和随机种子，
        并行运行，每 migration_interval 代执行跨岛迁移。
        """
        started_at = datetime.now(timezone.utc).isoformat()
        t0 = time.time()

        result = DistributedEvolutionResult(
            template_id=template_id,
            started_at=started_at,
        )

        # 1. 创建岛屿配置
        island_configs = self._create_island_configs(
            num_islands, generations, population_size, tier
        )

        # 2. 逐代并行运行（每代所有岛并行，代间同步以支持迁移）
        self._running = True
        try:
            self._run_island_evolution(
                db, template_id, island_configs,
                migration_interval, migration_top_k, result,
            )
        except Exception as e:
            logger.error(f"[DistributedEvo] 进化异常: {e}", exc_info=True)
        finally:
            self._running = False

        # 3. 聚合 champion
        result.aggregated_champion = self._aggregate_champions(result.islands)
        result.total_duration_s = time.time() - t0
        result.completed_at = datetime.now(timezone.utc).isoformat()

        logger.info(
            f"[DistributedEvo] 完成: {num_islands}岛, "
            f"总耗时={result.total_duration_s:.0f}s, "
            f"最佳Sharpe={result.aggregated_champion.get('sharpe', 0):.2f}"
        )
        return result

    # ══════════════════════════════════════════════════
    #  岛屿配置生成
    # ══════════════════════════════════════════════════

    def _create_island_configs(
        self,
        num_islands: int,
        generations: int,
        population_size: int,
        tier: str,
    ) -> List[IslandConfig]:
        """生成多样化的岛屿配置"""
        configs = []
        for i in range(num_islands):
            symbols = (
                DEFAULT_ISLAND_SYMBOLS[i]
                if i < len(DEFAULT_ISLAND_SYMBOLS)
                else [random.choice(["BTC", "ETH", "SOL", "DOGE", "LINK", "AVAX", "ADA"])
                      for _ in range(random.randint(2, 4))]
            )
            time_range = (
                DEFAULT_TIME_RANGES[i]
                if i < len(DEFAULT_TIME_RANGES)
                else random.choice(DEFAULT_TIME_RANGES)
            )
            seed = 42 + i * 137  # 素数偏移确保多样性

            cfg = IslandConfig(
                island_id=f"island_{i:02d}_{uuid.uuid4().hex[:6]}",
                symbols=symbols,
                time_range_days=time_range,
                random_seed=seed,
                generations=generations,
                population_size=population_size,
                tier=tier,
            )
            configs.append(cfg)
            logger.info(
                f"[DistributedEvo] 岛 {cfg.island_id}: "
                f"symbols={symbols}, range={time_range}d, seed={seed}"
            )
        return configs

    # ══════════════════════════════════════════════════
    #  核心进化循环（逐代并行 + 跨岛迁移）
    # ══════════════════════════════════════════════════

    def _run_island_evolution(
        self,
        db: Session,
        template_id: str,
        configs: List[IslandConfig],
        migration_interval: int,
        migration_top_k: int,
        result: DistributedEvolutionResult,
    ):
        """逐代运行所有岛屿，每 migration_interval 代同步并迁移"""
        from backend.database.models import StrategyTemplate

        tpl = db.query(StrategyTemplate).filter(
            StrategyTemplate.template_id == template_id
        ).first()
        if not tpl:
            logger.warning(f"[DistributedEvo] 模板 {template_id} 不存在")
            return

        # 初始化岛屿进度
        progresses: Dict[str, IslandProgress] = {}
        for cfg in configs:
            progresses[cfg.island_id] = IslandProgress(
                island_id=cfg.island_id,
                population=[self._random_genome(cfg.random_seed + g)
                           for g in range(cfg.population_size)],
                status="running",
            )

        total_generations = max(cfg.generations for cfg in configs)

        for gen in range(total_generations):
            if not self._running:
                break

            # 并行执行当前代（所有岛）
            gen_futures = {}
            active_configs = [
                cfg for cfg in configs if gen < cfg.generations
            ]
            with ThreadPoolExecutor(max_workers=len(active_configs)) as executor:
                for cfg in active_configs:
                    prog = progresses[cfg.island_id]
                    prog.current_generation = gen + 1

                    future = executor.submit(
                        self._run_single_generation,
                        db, tpl, cfg, prog, gen,
                    )
                    gen_futures[future] = cfg.island_id

                for future in as_completed(gen_futures):
                    island_id = gen_futures[future]
                    try:
                        updated_prog = future.result(timeout=600)
                        if updated_prog:
                            progresses[island_id] = updated_prog
                    except Exception as e:
                        logger.error(
                            f"[DistributedEvo] 岛 {island_id} 第{gen+1}代异常: {e}"
                        )
                        progresses[island_id].status = "failed"
                        progresses[island_id].error = str(e)[:200]

            # 检查是否需要跨岛迁移
            if (gen + 1) % migration_interval == 0 and gen + 1 < total_generations:
                self._migrate_top_individuals(
                    progresses, migration_top_k, gen + 1, result
                )

        # 收集最终结果
        result.islands = list(progresses.values())
        for prog in result.islands:
            if prog.status == "running":
                prog.status = "completed"

    # ══════════════════════════════════════════════════
    #  单代运行
    # ══════════════════════════════════════════════════

    def _run_single_generation(
        self,
        db: Session,
        tpl,
        cfg: IslandConfig,
        prog: IslandProgress,
        gen: int,
    ) -> Optional[IslandProgress]:
        """运行单个岛屿的单代进化"""
        from backend.services.strategy_evolver import StrategyEvolver

        evolver = StrategyEvolver()

        # 将移民个体注入种群（如果存在）
        if prog.immigrants:
            # 替换种群中最差的个体
            sorted_pop = sorted(
                prog.population,
                key=lambda g: g.get("fitness", g.get("sharpe", -999)),
            )
            for i, immigrant in enumerate(prog.immigrants):
                if i < len(sorted_pop):
                    sorted_pop[i] = immigrant
            prog.population = sorted_pop
            logger.info(
                f"[DistributedEvo] 岛 {cfg.island_id} 注入了 "
                f"{len(prog.immigrants)} 个移民个体"
            )
            prog.immigrants = []

        # 对种群中每个 genome 执行回测
        backtest_results = []
        for genome in prog.population:
            bt = evolver._run_single_backtest_for_genome(tpl, genome, db)
            if bt:
                genome["fitness"] = self._fitness(bt)
                genome["_bt"] = bt
                backtest_results.append((genome, bt))

        if not backtest_results:
            return prog

        # 按 fitness 排序
        backtest_results.sort(key=lambda x: x[0].get("fitness", -999), reverse=True)

        # 精英保留 + 变异生成新种群
        elite_count = max(2, cfg.population_size // 4)
        elites = [copy.deepcopy(g) for g, _ in backtest_results[:elite_count]]

        new_population = list(elites)
        while len(new_population) < cfg.population_size:
            parent = random.choice(elites)
            child = self._mutate_genome(
                copy.deepcopy(parent), cfg.random_seed + gen + len(new_population)
            )
            new_population.append(child)

        # 更新进度
        best_genome, best_bt = backtest_results[0]
        prog.population = new_population
        prog.best_sharpe = max(prog.best_sharpe, best_bt.get("sharpe", -999))
        prog.best_win_rate = max(prog.best_win_rate, best_bt.get("win_rate", 0))
        prog.champion = {
            "genome": best_genome,
            "sharpe": best_bt.get("sharpe", 0),
            "win_rate": best_bt.get("win_rate", 0),
            "max_drawdown": best_bt.get("max_drawdown", 0),
            "total_trades": best_bt.get("total_trades", 0),
            "total_return": best_bt.get("total_return", 0),
            "generation": gen + 1,
            "island_id": cfg.island_id,
        }
        prog.generation_history.append({
            "generation": gen + 1,
            "best_sharpe": best_bt.get("sharpe", 0),
            "best_win_rate": best_bt.get("win_rate", 0),
            "avg_fitness": sum(
                g.get("fitness", 0) for g, _ in backtest_results
            ) / max(len(backtest_results), 1),
            "population_diversity": len(
                set(str(g.get("genome", {})) for g in prog.population)
            ) / max(len(prog.population), 1),
        })

        return prog

    # ══════════════════════════════════════════════════
    #  跨岛迁移
    # ══════════════════════════════════════════════════

    def _migrate_top_individuals(
        self,
        progresses: Dict[str, IslandProgress],
        top_k: int,
        generation: int,
        result: DistributedEvolutionResult,
    ):
        """每 migration_interval 代，跨岛迁移 top K 个个体"""
        with self._migration_lock:
            active_islands = [
                pid for pid, p in progresses.items()
                if p.status == "running" and len(p.population) >= top_k
            ]
            if len(active_islands) < 2:
                return

            # 收集每个岛的最佳个体
            island_elites: Dict[str, List[Dict]] = {}
            for island_id in active_islands:
                prog = progresses[island_id]
                sorted_pop = sorted(
                    prog.population,
                    key=lambda g: g.get("fitness", g.get("sharpe", -999)),
                    reverse=True,
                )
                island_elites[island_id] = [
                    copy.deepcopy(g) for g in sorted_pop[:top_k]
                ]

            # 轮换迁移：岛 i 的精英 → 岛 i+1
            island_list = active_islands
            migration_log = []
            for i, source_id in enumerate(island_list):
                target_id = island_list[(i + 1) % len(island_list)]
                elites = island_elites[source_id]
                progresses[target_id].immigrants = elites
                progresses[target_id].status = "migrating"
                migration_log.append({
                    "from": source_id,
                    "to": target_id,
                    "individuals": [
                        {"fitness": g.get("fitness", 0)} for g in elites
                    ],
                })

            result.migration_events.append({
                "generation": generation,
                "migrations": migration_log,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            logger.info(
                f"[DistributedEvo] 第{generation}代迁移完成: "
                f"{len(migration_log)} 对岛屿交换了精英"
            )

            # 恢复状态
            for prog in progresses.values():
                if prog.status == "migrating":
                    prog.status = "running"

    # ══════════════════════════════════════════════════
    #  Champion 聚合
    # ══════════════════════════════════════════════════

    def _aggregate_champions(
        self, islands: List[IslandProgress]
    ) -> Optional[Dict[str, Any]]:
        """从所有岛屿的 champion 中选出全局最优"""
        best = None
        best_sharpe = -999.0

        for prog in islands:
            if prog.champion and prog.champion.get("sharpe", -999) > best_sharpe:
                best_sharpe = prog.champion["sharpe"]
                best = dict(prog.champion)
                best["source_island"] = prog.island_id

        if best:
            # 添加聚合统计
            all_sharpes = [
                p.champion.get("sharpe", 0) for p in islands
                if p.champion
            ]
            best["all_island_sharpes"] = all_sharpes
            best["mean_island_sharpe"] = (
                sum(all_sharpes) / len(all_sharpes) if all_sharpes else 0
            )
            best["num_islands"] = len(islands)
            best["total_migrations"] = sum(
                len(m.get("migrations", [])) for m in (
                    getattr(self, "_last_result", None) and
                    self._last_result.migration_events or []
                )
            ) if hasattr(self, "_last_result") else 0

        return best

    # ══════════════════════════════════════════════════
    #  Fitness 计算
    # ══════════════════════════════════════════════════

    @staticmethod
    def _fitness(bt: Dict[str, Any]) -> float:
        """综合 fitness = Sharpe × 0.5 + WR × 0.3 + trades_bonus × 0.2"""
        sharpe = float(bt.get("sharpe", 0) or 0)
        wr = float(bt.get("win_rate", 0) or 0)
        trades = int(bt.get("total_trades", 0) or 0)

        # trades_bonus: 交易数在 20~200 之间为最佳
        if trades >= 20 and trades <= 200:
            trades_bonus = 1.0
        elif trades > 200:
            trades_bonus = max(0.3, 1.0 - (trades - 200) / 500)
        elif trades > 0:
            trades_bonus = trades / 20
        else:
            trades_bonus = 0.0

        return sharpe * 0.5 + wr * 0.3 + trades_bonus * 0.2

    # ══════════════════════════════════════════════════
    #  Genome 工具
    # ══════════════════════════════════════════════════

    @staticmethod
    def _random_genome(seed: int) -> Dict[str, Any]:
        """生成随机 genome"""
        rng = random.Random(seed)
        return {
            "sl_multiplier": round(rng.uniform(0.8, 2.0), 2),
            "tp_multiplier": round(rng.uniform(0.8, 2.5), 2),
            "confidence_threshold": rng.randint(40, 75),
            "max_hold_bars": rng.randint(8, 48),
            "rsi_period": rng.randint(10, 20),
            "rsi_oversold": rng.randint(20, 35),
            "rsi_overbought": rng.randint(65, 80),
            "ema_fast": rng.randint(5, 20),
            "ema_slow": rng.randint(30, 100),
            "atr_mult": round(rng.uniform(0.5, 3.0), 2),
            "trailing_pct": round(rng.uniform(0.01, 0.08), 3),
            "position_size_pct": round(rng.uniform(0.03, 0.25), 2),
            "_seed": seed,
        }

    @staticmethod
    def _mutate_genome(genome: Dict[str, Any], seed: int) -> Dict[str, Any]:
        """变异 genome（高斯扰动 + 随机重置）"""
        rng = random.Random(seed)
        mutated = dict(genome)
        mutation_rate = 0.3
        mutation_scale = 0.15

        numeric_params = {
            "sl_multiplier": (0.5, 3.0),
            "tp_multiplier": (0.5, 4.0),
            "confidence_threshold": (30, 85),
            "max_hold_bars": (4, 72),
            "rsi_period": (7, 25),
            "rsi_oversold": (15, 40),
            "rsi_overbought": (60, 85),
            "ema_fast": (3, 30),
            "ema_slow": (20, 150),
            "atr_mult": (0.3, 4.0),
            "trailing_pct": (0.005, 0.12),
            "position_size_pct": (0.01, 0.30),
        }

        for param, (lo, hi) in numeric_params.items():
            if param not in mutated:
                continue
            if rng.random() < mutation_rate:
                if rng.random() < 0.15:
                    # 完全随机重置
                    mutated[param] = round(rng.uniform(lo, hi), 3)
                else:
                    # 高斯扰动
                    current = float(mutated[param])
                    delta = rng.gauss(0, (hi - lo) * mutation_scale)
                    new_val = current + delta
                    mutated[param] = round(max(lo, min(hi, new_val)), 3)

        mutated.pop("_seed", None)
        mutated["_seed"] = seed
        mutated.pop("fitness", None)
        mutated.pop("_bt", None)

        return mutated

    # ══════════════════════════════════════════════════
    #  状态查询
    # ══════════════════════════════════════════════════

    def get_status(self) -> Dict[str, Any]:
        """获取当前分布式进化状态"""
        return {
            "running": self._running,
            "num_islands": len(self._islands),
            "islands": {
                pid: {
                    "status": p.status,
                    "generation": p.current_generation,
                    "best_sharpe": p.best_sharpe,
                    "best_win_rate": p.best_win_rate,
                }
                for pid, p in self._islands.items()
            },
        }

    def stop(self):
        """停止所有进化"""
        self._running = False
        logger.info("[DistributedEvo] 已请求停止所有岛屿")


# 全局单例
_orchestrator: Optional[DistributedEvolutionOrchestrator] = None


def get_distributed_evolution() -> DistributedEvolutionOrchestrator:
    """获取全局分布式进化编排器"""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = DistributedEvolutionOrchestrator()
    return _orchestrator
