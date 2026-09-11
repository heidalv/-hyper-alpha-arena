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
    bad=[]
    for r in rows:
        e=float(r['entry_price'] or 0); cp=float(r['close_price'] or 0)
        sz=float(r['original_size'] or r['size'] or 0)
        if e<=0 or cp<=0 or sz<=0: continue
        sgn=1 if r['side']=='long' else -1
        gross=(cp-e)*sgn*sz + float(r['partial_realized_pnl'] or 0)
        u=float(r['unrealized_pnl'] or 0)
        bad.append((abs(gross-u), r, gross, u))
    bad.sort(key=lambda x:-x[0])
    print("=== 偏差最大的 12 笔 ===")
    for d,r,g,u in bad[:12]:
        print(f"  {r['symbol']:<8} {r['timeframe_tier']:<5} size={float(r['size']):.4f} orig={float(r['original_size'] or 0):.4f} "
              f"entry={float(r['entry_price']):.4f} close={float(r['close_price']):.4f} partial={float(r['partial_realized_pnl'] or 0):.2f} "
              f"unreal={u:>+9.2f} 复算={g:>+9.2f} 差={d:.2f} reason={str(r['close_reason'])[:28]}")
    print("\n=== 偏差分布 ===")
    ds=[d for d,_,_,_ in bad]
    print(f"  n={len(ds)} med={st.median(ds):.3f} mean={sum(ds)/len(ds):.3f} p90={sorted(ds)[int(0.9*len(ds))]:.3f} max={max(ds):.2f}")
    print(f"  偏差 <0.01 的笔数: {sum(1 for x in ds if x<0.01)} / {len(ds)}")
    print(f"  偏差 >1 的笔数: {sum(1 for x in ds if x>1)}")
    print("\n=== size vs original_size 是否一致 ===")
    same=sum(1 for r in rows if abs(float(r['size'] or 0)-float(r['original_size'] or 0))<1e-9)
    print(f"  size==original_size: {same}/{len(rows)}")
    print("\n=== 是否存在部分平仓（partial_realized_pnl != 0）===")
    print(f"  partial != 0: {sum(1 for r in rows if abs(float(r['partial_realized_pnl'] or 0))>1e-9)}/{len(rows)}")
