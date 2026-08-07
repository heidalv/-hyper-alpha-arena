#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from database.connection import SessionLocal
from database.models import AIDecisionLog, Trade
from datetime import datetime, timedelta
from sqlalchemy import desc

db = SessionLocal()
try:
    # 检查最近10分钟的决策
    recent = db.query(AIDecisionLog).filter(
        AIDecisionLog.decision_time > datetime.now() - timedelta(minutes=10)
    ).order_by(desc(AIDecisionLog.decision_time)).limit(10).all()

    print('最近10分钟的AI决策:')
    print('=' * 120)
    for d in recent:
        print(f'{d.decision_time} | {d.operation} | {d.symbol} | executed={d.executed} | order_id={d.order_id}')

    print()
    print('最近10分钟的Trade记录:')
    trades = db.query(Trade).filter(
        Trade.trade_time > datetime.now() - timedelta(minutes=10)
    ).order_by(desc(Trade.trade_time)).limit(10).all()

    if trades:
        for t in trades:
            print(f'{t.trade_time} | {t.symbol} | {t.side} | {t.quantity} @ ${t.price}')
    else:
        print('没有Trade记录')

finally:
    db.close()
