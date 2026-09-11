from sqlalchemy import create_engine, text
import json, statistics as st
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
def parse(v):
    if v is None: return {}
    if isinstance(v,dict): return v
    try: return json.loads(v)
    except Exception:
        try: return eval(v)
        except Exception: return {}
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows=[dict(r._mapping) for r in c.execute(text("""
        select id,symbol,side,entry_price,exit_price,position_size,leverage,pnl,pnl_pct,holding_period,
               decision_context,signal_context,ai_reasoning,opened_at,closed_at,strategy_id,tenant_id
        from strategy_trades
        where cast(decision_context as text) like '%''confidence'': 0.5%' and cast(decision_context as text) like '%swing%'
        order by closed_at desc
    """)).fetchall()]
    print("conf=0.5 swing rows:", len(rows))
    for r in rows[:8]:
        print(r['id'], r['symbol'], r['side'], 'lev',r['leverage'], 'pnl',round(float(r['pnl'] or 0),2),
              r['opened_at'], '->', r['closed_at'], 'strat',r['strategy_id'],'tenant',r['tenant_id'])
        print('   dctx:', json.dumps(parse(r['decision_context']), ensure_ascii=False)[:600])
        print('   ai:', (r['ai_reasoning'] or '')[:200])
    print("\n### by tenant")
    g={}
    for r in rows: g.setdefault(r['tenant_id'],[]).append(float(r['pnl'] or 0))
    for k in sorted(g,key=lambda x:(x is None,x)):
        v=g[k]; print(f"  tenant={k}: n={len(v)} sum={round(sum(v),2)}")
    print("\n### by strategy_id top")
    g={}
    for r in rows: g.setdefault(r['strategy_id'],[]).append(float(r['pnl'] or 0))
    for k,v in sorted(g.items(), key=lambda kv:-len(kv[1]))[:10]:
        print(f"  {k}: n={len(v)} sum={round(sum(v),2)}")
    print("\n### by date")
    g={}
    for r in rows: g.setdefault(str(r['opened_at'])[:10],[]).append(float(r['pnl'] or 0))
    for k in sorted(g): print(f"  {k}: n={len(g[k])} sum={round(sum(g[k]),2)}")
    print("\n### by symbol")
    g={}
    for r in rows: g.setdefault(r['symbol'],[]).append(float(r['pnl'] or 0))
    for k,v in sorted(g.items(), key=lambda kv:-len(kv[1]))[:15]:
        print(f"  {k}: n={len(v)} sum={round(sum(v),2)} avg={round(sum(v)/len(v),3)}")
