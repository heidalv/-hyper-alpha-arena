from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
with eng.connect() as c:
    c.execute(text("set statement_timeout='600000'"))
    print("### 4h/1d 各币深度（asterdex，近 90 天 bars 数）")
    for tf, days in (("4h", 90), ("1d", 90)):
        rows = c.execute(text("""
            select symbol, count(*) n, min(timestamp) mn, max(timestamp) mx
            from crypto_klines
            where period=:tf and exchange='asterdex'
              and timestamp > extract(epoch from now())::bigint - :secs
            group by 1 having count(*) > 0
            order by n desc limit 60
        """), {"tf": tf, "secs": days*86400}).fetchall()
        print(f"-- {tf}: {len(rows)} symbols")
        print("   " + ", ".join(f"{s}({n})" for s,n,_,_ in rows[:40]))
    print("\n### 全量深度（不限时间）前 40 币 4h bars")
    for r in c.execute(text("""
        select symbol, count(*) n from crypto_klines
        where period='4h' and exchange='asterdex' group by 1 order by n desc limit 40
    """)):
        print(f"   {r[0]}: {r[1]}")
