from sqlalchemy import create_engine, text
import statistics as st
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### mid/long paper_positions: sl/tp distances & rr & leverage")
    rows=[dict(r._mapping) for r in c.execute(text("""
        select id,symbol,side,entry_price,close_price,sl_price,tp_price,leverage,size,original_size,margin,original_margin,
               timeframe_tier,trade_nature,close_reason,opened_at,closed_at,partial_realized_pnl,peak_pnl_pct,trough_pnl_pct
        from paper_positions where timeframe_tier in ('mid','long') and status='closed'
    """)).fetchall()]
    print("n=",len(rows))
    def dists(key):
        out=[]
        for r in rows:
            e=r['entry_price']; v=r[key]
            if e and v: out.append(abs(float(v)-float(e))/float(e)*100)
        return out
    for k in ('sl_price','tp_price'):
        d=dists(k)
        if d: print(f"  {k} distance %: n={len(d)} med={st.median(d):.2f} mean={sum(d)/len(d):.2f} p10={sorted(d)[len(d)//10]:.2f} p90={sorted(d)[len(d)*9//10]:.2f} min={min(d):.2f} max={max(d):.2f}")
    rr=[]
    for r in rows:
        e=r['entry_price']; sl=r['sl_price']; tp=r['tp_price']
        if e and sl and tp:
            try:
                rr.append(abs(float(tp)-float(e))/abs(float(e)-float(sl)))
            except Exception: pass
    if rr: print(f"  RR: n={len(rr)} med={st.median(rr):.2f} mean={sum(rr)/len(rr):.2f} p10={sorted(rr)[len(rr)//10]:.2f} p90={sorted(rr)[len(rr)*9//10]:.2f}")
    lev=[float(r['leverage']) for r in rows if r['leverage']]
    if lev: print(f"  leverage: n={len(lev)} med={st.median(lev)} mean={sum(lev)/len(lev):.2f} min={min(lev)} max={max(lev)}")
    print("\n### 实际亏损 vs SL 距离（是否越过止损）")
    over=[]
    for r in rows:
        e=r['entry_price']; sl=r['sl_price']; cp=r['close_price']; side=r['side']
        if not(e and sl and cp): continue
        sl_d=abs(float(sl)-float(e))/float(e)*100
        sgn=1 if side=='long' else -1
        actual=(float(cp)-float(e))/float(e)*100*sgn
        if r['close_reason'] in ('sl','exit_policy:sl_pct') or 'sl' == str(r['close_reason']):
            over.append((sl_d, actual, r['symbol'], r['close_reason']))
    print(f"  sl 平仓样本 {len(over)}")
    for sd,act,sym,cr in over[:15]:
        print(f"    {sym}: SL距离={sd:.2f}% 实际={act:+.2f}% 越界={act+sd:+.2f}pp ({cr})")
    if over:
        print(f"  平均: SL距离={sum(x[0] for x in over)/len(over):.2f}% 实际={sum(x[1] for x in over)/len(over):+.2f}% 平均越界={sum(x[1]+x[0] for x in over)/len(over):.2f}pp")
    print("\n### peak_pnl_pct 分布（最大浮盈，看是否给过机会）")
    pk=[float(r['peak_pnl_pct']) for r in rows if r['peak_pnl_pct'] is not None]
    if pk:
        print(f"  n={len(pk)} med={st.median(pk):.2f} mean={sum(pk)/len(pk):.2f} 峰值>2%占比={sum(1 for x in pk if x>2)/len(pk):.2f} >5%占比={sum(1 for x in pk if x>5)/len(pk):.2f}")
    print("\n### 按 peak_pnl 分桶看最终结果（是否把赢单变成输单）")
    import collections
    g=collections.defaultdict(list)
    for r in rows:
        p=r['peak_pnl_pct']
        if p is None: continue
        e=r['entry_price']; cp=r['close_price']; side=r['side']
        if not (e and cp): continue
        sgn=1 if side=='long' else -1
        act=(float(cp)-float(e))/float(e)*100*sgn
        b='peak<0' if float(p)<0 else ('peak0-1' if float(p)<1 else ('peak1-3' if float(p)<3 else ('peak3-5' if float(p)<5 else 'peak>5')))
        g[b].append(act)
    for k in ('peak<0','peak0-1','peak1-3','peak3-5','peak>5'):
        v=g.get(k) or []
        if v: print(f"  {k}: n={len(v)} 最终均值={sum(v)/len(v):+.3f}% 最终>0占比={sum(1 for x in v if x>0)/len(v):.2f}")
