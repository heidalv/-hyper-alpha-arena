"""strategy_orchestrator —— 多层级策略编排包。

聚合长线规划、短线战术、风险分配、目标设定四个子模块，统一对外暴露常用符号，
避免调用方（如 unified_data_pool）逐个深路径导入。

历史上本文件为空，导致 `from services.strategy_orchestrator import
get_short_term_tactician, ShortTermContext` 失败，短线战术分析整条链路被静默跳过。
"""
from . import long_term_planner, short_term_tactician, risk_allocator, goal_setter
from .long_term_planner import (
    LongTermPlanner,
    PlanningResult,
    MarketCycle,
    CycleIndicators,
    RiskBudget,
    long_term_planner as long_term_planner_instance,
)
from .short_term_tactician import (
    ShortTermTactician,
    ShortTermContext,
    TacticalConfig,
    TacticalSignal,
    TacticalAction,
    EntryTiming,
    MarketCondition,
    get_short_term_tactician,
)

__all__ = [
    # 子模块
    "long_term_planner",
    "short_term_tactician",
    "risk_allocator",
    "goal_setter",
    # 长线
    "LongTermPlanner",
    "PlanningResult",
    "MarketCycle",
    "CycleIndicators",
    "RiskBudget",
    "long_term_planner_instance",
    # 短线
    "ShortTermTactician",
    "ShortTermContext",
    "TacticalConfig",
    "TacticalSignal",
    "TacticalAction",
    "EntryTiming",
    "MarketCondition",
    "get_short_term_tactician",
]
