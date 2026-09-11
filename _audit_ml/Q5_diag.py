from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
with eng.connect() as c:
    c.execute(text("set statement_timeout='120000'"))
    for sql in [
        "select count(*) from perp_funding where exchange='asterdex'",
        "select count(*) from perp_funding where exchange='asterdex' and timestamp > 1788000000000",
        "select symbol, funding_rate from perp_funding where exchange='asterdex' and timestamp > 1788000000000 limit 3",
        "select symbol, funding_rate::text from perp_funding where exchange='asterdex' and timestamp > 1788000000000 limit 3",
    ]:
        try:
            r = c.execute(text(sql)).fetchall()
            print("OK:", sql[:80], "->", r[:3] if 'limit' in sql else r)
        except Exception as e:
            print("ERR:", sql[:80], "->", str(e)[:120])
