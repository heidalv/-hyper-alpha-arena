from sqlalchemy import create_engine, text
import statistics as st
from collections import defaultdict
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
with eng.connect() as c:
    c.execute(text("set statement_timeout='600000'"))
    print("### 近 30 天 funding 分布（按交易所）")
    for r in c.execute(text("""
        select exchange, count(*) n, count(distinct symbol) syms,
               round(avg(funding_rate)::numeric,6) avg_rate,
               round(percentile_cont(0.5) within group (order by funding_rate)::numeric,6) med,
               round(percentile_cont(0.9) within group (order by funding_rate)::numeric,6) p90,
               round(percentile_cont(0.1) within group (order by funding_rate)::numeric,6) p10,
               round(max(funding_rate)::numeric,6) mx, round(min(funding_rate)::numeric,6) mn
        from perp_funding
        where timestamp > (extract(epoch from now())*1000 - 30*86400*1000)
        group by 1 order by n desc
    """)):
        print(dict(r._mapping))
    print("\n### 主流币近 30 天年均化 funding（8h 费率 × 3 × 365）")
    for r in c.execute(text("""
        select symbol, count(*) n, round(avg(funding_rate)::numeric*3*365*100,2) apr_pct,
               round(percentile_cont(0.5) within group (order by funding_rate)::numeric*3*365*100,2) apr_med
        from perp_funding
        where exchange='asterdex' and timestamp > (extract(epoch from now())*1000 - 30*86400*1000)
          and symbol in ('BTC','ETH','SOL','XRP','DOGE','ADA','BNB','LINK','AVAX','UNI','LTC','DOT')
        group by 1 order by apr_pct desc
    """)):
        print(dict(r._mapping))
