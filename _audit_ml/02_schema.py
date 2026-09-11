from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    for t in ["paper_positions","strategy_trades","trade_facts","position_exit_events","accounts","full_auto_sessions","decision_snapshots","mlto_thesis"]:
        cols = c.execute(text("""select column_name,data_type from information_schema.columns
                                 where table_schema='public' and table_name=:t order by ordinal_position"""),{"t":t}).fetchall()
        print("="*10, t, len(cols))
        print(", ".join(f"{n}:{d}" for n,d in cols))
