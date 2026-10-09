import pathlib, psycopg, statistics
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
# 1) 取 asterdex ASTER 的缺口窗口
with psycopg.connect(dsn("MARKET_DATABASE_URL")) as c:
    cur=c.cursor()
    cur.execute("""
      with s as (select timestamp, lag(timestamp) over (order by timestamp) prev
                 from market_orderbook_snapshots
                 where exchange='asterdex' and symbol='ASTER' and timestamp >= %s and timestamp < %s)
      select prev, timestamp from s where prev is not null and (timestamp-prev)/1000.0 > 60 order by prev
    """, (A,B))
    gaps=[(int(r[0]), int(r[1])) for r in cur.fetchall()]
p(f"asterdex/ASTER 的 >60s 缺口事件 {len(gaps)} 个")
# 2) 车道腿
with psycopg.connect(dsn("DATABASE_URL")) as c:
    cur=c.cursor()
    cur.execute("""select ts from lane_ledger where lane_id='mm_asterdex' and ts >= %s and ts < %s""",
                (datetime(2026,9,21,18,0,tzinfo=CST), datetime(2026,9,22,9,20,tzinfo=CST)))
    ts=[r[0].timestamp() for r in cur.fetchall()]
ts.sort()
tot_h=len(ts)/((B-A)/1000/3600)
p(f"车道腿数 {len(ts)}   全天平均 {tot_h:.1f} 腿/小时 = {tot_h/60:.2f} 腿/分钟")
def cnt(a,b):
    import bisect
    return bisect.bisect_left(ts,b)-bisect.bisect_left(ts,a)
p(f"\n  {'缺口开始':<20}{'时长s':>7}{'缺口内腿数':>11}{'缺口内每分钟':>13}{'前5分钟每分钟':>14}")
ing=[]; pre=[]
for a,b in gaps:
    a_s=a/1000.0; b_s=b/1000.0
    dur=(b_s-a_s)/60.0
    n_in=cnt(a_s,b_s)
    n_pre=cnt(a_s-300,a_s)
    ing.append(n_in/dur if dur else 0); pre.append(n_pre/5.0)
    p(f"  {datetime.fromtimestamp(a_s,CST):%m-%d %H:%M:%S}{b_s-a_s:>7.0f}{n_in:>11}{n_in/dur if dur else 0:>13.2f}{n_pre/5.0:>14.2f}")
p(f"\n  缺口期间平均 {statistics.mean(ing):.2f} 腿/分钟  vs  缺口前 5 分钟 {statistics.mean(pre):.2f} 腿/分钟"
  f"  ⇒ 比值 {statistics.mean(ing)/max(statistics.mean(pre),1e-9):.2f}")
p(f"  缺口期间完全无成交的比例：{100*sum(1 for x in ing if x==0)/len(ing):.0f}%")
p(f"  全天基线 {tot_h/60:.2f} 腿/分钟")
pathlib.Path("logs/_tmp_timeline/gap_impact.txt").write_text("\n".join(OUT), encoding="utf-8")
print("\n".join(OUT[-6:]))
