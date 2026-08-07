#!/usr/bin/env python3
"""Check ETH trading history"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from database.connection import SessionLocal
from database.models import AIDecisionLog, Trade
from datetime import datetime, timedelta
from sqlalchemy import desc

db = SessionLocal()
try:
    # Check all ETH buy/sell decisions in last 24 hours
    eth_decisions = db.query(AIDecisionLog).filter(
        AIDecisionLog.symbol == 'ETH',
        AIDecisionLog.operation.in_(['buy', 'sell', 'close']),
        AIDecisionLog.decision_time > datetime.now() - timedelta(hours=24)
    ).order_by(desc(AIDecisionLog.decision_time)).all()

    print(f'ETH Trading Decisions (Last 24 Hours): {len(eth_decisions)}')
    print('=' * 120)
    for d in eth_decisions:
        print(f'{d.decision_time} | {d.operation} | executed={d.executed} | order_id={d.order_id} | tp={d.tp_order_id} | sl={d.sl_order_id}')

    print()
    print('Trades table (last 24 hours):')
    trades = db.query(Trade).filter(
        Trade.symbol == 'ETH',
        Trade.trade_time > datetime.now() - timedelta(hours=24)
    ).order_by(desc(Trade.trade_time)).all()

    if trades:
        for t in trades:
            print(f'{t.trade_time} | {t.side} | {t.quantity} @ ${t.price} | order_id={t.order_id}')
    else:
        print('No ETH trades found in trades table')

    print()
    print('All executed decisions (any symbol, last 24 hours):')
    all_executed = db.query(AIDecisionLog).filter(
        AIDecisionLog.executed == 'true',
        AIDecisionLog.decision_time > datetime.now() - timedelta(hours=24)
    ).order_by(desc(AIDecisionLog.decision_time)).all()

    if all_executed:
        for d in all_executed:
            print(f'{d.decision_time} | {d.symbol} | {d.operation} | order_id={d.order_id}')
    else:
        print('No executed decisions found!')

finally:
    db.close()
