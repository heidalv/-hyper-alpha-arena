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
def dsn(key="DATABASE_URL"):
    s=env().get(key,"")
    for j in ("+psycopg2","+psycopg","+asyncpg"): s=s.replace(j,"")
    return s
A=datetime(2026,9,21,18,0,tzinfo=CST); B=datetime(2026,9,22,9,20,tzinfo=CST)
OUT=[]
def p(*a): OUT.append(" ".join(str(x) for x in a))
with psycopg.connect(dsn()) as c:
    cur=c.cursor()
    p("== 车道成交自带 mid 的『重复值跨度』检验 ==")
    p("   （若 mid 在数据缺口期间被冻结，会出现同一 mid 值横跨 >60s 的连续成交）")
    cur.execute("""
      with s as (
        select ts, symbol, (meta_json->>'mid_px')::float mid,
               lag(ts) over (partition by symbol order by ts) pts,
               lag((meta_json->>'mid_px')::float) over (partition by symbol order by ts) pmid
        from lane_ledger where lane_id='mm_asterdex' and ts >= %s and ts < %s
      )
      select symbol, count(*) filter (where mid = pmid and extract(epoch from (ts-pts)) > 60),
             coalesce(max(extract(epoch from (ts-pts))) filter (where mid = pmid), 0),
             percentile_cont(0.5) within group (order by extract(epoch from (ts-pts)))
      from s where pts is not null group by 1 order by 1
    """, (A,B))
    p(f"  {'币':<8}{'同mid且>60s的成交数':>22}{'同mid最长间隔s':>18}{'相邻成交间隔中位s':>20}")
    for r in cur.fetchall():
        p(f"  {r[0]:<8}{r[1]:>22}{float(r[2]):>18.1f}{float(r[3]):>20.1f}")
    p("\n== 01:00-01:30 的成交间隔最大 10 段 ==")
    cur.execute("""
      with s as (select ts, symbol, (meta_json->>'mid_px')::float mid,
                        lag(ts) over (partition by symbol order by ts) pts,
                        lag((meta_json->>'mid_px')::float) over (partition by symbol order by ts) pmid
                 from lane_ledger where lane_id='mm_asterdex'
                   and ts >= '2026-09-22 01:00+08' and ts < '2026-09-22 01:30+08')
      select to_char(ts at time zone 'Asia/Shanghai','HH24:MI:SS') t, symbol,
             round(extract(epoch from (ts-pts))::numeric,0) dt, (mid=pmid) same
      from s where pts is not null order by ts-pts desc limit 10
    """)
    for r in cur.fetchall(): p(f"    {r[0]}  {r[1]:<7} 间隔 {float(r[2]):>5.0f}s   mid相同={r[3]}")
    p("\n== 01:00 那一小时的中间价移动（用成交 mid 重建） ==")
    cur.execute("""
      select symbol, count(*), min((meta_json->>'mid_px')::float), max((meta_json->>'mid_px')::float),
             round((10000*(max((meta_json->>'mid_px')::float)/min((meta_json->>'mid_px')::float)-1))::numeric,1)
      from lane_ledger where lane_id='mm_asterdex'
        and ts >= '2026-09-22 01:00+08' and ts < '2026-09-22 02:00+08' group by 1 order by 1
    """)
    for r in cur.fetchall(): p(f"    {r[0]:<7} n={r[1]:>4} mid {float(r[2]):<12} → {float(r[3]):<12} 幅度 {float(r[4]):>7.1f} bp")
pathlib.Path("logs/_tmp_timeline/mid_freeze.txt").write_text("\n".join(OUT), encoding="utf-8")
print("saved")
