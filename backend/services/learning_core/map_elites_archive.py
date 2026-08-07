"""
MAP-Elites 质量-多样性冠军库（整改#19）—— 对标 Mouret&Clune 2015 / MOME(arXiv 2202.03057)。

用"行为特征空间网格 + 每格留精英"替换单一 champion recovery：
  - 行为维度 = (regime, timeframe, volatility_bucket)。
  - 每格保留 fitness 最高的基因组（MOME 模式则每格保留一个小 Pareto 前沿）。
  - 运行时按当前 regime 描述选最适配 elite；无精确格则退最近邻。

零风险：默认关（MAP_ELITES_ENABLED=false），仅作为单 champion recovery 的并行增强；
不改动现有 champion_recovery。可 JSON 序列化，便于持久化/前端展示。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_REGIMES = ["trending_up", "trending_down", "ranging", "volatile", "extreme"]
_TIMEFRAMES = ["short", "mid", "long"]
_VOL_BUCKETS = ["low", "med", "high"]


def is_enabled() -> bool:
    return os.environ.get("MAP_ELITES_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def mode() -> str:
    return os.environ.get("MAP_ELITES_MODE", "single").strip().lower()   # 'single' | 'mome'


@dataclass(frozen=True)
class BehaviorDescriptor:
    """行为特征（网格维度）。frozen → 可作 dict key。"""
    regime: str
    timeframe: str = "mid"
    volatility_bucket: str = "med"

    def key(self) -> Tuple[str, str, str]:
        return (self.regime, self.timeframe, self.volatility_bucket)

    @staticmethod
    def from_market(regime: str, timeframe: str = "mid",
                    volatility_value: Optional[float] = None) -> "BehaviorDescriptor":
        r = (regime or "ranging").strip().lower()
        if r not in _REGIMES:
            # 归一到最近的已知 regime 名
            if "trend" in r and "down" in r:
                r = "trending_down"
            elif "trend" in r:
                r = "trending_up"
            elif "extreme" in r:
                r = "extreme"
            elif "vol" in r:
                r = "volatile"
            else:
                r = "ranging"
        vb = "med"
        if volatility_value is not None:
            vb = "low" if volatility_value < 0.01 else ("high" if volatility_value > 0.03 else "med")
        tf = timeframe if timeframe in _TIMEFRAMES else "mid"
        return BehaviorDescriptor(regime=r, timeframe=tf, volatility_bucket=vb)


@dataclass
class EliteEntry:
    champion_genome: dict
    fitness: float
    behavior: BehaviorDescriptor
    metrics: dict = field(default_factory=dict)
    cumulative_trial_count: int = 0     # 喂 PBO（整改#21）

    def to_dict(self) -> dict:
        return {
            "champion_genome": self.champion_genome,
            "fitness": self.fitness,
            "behavior": {"regime": self.behavior.regime, "timeframe": self.behavior.timeframe,
                         "volatility_bucket": self.behavior.volatility_bucket},
            "metrics": self.metrics,
            "cumulative_trial_count": self.cumulative_trial_count,
        }


class MAPElitesArchive:
    """行为特征空间分网格，每格只留 fittest 个体。"""

    def __init__(self):
        self.grid: Dict[Tuple[str, str, str], EliteEntry] = {}

    def add(self, genome: dict, fitness: float, behavior: BehaviorDescriptor,
            metrics: Optional[dict] = None, trial_count: int = 0) -> bool:
        """若该格为空或新个体更优 → 替换。返回是否写入。"""
        key = behavior.key()
        cur = self.grid.get(key)
        if cur is None or fitness > cur.fitness:
            self.grid[key] = EliteEntry(genome, float(fitness), behavior, metrics or {}, int(trial_count))
            return True
        return False

    def select_elite(self, current_behavior: BehaviorDescriptor) -> Optional[EliteEntry]:
        """运行时按当前 regime 描述选适配 elite；无精确格退最近邻。"""
        key = current_behavior.key()
        if key in self.grid:
            return self.grid[key]
        return self._nearest_behavior(current_behavior)

    def _nearest_behavior(self, target: BehaviorDescriptor) -> Optional[EliteEntry]:
        if not self.grid:
            return None
        # 汉明距离：regime 权重最高，其次 timeframe、vol
        def dist(entry: EliteEntry) -> tuple:
            b = entry.behavior
            d_regime = 0 if b.regime == target.regime else 1
            d_tf = 0 if b.timeframe == target.timeframe else 1
            d_vb = 0 if b.volatility_bucket == target.volatility_bucket else 1
            # 距离相同则偏好更高 fitness（负号）
            return (d_regime * 4 + d_tf * 2 + d_vb, -entry.fitness)
        return min(self.grid.values(), key=dist)

    def all_elites(self) -> List[EliteEntry]:
        return list(self.grid.values())

    def coverage(self) -> int:
        """已填充的行为格数（多样性度量）。"""
        return len(self.grid)

    def best_overall(self) -> Optional[EliteEntry]:
        return max(self.grid.values(), key=lambda e: e.fitness) if self.grid else None

    def to_dict(self) -> dict:
        return {"mode": "single", "coverage": self.coverage(),
                "elites": [e.to_dict() for e in self.all_elites()]}


def _dominates(a: Dict[str, float], b: Dict[str, float]) -> bool:
    """a 是否 Pareto 支配 b（所有目标 ≥ 且至少一个 >）。目标默认越大越好。"""
    ge = all(a.get(k, 0.0) >= b.get(k, 0.0) for k in set(a) | set(b))
    gt = any(a.get(k, 0.0) > b.get(k, 0.0) for k in set(a) | set(b))
    return ge and gt


@dataclass
class ParetoEntry:
    genome: dict
    objectives: Dict[str, float]
    behavior: BehaviorDescriptor
    cumulative_trial_count: int = 0


class MOMEArchive:
    """MOME 扩展：每格维持一个小 Pareto 前沿（多目标 + 多样性）。"""

    def __init__(self, max_front: int = 5):
        self.grid: Dict[Tuple[str, str, str], List[ParetoEntry]] = {}
        self.max_front = max_front

    def add(self, genome: dict, objectives: Dict[str, float], behavior: BehaviorDescriptor,
            trial_count: int = 0) -> bool:
        key = behavior.key()
        front = self.grid.setdefault(key, [])
        cand = ParetoEntry(genome, dict(objectives), behavior, int(trial_count))
        # 被现有前沿支配 → 拒绝
        if any(_dominates(e.objectives, cand.objectives) for e in front):
            return False
        # 移除被 cand 支配的旧成员
        front[:] = [e for e in front if not _dominates(cand.objectives, e.objectives)]
        front.append(cand)
        # 前沿过大 → 按目标之和裁剪（保留综合最优）
        if len(front) > self.max_front:
            front.sort(key=lambda e: sum(e.objectives.values()), reverse=True)
            del front[self.max_front:]
        return True

    def select_front(self, current_behavior: BehaviorDescriptor) -> List[ParetoEntry]:
        return self.grid.get(current_behavior.key(), [])

    def coverage(self) -> int:
        return len(self.grid)

    def all_fronts(self) -> Dict[Tuple[str, str, str], List[ParetoEntry]]:
        return self.grid


# 模块级单例（可选使用）
_archive_singleton: Optional[MAPElitesArchive] = None


def get_archive() -> MAPElitesArchive:
    global _archive_singleton
    if _archive_singleton is None:
        _archive_singleton = MAPElitesArchive()
    return _archive_singleton
