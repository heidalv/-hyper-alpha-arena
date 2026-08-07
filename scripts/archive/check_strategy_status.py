#!/usr/bin/env python3
import sys
sys.path.insert(0, '/app/backend')

# Check strategy manager status
print("=== 策略管理器状态检查 ===\n")

try:
    from services.trading_strategy import hyper_strategy_manager, binance_strategy_manager
    
    print("\n--- Hyperliquid策略管理器 ---")
    print(f"✓ 运行状态: {hyper_strategy_manager.running}")
    print(f"✓ 已加载策略数量: {len(hyper_strategy_manager.strategies)}")
    
    if hyper_strategy_manager.strategies:
        print("\n策略详情:")
        for aid, strategy in hyper_strategy_manager.strategies.items():
            print(f"  账户ID {aid}:")
            print(f"    - 启用: {strategy.enabled}")
            print(f"    - 触发间隔: {strategy.trigger_interval}秒")
            print(f"    - 信号池: {strategy.signal_pool_id}")
            print(f"    - 最后触发: {strategy.last_trigger_at}")
            print(f"    - 运行中: {strategy.running}")
    
    print("\n--- Binance策略管理器 ---")
    print(f"✓ 运行状态: {binance_strategy_manager.running}")
    print(f"✓ 已加载策略数量: {len(binance_strategy_manager.strategies)}")
    
    if binance_strategy_manager.strategies:
        print("\n策略详情:")
        for aid, strategy in binance_strategy_manager.strategies.items():
            print(f"  账户ID {aid}:")
            print(f"    - 启用: {strategy.enabled}")
            print(f"    - 触发间隔: {strategy.trigger_interval}秒")
            print(f"    - 信号池: {strategy.signal_pool_id}")
            print(f"    - 最后触发: {strategy.last_trigger_at}")
            print(f"    - 运行中: {strategy.running}")
    
    if not hyper_strategy_manager.strategies and not binance_strategy_manager.strategies:
        print("\n⚠ 没有加载任何策略")
except Exception as e:
    print(f"❌ 无法访问StrategyManager: {e}")
    import traceback
    traceback.print_exc()

# Check market stream status
print("\n\n=== 市场数据流状态 ===\n")
try:
    from backend.services.market_data_hub import market_data_hub
    print(f"✓ MarketDataHub: running={market_data_hub.is_running}")
    print(f"  symbols: {market_data_hub._symbols}")
    print(f"  l2_entries: {len(market_data_hub._l2_store)}")
except Exception as e:
    print(f"❌ 无法访问MarketStream: {e}")

# Check if price updates are being received
print("\n\n=== 价格缓存状态 ===\n")
try:
    from services.price_cache import _price_cache
    from datetime import datetime, timedelta
    
    recent_count = 0
    now = datetime.now()
    cutoff = now - timedelta(minutes=5)
    
    for symbol, (price, timestamp) in _price_cache.items():
        if timestamp > cutoff.timestamp():
            recent_count += 1
    
    print(f"✓ 缓存交易对总数: {len(_price_cache)}")
    print(f"✓ 最近5分钟有更新: {recent_count}个")
    
    if len(_price_cache) > 0:
        latest = sorted(_price_cache.items(), key=lambda x: x[1][1], reverse=True)[:5]
        print("\n最新价格更新:")
        for symbol, (price, timestamp) in latest:
            age = int(now.timestamp() - timestamp)
            print(f"  {symbol}: ${price:.2f} ({age}秒前)")
except Exception as e:
    print(f"❌ 无法访问价格缓存: {e}")

print("\n\n=== 诊断建议 ===\n")
print("如果策略管理器运行中但没有触发:")
print("1. 检查账户的 auto_trading_enabled 是否为 true")
print("2. 检查策略配置的 enabled 是否为 true")
print("3. 检查是否有价格更新触发")
print("4. 检查最后触发时间 + 触发间隔 是否已经到达")
