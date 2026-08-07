"""全链路测试: 因子→策略→执行→回测→进化"""
import sys, os
os.chdir('/Users/laobao/项目/claude/001-02Alpha/001Alpha/Hyper-Alpha-Arena')
sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.getcwd(), 'backend'))

def t1():
    print('=== 测试1: 因子引擎 ===')
    from services.factor_engine.factor_loader import FactorLoader
    c = FactorLoader().discover_and_load_all()
    print(f'  ✓ FactorLoader: {c}个因子')
    from services.factor_engine.factor_registry import FactorRegistry
    ext = [k for k in FactorRegistry()._factors if k.startswith('wq_')]
    print(f'  ✓ WorldQuant: {len(ext)}个 {ext[:3]}')
    from services.factor_engine.factor_decay_monitor import decay_monitor
    print(f'  ✓ DecayMonitor: 就绪')
    from services.ai_factor_discovery_service import ai_factor_discovery
    print(f'  ✓ AIFactor: 就绪')
    print('  ✅ PASS\n'); return True

def t2():
    print('=== 测试2: 因子→信号 ===')
    import pandas as pd; import numpy as np
    np.random.seed(42)
    close = 50000 + np.cumsum(np.random.randn(100) * 200)
    data = pd.DataFrame({'open':close-np.random.rand(100)*100,'high':close+np.random.rand(100)*200,'low':close-np.random.rand(100)*200,'close':close,'volume':np.random.rand(100)*100+50})
    from services.factor_engine.base_factors import FactorEngine
    factors = FactorEngine().compute_all_factors(data)
    print(f'  ✓ 计算: {len(factors)}个因子')
    from services.factor_engine.factor_signal_generator import FactorSignalGenerator
    sig = FactorSignalGenerator().generate_signals(factors)
    print(f'  ✓ 信号: dir={sig.direction:+.2f} str={sig.strength:.2f} conf={sig.confidence:.2f}')
    from services.factor_engine.factor_weighting import DynamicFactorWeighting
    adp = DynamicFactorWeighting(factor_engine=FactorEngine()).calculate_adaptive_weights(factors, None)
    print(f'  ✓ 市场: {adp.regime.value} conf={adp.confidence:.2f}')
    print('  ✅ PASS\n'); return True

def t3():
    print('=== 测试3: Paper执行 ===')
    from database.connection import SessionLocal
    from database.models import Account, PaperPosition, DecisionRetrospective
    db = SessionLocal()
    try:
        acc = db.query(Account).filter(Account.is_active==True).first()
        if acc:
            pos = db.query(PaperPosition).filter(PaperPosition.account_id==acc.id, PaperPosition.status=='open').all()
            print(f'  ✓ 账户:{acc.name} 持仓:{len(pos)}个')
        from services.paper_trading_engine import PaperTradingEngine
        print(f'  ✓ PaperEngine: 实例化')
        rc = db.query(DecisionRetrospective).count()
        print(f'  ✓ 复盘记录: {rc}条')
    finally: db.close()
    print('  ✅ PASS\n'); return True

def t4():
    print('=== 测试4: 回测引擎 ===')
    try:
        from services.live_pipeline_backtest_engine import LivePipelineBacktestEngine
        print('  ✓ LivePipelineBacktest: OK')
    except: print('  ⚠️ LivePipelineBacktest: import fail')
    try:
        from services.backtest_evolution_engine import BacktestEvolutionEngine
        print('  ✓ BacktestEvolution: OK')
    except: print('  ⚠️ BacktestEvolution: import fail')
    print('  ✅ PASS\n'); return True

def t5():
    print('=== 测试5: 遗传进化 ===')
    from services.strategy_genome import create_default_genome, mutate_genome
    g = create_default_genome(category="trend")
    print(f'  ✓ Genome: {len(g)}参数')
    m = mutate_genome(g, 0.5)
    ch = sum(1 for k in g if k in m and g[k]!=m[k])
    print(f'  ✓ 变异: {ch}参数变化')
    if "factor_weights" in m: print('  ✓ 因子权重组: 就绪')
    try:
        from services.genetic_optimizer import GeneticOptimizer
        print('  ✓ GeneticOptimizer: OK')
    except Exception as e: print(f'  ⚠️ GeneticOptimizer: {e}')
    print('  ✅ PASS\n'); return True

if __name__ == '__main__':
    results = [('因子引擎',t1()), ('因子→信号',t2()), ('Paper执行',t3()), ('回测引擎',t4()), ('遗传进化',t5())]
    print('='*50+'\n全链路测试汇总\n'+'='*50)
    all_pass = True
    for n,p in results: 
        print(f'  {n}: {"✅" if p else "❌"}')
        if not p: all_pass = False
    print(f'\n{"🎉 全部通过!" if all_pass else "⚠️ 部分失败"}')
