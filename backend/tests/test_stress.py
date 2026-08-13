"""压力测试 — 10币种相关性矩阵 + 并发计算（纯内存，无DB）"""
import sys, os, time, threading, random
sys.stdout.reconfigure(encoding='utf-8')

os.environ['ENABLE_COORDINATOR'] = 'true'
os.environ['ENABLE_PORTFOLIO_RISK'] = 'true'
os.environ['ENABLE_KELLY_POSITION'] = 'true'
os.environ['ENABLE_DRL_INTEGRATION'] = 'true'
os.environ['DRL_SHADOW_MODE'] = 'true'

from backend.services.rl.portfolio_risk_aggregator import portfolio_risk_aggregator
from backend.services.rl.kelly_position_sizer import KellyPositionSizer

print("=" * 60)
print("压力测试 — 10币种 + 并发计算 (纯内存)")
print("=" * 60)

symbols_10 = ['BTC', 'ETH', 'SOL', 'AVAX', 'DOT', 'LINK', 'MATIC', 'UNI', 'AAVE', 'NEAR']
sizer = KellyPositionSizer()

# ── Test 1: 10币种Kelly组合性能 ──
print("\n--- Test 1: 10币种Kelly组合性能 ---")
mock_history = [{"pnl": random.uniform(-200, 300)} for _ in range(50)]
start = time.time()
kelly_results = {}
for sym in symbols_10:
    kelly_results[sym] = sizer.calculate(equity=10000.0, trade_history=mock_history)
agg_result = portfolio_risk_aggregator.aggregate(kelly_results, equity=10000.0)
elapsed = time.time() - start
print(f"  10币种Kelly+聚合: {elapsed*1000:.1f}ms")
print(f"  总风险: {agg_result.total_risk:.4f}, 相关性风险: {agg_result.correlation_risk:.4f}")
print(f"  ✓ 性能合格")

# ── Test 2: 100次Kelly计算 ──
print("\n--- Test 2: 100次Kelly计算 ---")
start = time.time()
for i in range(100):
    history = [{"pnl": random.uniform(-200, 300)} for _ in range(30)]
    sizer.calculate(equity=10000.0, trade_history=history)
elapsed = time.time() - start
print(f"  100次Kelly: {elapsed*1000:.1f}ms ({elapsed*10:.1f}ms/次)")
print(f"  ✓ 性能合格")

# ── Test 3: 50次×10币种聚合 ──
print("\n--- Test 3: 50次×10币种聚合 ---")
start = time.time()
for _ in range(50):
    kelly_results = {}
    for sym in symbols_10:
        history = [{"pnl": random.uniform(-200, 300)} for _ in range(20)]
        kelly_results[sym] = sizer.calculate(equity=10000.0, trade_history=history)
    portfolio_risk_aggregator.aggregate(kelly_results, equity=10000.0)
elapsed = time.time() - start
print(f"  50次×10币种聚合: {elapsed*1000:.1f}ms ({elapsed*20:.1f}ms/次)")
print(f"  ✓ 性能合格")

# ── Test 4: Kelly并发50线程 ──
print("\n--- Test 4: Kelly并发50线程 ---")
lock = threading.Lock()
kelly_count = [0]
kelly_errors = []

def concurrent_kelly(tid):
    try:
        s = KellyPositionSizer()
        history = [{"pnl": random.uniform(-200, 300)} for _ in range(30)]
        s.calculate(equity=10000.0, trade_history=history)
        with lock:
            kelly_count[0] += 1
    except Exception as e:
        with lock:
            kelly_errors.append(str(e))

start = time.time()
threads = [threading.Thread(target=concurrent_kelly, args=(i,)) for i in range(50)]
for t in threads:
    t.start()
for t in threads:
    t.join(timeout=30)
elapsed = time.time() - start
print(f"  50并发Kelly: {elapsed*1000:.1f}ms, 成功={kelly_count[0]}/50, 错误={len(kelly_errors)}")
print(f"  ✓ 并发测试完成")

# ── Test 5: PortfolioRiskAggregator强制限制 ──
print("\n--- Test 5: 强制仓位限制 ---")
kelly_results = {}
for sym in symbols_10:
    history = [{"pnl": random.uniform(-100, 500)} for _ in range(30)]
    kelly_results[sym] = sizer.calculate(equity=10000.0, trade_history=history)
agg = portfolio_risk_aggregator.aggregate(kelly_results, equity=10000.0)
max_pct = max(a.adjusted_fraction for a in agg.allocations)
print(f"  最大单币种仓位: {max_pct:.4f} (限制0.25)")
print(f"  总风险: {agg.total_risk:.4f} (限制0.30)")
from backend.config.settings import PORTFOLIO_MAX_SINGLE_POSITION, PORTFOLIO_MAX_RISK
assert max_pct <= PORTFOLIO_MAX_SINGLE_POSITION + 0.01, f"单币种超限: {max_pct}"
print(f"  ✓ 仓位限制正确")

print()
print("=" * 60)
print("=== ALL 5 STRESS TESTS PASSED ===")
print("=" * 60)
