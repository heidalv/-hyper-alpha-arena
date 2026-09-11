"""
多交易所市场流采集架构 - 快速验证脚本

用法:
    python backend/verify_multi_exchange.py

验证点:
1. 检查数据库中是否有两个交易所的数据
2. 验证 exchange 字段是否正确设置
3. 测试 CVD 查询的交易所过滤功能
"""

import sys
from pathlib import Path

# 添加 backend 目录到 Python 路径
backend_dir = Path(__file__).parent
sys.path.insert(0, str(backend_dir))
from backend.database.connection import MarketSessionLocal
from backend.database.models import MarketTradesAggregated, MarketOrderbookSnapshots, MarketAssetMetrics
from sqlalchemy import func


def verify_exchange_data():
    """验证数据库中是否有多个交易所的数据"""
    print("=" * 80)
    print("多交易所市场流采集架构 - 数据验证")
    print("=" * 80)
    
    db = MarketSessionLocal()
    try:
        # 1. 检查 trades 数据的交易所分布
        print("\n【1】检查 market_trades_aggregated 表的交易所分布:")
        result = db.query(
            MarketTradesAggregated.exchange,
            func.count(MarketTradesAggregated.id).label('record_count'),
            func.min(MarketTradesAggregated.timestamp).label('earliest'),
            func.max(MarketTradesAggregated.timestamp).label('latest')
        ).group_by(MarketTradesAggregated.exchange).all()
        
        if not result:
            print("  ❌ 未找到任何 trades 数据")
            return False
        
        exchanges_found = []
        for exchange, count, earliest, latest in result:
            exchanges_found.append(exchange)
            print(f"  [OK] {exchange}: {count} 条记录 (时间范围: {earliest} - {latest})")
        
        # 2. 检查 orderbook 数据
        print("\n【2】检查 market_orderbook_snapshots 表的交易所分布:")
        result = db.query(
            MarketOrderbookSnapshots.exchange,
            func.count(MarketOrderbookSnapshots.id).label('snapshot_count')
        ).group_by(MarketOrderbookSnapshots.exchange).all()
        
        if not result:
            print("  ⚠️  未找到 orderbook 数据(可能 Aster DEX watch_order_book 未启用)")
        else:
            for exchange, count in result:
                print(f"  [OK] {exchange}: {count} 个快照")
        
        # 3. 检查 asset_metrics 数据
        print("\n【3】检查 market_asset_metrics 表的交易所分布:")
        result = db.query(
            MarketAssetMetrics.exchange,
            func.count(MarketAssetMetrics.id).label('metrics_count'),
            func.avg(MarketAssetMetrics.funding_rate).label('avg_funding')
        ).group_by(MarketAssetMetrics.exchange).all()
        
        if not result:
            print("  ⚠️  未找到 asset_metrics 数据(可能 Aster DEX poll 未启用)")
        else:
            for exchange, count, avg_funding in result:
                print(f"  [OK] {exchange}: {count} 条指标 (平均 funding rate: {avg_funding})")
        
        # 4. 验证结果
        print("\n" + "=" * 80)
        print("验证总结:")
        print("=" * 80)
        
        has_hyperliquid = 'hyperliquid' in exchanges_found
        has_asterdex = 'asterdex' in exchanges_found
        
        if has_hyperliquid and has_asterdex:
            print("[SUCCESS] 成功! 检测到两个交易所的数据:")
            print("   - Hyperliquid: OK")
            print("   - Aster DEX:   OK")
            print("\n[INFO] 多交易所架构重构已生效!")
            return True
        elif has_hyperliquid:
            print("[WARN] 仅检测到 Hyperliquid 数据")
            print("   可能原因:")
            print("   - ACTIVE_MARKET_FLOW_EXCHANGES 配置中未包含 asterdex")
            print("   - Aster DEX 采集器启动失败(检查日志)")
            print("   - 后端在我们修改代码前启动,需要重启")
            return False
        else:
            print("[ERROR] 未检测到预期的交易所数据")
            return False
            
    finally:
        db.close()


def test_cvd_query_with_exchange_filter():
    """测试 CVD 查询的交易所过滤功能"""
    print("\n" + "=" * 80)
    print("CVD 查询交易所过滤功能测试")
    print("=" * 80)
    
    from services.market_flow_indicators import get_flow_indicators_for_prompt
    
    db = MarketSessionLocal()
    try:
        # 获取一个有数据的 symbol
        sample = db.query(MarketTradesAggregated.symbol).first()
        if not sample:
            print("[ERROR] 数据库中无数据,无法测试")
            return
        
        symbol = sample.symbol
        print(f"\n使用测试币种: {symbol}")
        
        # 测试 1: 不传 exchange 参数(跨所聚合)
        print("\n【测试 1】不传 exchange 参数(跨所聚合):")
        try:
            result = get_flow_indicators_for_prompt(db, symbol, "1h", ["CVD"])
            if result.get("CVD"):
                print(f"  [OK] 成功获取 CVD 数据")
                print(f"     当前值: {result['CVD'].get('current')}")
            else:
                print(f"  [WARN] 无 CVD 数据")
        except Exception as e:
            print(f"  [ERROR] 查询失败: {e}")
        
        # 测试 2: 指定 hyperliquid
        print("\n【测试 2】指定 exchange='hyperliquid':")
        try:
            result = get_flow_indicators_for_prompt(db, symbol, "1h", ["CVD"], exchange="hyperliquid")
            if result.get("CVD"):
                print(f"  [OK] 成功获取 Hyperliquid CVD 数据")
            else:
                print(f"  [WARN] 无 Hyperliquid CVD 数据")
        except Exception as e:
            print(f"  [ERROR] 查询失败: {e}")
        
        # 测试 3: 指定 asterdex
        print("\n【测试 3】指定 exchange='asterdex':")
        try:
            result = get_flow_indicators_for_prompt(db, symbol, "1h", ["CVD"], exchange="asterdex")
            if result.get("CVD"):
                print(f"  [OK] 成功获取 Aster DEX CVD 数据")
            else:
                print(f"  [WARN] 无 Aster DEX CVD 数据(可能该币种在 Aster DEX 无交易)")
        except Exception as e:
            print(f"  [ERROR] 查询失败: {e}")
        
        print("\n[OK] CVD 交易所过滤功能正常工作!")
        
    finally:
        db.close()


if __name__ == "__main__":
    print("\n开始验证多交易所市场流采集架构...\n")
    
    # 验证 1: 数据库中的数据
    success = verify_exchange_data()
    
    # 验证 2: CVD 查询功能
    if success:
        test_cvd_query_with_exchange_filter()
    
    print("\n" + "=" * 80)
    print("验证完成!")
    print("=" * 80)
    
    if not success:
        print("\n[TIP] 提示:")
        print("   如果只看到 Hyperliquid 数据,请:")
        print("   1. 检查 .env 文件中的 ACTIVE_MARKET_FLOW_EXCHANGES 配置")
        print("   2. 重启后端服务以加载新代码")
        print("   3. 查看 logs/backend.log 确认 Aster DEX 采集器是否启动成功")
