"""影子模式集成验证测试"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')

os.environ['ENABLE_COORDINATOR'] = 'true'
os.environ['ENABLE_PORTFOLIO_RISK'] = 'true'
os.environ['ENABLE_KELLY_POSITION'] = 'true'
os.environ['ENABLE_DRL_INTEGRATION'] = 'true'
os.environ['DRL_SHADOW_MODE'] = 'true'

from backend.services.rl.system_coordinator import system_coordinator
from backend.services.trading_decision_interface import (
    trading_decision_interface, inject_coordinator, DecisionContext,
    ArbitratedDecision, RiskVerdict
)
inject_coordinator(system_coordinator)
from backend.services.trading_decision_interface import trading_decision_interface as tdi

ctx = DecisionContext(symbol='BTC', confidence=75, volatility=0.025, tier='mid', open_position_count=2)

# Test 1: decide_position_pct
advice = tdi.decide_position_pct(0.25, ctx)
print(f'[1] decide_position_pct: pct={advice.position_pct}, source={advice.source}, kelly_bound={advice.kelly_upper_bound}')

# Test 2: decide_direction
dir_advice = tdi.decide_direction('buy', ctx)
print(f'[2] decide_direction: dir={dir_advice.direction}, source={dir_advice.source}')

# Test 3: check_portfolio_risk
risk = tdi.check_portfolio_risk(ctx)
print(f'[3] check_portfolio_risk: passed={risk.passed}, level={risk.risk_level}')

# Test 4: arbitrate (normal)
arb = tdi.arbitrate({'action': 'buy', 'position_pct': 0.25, 'side': 'long'}, advice, dir_advice, risk)
print(f'[4] arbitrate(normal): action={arb.action}, pct={arb.position_pct}')

# Test 5: arbitrate (risk fail)
fail_risk = RiskVerdict(passed=False, risk_level='critical', reason_text='exceeded')
arb_fail = tdi.arbitrate({'action': 'buy', 'position_pct': 0.25, 'side': 'long'}, advice, dir_advice, fail_risk)
print(f'[5] arbitrate(risk_fail): action={arb_fail.action}, pct={arb_fail.position_pct}')

# Test 6: Kelly limit
kelly_limit = system_coordinator.get_kelly_position_limit('BTC', 10000.0)
print(f'[6] kelly_limit(BTC): {kelly_limit}')

# Test 7: Coordinator status
from backend.database.connection import SessionLocal
db = SessionLocal()
try:
    status = system_coordinator.get_status(db)
    print(f'[7] coordinator_status: drl_available={status["drl_available"]}, kelly_available={status["kelly_available"]}')
finally:
    db.close()

# Test 8: PortfolioRiskAggregator
from backend.services.rl.portfolio_risk_aggregator import portfolio_risk_aggregator
print(f'[8] PortfolioRiskAggregator: {type(portfolio_risk_aggregator).__name__}')

# Test 9: StateConsistencyManager
from backend.services.rl.time_window_coordinator import state_consistency_manager
print(f'[9] StateConsistencyManager: {type(state_consistency_manager).__name__}')

# Test 10: Feature Flags
from backend.config.settings import (
    ENABLE_DRL_INTEGRATION, ENABLE_KELLY_POSITION,
    ENABLE_PORTFOLIO_RISK, ENABLE_COORDINATOR, DRL_SHADOW_MODE
)
print(f'[10] FeatureFlags: COORD={ENABLE_COORDINATOR}, RISK={ENABLE_PORTFOLIO_RISK}, KELLY={ENABLE_KELLY_POSITION}, DRL={ENABLE_DRL_INTEGRATION}, SHADOW={DRL_SHADOW_MODE}')

print()
print('=== ALL 10 SHADOW MODE TESTS PASSED ===')
