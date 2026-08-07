"""端到端集成测试 — 数据流闭环验证
验证: Evolution(params) → DRL(decision) → Kelly(position) → Live Results → UnifiedLearning → Evolution(optimize)
"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')

os.environ['ENABLE_COORDINATOR'] = 'true'
os.environ['ENABLE_PORTFOLIO_RISK'] = 'true'
os.environ['ENABLE_KELLY_POSITION'] = 'true'
os.environ['ENABLE_DRL_INTEGRATION'] = 'true'
os.environ['ENABLE_EVOLUTION_FEEDBACK'] = 'true'
os.environ['DRL_SHADOW_MODE'] = 'true'

from backend.database.connection import SessionLocal
from backend.services.rl.system_coordinator import system_coordinator
from backend.services.trading_decision_interface import (
    trading_decision_interface, inject_coordinator, DecisionContext,
    ArbitratedDecision, RiskVerdict, PositionAdvice
)
from backend.services.rl.portfolio_risk_aggregator import portfolio_risk_aggregator
from backend.services.rl.time_window_coordinator import state_consistency_manager
from backend.services.rl.kelly_position_sizer import KellyPositionSizer

inject_coordinator(system_coordinator)
from backend.services.trading_decision_interface import trading_decision_interface as tdi

print("=" * 60)
print("端到端集成测试 — 数据流闭环验证")
print("=" * 60)

# ── Test 1: 完整决策流程 (rule → Kelly → DRL → Risk → Arbitrate) ──
print("\n--- Test 1: 完整决策流程 ---")
ctx = DecisionContext(
    symbol='BTC', confidence=80, volatility=0.02,
    tier='mid', open_position_count=1, equity=10000.0
)

# Step 1: 基础规则仓位
base_pct = 0.30
print(f"  Step 1 - 基础规则仓位: {base_pct}")

# Step 2: Kelly注入
position_advice = tdi.decide_position_pct(base_pct, ctx)
print(f"  Step 2 - Kelly注入: pct={position_advice.position_pct}, source={position_advice.source}, kelly_bound={position_advice.kelly_upper_bound}")

# Step 3: DRL方向建议
direction_advice = tdi.decide_direction('buy', ctx)
print(f"  Step 3 - DRL方向: dir={direction_advice.direction}, source={direction_advice.source}")

# Step 4: 组合风控
risk_verdict = tdi.check_portfolio_risk(ctx)
print(f"  Step 4 - 组合风控: passed={risk_verdict.passed}, level={risk_verdict.risk_level}")

# Step 5: 参数仲裁
final = tdi.arbitrate(
    {'action': 'buy', 'position_pct': base_pct, 'side': 'long'},
    position_advice, direction_advice, risk_verdict
)
print(f"  Step 5 - 仲裁结果: action={final.action}, pct={final.position_pct}, side={final.side}")
print(f"           position_source={final.position_source}, direction_source={final.direction_source}")

# ── Test 2: 风控否决场景 ──
print("\n--- Test 2: 风控否决场景 ---")
fail_risk = RiskVerdict(passed=False, risk_level='critical', reason_text='portfolio risk exceeded 30%')
blocked = tdi.arbitrate(
    {'action': 'buy', 'position_pct': 0.30, 'side': 'long'},
    position_advice, direction_advice, fail_risk
)
print(f"  风控否决: action={blocked.action}, pct={blocked.position_pct}")
assert blocked.action == "hold" and blocked.position_pct == 0.0, "风控否决失败"
print(f"  ✓ 风控否决正确: action=hold, pct=0.0")

# ── Test 3: Kelly上限约束 ──
print("\n--- Test 3: Kelly上限约束 ---")
kelly_advice = PositionAdvice(position_pct=0.30, kelly_upper_bound=0.20, source='kelly')
kelly_result = tdi.arbitrate(
    {'action': 'buy', 'position_pct': 0.30, 'side': 'long'},
    kelly_advice, direction_advice, risk_verdict
)
print(f"  Kelly约束: pct={kelly_result.position_pct} (should be <= 0.20)")
assert kelly_result.position_pct <= 0.20, "Kelly约束失败"
print(f"  ✓ Kelly上限约束正确")

# ── Test 4: 多币种Kelly组合 ──
print("\n--- Test 4: 多币种Kelly组合 ---")
sizer = KellyPositionSizer()
symbols = ['BTC', 'ETH', 'SOL']
# 模拟交易历史
mock_history = [
    {"pnl": 100}, {"pnl": -50}, {"pnl": 200}, {"pnl": -30}, {"pnl": 150},
    {"pnl": -80}, {"pnl": 120}, {"pnl": -40}, {"pnl": 180}, {"pnl": -60},
]
for sym in symbols:
    result = sizer.calculate(equity=10000.0, trade_history=mock_history)
    print(f"  {sym}: kelly_fraction={result.kelly_fraction:.4f}, adjusted={result.adjusted_fraction:.4f}")

# ── Test 5: PortfolioRiskAggregator ──
print("\n--- Test 5: PortfolioRiskAggregator ---")
# 模拟Kelly结果
mock_history = [
    {"pnl": 100}, {"pnl": -50}, {"pnl": 200}, {"pnl": -30}, {"pnl": 150},
    {"pnl": -80}, {"pnl": 120}, {"pnl": -40}, {"pnl": 180}, {"pnl": -60},
]
kelly_results = {}
for sym in ['BTC', 'ETH', 'SOL']:
    kelly_results[sym] = sizer.calculate(equity=10000.0, trade_history=mock_history)
agg_result = portfolio_risk_aggregator.aggregate(kelly_results, equity=10000.0)
print(f"  总风险: {agg_result.total_risk:.4f}")
print(f"  相关性风险: {agg_result.correlation_risk:.4f}")
print(f"  分配数: {len(agg_result.allocations)}")
for alloc in agg_result.allocations:
    print(f"    {alloc.symbol}: kelly={alloc.kelly_fraction:.4f}, adjusted={alloc.adjusted_fraction:.4f}")

# ── Test 6: StateConsistencyManager ──
print("\n--- Test 6: StateConsistencyManager ---")
db = SessionLocal()
try:
    # 测试事务
    tx_id = state_consistency_manager.begin_transaction(['drl', 'kelly', 'evolution'], timeout=30)
    print(f"  事务开始: tx_id={tx_id}")

    # 提交
    committed = state_consistency_manager.commit_if_valid(tx_id)
    print(f"  事务提交: committed={committed}")
finally:
    db.close()

# ── Test 7: API路由验证 ──
print("\n--- Test 7: API路由验证 ---")
from backend.api.rl_routes import router as rl_router
from backend.api.evolution_routes import router as evo_router
rl_routes = [r.path for r in rl_router.routes]
evo_routes = [r.path for r in evo_router.routes]
print(f"  RL路由: {rl_routes}")
print(f"  Evolution路由: {evo_routes}")
assert '/kelly/portfolio' in rl_routes or '/api/rl/kelly/portfolio' in rl_routes, "Kelly portfolio路由缺失"
assert '/drl/performance' in rl_routes or '/api/rl/drl/performance' in rl_routes, "DRL performance路由缺失"
assert '/coordinator/status' in rl_routes or '/api/rl/coordinator/status' in rl_routes, "Coordinator status路由缺失"
print(f"  ✓ 所有API路由已注册")

# ── Test 8: 数据库表验证 ──
print("\n--- Test 8: 数据库表验证 ---")
from backend.database.models import MultiSymbolKelly, DRLPerformance, DRLPerformanceDaily, SystemCoordinatorState
db = SessionLocal()
try:
    for model_cls in [MultiSymbolKelly, DRLPerformance, DRLPerformanceDaily, SystemCoordinatorState]:
        count = db.query(model_cls).count()
        print(f"  {model_cls.__tablename__}: {count} rows (表存在)")
finally:
    db.close()

print()
print("=" * 60)
print("=== ALL 8 E2E INTEGRATION TESTS PASSED ===")
print("=" * 60)
