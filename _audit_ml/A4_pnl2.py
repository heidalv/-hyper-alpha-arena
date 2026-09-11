from sqlalchemy import create_engine, text
import statistics as st
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows=[dict(r._mapping) for r in c.execute(text("""
        select id,symbol,side,entry_price,close_price,size,original_size,leverage,margin,original_margin,
               partial_realized_pnl,partial_fee_paid,unrealized_pnl,status,timeframe_tier,close_reason
        from paper_positions where timeframe_tier in ('mid','long') and status='closed'
    """)).fetchall()]
    ok=bad=0; vals=[]; devs=[]
    for r in rows:
        e=float(r['entry_price'] or 0); cp=float(r['close_price'] or 0)
        sz=float(r['original_size'] or r['size'] or 0)
        if e<=0 or cp<=0 or sz<=0: continue
        sgn=1 if r['side']=='long' else -1
        gross=(cp-e)*sgn*sz + float(r['partial_realized_pnl'] or 0)
        u=float(r['unrealized_pnl'] or 0)
        d=abs(gross-u); devs.append(d)
        if d < max(0.02, abs(u)*0.02): ok+=1
        else: bad+=1
        notional=e*sz
        pr=(cp-e)/e*sgn
        if notional*pr:
            vals.append(u/(notional*pr))
    print(f"=== 用 original_size 复算 unrealized_pnl ===")
    print(f"  一致 {ok} / 不一致 {bad}  (偏差 med={st.median(devs):.3f} mean={sum(devs)/len(devs):.3f} max={max(devs):.2f})")
    if vals:
        print(f"  隐含杠杆 u/(notional×price_ret): n={len(vals)} med={st.median(vals):.3f} mean={sum(vals)/len(vals):.3f} "
              f"p10={sorted(vals)[len(vals)//10]:.3f} p90={sorted(vals)[len(vals)*9//10]:.3f}")
    print("\n=== 报告口径的 leverage 字段分布 ===")
    lv=[float(r['leverage']) for r in rows if r['leverage']]
    print(f"  med={st.median(lv)} mean={sum(lv)/len(lv):.2f} min={min(lv)} max={max(lv)}")
