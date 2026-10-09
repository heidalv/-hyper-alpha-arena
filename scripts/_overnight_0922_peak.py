import pathlib, psycopg
from datetime import datetime, timedelta, timezone
CST=timezone(timedelta(hours=8))
def dsn():
    env={}
    for line in pathlib.Path(".env").read_text(encoding="utf-8",errors="replace").splitlines():
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k,v=line.split("=",1); env[k.strip()]=v.strip().strip('"').strip("'")
    u=env["DATABASE_URL"]
    for j in ("+psycopg2","+psycopg","+asyncpg"): u=u.replace(j,"")
    return u
OUT=[]
def p(*a): OUT.append(" ".join(str(x) for x in a))
with psycopg.connect(dsn()) as c:
    cur=c.cursor()
    # 重建全历史库存，找 ASTER/XRP 峰值时刻
    cur.execute("""select ts, symbol, (meta_json->>'qty')::float, lower(meta_json->>'side'),
                          (meta_json->>'mid_px')::float,
                          (lower(coalesce(meta_json->>'flatten','false')) in ('true','1'))
                   from lane_ledger where lane_id='mm_asterdex' and event='fill'
                     and ts >= '2026-09-21 18:00' order by ts""")
    inv={}; mids={}; peak={}
    rows=cur.fetchall()
    for ts,sym,qty,side,mid,flat in rows:
        inv[sym]=inv.get(sym,0.0)+(1.0 if side=="buy" else -1.0)*float(qty or 0.0)
        if mid: mids[sym]=float(mid)
        ntl=abs(inv[sym])*mids.get(sym,0)
        if sym not in peak or ntl>peak[sym][0]: peak[sym]=(ntl,ts,inv[sym],flat)
    p("== 各币峰值|名义| 时刻 ==")
    for s,(ntl,ts,q,flat) in sorted(peak.items(), key=lambda x:-x[1][0]):
        p(f"  {s:<7} ${ntl:>8.0f} @ {ts:%m-%d %H:%M:%S}  qty={q:+.2f} 该腿flatten={flat}")
    # 峰值附近的 ASTER 明细
    s0 = max(peak.items(), key=lambda x: x[1][0])[0]
    t0 = peak[s0][1]
    p(f"\n== {s0} 峰值前后 25 条腿 ==")
    cur.execute("""select ts, symbol, (meta_json->>'qty')::float, lower(meta_json->>'side'),
                          (meta_json->>'mid_px')::float, notional,
                          (lower(coalesce(meta_json->>'flatten','false')) in ('true','1')),
                          (meta_json->>'source')
                   from lane_ledger where lane_id='mm_asterdex' and symbol=%s
                     and ts between %s and %s order by ts""",
                (s0, t0-timedelta(minutes=6), t0+timedelta(minutes=4)))
    inv2=0.0
    # 需要起点库存：用全历史
    cur.execute("""select (meta_json->>'qty')::float, lower(meta_json->>'side')
                   from lane_ledger where lane_id='mm_asterdex' and symbol=%s and event='fill'
                     and ts < %s order by ts""", (s0, t0-timedelta(minutes=6)))
    for q,side in cur.fetchall(): inv2 += (1.0 if side=="buy" else -1.0)*float(q or 0)
    cur.execute("""select ts, symbol, (meta_json->>'qty')::float, lower(meta_json->>'side'),
                          (meta_json->>'mid_px')::float, notional,
                          (lower(coalesce(meta_json->>'flatten','false')) in ('true','1'))
                   from lane_ledger where lane_id='mm_asterdex' and symbol=%s
                     and ts between %s and %s order by ts""",
                (s0, t0-timedelta(minutes=6), t0+timedelta(minutes=4)))
    for ts,sym,qty,side,mid,ntl,flat in cur.fetchall():
        inv2 += (1.0 if side=="buy" else -1.0)*float(qty or 0)
        p(f"  {ts:%H:%M:%S} {side:<5} qty={float(qty):>9.2f} mid={mid} notional={float(ntl):>7.1f} "
          f"-> 库存 {inv2:>9.2f} (${abs(inv2)*(mid or 0):>7.0f}) {'FLAT' if flat else ''}")
pathlib.Path("logs/_tmp_timeline/peak.txt").write_text("\n".join(OUT), encoding="utf-8")
print("saved")
