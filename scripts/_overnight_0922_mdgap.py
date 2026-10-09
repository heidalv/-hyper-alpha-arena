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
    p("== 车道宇宙 4 币的盘口快照连续性（车道 mid 的输入表） ==")
    cur.execute("""select column_name from information_schema.columns
                   where table_name='market_orderbook_snapshots' order by ordinal_position""")
    p("  列:", [r[0] for r in cur.fetchall()])
    cur.execute("""select exchange, count(*) from market_orderbook_snapshots
                   where timestamp >= '2026-09-21 18:00' group by 1 order by 2 desc limit 5""")
    p("  交易所:", cur.fetchall())
    cur.execute("""
      with s as (
        select symbol, timestamp,
               lag(timestamp) over (partition by symbol order by timestamp) prev
        from market_orderbook_snapshots
        where timestamp >= '2026-09-21 17:30' and timestamp < '2026-09-22 09:20'
          and symbol in ('ASTER','XRP','SOL','HYPE')
      )
      select symbol, count(*),
             round(percentile_cont(0.5) within group (order by extract(epoch from (timestamp-prev)))::numeric,1),
             round(max(extract(epoch from (timestamp-prev)))::numeric,1),
             count(*) filter (where extract(epoch from (timestamp-prev)) > 60)
      from s where prev is not null group by 1 order by 1
    """)
    p("\n  {'币':<8}{'快照数':>8}{'间隔中位s':>11}{'最大间隔s':>11}{'>60s缺口数':>12}")
    for r in cur.fetchall():
        p(f"  {r[0]:<8}{r[1]:>8}{float(r[2]):>11.1f}{float(r[3]):>11.1f}{r[4]:>12}")
    p("\n== 每小时：4 币合计快照数 / >60s 缺口数 ==")
    cur.execute("""
      with s as (
        select symbol, timestamp,
               lag(timestamp) over (partition by symbol order by timestamp) prev
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
        p(f"  {r[0]:%m-%d %H:%M}  快照 {r[1]:>6}   >60s缺口 {r[2]:>3}   最大间隔 {float(r[3]):>6.0f}s")
pathlib.Path("logs/_tmp_timeline/md_gaps.txt").write_text("\n".join(OUT), encoding="utf-8")
print("\n".join(OUT[:12]))
