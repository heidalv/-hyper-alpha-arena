import pathlib, psycopg
def dsn():
    env={}
    for line in pathlib.Path(".env").read_text(encoding="utf-8",errors="replace").splitlines():
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k,v=line.split("=",1); env[k.strip()]=v.strip().strip('"').strip("'")
    s=env["DATABASE_URL"]
    for j in ("+psycopg2","+psycopg","+asyncpg"): s=s.replace(j,"")
    return s
OUT=[]
def p(*a): OUT.append(" ".join(str(x) for x in a))
with psycopg.connect(dsn()) as c:
    cur=c.cursor()
    p("== 账户101：18:00:34 重置前后 ==")
    cur.execute("""select created_at, action, amount_usd, balance_after from arbitrage_paper_ledgers
                   where account_id=101 and created_at between '2026-09-21 17:59:30' and '2026-09-21 18:02:00'
                   order by created_at""")
    for r in cur.fetchall(): p("  ", r)
    p("\n== 窗口内按 action 分类 ==")
    cur.execute("""select action, count(*), round(sum(amount_usd)::numeric,4) from arbitrage_paper_ledgers
                   where account_id=101 and created_at >= '2026-09-21 18:00:34' group by 1 order by 3""")
    for r in cur.fetchall(): p("  ", r)
    p("\n== 若权益口径含费 vs 不含费 ==")
    cur.execute("""select
        (select round(sum(amount_usd)::numeric,4) from arbitrage_paper_ledgers
          where account_id=101 and created_at >= '2026-09-21 18:00:34' and action='paper_pnl') pnl,
        (select round(sum(amount_usd)::numeric,4) from arbitrage_paper_ledgers
          where account_id=101 and created_at >= '2026-09-21 18:00:34' and action='paper_fee') fee,
        (select round(realized_pnl::numeric,4) from arbitrage_paper_accounts where id=101) realized,
        (select round(available_balance::numeric,4) from arbitrage_paper_accounts where id=101) bal""")
    p("   pnl, fee, realized_pnl, available_balance =", cur.fetchone())
    p("\n== 全历史 fee 合计 vs realized_pnl ==")
    cur.execute("""select action, round(sum(amount_usd)::numeric,4), count(*) from arbitrage_paper_ledgers
                   where account_id=101 group by 1""")
    for r in cur.fetchall(): p("  ", r)
pathlib.Path("logs/_tmp_timeline/acct.txt").write_text("\n".join(OUT), encoding="utf-8")
print("saved")
