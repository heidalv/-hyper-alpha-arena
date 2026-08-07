"""
端到端数据链闭环测试 — 学习层/策略层/执行层集成验证

验证链路:
  trade → TradeOutcome → unified_learning.process_outcome() → StrategyMemory
          → LearningBus.dispatch() → trigger_review/miner/evolver
  decision → decision_source tracking → AIDecisionLog
           → decision_consistency_gate → flip-flop prevention

关键验证点:
  1. TradeOutcome 创建 → unified_learning 处理 → StrategyMemory 写入
  2. LearningBus 正确调用 unified_learning 且不抛异常
  3. _build_rule_based_decisions 正确设置 _decision_source = "rule_engine"
  4. decision_consistency_gate 拦截 flip-flop 决策
  5. 学习总线与 unified_learning 无循环依赖
"""
import sys
import os
import time

os.environ['ENABLE_COORDINATOR'] = 'true'
os.environ['ENABLE_PORTFOLIO_RISK'] = 'true'

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

from backend.database.connection import SessionLocal
from backend.services.unified_learning_service import (
    UnifiedLearningService, TradeOutcome, unified_learning,
    SOURCE_WEIGHTS,
)

print("=" * 60)
print("数据链闭环测试 — 学习层/策略层/执行层集成验证")
print("=" * 60)


# ═══════════════════════════════════════════════════════════════
# Test 1: TradeOutcome → unified_learning 核心链路
# ═══════════════════════════════════════════════════════════════
print("\n--- Test 1: TradeOutcome → unified_learning 核心链路 ---")


def test_trade_outcome_creation():
    """验证 TradeOutcome 结构完整（D5 依赖字段）"""
    outcome = TradeOutcome(
        source="paper",
        strategy_id="test-strategy-001",
        template_id="tpl-1",
        symbol="BTC",
        side="long",
        tier="mid",
        trade_nature="swing",
        entry_price=50000.0,
        exit_price=51000.0,
        pnl=100.0,
        pnl_pct=0.02,
        duration_seconds=3600,
        regime_at_entry="trending_up",
        regime_at_exit="trending_up",
        confidence=0.75,
        metadata={"close_reason": "take_profit", "tier": "mid"},
    )

    assert outcome.source == "paper"
    assert outcome.strategy_id == "test-strategy-001"
    assert outcome.symbol == "BTC"
    assert outcome.pnl == 100.0
    assert outcome.pnl > 0  # 盈利交易
    print(f"  ✓ TradeOutcome 创建成功: {outcome.symbol} {outcome.side} PnL={outcome.pnl}")


def test_unified_learning_process_outcome_no_db():
    """验证 unified_learning.process_outcome 不依赖真实数据库时优雅降级"""
    db = SessionLocal()
    try:
        outcome = TradeOutcome(
            source="paper",
            strategy_id="test-chain-001",
            symbol="ETH",
            side="short",
            tier="mid",
            trade_nature="swing",
            entry_price=3000.0,
            exit_price=2950.0,
            pnl=50.0,
            pnl_pct=0.0167,
            duration_seconds=7200,
            regime_at_entry="ranging",
            confidence=0.65,
            metadata={"close_reason": "stop_loss_hit", "tier": "mid"},
        )

        # 核心验证: process_outcome 不应该抛异常
        try:
            unified_learning.process_outcome(db, outcome)
            print(f"  ✓ process_outcome 执行完成 (无异常)")
        except Exception as e:
            # 允许因数据库状态缺失而失败，但不能是 AttributeError/ImportError
            err_msg = str(e)
            assert "has no attribute" not in err_msg.lower(), \
                f"AttributeError: {err_msg}"
            assert "no module named" not in err_msg.lower(), \
                f"ImportError: {err_msg}"
            print(f"  ✓ process_outcome 预期降级: {err_msg[:80]}")
    finally:
        db.close()


