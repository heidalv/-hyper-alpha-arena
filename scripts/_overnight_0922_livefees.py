import pathlib, psycopg
def env():
    e={}
    for line in pathlib.Path(".env").read_text(encoding="utf-8",errors="replace").splitlines():
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k,v=line.split("=",1); e[k.strip()]=v.strip().strip('"').strip("'")
    return e
def dsn(key="DATABASE_URL"):
    s=env().get(key,"")
    for j in ("+psycopg2","+psycopg","+asyncpg"): s=s.replace(j,"")
    return s
OUT=[]
def p(*a): OUT.append(" ".join(str(x) for x in a))
with psycopg.connect(dsn()) as c:
    cur=c.cursor()
    for t in ("live_orders","trades","trade_facts"):
        cur.execute("""select column_name, data_type from information_schema.columns
                       where table_name=%s order by ordinal_position""",(t,))
        p(f"\n== {t} 列 ==")
        p("  ", [f"{r[0]}:{r[1]}" for r in cur.fetchall()])
        cur.execute(f"select count(*) from {t}")
        p("   rows:", cur.fetchone()[0])
    p("\n== live_orders 按交易所/时间 ==")
    try:
        cur.execute("""select exchange, count(*), min(created_at), max(created_at)
                       from live_orders group by 1 order by 2 desc""")
        for r in cur.fetchall(): p("  ", r)
    except Exception as e: p("  err:", e)
    p("\n== live_orders 最近 15 条（含 fee 与实际价格） ==")
    try:
        cur.execute("""select created_at, exchange, symbol, side, order_type, size, price,
                              filled_size, filled_price, fee
                       from live_orders order by created_at desc limit 15""")
        for r in cur.fetchall(): p("  ", r)
    except Exception as e: p("  err:", e)
    p("\n== live_orders: fee / 名义 的分布（按 exchange+order_type） ==")
    try:
        cur.execute("""
          select exchange, order_type, count(*),
                 round(avg(case when filled_size*filled_price > 0
                       then fee/(filled_size*filled_price)*10000 end)::numeric,3) bp_avg,
                 round(percentile_cont(0.5) within group (order by
                       case when filled_size*filled_price > 0
                       then fee/(filled_size*filled_price)*10000 end)::numeric,3) bp_med
          from live_orders where coalesce(fee,0) <> 0
          group by 1,2 order by 3 desc
        """)
        for r in cur.fetchall(): p("  ", r)
    except Exception as e: p("  err:", e)
pathlib.Path("logs/_tmp_timeline/livefees.txt").write_text("\n".join(OUT), encoding="utf-8")
print("saved")
