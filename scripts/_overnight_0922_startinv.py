import pathlib, psycopg
from datetime import datetime, timedelta, timezone
CST=timezone(timedelta(hours=8))
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
    cur.execute("""select ts, symbol, (meta_json->>'qty')::float, lower(meta_json->>'side'),
                          (meta_json->>'mid_px')::float
                   from lane_ledger where lane_id='mm_asterdex' and event='fill' order by ts""")
    inv={}; mids={}; snap={}
    marks=[datetime(2026,9,21,18,0,0,tzinfo=CST).timestamp(), datetime(2026,9,21,18,0,34,tzinfo=CST).timestamp()]
    for ts,sym,qty,side,mid in cur.fetchall():
        t=ts.timestamp()
        if t < marks[0]:
            inv[sym]=inv.get(sym,0.0)+(1.0 if side=="buy" else -1.0)*float(qty or 0.0)
            if mid: mids[sym]=float(mid)
        if t < marks[1]:
            snap[sym]=(inv.get(sym,0.0), mids.get(sym,0.0))
    p("== 窗口起点（18:00:34 重置时刻）继承的库存 ==")
    g=0.0; n=0.0
    for s,(q,m) in sorted(snap.items()):
        if abs(q) > 1e-9:
            p(f"  {s:<8} qty={q:>12.4f}  mid={m:<10}  名义 ${abs(q)*m:>8.2f}")
            g+=abs(q)*m; n+=q*m
    p(f"  ⇒ 总敞口 ${g:.2f}  净敞口 ${n:+.2f}  （此时的统计时代权益基准 = $300）")
pathlib.Path("logs/_tmp_timeline/start_inv.txt").write_text("\n".join(OUT), encoding="utf-8")
print("\n".join(OUT))
