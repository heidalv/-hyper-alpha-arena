"""
测试脚本: 检查币安AI策略持仓显示问题

使用方法:
1. 确保后端正在运行
2. 访问 http://localhost:8000/api/ai-trading/accounts/{account_id}/positions-with-plans
3. 检查返回的持仓数据
"""
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from backend.database.connection import SessionLocal
from backend.database.models import AIDecisionLog, Account
from sqlalchemy import desc, or_


def check_ai_decision_logs(account_id=None):
    """检查AI决策记录"""
    db = SessionLocal()
    try:
        query = db.query(AIDecisionLog)

        if account_id:
            query = query.filter(AIDecisionLog.account_id == account_id)

        # 获取最近20条记录
        decisions = query.order_by(desc(AIDecisionLog.decision_time)).limit(20).all()

        print("\n" + "="*100)
        print(f"{'='*40} 最近AI决策记录 (共{len(decisions)}条) {'='*40}")
        print("="*100)

        for i, d in enumerate(decisions, 1):
            print(f"\n[{i}] 决策ID: {d.id}")
            print(f"    账户ID: {d.account_id}")
            print(f"    时间: {d.decision_time}")
            print(f"    操作: {d.operation}")
            print(f"    交易对: {d.symbol}")
            print(f"    执行状态: {d.executed}")
            print(f"    订单ID: {d.order_id}")
            print(f"    TP订单ID: {d.tp_order_id}")
            print(f"    SL订单ID: {d.sl_order_id}")
            print(f"    原因: {d.reason[:100] if d.reason else 'None'}...")

            # 检查decision_snapshot
            if d.decision_snapshot:
                try:
                    import json
                    snapshot = json.loads(d.decision_snapshot)
                    snapshot_symbol = snapshot.get('symbol', 'N/A')
                    print(f"    Snapshot中的symbol: {snapshot_symbol}")
                except:
                    pass

        return decisions

    finally:
        db.close()


def check_position_match(account_id):
    """检查持仓与决策记录的匹配情况"""
    db = SessionLocal()
    try:
        # 获取账户
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            print(f"❌ 账户 {account_id} 不存在")
            return

        print(f"\n{'='*100}")
        print(f"账户: {account.name} (ID: {account_id})")
        print(f"币安启用: {account.binance_enabled}")
        print(f"{'='*100}")

        # Binance 已移除（Phase 1），不再从交易所拉取持仓
        try:
            positions = []
            print("\n[提示] Binance 已移除（Phase 1），仅显示数据库中的决策记录。")

            print(f"\n📊 币安实际持仓 (共{len(positions)}个，已停用):")
            print("-"*100)

            for pos in positions:
                raw_symbol = pos.get('symbol')
                simple_symbol = raw_symbol.replace('/USDT', '').replace('USDT', '') if raw_symbol else ''
                position_size = float(pos.get('size', 0) or pos.get('position_amt', 0))
                side = pos.get('side', 'long')

                print(f"\n交易对: {raw_symbol} (简化: {simple_symbol})")
                print(f"  方向: {side}")
                print(f"  数量: {position_size}")
                print(f"  入场价: {pos.get('entry_price', 0)}")
                print(f"  标记价: {pos.get('mark_price', 0)}")
                print(f"  未实现盈亏: {pos.get('unrealized_pnl', 0)}")

                # 🔥 查找对应的开仓决策(使用修复后的逻辑)
                print(f"\n  🔍 查找开仓决策:")

                # 旧逻辑(只查询简化symbol)
                old_decision = db.query(AIDecisionLog).filter(
                    AIDecisionLog.account_id == account_id,
                    AIDecisionLog.symbol == simple_symbol,
                    AIDecisionLog.operation.in_(["buy", "sell"]),
                    AIDecisionLog.executed == "true"
                ).order_by(desc(AIDecisionLog.decision_time)).first()

                # 新逻辑(同时查询两种格式)
                new_decision = db.query(AIDecisionLog).filter(
                    AIDecisionLog.account_id == account_id,
                    or_(
                        AIDecisionLog.symbol == simple_symbol,
                        AIDecisionLog.symbol == raw_symbol
                    ),
                    AIDecisionLog.operation.in_(["buy", "sell"]),
                    AIDecisionLog.executed == "true"
                ).order_by(desc(AIDecisionLog.decision_time)).first()

                print(f"    旧逻辑结果: {'✅ 找到' if old_decision else '❌ 未找到'}")
                if old_decision:
                    print(f"      决策ID: {old_decision.id}, symbol: {old_decision.symbol}, 时间: {old_decision.decision_time}")

                print(f"    新逻辑结果: {'✅ 找到' if new_decision else '❌ 未找到'}")
                if new_decision:
                    print(f"      决策ID: {new_decision.id}, symbol: {new_decision.symbol}, 时间: {new_decision.decision_time}")
                    print(f"      TP订单ID: {new_decision.tp_order_id}")
                    print(f"      SL订单ID: {new_decision.sl_order_id}")

                    # 解析止盈止损
                    if new_decision.decision_snapshot:
                        try:
                            import json
                            snapshot = json.loads(new_decision.decision_snapshot)
                            tp = snapshot.get("take_profit_price")
                            sl = snapshot.get("stop_loss_price")
                            if tp or sl:
                                print(f"      止盈价格: {tp}")
                                print(f"      止损价格: {sl}")
                        except:
                            pass

        except Exception as e:
            print(f"❌ 获取币安持仓失败: {e}")
            import traceback
            traceback.print_exc()

    finally:
        db.close()


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='检查币安AI策略持仓显示')
    parser.add_argument('--account-id', type=int, help='指定账户ID')
    parser.add_argument('--all', action='store_true', help='检查所有账户')

    args = parser.parse_args()

    # 1. 检查AI决策记录
    check_ai_decision_logs(args.account_id)

    # 2. 如果指定了账户,检查持仓匹配
    if args.account_id:
        check_position_match(args.account_id)
    elif args.all:
        db = SessionLocal()
        try:
            accounts = db.query(Account).filter(
                Account.binance_enabled == "true",
                Account.is_active == "true"
            ).all()

            for acc in accounts:
                check_position_match(acc.id)
        finally:
            db.close()
    else:
        print("\n💡 提示: 使用 --account-id <ID> 检查特定账户的持仓匹配情况")
        print("💡 提示: 使用 --all 检查所有币安账户的持仓匹配情况")


if __name__ == '__main__':
    main()