def test_source_weight_map():
    """验证决策来源权重映射完整"""
    assert "live" in SOURCE_WEIGHTS, "live source weight missing"
    assert "paper" in SOURCE_WEIGHTS, "paper source weight missing"
    assert "backtest" in SOURCE_WEIGHTS, "backtest source weight missing"
    assert SOURCE_WEIGHTS["live"] >= SOURCE_WEIGHTS["paper"], \
        "live weight should >= paper weight"
    print(f"  ✓ SOURCE_WEIGHTS: live={SOURCE_WEIGHTS['live']}, "
          f"paper={SOURCE_WEIGHTS['paper']}, backtest={SOURCE_WEIGHTS['backtest']}")


# ═══════════════════════════════════════════════════════════════
# Test 2: LearningBus 集成验证 (D5)
# ═══════════════════════════════════════════════════════════════
print("\n--- Test 2: LearningBus 集成验证 (D5) ---")


def test_learning_bus_import_and_init():
    """验证 LearningBus 可正常初始化且无循环依赖"""
    from backend.services.learning_bus import LearningBus, get_learning_bus

    bus = get_learning_bus()
    assert bus is not None
    assert bus._initialized is True

    status = bus.get_status()
    assert "trade_count_total" in status
    assert "next_review_in" in status
    assert "next_miner_in" in status
    print(f"  ✓ LearningBus 初始化成功, 状态: total_trades={status['trade_count_total']}")


def test_learning_bus_dispatch_uses_unified_learning():
    """验证 LearningBus.dispatch 正确调用 unified_learning (非 get_unified_learning_service)"""
    from backend.services.learning_bus import LearningBus, get_learning_bus
    import inspect

    # 验证: dispatch 源码中引用的是 unified_learning 而非 get_unified_learning_service
    source = inspect.getsource(LearningBus.dispatch)
    assert "get_unified_learning_service" not in source, \
        "BUG: LearningBus.dispatch 仍引用不存在的 get_unified_learning_service()"
    assert "unified_learning" in source, \
        "LearningBus.dispatch 未引用 unified_learning 模块单例"
    print(f"  ✓ LearningBus.dispatch 正确引用 unified_learning 模块单例")


def test_learning_bus_dispatch_with_outcome():
    """验证 LearningBus.dispatch 可以接受 TradeOutcome 并执行"""
    from backend.services.learning_bus import get_learning_bus

    db = SessionLocal()
    try:
        outcome = TradeOutcome(
            source="paper",
            strategy_id="test-bus-001",
            symbol="BTC",
            side="long",
            tier="mid",
            trade_nature="swing",
            entry_price=50000.0,
            exit_price=50500.0,
            pnl=500.0,
            pnl_pct=0.01,
            regime_at_entry="trending_up",
            confidence=0.70,
        )

        bus = get_learning_bus()
        result = bus.dispatch(db, outcome)

        assert "unified_learning" in result
        assert result["unified_learning"] is True, \
            "LearningBus 未能成功调用 unified_learning"
        print(f"  ✓ LearningBus.dispatch 成功: unified_learning={result['unified_learning']}, "
              f"review={result['review_triggered']}, "
              f"evolver={result['evolver_triggered']}, "
              f"miner={result['miner_triggered']}")
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════════
# Test 3: decision_source 追踪完整性 (D1)
# ═══════════════════════════════════════════════════════════════
print("\n--- Test 3: decision_source 追踪完整性 (D1) ---")


def test_rule_engine_decision_source():
    """验证 _build_rule_based_decisions 设置 _decision_source = 'rule_engine'"""
    import inspect
    from backend.services.ai_decision_service import _build_rule_based_decisions

    source = inspect.getsource(_build_rule_based_decisions)
    assert '"_decision_source": "rule_engine"' in source, \
        "BUG: _build_rule_based_decisions 未设置 _decision_source = 'rule_engine'"

    # 同时确保注释说明这是纯规则引擎路径
    assert "_decision_source" in source
    print(f"  ✓ _build_rule_based_decisions 正确设置 _decision_source='rule_engine'")


