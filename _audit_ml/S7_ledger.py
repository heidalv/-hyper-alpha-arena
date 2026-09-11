from sqlalchemy import create_engine, text
from collections import Counter
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("按 lane/symbol/event")
    for r in c.execute(text("""select lane_id, symbol, event, count(*) n, round(avg(net_bp)::numeric,3) avg_net,
                                      round(min(net_bp)::numeric,2) mn, round(max(net_bp)::numeric,2) mx,
                                      round(sum(notional)::numeric,2) notional
                               from lane_ledger group by 1,2,3 order by n desc limit 20""")):
        print("  ", dict(r._mapping))
    print("\nnet_bp 取值分布")
    for r in c.execute(text("select net_bp, count(*) n from lane_ledger group by 1 order by n desc limit 10")):
        print("  ", dict(r._mapping))
    print("\nspread_bp / fee_bp / slippage_bp 分布")
    for col in ("spread_bp","fee_bp","slippage_bp","price_bp","funding_bp"):
        rows=c.execute(text(f"select {col}, count(*) n from lane_ledger group by 1 order by n desc limit 4")).fetchall()
        print(f"  {col}: {[(str(a),b) for a,b in rows]}")
