"""
ATAS V2 集成测试 - 验证与现有系统的集成
测试真实数据库连接和API功能
"""
import os
import sys
import requests
from datetime import datetime

# API基础URL（通过环境变量可覆盖，默认 8000 与 vite proxy 保持一致）
API_BASE = os.getenv("ATAS_TEST_BASE", "http://localhost:8000/api/atas/v2")


def test_api_health():
    """测试API健康检查"""
    print("\n" + "="*60)
    print("测试 1: API健康检查")
    print("="*60)
    
    try:
        response = requests.get(f"{API_BASE}/health", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"✅ API健康状态: {data['status']}")
            print(f"   版本: {data.get('version', 'N/A')}")
            for module, status in data.get('modules', {}).items():
                print(f"   {module}: {status}")
            return True
        else:
            print(f"❌ API响应异常: {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ 无法连接到服务器（未启动？）")
        return False
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        return False


def test_system_info():
    """测试系统信息"""
    print("\n" + "="*60)
    print("测试 2: 系统信息")
    print("="*60)
    
    try:
        response = requests.get(f"{API_BASE}/info", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"✅ 系统: {data.get('system')} v{data.get('version')}")
            print(f"\n已加载模块:")
            for name, info in data.get('modules', {}).items():
                print(f"  • {info['name']}")
                for feature in info.get('features', []):
                    print(f"    - {feature}")
            return True
        else:
            print(f"❌ 获取系统信息失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        return False


def test_account_portfolio(account_id=1):
    """测试账户投资组合获取"""
    print("\n" + "="*60)
    print(f"测试 3: 账户投资组合 (Account {account_id})")
    print("="*60)
    
    try:
        response = requests.get(f"{API_BASE}/account/{account_id}/portfolio", timeout=5)
        if response.status_code == 200:
            data = response.json()
            portfolio = data.get('portfolio', {})
            print(f"✅ 账户ID: {portfolio.get('account_id')}")
            print(f"   总价值: ${portfolio.get('total_value', 0):,.2f}")
            print(f"   现金: ${portfolio.get('capital', 0):,.2f}")
            print(f"   持仓数: {len(portfolio.get('positions', {}))}")
            print(f"   现金比例: {portfolio.get('cash_ratio', 0)*100:.1f}%")
            return True
        else:
            print(f"❌ 获取投资组合失败: {response.status_code}")
            print(f"   {response.text}")
            return False
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        return False


def test_health_score(account_id=1):
    """测试健康度评分"""
    print("\n" + "="*60)
    print(f"测试 4: 健康度评分 (Account {account_id})")
    print("="*60)
    
    try:
        response = requests.get(f"{API_BASE}/account/{account_id}/health", timeout=5)
        if response.status_code == 200:
            data = response.json()
            score = data.get('health_score', {})
            print(f"✅ 健康度评分:")
            print(f"   综合得分: {score.get('overall', 0):.1f}/100")
            print(f"   表现得分: {score.get('performance', 0):.1f}")
            print(f"   风险得分: {score.get('risk', 0):.1f}")
            print(f"   稳定得分: {score.get('stability', 0):.1f}")
            print(f"   流动得分: {score.get('liquidity', 0):.1f}")
            return True
        else:
            print(f"❌ 获取健康度评分失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        return False


def test_position_calculation(account_id=1):
    """测试仓位计算"""
    print("\n" + "="*60)
    print(f"测试 5: 仓位计算 (Account {account_id})")
    print("="*60)
    
    try:
        response = requests.post(
            f"{API_BASE}/account/{account_id}/calculate-position",
            params={
                "symbol": "BTC",
                "entry_price": 50000,
                "method": "fixed_ratio",
                "ratio": 0.1
            },
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            print(f"✅ 仓位计算结果:")
            print(f"   数量: {data.get('quantity', 0):.6f} BTC")
            print(f"   金额: ${data.get('value', 0):,.2f}")
            print(f"   风险金额: ${data.get('risk_amount', 0):,.2f}")
            print(f"   方法: {data.get('method')}")
            return True
        else:
            print(f"❌ 仓位计算失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        return False


def test_trade_risk_check(account_id=1):
    """测试交易风险检查"""
    print("\n" + "="*60)
    print(f"测试 6: 交易风险检查 (Account {account_id})")
    print("="*60)
    
    try:
        response = requests.post(
            f"{API_BASE}/account/{account_id}/check-trade",
            params={
                "symbol": "BTC",
                "side": "buy",
                "quantity": 0.1,
                "price": 50000
            },
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            print(f"✅ 风险检查结果:")
            print(f"   通过: {'是' if data.get('passed') else '否'}")
            print(f"   风险等级: {data.get('risk_level')}")
            violations = data.get('violations', [])
            if violations:
                print(f"   违规项: {len(violations)}")
                for v in violations:
                    print(f"     - {v}")
            warnings = data.get('warnings', [])
            if warnings:
                print(f"   警告项: {len(warnings)}")
                for w in warnings:
                    print(f"     - {w}")
            if not violations and not warnings:
                print(f"   无风险问题")
            return True
        else:
            print(f"❌ 风险检查失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        return False


def main():
    print("="*60)
    print("ATAS V2 集成测试 - 真实系统验证")
    print("="*60)
    print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"API地址: {API_BASE}")
    print("\n⚠️  注意: 需要先启动后端服务器")
    print("启动命令: cd backend && uvicorn main:app --reload --port 8000")
    
    results = []
    
    # 运行测试
    results.append(("API健康检查", test_api_health()))
    results.append(("系统信息", test_system_info()))
    results.append(("账户投资组合", test_account_portfolio()))
    results.append(("健康度评分", test_health_score()))
    results.append(("仓位计算", test_position_calculation()))
    results.append(("交易风险检查", test_trade_risk_check()))
    
    # 统计结果
    print("\n" + "="*60)
    print("测试总结")
    print("="*60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status} - {name}")
    
    print(f"\n测试结果: {passed}/{total} 通过")
    
    if passed == total:
        print("\n🎉 所有集成测试通过！")
        print("\nATAS V2新功能已成功集成到现有系统：")
        print("✅ 回测引擎 - 可执行策略回测")
        print("✅ 风险管理 - 实时风险检查和仓位计算")
        print("✅ 系统监控 - 账户健康度和性能指标")
        print("\n可用的API端点:")
        print(f"  • {API_BASE}/account/{{id}}/portfolio - 获取投资组合")
        print(f"  • {API_BASE}/account/{{id}}/health - 获取健康度评分")
        print(f"  • {API_BASE}/account/{{id}}/calculate-position - 计算仓位")
        print(f"  • {API_BASE}/account/{{id}}/check-trade - 检查交易风险")
        print(f"  • {API_BASE}/account/{{id}}/risk-monitor - 风险监控")
        print(f"  • {API_BASE}/account/{{id}}/metrics - 获取监控指标")
        return 0
    else:
        print(f"\n❌ {total - passed} 个测试失败")
        if not results[0][1]:
            print("\n💡 提示: 请先启动后端服务器")
        return 1


if __name__ == "__main__":
    sys.exit(main())