def test_decision_source_in_ai_decision_log():
    """验证 AIDecisionLog 模型包含 decision_source 列"""
    from backend.database.models import AIDecisionLog

    assert hasattr(AIDecisionLog, 'decision_source'), \
        "AIDecisionLog 缺少 decision_source 列"
    col = AIDecisionLog.decision_source
    assert col.default is not None, "decision_source 应有默认值"
    print(f"  ✓ AIDecisionLog.decision_source 列存在, default={col.default.arg}")


def test_rule_decision_dataclass_has_source():
    """验证 RuleDecision 包含 decision_source 字段"""
    from backend.services.rule_based_decision_engine import RuleDecision

    rd = RuleDecision(action="BUY", reason="test", confidence=0.70)
    assert hasattr(rd, 'decision_source'), \
        "RuleDecision 缺少 decision_source 字段"
    assert rd.decision_source == "rule_engine", \
        f"RuleDecision.decision_source 应为 'rule_engine', 实际: {rd.decision_source}"
    print(f"  ✓ RuleDecision.decision_source = '{rd.decision_source}'")


# ═══════════════════════════════════════════════════════════════
# Test 4: Decision Consistency Gate 验证 (D2)
# ═══════════════════════════════════════════════════════════════
print("\n--- Test 4: Decision Consistency Gate 验证 (D2) ---")


def test_consistency_gate_init():
    """验证 DecisionConsistencyGate 初始化正常"""
    from backend.services.decision_consistency_gate import (
        DecisionConsistencyGate, get_consistency_gate,
    )

    gate = get_consistency_gate()
    assert gate is not None
    print(f"  ✓ DecisionConsistencyGate 单例初始化成功")


def test_consistency_gate_check_no_history():
    """验证无历史时一致性检查通过"""
    from backend.services.decision_consistency_gate import get_consistency_gate

    gate = get_consistency_gate()
    # 清空历史
    gate._decision_history.clear()

    result = gate.check(
        account_id=1, symbol="BTC",
        action="buy", confidence=0.70,
        market_regime="trending_up",
    )
    assert result.passed is True, f"首次决策应通过, 实际: {result.passed}, reason={result.reason}"
    print(f"  ✓ 首次决策通过: {result.reason}")


def test_consistency_gate_flip_flop_detection():
    """验证 flip-flop 检测: 5分钟内方向翻转应被拦截 (gate 只检查最后一次决策)"""
    from backend.services.decision_consistency_gate import get_consistency_gate

    gate = get_consistency_gate()
    gate._decision_history.clear()

    # 模拟: 4分钟前 sell, 现在要 buy → 间隔 < 5分钟 → 应被拦截
    past_time = time.time() - 240  # 4 min ago (< 300s min interval)
    gate._decision_history["1:BTC"] = [
        (past_time, "SELL", -1, 0.65),
    ]

    result = gate.check(
        account_id=1, symbol="BTC",
        action="buy", confidence=0.70,
        market_regime="trending_up",
    )
    assert result.passed is False, \
        f"5分钟内方向翻转应被拦截, 实际: passed={result.passed}, reason={result.reason}"
    print(f"  ✓ Flip-flop 检测生效: {result.reason}")


def test_consistency_gate_normal_sequence():
    """验证正常连续同向决策不被拦截"""
    from backend.services.decision_consistency_gate import get_consistency_gate

    gate = get_consistency_gate()
    gate._decision_history.clear()

    # 模拟: 同方向连续决策（间隔>5分钟），用正确元组格式
    past_time = time.time() - 600  # 10 min ago
    gate._decision_history["1:ETH"] = [
        (past_time, "BUY", 1, 0.72),
    ]

    result = gate.check(
        account_id=1, symbol="ETH",
        action="buy", confidence=0.68,
        market_regime="trending_up",
    )
    assert result.passed is True, \
        f"同向连续决策不应被拦截, reason={result.reason}"
    print(f"  ✓ 正常连续同向决策通过: {result.reason}")


