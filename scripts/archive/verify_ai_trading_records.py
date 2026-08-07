"""
AI交易数据记录验证脚本

验证：
1. 历史交易记录是否正确保存到trades表
2. 持仓数据是否正确显示
3. AI决策记录的完整性
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from sqlalchemy.orm import Session
from database.connection import SessionLocal
from database.models import Account, AIDecisionLog, Trade, Position
from sqlalchemy import desc, and_
from datetime import datetime, timedelta

def verify_ai_trading_data():
    """验证AI交易数据记录的完整性"""

    db = SessionLocal()

    try:
        print("=" * 80)
        print("AI交易数据记录验证报告")
        print("=" * 80)
        print()

        # 1. 检查AI决策记录
        print("[1] 检查AI决策记录 (ai_decision_logs)")
        print("-" * 80)

        total_decisions = db.query(AIDecisionLog).count()
        executed_decisions = db.query(AIDecisionLog).filter(
            AIDecisionLog.executed == "true"
        ).count()
        buy_decisions = db.query(AIDecisionLog).filter(
            AIDecisionLog.operation == "buy",
            AIDecisionLog.executed == "true"
        ).count()
        sell_decisions = db.query(AIDecisionLog).filter(
            AIDecisionLog.operation == "sell",
            AIDecisionLog.executed == "true"
        ).count()
        close_decisions = db.query(AIDecisionLog).filter(
            AIDecisionLog.operation == "close",
            AIDecisionLog.executed == "true"
        ).count()

        print(f"总决策数: {total_decisions}")
        print(f"已执行: {executed_decisions}")
        print(f"  - 做多(buy): {buy_decisions}")
        print(f"  - 做空(sell): {sell_decisions}")
        print(f"  - 平仓(close): {close_decisions}")
        print()

        # 2. 检查Trade表（已完成交易）
        print("[2] 检查Trade表（已完成交易）")
        print("-" * 80)

        total_trades = db.query(Trade).count()
        print(f"Trade表总记录数: {total_trades}")

        if total_trades == 0:
            print("[问题] Trade表为空！AI执行的交易没有被记录到已完成交易")
            print()
            print("原因分析：")
            print("1. 币安交易执行后只保存到ai_decision_logs，没有保存到trades表")
            print("2. 需要在trading_commands.py中添加Trade记录逻辑")
        else:
            # 显示最近5条交易
            recent_trades = db.query(Trade).order_by(
                desc(Trade.trade_time)
            ).limit(5).all()

            print("\n最近5条交易:")
            for trade in recent_trades:
                print(f"  - {trade.symbol} {trade.side} {trade.quantity} @ ${trade.price}")
                print(f"    时间: {trade.trade_time}")
                print(f"    账户ID: {trade.account_id}")
        print()

        # 3. 检查Position表（持仓）
        print("[3] 检查Position表（持仓）")
        print("-" * 80)

        total_positions = db.query(Position).count()
        active_positions = db.query(Position).filter(
            Position.szi != 0
        ).count()

        print(f"Position表总记录数: {total_positions}")
        print(f"活跃持仓数: {active_positions}")

        if active_positions == 0:
            print("[问题] 当前无活跃持仓")
        else:
            print("\n活跃持仓:")
            positions = db.query(Position).filter(
                Position.szi != 0
            ).all()

            for pos in positions:
                pnl = float(pos.unrealized_pnl or 0)
                print(f"  - {pos.symbol}: {pos.szi} (盈亏: ${pnl:.2f})")
        print()

        # 4. 检查币安账户的AI交易
        print("[4] 检查币安账户AI交易")
        print("-" * 80)

        binance_accounts = db.query(Account).filter(
            Account.binance_enabled == "true"
        ).all()

        if not binance_accounts:
            print("[信息] 没有启用币安的账户")
        else:
            for account in binance_accounts:
                print(f"\n账户: {account.name} (ID: {account.id})")

                # 该账户的已执行AI决策
                account_decisions = db.query(AIDecisionLog).filter(
                    AIDecisionLog.account_id == account.id,
                    AIDecisionLog.executed == "true",
                    AIDecisionLog.operation.in_(["buy", "sell"])
                ).order_by(desc(AIDecisionLog.decision_time)).limit(5).all()

                print(f"  最近AI决策: {len(account_decisions)}条")
                for decision in account_decisions:
                    print(f"    - {decision.decision_time} {decision.operation.upper()} {decision.symbol}")
                    print(f"      order_id: {decision.order_id}")
                    print(f"      tp_order_id: {decision.tp_order_id}")
                    print(f"      sl_order_id: {decision.sl_order_id}")

                # 该账户的Trade记录
                account_trades = db.query(Trade).filter(
                    Trade.account_id == account.id
                ).count()

                print(f"  Trade表记录: {account_trades}条")

                if account_trades == 0 and len(account_decisions) > 0:
                    print("  [问题] AI已执行交易，但Trade表无记录！")

        print()
        print("=" * 80)
        print("问题总结")
        print("=" * 80)
        print()

        issues_found = []

        # 检查问题1: Trade表为空但AI决策已执行
        if total_trades == 0 and executed_decisions > 0:
            issues_found.append({
                "问题": "历史交易记录缺失",
                "严重性": "高",
                "描述": "AI策略执行的交易在Trade表（已完成交易）中没有记录",
                "影响": "前端'已完成交易'页面显示为空",
                "原因": "币安交易执行后只保存到ai_decision_logs，没有保存到trades表"
            })

        # 检查问题2: 持仓数据为空
        if active_positions == 0 and buy_decisions + sell_decisions > 0:
            # 检查是否有开仓决策但没有持仓
            recent_open = db.query(AIDecisionLog).filter(
                AIDecisionLog.operation.in_(["buy", "sell"]),
                AIDecisionLog.executed == "true",
                AIDecisionLog.decision_time >= datetime.now() - timedelta(hours=24)
            ).count()

            if recent_open > 0:
                issues_found.append({
                    "问题": "持仓数据为空",
                    "严重性": "中",
                    "描述": f"最近24小时有{recent_open}次开仓决策，但Position表无持仓",
                    "影响": "前端持仓页面显示为空",
                    "可能原因": "1. 已全部平仓 2. Position表未正确同步 3. 使用币安API查询而非本地数据库"
                })

        # 检查问题3: executed状态
        false_executed = db.query(AIDecisionLog).filter(
            AIDecisionLog.executed == "false",
            AIDecisionLog.operation.in_(["buy", "sell", "close"])
        ).count()

        if false_executed > 0:
            recent_false = db.query(AIDecisionLog).filter(
                AIDecisionLog.executed == "false",
                AIDecisionLog.operation.in_(["buy", "sell", "close"]),
                AIDecisionLog.decision_time >= datetime.now() - timedelta(hours=24)
            ).count()

            if recent_false > 0:
                issues_found.append({
                    "问题": "executed状态仍为false",
                    "严重性": "低",
                    "描述": f"最近24小时有{recent_false}条未执行记录",
                    "状态": "已修复 - binance_trading_client.py已添加'open'状态",
                    "说明": "如果订单状态是'open'（期货持仓），现在会正确标记为executed=true"
                })

        if not issues_found:
            print("[OK] 未发现问题！所有数据记录正常。")
        else:
            print(f"[发现] 共发现 {len(issues_found)} 个问题：\n")

            for i, issue in enumerate(issues_found, 1):
                print(f"{i}. {issue['问题']}")
                print(f"   严重性: {issue['严重性']}")
                print(f"   描述: {issue['描述']}")
                print(f"   影响: {issue['影响']}")
                if '原因' in issue:
                    print(f"   原因: {issue['原因']}")
                if '状态' in issue:
                    print(f"   状态: {issue['状态']}")
                if '可能原因' in issue:
                    print(f"   可能原因: {issue['可能原因']}")
                print()

        print("=" * 80)
        print("验证完成")
        print("=" * 80)

        return issues_found

    finally:
        db.close()


if __name__ == "__main__":
    verify_ai_trading_data()
