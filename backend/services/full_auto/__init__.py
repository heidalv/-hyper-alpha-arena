"""
full_auto 目标包骨架（整改#8）。

规划终态（对标文档 §整改#8）：
  full_auto/
  ├── orchestrator.py   # FullAutoOrchestrator — 仅协调、依赖注入，无业务逻辑
  ├── loops/            # coordinator/midlong/scalp/arbitrage/learning/maintenance 各独立循环
  └── state.py          # 显式 State 对象（本轮已落地，衔接整改#9 事件溯源）

铁律 G3：特征化测试网（§10.9 C1–C7）已全绿；scalp loop 已拆至 `loops/scalp_loop.py`，
monolith 保留 thin shim 转发。
"""
from backend.services.full_auto.state import FullAutoState
from backend.services.full_auto.intent_snapshot import (
    LoopTickSnapshot, TradeIntent, assert_golden_match, canonical_json,
)
from backend.services.full_auto.monolith_replay import (
    build_monolith_view, ops_to_events, replay_matches_monolith,
)
from backend.services.full_auto.loops.scalp_loop import run_scalp_independent
from backend.services.full_auto.loops.midlong_loop import run_midlong_independent
from backend.services.full_auto.loops.coordinator_loop import run_unified_loop
from backend.services.full_auto.loops.trading_cycle_loop import run_trading_cycle
from backend.services.full_auto.loops.arbitrage_loop import run_arbitrage_tick, run_rebate_arb_tick
from backend.services.full_auto.loops.learning_loop import run_learning_integration, run_mlto_learning_tick
from backend.services.full_auto.loops.maintenance_loop import run_maintenance_cycle
from backend.services.full_auto.orchestrator import FullAutoOrchestrator, get_orchestrator

__all__ = [
    "FullAutoState",
    "FullAutoOrchestrator",
    "get_orchestrator",
    "LoopTickSnapshot",
    "TradeIntent",
    "assert_golden_match",
    "canonical_json",
    "build_monolith_view",
    "ops_to_events",
    "replay_matches_monolith",
    "run_scalp_independent",
    "run_midlong_independent",
    "run_unified_loop",
    "run_trading_cycle",
    "run_arbitrage_tick",
    "run_rebate_arb_tick",
    "run_learning_integration",
    "run_mlto_learning_tick",
    "run_maintenance_cycle",
]
