from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### strategy_trades: leverage vs implied notional")
    rows=[dict(r._mapping) for r in c.execute(text("""
        select id,symbol,side,entry_price,exit_price,position_size,leverage,pnl,decision_context,strategy_id
        from strategy_trades where position_size>0 and entry_price>0 and exit_price>0
        order by id desc limit 4000""")).fetchall()]
    import json
    def parse(v):
        try: return json.loads(v) if isinstance(v,str) else (v or {})
        except Exception:
            try: return eval(v) if isinstance(v,str) else {}
            except Exception: return {}
    sel=[]
    for r in rows:
        if str(r['strategy_id'] or '').startswith('e2e_'): continue
        d=parse(r['decision_context'])
        if d.get('nature') not in ('swing','trend_follow','position'): continue
        sel.append((r,d))
    print("n=",len(sel))
    import statistics as st
    for r,d in sel[:12]:
        notional=float(r['entry_price'])*float(r['position_size'])
        price_ret=(float(r['exit_price'])-float(r['entry_price']))/float(r['entry_price'])*(1 if r['side']=='long' else -1)
        print(f"  {r['symbol']:<8} lev={r['leverage']:<5} notional={notional:>9.2f} price_ret={price_ret*100:>+7.3f}% "
              f"pnl={float(r['pnl']):>+8.2f} implied_lev={float(r['pnl'])/(notional*price_ret) if notional*price_ret else 0:>+6.2f}")
    # 统计
    levs=[]; implied=[]
    for r,d in sel:
        notional=float(r['entry_price'])*float(r['position_size'])
        price_ret=(float(r['exit_price'])-float(r['entry_price']))/float(r['entry_price'])*(1 if r['side']=='long' else -1)
        if notional*price_ret:
            implied.append(float(r['pnl'])/(notional*price_ret))
            levs.append(float(r['leverage'] or 0))
    if implied:
        print(f"\n  implied leverage: med={st.median(implied):.3f} mean={sum(implied)/len(implied):.3f} min={min(implied):.2f} max={max(implied):.2f}")
        print(f"  reported leverage: med={st.median(levs):.2f} mean={sum(levs)/len(levs):.2f}")
    print("\n### paper_positions: margin/size/leverage consistency (mid/long)")
    rows=[dict(r._mapping) for r in c.execute(text("""
        select id,symbol,side,entry_price,size,original_size,margin,original_margin,leverage,unrealized_pnl,
               partial_realized_pnl,close_price,status
        from paper_positions where timeframe_tier in ('mid','long') order by id desc limit 300""")).fetchall()]
    bad=0
    for r in rows[:10]:
        e=float(r['entry_price'] or 0); s=float(r['size'] or 0); m=float(r['margin'] or 0); l=float(r['leverage'] or 0)
        notional=e*s
        print(f"  {r['symbol']:<8} lev={l:<5} size={s:<12.4f} margin={m:<9.2f} notional={notional:<10.2f} "
              f"notional/margin={notional/m if m else 0:>7.2f} status={r['status']}")