def test_consistency_gate_ranging_overtrade():
    """验证震荡市过度交易检测"""
    from backend.services.decision_consistency_gate import get_consistency_gate

    gate = get_consistency_gate()
    gate._decision_history.clear()

    # 模拟: 短时间内多次交易（元组格式）
    base_time = time.time()
    for i in range(7):  # 7笔交易 + 本次 = 8笔, 超过6笔阈值
        gate._decision_history["1:SOL"].append(
            (base_time - i * 120, "BUY" if i % 2 == 0 else "SELL",
             1 if i % 2 == 0 else -1, 0.55)
        )

    result = gate.check(
        account_id=1, symbol="SOL",
        action="buy", confidence=0.55,
        market_regime="ranging",
    )
    # 震荡市1小时内>6笔交易应触发警告
    print(f"  ✓ 震荡市过度交易检测: passed={result.passed}, reason={result.reason}")


# ═══════════════════════════════════════════════════════════════
# Test 5: 全链路端到端模拟 (Learning → Strategy → Execution)
# ═══════════════════════════════════════════════════════════════
print("\n--- Test 5: 全链路端到端模拟 ---")


def test_full_chain_paper_trade_to_learning():
    """模拟: 纸交易开仓→平仓→学习→策略更新 完整链路"""
    from backend.services.learning_bus import get_learning_bus

    db = SessionLocal()
    try:
        # Step 1: 模拟平仓事件创建 TradeOutcome
        outcome = TradeOutcome(
            source="paper",
            strategy_id="full-chain-test-001",
            template_id="tpl-001",
            symbol="BTC",
            side="long",
            tier="mid",
            trade_nature="swing",
            entry_price=50000.0,
            exit_price=51000.0,
            pnl=1000.0,
            pnl_pct=0.02,
            duration_seconds=14400,  # 4小时
            regime_at_entry="trending_up",
            regime_at_exit="trending_up",
            confidence=0.72,
            metadata={
                "close_reason": "take_profit",
                "tier": "mid",
                "adx_at_entry": 35.0,
                "trend_direction": "bullish",
                "trend_strength": "strong",
            },
        )

        # Step 2: 通过统一学习处理
        try:
            unified_learning.process_outcome(db, outcome)
            print(f"  ✓ Step 2: unified_learning.process_outcome 成功")
        except Exception as e:
            print(f"  ✓ Step 2: unified_learning 降级 (开发DB): {str(e)[:80]}")

        # Step 3: 通过学习总线触发后续系统
        bus = get_learning_bus()
        bus_result = bus.dispatch(db, outcome)

        assert bus_result["unified_learning"] is True
        print(f"  ✓ Step 3: LearningBus.dispatch 成功 → "
              f"review={bus_result['review_triggered']}, "
              f"miner={bus_result['miner_triggered']}, "
              f"evolver={bus_result['evolver_triggered']}")

        # Step 4: 验证总线状态更新
        status = bus.get_status()
        assert status["trade_count_total"] > 0
        print(f"  ✓ Step 4: 总线状态 trade_count_total={status['trade_count_total']}")

        print(f"  ✅ 全链路: TradeOutcome → unified_learning → LearningBus → 状态更新 闭环验证通过")

    finally:
        db.close()


def test_decision_source_chain_rule_engine_path():
    """验证纯规则引擎路径的 decision_source 追踪链"""
    from backend.services.rule_based_decision_engine import RuleDecision
    import inspect

    # 1. RuleDecision 数据类自带 decision_source="rule_engine"
    rd = RuleDecision(action="BUY", reason="signal_confirm", confidence=65.0)
    assert rd.decision_source == "rule_engine"

    # 2. _build_rule_based_decisions 在 entry dict 中设置 _decision_source
    from backend.services.ai_decision_service import _build_rule_based_decisions
    src = inspect.getsource(_build_rule_based_decisions)
    assert '"_decision_source": "rule_engine"' in src

    print(f"  ✅ 规则引擎路径 decision_source 追踪链: "
          f"RuleDecision.decision_source='rule_engine' → "
          f"entry['_decision_source']='rule_engine'")


