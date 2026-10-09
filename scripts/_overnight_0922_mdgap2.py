import pathlib, psycopg
def env():
    e={}
    for line in pathlib.Path(".env").read_text(encoding="utf-8",errors="replace").splitlines():
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k,v=line.split("=",1); e[k.strip()]=v.strip().strip('"').strip("'")
    return e
def dsn(key):
    s=env().get(key,"")
    for j in ("+psycopg2","+psycopg","+asyncpg"): s=s.replace(j,"")
    return s
OUT=[]
def p(*a): OUT.append(" ".join(str(x) for x in a))
with psycopg.connect(dsn("MARKET_DATABASE_URL")) as c:
    cur=c.cursor()
    cur.execute("""select table_name from information_schema.tables
                   where table_schema='public' and (table_name like '%%orderbook%%'
                   or table_name like '%%book%%' or table_name like '%%depth%%') order by 1""")
    p("alpha_market 相关表:", [r[0] for r in cur.fetchall()])
    cur.execute("""select column_name, data_type from information_schema.columns
                   where table_name='market_orderbook_snapshots' order by ordinal_position""")
    p("\nmarket_orderbook_snapshots 列:", [r[0] for r in cur.fetchall()])
    for sym in ("ASTER","XRP","SOL","HYPE"):
        cur.execute("""
          with s as (
            select timestamp, lag(timestamp) over (order by timestamp) prev
            from market_orderbook_snapshots
            where symbol=%s and timestamp >= '2026-09-21 18:00' and timestamp < '2026-09-22 09:20'
          )
          select count(*),
                 round(percentile_cont(0.5) within group (order by extract(epoch from (timestamp-prev)))::numeric,1),
                 round(max(extract(epoch from (timestamp-prev)))::numeric,1),
                 count(*) filter (where extract(epoch from (timestamp-prev)) > 60)
          from s where prev is not null
        """, (sym,))
        r=cur.fetchone()
        p(f"  {sym:<7} 快照 {r[0]:>6}  间隔中位 {float(r[1]):>6.1f}s  最大 {float(r[2]):>7.1f}s  >60s 缺口 {r[3]}")
    p("\n每小时（4 币合计）：")
    cur.execute("""
      with s as (
        select symbol, timestamp, lag(timestamp) over (partition by symbol order by timestamp) prev
        from market_orderbook_snapshots
        where timestamp >= '2026-09-21 18:00' and timestamp < '2026-09-22 09:20'
          and symbol in ('ASTER','XRP','SOL','HYPE')
      )
      select date_trunc('hour', timestamp) h, count(*),
             count(*) filter (where extract(epoch from (timestamp-prev)) > 60),
             round(max(extract(epoch from (timestamp-prev)))::numeric,0)
      from s group by 1 order by 1
    """)
    for r in cur.fetchall():
        p(f"  {r[0]:%m-%d %H:%M}  快照 {r[1]:>6}   >60s 缺口 {r[2]:>3}   最大间隔 {float(r[3]):>5.0f}s")
pathlib.Path("logs/_tmp_timeline/md_gaps.txt").write_text("\n".join(OUT), encoding="utf-8")
print("saved")
