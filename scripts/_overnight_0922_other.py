import pathlib, psycopg
def dsn(u="alpha_arena"):
    env={}
    for line in pathlib.Path(".env").read_text(encoding="utf-8",errors="replace").splitlines():
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k,v=line.split("=",1); env[k.strip()]=v.strip().strip('"').strip("'")
    key="DATABASE_URL" if u=="alpha_arena" else "ANALYTICS_DATABASE_URL"
    s=env.get(key,"")
    for j in ("+psycopg2","+psycopg","+asyncpg"): s=s.replace(j,"")
    return s
OUT=[]
def p(*a): OUT.append(" ".join(str(x) for x in a))
with psycopg.connect(dsn()) as c:
    cur=c.cursor()
    p("== lane_ledger 全 lane 昨晚活动 ==")
    cur.execute("""select lane_id, count(*), round(sum(net_bp*notional/1e4)::numeric,3)
                   from lane_ledger where ts >= '2026-09-21 18:00' group by 1 order by 2 desc""")
    for r in cur.fetchall(): p("  ", r)
    p("\n== paper_positions 昨晚开启/平仓（全部策略） ==")
    try:
        cur.execute("""select strategy_id, count(*),
                       sum(case when closed_at is not null then 1 else 0 end),
                       round(coalesce(sum(partial_realized_pnl),0)::numeric,3)
                       from paper_positions where opened_at >= '2026-09-21 18:00' group by 1 order by 2 desc""")
        for r in cur.fetchall(): p("  ", r)
    except Exception as e: p("  err:", e)
    p("\n== paper_balances 变化 ==")
    try:
        cur.execute("select account_id, total_equity, realized_pnl, updated_at from paper_balances order by updated_at desc limit 8")
        for r in cur.fetchall(): p("  ", r)
    except Exception as e: p("  err:", e)
with psycopg.connect(dsn("alpha_analytics")) as c:
    cur=c.cursor()
    p("\n== alpha_analytics: ai_decision_logs 昨晚 ==")
    try:
        cur.execute("""select account_id, executed, count(*) from ai_decision_logs
                       where created_at >= '2026-09-21 18:00' group by 1,2 order by 3 desc limit 15""")
        for r in cur.fetchall(): p("  ", r)
    except Exception as e: p("  err:", e)
pathlib.Path("logs/_tmp_timeline/other_lanes.txt").write_text("\n".join(OUT), encoding="utf-8")
print("saved")
