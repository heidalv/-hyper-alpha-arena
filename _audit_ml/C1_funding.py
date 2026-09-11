from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### paper_funding_ledger")
    print("count:", c.execute(text("select count(*) from paper_funding_ledger")).scalar())
    for r in c.execute(text("""select count(*) n, round(sum(payment)::numeric,4) total,
                                      min(created_at) mn, max(created_at) mx
                               from paper_funding_ledger""")):
        print(dict(r._mapping))
    print("\n### 按账户")
    for r in c.execute(text("""select account_id, count(*) n, round(sum(payment)::numeric,4) total
                               from paper_funding_ledger group by 1 order by 1""")):
        print(dict(r._mapping))
    print("\n### 样本")
    for r in c.execute(text("select * from paper_funding_ledger order by id desc limit 5")):
        print(dict(r._mapping))
    print("\n### 配置")
    import os, sys
    sys.path.insert(0, os.getcwd())
    from dotenv import load_dotenv
    load_dotenv('.env', override=False)
    from backend.config import settings as S
    for k in ("FUNDING_SETTLE_ENABLED","FUNDING_SETTLE_INTERVAL_SEC","MIDLONG_FUNDING_GATE_ENABLED","MIDLONG_FUNDING_ABS_WARN"):
        print(f"  {k} =", getattr(S, k, os.getenv(k)))
