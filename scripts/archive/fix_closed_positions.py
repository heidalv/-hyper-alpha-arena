#!/usr/bin/env python3
"""
修复已关闭的持仓但没有平仓决策记录的问题

遍历所有 status='closed' 的 BinancePosition 记录，
为每个创建对应的 AIDecisionLog 平仓记录。
"""

import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker, Session
from database.models import AIDecisionLog, BinancePosition, Account
from datetime import datetime
import json

# 数据库连接
DATABASE_URL = "postgresql://alpha_user:alpha_pass@localhost:5432/alpha_arena"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

def fix_closed_positions():
    """为所有已关闭的持仓创建平仓决策记录"""

    print("=" * 80)
    print("修复已关闭持仓的平仓决策记录")
    print("=" * 80)

    with SessionLocal() as db:
        # 查询所有账户
        accounts = db.query(Account).all()
        print(f"\n找到 {len(accounts)} 个账户\n")

        total_created = 0

        for account in accounts:
            print(f"\n处理账户: {account.name} (ID={account.id})")
            print("-" * 60)

            # 查询该账户所有已关闭的持仓
            closed_positions = db.query(BinancePosition).filter(
                BinancePosition.account_id == account.id,
                BinancePosition.status == 'closed'
            ).all()

            print(f"找到 {len(closed_positions)} 个已关闭的持仓")

            if not closed_positions:
                continue

            for pos in closed_positions:
                symbol = pos.symbol.replace('/USDT', '').replace('/USDT:USDT', '')
                print(f"\n处理持仓: {symbol} (closed_at={pos.closed_at})")

                # 查找该symbol最新的开仓决策
                latest_open = db.query(AIDecisionLog).filter(
                    AIDecisionLog.account_id == account.id,
                    AIDecisionLog.symbol == pos.symbol,
                    AIDecisionLog.operation.in_(["buy", "sell"]),
                    AIDecisionLog.executed == "true",
                    AIDecisionLog.decision_time < pos.closed_at
                ).order_by(AIDecisionLog.decision_time.desc()).first()

                if not latest_open:
                    print(f"  ⚠️  未找到对应的开仓决策，跳过")
                    continue

                print(f"  找到开仓决策: ID={latest_open.id}, 时间={latest_open.decision_time}")

                # 检查是否已经存在平仓记录
                existing_close = db.query(AIDecisionLog).filter(
                    AIDecisionLog.account_id == account.id,
                    AIDecisionLog.symbol == pos.symbol,
                    AIDecisionLog.operation == "close",
                    AIDecisionLog.executed == "true",
                    AIDecisionLog.decision_time > latest_open.decision_time
                ).first()

                if existing_close:
                    print(f"  ✓ 已存在平仓记录: ID={existing_close.id}, 跳过")
                    continue

                # 创建平仓决策记录
                close_decision = AIDecisionLog(
                    account_id=account.id,
                    symbol=pos.symbol,
                    operation="close",
                    decision_type="auto",
                    target_portion_of_balance=1.0,
                    reason=f"批量修复平仓记录: {symbol} 持仓已于 {pos.closed_at} 关闭",
                    executed="true",
                    execution_time=pos.closed_at,
                    decision_time=pos.closed_at,
                    decision_snapshot=pos.decision_snapshot
                )

                db.add(close_decision)
                db.commit()
                db.refresh(close_decision)

                print(f"  ✅ 创建平仓记录: ID={close_decision.id}")
                total_created += 1

        print("\n" + "=" * 80)
        print(f"修复完成！共创建 {total_created} 个平仓决策记录")
        print("=" * 80)

        # 统计修复后的状态
        print("\n修复后的状态统计:")
        for account in accounts:
            # 统计开仓决策
            open_decisions = db.query(AIDecisionLog).filter(
                AIDecisionLog.account_id == account.id,
                AIDecisionLog.operation.in_(["buy", "sell"]),
                AIDecisionLog.executed == "true"
            ).count()

            # 统计有效的持仓（没有后续平仓记录的开仓决策）
            active_positions = []
            all_open_decisions = db.query(AIDecisionLog).filter(
                AIDecisionLog.account_id == account.id,
                AIDecisionLog.operation.in_(["buy", "sell"]),
                AIDecisionLog.executed == "true"
            ).all()

            for decision in all_open_decisions:
                close_exists = db.query(AIDecisionLog).filter(
                    AIDecisionLog.account_id == account.id,
                    AIDecisionLog.symbol == decision.symbol,
                    AIDecisionLog.operation == "close",
                    AIDecisionLog.executed == "true",
                    AIDecisionLog.decision_time > decision.decision_time
                ).first()

                if not close_exists:
                    active_positions.append(decision.id)

            print(f"  账户 {account.name}:")
            print(f"    - 总开仓决策: {open_decisions}")
            print(f"    - 有效持仓: {len(active_positions)}")
            print(f"    - 已平仓: {open_decisions - len(active_positions)}")


if __name__ == "__main__":
    try:
        fix_closed_positions()
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
