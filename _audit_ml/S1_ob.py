from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
with eng.connect() as c:
    c.execute(text("set statement_timeout='300000'"))
    for t in ("market_orderbook_snapshots","market_trades_aggregated","ticker_snapshots"):
        cols=[r[0] for r in c.execute(text("select column_name,data_type from information_schema.columns where table_name=:t order by ordinal_position"),{"t":t})]
        n=c.execute(text(f'select count(*) from "{t}"')).scalar()
        print(f"== {t}  n={n}\n   {cols}\n")
    print("### orderbook 样本")
    for r in c.execute(text("select * from market_orderbook_snapshots order by id desc limit 2")):
        d=dict(r._mapping)
        print({k:(str(v)[:200] if v is not None else None) for k,v in d.items()})
    print("\n### trades 样本")
    for r in c.execute(text("select * from market_trades_aggregated order by id desc limit 3")):
        d=dict(r._mapping)
        print({k:(str(v)[:160] if v is not None else None) for k,v in d.items()})
