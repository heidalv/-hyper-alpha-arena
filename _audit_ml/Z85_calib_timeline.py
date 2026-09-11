import sys
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena"); sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT/".env")
from sqlalchemy import text
from backend.database.connection import SessionLocal
db = SessionLocal()
try:
    print("=== swing_agent_score 样本按日（含 pnl）===")
    for r in db.execute(text("select date_trunc('day', created_at)::date d, count(*), count(trade_pnl) from signal_trade_feedback where signal_type='swing_agent_score' group by 1 order by 1")).fetchall():
        print("  ", [str(x) for x in r])
    print("=== trend_agent_score 样本按日 ===")
    for r in db.execute(text("select date_trunc('day', created_at)::date d, count(*), count(trade_pnl) from signal_trade_feedback where signal_type='trend_agent_score' group by 1 order by 1")).fetchall():
        print("  ", [str(x) for x in r])
    print("=== swing 样本最近 8 条（含 pnl 与否）===")
    for r in db.execute(text("select created_at, symbol, signal_value, trade_pnl from signal_trade_feedback where signal_type='swing_agent_score' order by created_at desc limit 8")).fetchall():
        print("  ", [str(x) for x in r])
    print("=== 已平仓 mid 仓位数量（校准样本来源）近期 ===")
    for r in db.execute(text("select date_trunc('day', closed_at)::date d, count(*) from paper_positions where timeframe_tier='mid' and status<>'open' and closed_at > now() - interval '20 days' group by 1 order by 1")).fetchall():
        print("  ", [str(x) for x in r])
finally:
    db.close()
