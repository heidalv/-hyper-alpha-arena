import pathlib, psycopg
def env():
    e={}
    for line in pathlib.Path(".env").read_text(encoding="utf-8",errors="replace").splitlines():
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k,v=line.split("=",1); e[k.strip()]=v.strip().strip('"').strip("'")
    return e
def dsn():
    s=env()["DATABASE_URL"]
    for j in ("+psycopg2","+psycopg","+asyncpg"): s=s.replace(j,"")
    return s
OUT=[]
def p(*a): OUT.append(" ".join(str(x) for x in a))
with psycopg.connect(dsn()) as c:
    cur=c.cursor()
    p("== 出库腿 taker 费：按时期拆分（对照账户 101 的 paper_fee） ==")
    cur.execute("""
      select case when ts <  timestamptz '2026-09-14 11:44:43+08' then 'A 账户创建前(9/9-9/14)'
                  else 'B 账户存在期(9/14 起)' end seg,
             count(*), round(sum(fee_bp*notional/1e4)::numeric,4)
      from lane_ledger where lane_id='mm_asterdex' and fee_bp <> 0 group by 1 order by 1""")
    for r in cur.fetchall(): p(f"   {r[0]:<26} 腿={r[1]:<6} 费={r[2]}")
    cur.execute("""select count(*), round(sum(amount_usd)::numeric,4) from arbitrage_paper_ledgers
                   where account_id=101 and action='paper_fee'""")
    r=cur.fetchone(); p(f"   账户101 paper_fee 流水       笔={r[0]:<6} 费={r[1]}")
    p("\n== 时间范围对照 ==")
    cur.execute("""select min(ts), max(ts) from lane_ledger where lane_id='mm_asterdex' and fee_bp <> 0""")
    p("   账本出库腿:", cur.fetchone())
    cur.execute("""select min(created_at), max(created_at) from arbitrage_paper_ledgers where account_id=101 and action='paper_fee'""")
    p("   账户费用流水:", cur.fetchone())
pathlib.Path("logs/_tmp_timeline/fee_timeline.txt").write_text("\n".join(OUT), encoding="utf-8")
print("\n".join(OUT))
