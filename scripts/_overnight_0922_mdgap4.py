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
with psycopg.connect(dsn("MARKET_DATABASE_URL")) as c:
    cur=c.cursor()
    p("== 只看 asterdex 的 4 币 ==")
    for sym in ("ASTER","XRP","SOL","HYPE"):
        cur.execute("""
          with s as (select timestamp, lag(timestamp) over (order by timestamp) prev
                     from market_orderbook_snapshots
                     where exchange='asterdex' and symbol=%s and timestamp >= %s and timestamp < %s)
          select count(*), percentile_cont(0.5) within group (order by (timestamp-prev)/1000.0),
                 max((timestamp-prev)/1000.0), count(*) filter (where (timestamp-prev)/1000.0 > 60)
          from s where prev is not null
        """, (sym,A,B))
        r=cur.fetchone()
        p(f"  {sym:<7} 快照 {r[0]:>6}  中位 {float(r[1]):>5.1f}s  最大 {float(r[2]):>8.1f}s  >60s {r[3]}")
    p("\n== asterdex 4 币：>60s 的缺口明细（时间 / 时长） ==")
    cur.execute("""
      with s as (select symbol, timestamp, lag(timestamp) over (partition by symbol order by timestamp) prev
                 from market_orderbook_snapshots
                 where exchange='asterdex' and symbol in ('ASTER','XRP','SOL','HYPE')
                   and timestamp >= %s and timestamp < %s)
      select to_char(to_timestamp(timestamp/1000.0) at time zone 'Asia/Shanghai','MM-DD HH24:MI:SS') t,
             symbol, round(((timestamp-prev)/1000.0)::numeric,0) gap_s
      from s where prev is not null and (timestamp-prev)/1000.0 > 60 order by timestamp
    """, (A,B))
    rows=cur.fetchall()
    p(f"  共 {len(rows)} 条")
    for r in rows: p(f"    {r[0]}  {r[1]:<7} {float(r[2]):>6.0f}s")
    p("\n== 各交易所 4 币快照数（确认车道读哪个源） ==")
    cur.execute("""select exchange, count(*) from market_orderbook_snapshots
                   where symbol in ('ASTER','XRP','SOL','HYPE') and timestamp >= %s and timestamp < %s
                   group by 1 order by 2 desc""", (A,B))
    for r in cur.fetchall(): p("  ", r)
pathlib.Path("logs/_tmp_timeline/md_gaps2.txt").write_text("\n".join(OUT), encoding="utf-8")
print("saved")