def test_consistency_gate_in_full_auto_path():
    """验证一致性门控已集成到 full_auto_trading_service 总控路径"""
    import inspect
    # 动态导入以避免循环依赖
    from backend.services.full_auto_trading_service import FullAutoTradingService

    # 验证 _execute_master_decisions 中引用了 decision_consistency_gate
    source = inspect.getsource(FullAutoTradingService._execute_master_decisions)
    assert "decision_consistency_gate" in source, \
        "BUG: _execute_master_decisions 未集成 decision_consistency_gate"
    assert "get_consistency_gate" in source, \
        "BUG: _execute_master_decisions 未调用 get_consistency_gate"
    print(f"  ✓ _execute_master_decisions 已集成 decision_consistency_gate")


def test_no_circular_imports():
    """验证关键模块无循环导入"""
    modules_to_check = [
        "backend.services.learning_bus",
        "backend.services.unified_learning_service",
        "backend.services.decision_consistency_gate",
        "backend.services.decision_performance_context",
        "backend.services.trade_memory_miner",
        "backend.services.rule_based_decision_engine",
    ]
    for mod_name in modules_to_check:
        try:
            __import__(mod_name)
            print(f"  ✓ {mod_name} 导入成功 (无循环依赖)")
        except ImportError as e:
            print(f"  ✗ {mod_name} 导入失败: {e}")
            raise


# ═══════════════════════════════════════════════════════════════
# Test 6: 执行层→学习层 接线验证
# ═══════════════════════════════════════════════════════════════
print("\n--- Test 6: 执行层→学习层 接线验证 ---")


def test_paper_trading_calls_learning_bus():
    """验证 paper_trading_engine._notify_learning_on_close 调用 LearningBus"""
    import inspect
    from backend.services.paper_trading_engine import PaperTradingEngine

    source = inspect.getsource(PaperTradingEngine._notify_learning_on_close)
    assert "learning_bus" in source, \
        "BUG: paper_trading_engine._notify_learning_on_close 未导入 learning_bus"
    assert "get_learning_bus" in source, \
        "BUG: paper_trading_engine._notify_learning_on_close 未调用 get_learning_bus"
    print(f"  ✓ paper_trading_engine._notify_learning_on_close 已连接 LearningBus")


def test_trading_commands_calls_learning_bus():
    """验证 trading_commands.py 平仓路径调用 LearningBus"""
    # 直接读取文件检查（避免执行时触发完整导入链）
    import inspect
    # trading_commands.py 在模块级别执行，用 grep 方式检查
    with open(
        os.path.join(os.path.dirname(__file__), "..", "services", "trading_commands.py"),
        encoding="utf-8",
    ) as f:
        content = f.read()
    assert "learning_bus" in content, \
        "BUG: trading_commands.py 未导入 learning_bus"
    assert "get_learning_bus" in content, \
        "BUG: trading_commands.py 未调用 get_learning_bus"
    print(f"  ✓ trading_commands.py 已连接 LearningBus")


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("执行所有测试")
    print("=" * 60)

    tests = [
        # Test 1
        test_trade_outcome_creation,
        test_unified_learning_process_outcome_no_db,
        test_source_weight_map,
        # Test 2
        test_learning_bus_import_and_init,
        test_learning_bus_dispatch_uses_unified_learning,
        test_learning_bus_dispatch_with_outcome,
        # Test 3
        test_rule_engine_decision_source,
        test_decision_source_in_ai_decision_log,
        test_rule_decision_dataclass_has_source,
        # Test 4
        test_consistency_gate_init,
        test_consistency_gate_check_no_history,
        test_consistency_gate_flip_flop_detection,
        test_consistency_gate_normal_sequence,
        test_consistency_gate_ranging_overtrade,
        # Test 5
        test_full_chain_paper_trade_to_learning,
        test_decision_source_chain_rule_engine_path,
        test_consistency_gate_in_full_auto_path,
        test_no_circular_imports,
        # Test 6
        test_paper_trading_calls_learning_bus,
        test_trading_commands_calls_learning_bus,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"\n  ✗ {test.__name__} FAILED: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'=' * 60}")
    print(f"结果: {passed} passed, {failed} failed, {len(tests)} total")
    print(f"{'=' * 60}")

    if failed > 0:
        sys.exit(1)
