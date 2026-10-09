import pathlib, psycopg
from datetime import datetime, timezone, timedelta
CST=timezone(timedelta(hours=8))
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
A=int(datetime(2026,9,21,18,0,tzinfo=CST).timestamp()*1000)
B=int(datetime(2026,9,22,9,20,tzinfo=CST).timestamp()*1000)
OUT=[]
def p(*a): OUT.append(" ".join(str(x) for x in a))
p(f"窗口 epoch ms: {A} → {B}")
with psycopg.connect(dsn("MARKET_DATABASE_URL")) as c:
    cur=c.cursor()
    cur.execute("""select column_name, data_type from information_schema.columns
                   where table_name='market_orderbook_snapshots' order by ordinal_position""")
    p("列:", [f"{r[0]}:{r[1]}" for r in cur.fetchall()])
    cur.execute("""select exchange, count(*) from market_orderbook_snapshots
                   where timestamp >= %s and timestamp < %s group by 1 order by 2 desc""", (A,B))
    p("交易所分布:", cur.fetchall())
    for sym in ("ASTER","XRP","SOL","HYPE"):
        cur.execute("""
          with s as (
            select timestamp, lag(timestamp) over (order by timestamp) prev
            from market_orderbook_snapshots
            where symbol=%s and timestamp >= %s and timestamp < %s
          )
          select count(*),
                 percentile_cont(0.5) within group (order by (timestamp-prev)/1000.0),
                 max((timestamp-prev)/1000.0),
                 count(*) filter (where (timestamp-prev)/1000.0 > 60)
          from s where prev is not null
        """, (sym, A, B))
        r=cur.fetchone()
        p(f"  {sym:<7} 快照 {r[0]:>6}  间隔中位 {float(r[1]):>6.1f}s  最大 {float(r[2]):>8.1f}s  >60s 缺口 {r[3]}")
    p("\n每小时（4 币合计）：")
    cur.execute("""
      with s as (
        select symbol, timestamp, lag(timestamp) over (partition by symbol order by timestamp) prev
        from market_orderbook_snapshots
        where timestamp >= %s and timestamp < %s and symbol in ('ASTER','XRP','SOL','HYPE')
      )
      select to_char(to_timestamp(timestamp/1000.0) at time zone 'Asia/Shanghai','MM-DD HH24:00') h,
             count(*),
             count(*) filter (where (timestamp-prev)/1000.0 > 60),
             round(max((timestamp-prev)/1000.0)::numeric,0)
      from s group by 1 order by 1
    """, (A,B))
    for r in cur.fetchall():
        p(f"  {r[0]}  快照 {r[1]:>6}   >60s 缺口 {r[2]:>3}   最大间隔 {float(r[3]):>5.0f}s")
pathlib.Path("logs/_tmp_timeline/md_gaps.txt").write_text("\n".join(OUT), encoding="utf-8")
print("saved")
