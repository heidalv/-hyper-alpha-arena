from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
with eng.connect() as c:
    c.execute(text("set statement_timeout='600000'"))
    print("### 候选面板（4h 深度>=2000 且 1d 深度>=300，排除股票/杠杆标签）")
    rows = c.execute(text("""
        with h4 as (
          select symbol, count(*) n from crypto_klines
          where period='4h' and exchange='asterdex' group by 1
        ), d1 as (
          select symbol, count(*) n from crypto_klines
          where period='1d' and exchange='asterdex' group by 1
        )
        select h4.symbol, h4.n as n4, coalesce(d1.n,0) as n1
        from h4 left join d1 on h4.symbol=d1.symbol
        where h4.n >= 2000 and coalesce(d1.n,0) >= 300
        order by h4.n desc
    """)).fetchall()
    print("n=", len(rows))
    print("   " + ", ".join(f"{s}" for s,_,_ in rows))
    print("\n### 排除明显非加密（含数字前缀/股票代码）后")
    import re
    crypto=[s for s,_,_ in rows if not re.match(r'^(1000|1M)', s) and s not in ('TSLA','MU','INTC','SOXL','CRCL','SKHYNIX','MAGMA','EDGE','BTW','HOME','FLOCK','SKR','HUMA')]
    print(f"n={len(crypto)}")
    print("   " + ", ".join(crypto))
