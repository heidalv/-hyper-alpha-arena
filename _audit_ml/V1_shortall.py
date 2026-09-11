from sqlalchemy import create_engine, text
from collections import defaultdict
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### 全部 mid/long 空头（closed）")
    rows=[dict(r._mapping) for r in c.execute(text("""
        select id,account_id,symbol,side,entry_price,close_price,sl_price,tp_price,original_size,size,
               partial_realized_pnl,timeframe_tier,trade_nature,close_reason,opened_at,closed_at,
               peak_pnl_pct,trough_pnl_pct,leverage,margin,original_margin
        from paper_positions where timeframe_tier in ('mid','long') and side='short' and status='closed'
        order by opened_at
    """)).fetchall()]
    print("n =", len(rows))
    print("\n按月")
    g=defaultdict(list)
    for r in rows:
        g[str(r['opened_at'])[:7]].append(r)
    for k in sorted(g): print(f"  {k}: {len(g[k])}")
    print("\n按 close_reason 前缀")
    g=defaultdict(int)
    for r in rows:
        reason=str(r['close_reason'] or '?')
        g[reason.split(':')[0][:30]] += 1
    for k,v in sorted(g.items(), key=lambda kv:-kv[1])[:15]: print(f"  {k}: {v}")
    print("\n按币")
    g=defaultdict(int)
    for r in rows: g[r['symbol']]+=1
    for k,v in sorted(g.items(), key=lambda kv:-kv[1])[:15]: print(f"  {k}: {v}")
    print("\n按 nature")
    g=defaultdict(int)
    for r in rows: g[str(r['trade_nature'])]+=1
    print(dict(g))
    print("\n按账户")
    g=defaultdict(int)
    for r in rows: g[r['account_id']]+=1
    print(dict(g))
