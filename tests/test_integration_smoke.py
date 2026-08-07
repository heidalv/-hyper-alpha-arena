"""
端到端集成冒烟测试（P0-P5 全模块导入）。

验证所有阶段产出的模块可协同导入，无循环依赖、无 ImportError。
这是"集成验证"——不是行为测试（行为在各阶段单测覆盖），而是装配完整性。
"""
from __future__ import annotations

import importlib

import pytest

# P0-P5 全部新模块（按层组织）
ALL_NEW_MODULES = [
    # P0 地基
    "backend.config.env_registry",
    # P1 因子纪律
    "backend.services.factor_engine.expr.ops",
    "backend.services.factor_engine.expr.audit",
    "backend.services.factor_engine.expr.parser",
    "backend.services.factor_engine.expr.frac_diff",
    "backend.services.factor_engine.lifecycle",
    "backend.services.factor_engine.capacity",
    "backend.services.factor_engine.perp_factors",
    "backend.services.factor_engine.dataset_cache",
    "backend.services.factor_engine.evaluation",
    "backend.services.factor_engine.purge_pipeline",
    "backend.services.labeling.triple_barrier",
    # P2 热路径
    "backend.services.contracts.types",
    "backend.services.contracts.bridge",
    "backend.services.bus.hot_ring",
    "backend.services.bus.alpha_bus",
    "backend.services.cache.single_source",
    "backend.services.exchange.l2_rebuilder",
    "backend.services.exchange.time_align",
    "backend.services.data.derivatives_collector",
    "backend.services.data.onchain_collector",
    "backend.services.alpha.regime_refined",
    "backend.services.alpha.universe",
    # P3 执行
    "backend.services.execution.client",
    "backend.services.execution.algo",
    "backend.services.execution.circuit_breaker",
    "backend.services.execution.backtest_client",
    # P4 进化
    "backend.services.evolution.drift_watcher",
    "backend.services.evolution.online_weights",
    "backend.services.evolution.shadow_judge",
    "backend.services.evolution.hpo_orchestrator",
    "backend.services.evolution.alpha_miner",
    "backend.services.eval.adversarial_validation",
    "backend.services.learning_core.mechanism_router",
    "backend.services.alpha.ensemble",
    # P5 中长线协同
    "backend.services.agents.midlong.agent",
    "backend.services.portfolio.unified",
    "backend.services.portfolio.cross_horizon_breaker",
]


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("module_name", ALL_NEW_MODULES)
def test_module_importable(module_name):
    """每个新模块可独立导入（无 ImportError / 循环依赖）。"""
    importlib.import_module(module_name)


def test_all_layer_count():
    """确认 P0-P5 五层 + 契约层全部就位。"""
    importlib.import_module("backend.services.contracts.types")
    importlib.import_module("backend.services.factor_engine.expr.ops")
    importlib.import_module("backend.services.execution.client")
    importlib.import_module("backend.services.evolution.drift_watcher")
    importlib.import_module("backend.services.portfolio.unified")


def test_cross_layer_dataclass_compatible():
    """契约 dataclass 跨层可用（L2→L3→L4→L5 数据流不破坏）。"""
    from backend.services.cache.single_source import SingleSourceCache
    from backend.services.contracts.types import (
        Instrument,
        MarketSnapshot,
    )
    inst = Instrument(symbol="BTC-PERP", venue="binance", kind="perp")
    snap = MarketSnapshot(ts_ns=1, instrument=inst, bid=50000, ask=50001,
                          mid=50000.5, last_trade=50000, last_trade_size=0.1)
    cache = SingleSourceCache()
    cache.update_snapshot(snap)
    assert cache.get_snapshot("BTC-PERP") is not None


def test_evolution_pipeline_assembles():
    """进化闭环可装配（drift→adapt→shadow judge→lifecycle）。"""
    from backend.services.evolution.drift_watcher import DriftWatcher
    from backend.services.evolution.shadow_judge import ShadowJudge
    dw = DriftWatcher()
    sj = ShadowJudge()
    # 二者可共存（无循环依赖）
    assert dw is not None
    assert sj is not None


def test_execution_dual_track_assembles():
    """双轨执行 + 熔断可装配。"""
    from backend.services.execution.circuit_breaker import ExecutionCircuitBreaker
    cb = ExecutionCircuitBreaker()
    assert cb.state.value == "NORMAL"
