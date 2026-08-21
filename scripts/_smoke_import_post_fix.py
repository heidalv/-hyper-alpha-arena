# -*- coding: utf-8 -*-
"""导入冒烟：验证所有改动模块及其主要依赖可正常导入（启动等价检查）。"""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.core.tenant import set_system_identity
set_system_identity()

MODULES = [
    "backend.services.paper_trading_engine",
    "backend.services.scalp.structure_stop_calculator",
    "backend.services.scalp_factor_router",
    "backend.services.scalp.scalp_score_calibration",
    "backend.services.trading_analysts",
    "backend.services.full_auto_trading_service",
    "backend.services.full_auto.mlto_cycle",
    "backend.services.full_auto.midlong_position_manager",
    "backend.services.full_auto.loops.scalp_loop",
    "backend.services.profit_protection_manager",
    "backend.services.unified_risk_gate",
    "backend.services.signal_frequency_guard",
    "backend.services.reentry_cooldown",
    "backend.services.strategy_learning_service",
    "backend.services.training_orchestrator",
    "backend.services.full_auto.strategy_lifecycle",
    "backend.services.memory_ev_gate",
    "backend.services.tp_sl_authority",
    "backend.services.event_sourcing.phase2",
    "backend.services.unified_learning_service",
    "backend.database.models",
    "backend.database.connection",
    "backend.api.account_routes",
    "backend.services.reconcile_pnl_daily",
]
ok, bad = 0, []
for m in MODULES:
    try:
        __import__(m)
        ok += 1
    except Exception as e:
        bad.append((m, f"{type(e).__name__}: {e}"))
print(f"imported {ok}/{len(MODULES)}")
for m, e in bad:
    print(f"  FAIL {m}: {e}")
sys.exit(1 if bad else 0)
