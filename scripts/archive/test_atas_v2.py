"""
ATAS V2 集成测试脚本
验证所有新模块是否正常加载和工作
"""
import sys
import traceback
from datetime import datetime

def test_module_import(module_name, display_name):
    """测试模块导入"""
    try:
        if module_name == "backtest_engine":
            from backend.services.backtest_engine import BacktestEngine, BacktestConfig, BacktestMode
            print(f"✅ {display_name}: 已加载")
            return True
        elif module_name == "backtest_reporting":
            from backend.services.backtest_reporting import (
                BacktestReportGenerator, BacktestMetricsCalculator
            )
            print(f"✅ {display_name}: 已加载")
            return True
        elif module_name == "risk_management":
            from backend.services.risk_management import (
                RiskController, PositionManager, StopLossEngine, RiskMonitor
            )
            print(f"✅ {display_name}: 已加载")
            return True
        elif module_name == "system_monitoring":
            from backend.services.system_monitoring import (
                MonitoringDashboard, HealthScoreCalculator, AlertSystem
            )
            print(f"✅ {display_name}: 已加载")
            return True
        return False
    except Exception as e:
        print(f"❌ {display_name}: 加载失败 - {str(e)}")
        traceback.print_exc()
        return False


def test_basic_functionality():
    """测试基本功能"""
    print("\n" + "="*60)
    print("测试基本功能")
    print("="*60)
    
    # 测试回测引擎
    try:
        from backend.services.backtest_engine import BacktestEngine, BacktestConfig
        config = BacktestConfig(initial_capital=100000)
        engine = BacktestEngine(config)
        print("✅ 回测引擎初始化成功")
    except Exception as e:
        print(f"❌ 回测引擎初始化失败: {e}")
    
    # 测试风险控制器
    try:
        from backend.services.risk_management import RiskController
        controller = RiskController()
        result = controller.check_risk(
            portfolio={"total_value": 100000, "capital": 50000, "positions": {}},
            new_order=None
        )
        print(f"✅ 风险控制器运行成功 - 风险等级: {result.risk_level.value}")
    except Exception as e:
        print(f"❌ 风险控制器运行失败: {e}")
    
    # 测试仓位管理器
    try:
        from backend.services.risk_management import PositionManager, PositionSizingMethod
        manager = PositionManager()
        result = manager.calculate(
            method=PositionSizingMethod.FIXED_RATIO,
            account_value=100000,
            entry_price=50000,
            ratio=0.1
        )
        print(f"✅ 仓位管理器运行成功 - 数量: {result.quantity:.4f}")
    except Exception as e:
        print(f"❌ 仓位管理器运行失败: {e}")
    
    # 测试监控仪表板
    try:
        from backend.services.system_monitoring import MonitoringDashboard
        dashboard = MonitoringDashboard()
        metrics = dashboard.get_metrics({"active_strategies": 3, "positions": {}, "daily_pnl": 1500})
        print(f"✅ 监控仪表板运行成功 - CPU: {metrics.cpu_usage:.1f}%")
    except Exception as e:
        print(f"❌ 监控仪表板运行失败: {e}")
    
    # 测试健康度评分
    try:
        from backend.services.system_monitoring import HealthScoreCalculator
        calculator = HealthScoreCalculator()
        score = calculator.calculate(
            portfolio={"daily_pnl": 1000, "current_drawdown": 0.08, "cash_ratio": 0.3},
            metrics={"volatility": 0.02}
        )
        print(f"✅ 健康度评分运行成功 - 总分: {score.overall:.1f}")
    except Exception as e:
        print(f"❌ 健康度评分运行失败: {e}")


def main():
    print("="*60)
    print("ATAS V2 系统集成测试")
    print("="*60)
    print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # 检查依赖
    print("检查Python依赖...")
    dependencies = {
        "pandas": "数据处理",
        "numpy": "数值计算",
        "matplotlib": "图表生成",
        "psutil": "系统监控",
        "scipy": "科学计算",
        "scikit-learn": "机器学习"
    }
    
    dep_results = []
    for dep, desc in dependencies.items():
        try:
            __import__(dep)
            print(f"✅ {dep:20} - {desc}")
            dep_results.append(True)
        except ImportError:
            print(f"❌ {dep:20} - {desc} [未安装]")
            dep_results.append(False)
    
    print()
    
    # 测试模块导入
    print("测试ATAS V2模块导入...")
    modules = {
        "backtest_engine": "回测引擎",
        "backtest_reporting": "回测报告",
        "risk_management": "风险管理",
        "system_monitoring": "系统监控"
    }
    
    module_results = []
    for module, desc in modules.items():
        result = test_module_import(module, desc)
        module_results.append(result)
    
    # 测试基本功能
    if all(module_results):
        test_basic_functionality()
    else:
        print("\n⚠️  部分模块加载失败，跳过功能测试")
    
    # 总结
    print("\n" + "="*60)
    print("测试总结")
    print("="*60)
    print(f"依赖检查: {sum(dep_results)}/{len(dep_results)} 通过")
    print(f"模块加载: {sum(module_results)}/{len(module_results)} 成功")
    
    if all(dep_results) and all(module_results):
        print("\n🎉 ATAS V2 系统集成测试全部通过！")
        print("\n系统已就绪，可以启动服务器：")
        print("  cd backend")
        print("  uvicorn main:app --reload --host 0.0.0.0 --port 8000")
        print("\nATAS V2 API端点:")
        print("  健康检查: GET  /api/atas/v2/health")
        print("  运行回测: POST /api/atas/v2/backtest/run")
        print("  风险检查: POST /api/atas/v2/risk/check")
        print("  仓位计算: POST /api/atas/v2/risk/position-size")
        print("  监控数据: GET  /api/atas/v2/monitoring/dashboard")
        print("  健康评分: GET  /api/atas/v2/monitoring/health-score")
        print("  系统信息: GET  /api/atas/v2/info")
        return 0
    else:
        print("\n❌ 部分测试失败，请检查错误信息")
        return 1


if __name__ == "__main__":
    sys.exit(main())
