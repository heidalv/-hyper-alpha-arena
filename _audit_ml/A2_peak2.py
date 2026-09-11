from sqlalchemy import create_engine, text
import statistics as st
from collections import defaultdict
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows=[dict(r._mapping) for r in c.execute(text("""
        select id,symbol,side,entry_price,close_price,size,original_size,leverage,margin,original_margin,
               partial_realized_pnl,peak_pnl_pct,peak_unrealized_pnl,unrealized_pnl,status,timeframe_tier,
               trade_nature,close_reason,opened_at,closed_at
        from paper_positions where timeframe_tier in ('mid','long')
    """)).fetchall()]
    print("n=",len(rows))
    # peak_pnl_pct 是小数口径 -> 转 %
    pk=[float(r['peak_pnl_pct'])*100 for r in rows if r['peak_pnl_pct'] is not None]
    print(f"\n=== peak_pnl_pct（换算为 %，价格口径）===")
    print(f"  n={len(pk)} med={st.median(pk):.3f}% mean={sum(pk)/len(pk):.3f}% "
          f"p25={sorted(pk)[len(pk)//4]:.3f}% p75={sorted(pk)[3*len(pk)//4]:.3f}% max={max(pk):.2f}%")
    for th in (1,2,3,5,8,10):
        print(f"  峰值 ≥{th}% 占比: {sum(1 for x in pk if x>=th)/len(pk):.3f}  (n={sum(1 for x in pk if x>=th)})")
    print("\n=== 峰值分桶 → 最终结果（是否把浮盈做成亏损）===")
    g=defaultdict(list)
    for r in rows:
        if r['peak_pnl_pct'] is None or r['status']!='closed': continue
        e=float(r['entry_price'] or 0); cp=float(r['close_price'] or 0)
        if e<=0 or cp<=0: continue
        sgn=1 if r['side']=='long' else -1
        act=(cp-e)/e*100*sgn
        p=float(r['peak_pnl_pct'])*100
        b='峰值<0' if p<0 else ('0-1%' if p<1 else ('1-3%' if p<3 else ('3-5%' if p<5 else '≥5%')))
        g[b].append(act)
    for k in ('峰值<0','0-1%','1-3%','3-5%','≥5%'):
        v=g.get(k) or []
        if v:
            print(f"  {k:<7} n={len(v):>3} 最终均值={sum(v)/len(v):>+7.3f}% 最终>0占比={sum(1 for x in v if x>0)/len(v):.3f} "
                  f"最终<-2%占比={sum(1 for x in v if x<-2)/len(v):.3f}")
    print("\n=== 峰值 ≥3% 但最终亏损的笔数（真实回吐）===")
    give=[]
    for r in rows:
        if r['peak_pnl_pct'] is None or r['status']!='closed': continue
        e=float(r['entry_price'] or 0); cp=float(r['close_price'] or 0)
        if e<=0 or cp<=0: continue
        sgn=1 if r['side']=='long' else -1
        act=(cp-e)/e*100*sgn
        p=float(r['peak_pnl_pct'])*100
        if p>=3 and act<0:
            give.append((r['symbol'],r['timeframe_tier'],round(p,2),round(act,2),str(r['close_reason'])[:40]))
    print(f"  n={len(give)}")
    for x in sorted(give,key=lambda y:y[3])[:12]: print("   ",x)
    print("\n=== leverage 与 pnl 口径（复算）===")
    bad=0; ok=0; vals=[]
    for r in rows:
        if r['status']!='closed': continue
        e=float(r['entry_price'] or 0); cp=float(r['close_price'] or 0); sz=float(r['original_size'] or r['size'] or 0)
        if e<=0 or cp<=0 or sz<=0: continue
        sgn=1 if r['side']=='long' else -1
        gross=(cp-e)*sgn*sz + float(r['partial_realized_pnl'] or 0)
        u=float(r['unrealized_pnl'] or 0)
        if abs(gross-u)<max(0.5, abs(gross)*0.05): ok+=1
        else: bad+=1
        notional=e*sz
        lev=float(r['leverage'] or 0)
        if notional*((cp-e)/e*sgn):
            vals.append(u/(notional*((cp-e)/e*sgn)))
    print(f"  unrealized_pnl 与 价格×size 复算一致: {ok} / 不一致: {bad}")
    if vals: print(f"  隐含杠杆 u/(notional×price_ret): med={st.median(vals):.3f} mean={sum(vals)/len(vals):.3f}")
