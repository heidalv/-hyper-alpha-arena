from sqlalchemy import create_engine, text
import statistics as st
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
def rows(c, sql, **p):
    return [dict(r._mapping) for r in c.execute(text(sql), p).fetchall()]
def agg(vals):
    vals=[v for v in vals if v is not None]
    if not vals: return dict(n=0)
    wins=[v for v in vals if v>0]; losses=[v for v in vals if v<=0]
    return dict(n=len(vals), sum=round(sum(vals),2), avg=round(sum(vals)/len(vals),3),
                wr=round(len(wins)/len(vals),3),
                avg_w=round(sum(wins)/len(wins),3) if wins else 0.0,
                avg_l=round(sum(losses)/len(losses),3) if losses else 0.0,
                med=round(st.median(vals),3), best=round(max(vals),2), worst=round(min(vals),2))
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    # realized pnl proxy: partial_realized_pnl + (close_price-entry)*size*dir ; use margin-relative pct
    sql = """
    select p.*, 
           case when p.status='closed' then
             coalesce(p.partial_realized_pnl,0) + (case when p.side='long' then (p.close_price-p.entry_price) else (p.entry_price-p.close_price) end) * coalesce(p.original_size,p.size)
           else null end as realized_pnl,
           case when p.status='closed' and coalesce(p.original_margin,p.margin)>0 then
             (coalesce(p.partial_realized_pnl,0) + (case when p.side='long' then (p.close_price-p.entry_price) else (p.entry_price-p.close_price) end) * coalesce(p.original_size,p.size))
             / coalesce(p.original_margin,p.margin) * 100
           else null end as pnl_pct_margin,
           extract(epoch from (p.closed_at-p.opened_at))/3600.0 as hold_h
    from paper_positions p
    where p.timeframe_tier in ('mid','long')
    """
    d = rows(c, sql)
    print("total mid/long rows:", len(d))
    for keyf in [lambda r: r['timeframe_tier'], lambda r: (r['timeframe_tier'], r['trade_nature']),
                 lambda r: (r['timeframe_tier'], r['side']), lambda r: (r['timeframe_tier'], r['close_reason']),
                 lambda r: (r['timeframe_tier'], r['account_id'])]:
        groups={}
        for r in d:
            if r['status']!='closed': continue
            groups.setdefault(keyf(r),[]).append(r['realized_pnl'])
        print("\n--- group by", keyf.__code__.co_consts)
        for k in sorted(groups, key=lambda x: str(x)):
            print(f"  {k}: {agg(groups[k])}")
    print("\n--- hold hours (closed) ---")
    for k in ['mid','long']:
        hs=[r['hold_h'] for r in d if r['status']=='closed' and r['timeframe_tier']==k and r['hold_h'] is not None]
        if hs:
            print(f"  {k}: n={len(hs)} med={round(st.median(hs),2)}h mean={round(sum(hs)/len(hs),2)}h min={round(min(hs),2)} max={round(max(hs),2)}")
    print("\n--- pnl_pct_margin by tier/side ---")
    groups={}
    for r in d:
        if r['status']!='closed': continue
        groups.setdefault((r['timeframe_tier'],r['side']),[]).append(r['pnl_pct_margin'])
    for k in sorted(groups,key=str):
        print(f"  {k}: {agg(groups[k])}")
