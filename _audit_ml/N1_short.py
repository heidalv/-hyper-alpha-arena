from sqlalchemy import create_engine, text
import statistics as st
from collections import defaultdict
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows=[dict(r._mapping) for r in c.execute(text("""
        select id,symbol,side,entry_price,close_price,original_size,size,partial_realized_pnl,
               timeframe_tier,trade_nature,close_reason,opened_at,closed_at,leverage
        from paper_positions where timeframe_tier in ('mid','long') and status='closed'
        order by opened_at
    """)).fetchall()]
    print("mid/long 已平仓:", len(rows))
    def pnl(r):
        e=float(r['entry_price'] or 0); cp=float(r['close_price'] or 0); sz=float(r['original_size'] or r['size'] or 0)
        if e<=0 or cp<=0 or sz<=0: return None
        sgn=1 if r['side']=='long' else -1
        return (cp-e)*sgn*sz + float(r['partial_realized_pnl'] or 0)
    # 空头按月/按方向
    g=defaultdict(list)
    for r in rows:
        p=pnl(r)
        if p is None: continue
        m=str(r['opened_at'])[:7]
        g[(r['side'],m)].append(p)
    print(f"\n{'方向':<6}{'月份':<9}{'n':>4}{'合计':>10}{'均值':>9}{'胜率':>7}")
    for k in sorted(g, key=lambda x:(x[0],x[1])):
        v=g[k]; w=sum(1 for x in v if x>0)
        print(f"{k[0]:<6}{k[1]:<9}{len(v):>4}{sum(v):>10.2f}{sum(v)/len(v):>9.3f}{w/len(v):>7.3f}")
    print("\n### 空头按 BTC 同期涨跌分组（入场时 30 天 BTC 趋势）")
