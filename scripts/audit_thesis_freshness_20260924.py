import sys
from datetime import datetime, timezone
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.database.connection import analytics_engine
SID = "fa_7e12e7a1b6"
def _utc(dt):
    if dt is None: return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
NOW = datetime.now(timezone.utc)
with analytics_engine.connect() as c:
    rows = c.execute(text("SELECT tier, symbol, accepted, coalesce(analysis_run_id,''), expires_at, updated_at "
                          "FROM mlto_thesis WHERE session_id=:s"), {"s": SID}).fetchall()
agg = {}
for tier, sym, acc, runid, exp, upd in rows:
    d = agg.setdefault(str(tier), {"n":0,"acc":0,"no_run":0,"exp":0,"max_upd":None})
    d["n"] += 1
    d["acc"] += 1 if acc else 0
    d["no_run"] += 1 if not str(runid or "").strip() else 0
    e = _utc(exp)
    d["exp"] += 1 if (e is not None and e <= NOW) else 0
    u = _utc(upd)
    if u and (d["max_upd"] is None or u > d["max_upd"]): d["max_upd"] = u
print("=== 活会话 mlto_thesis（fa_7e12e7a1b6），NOW(UTC) =", NOW.strftime("%Y-%m-%d %H:%M:%S"), "===")
print(f"{'tier':8s} {'总':>4s} {'accepted':>8s} {'缺run_id':>8s} {'已过期':>6s}  last_updated")
for t, d in sorted(agg.items()):
    print(f"{t:8s} {d['n']:4d} {d['acc']:8d} {d['no_run']:8d} {d['exp']:6d}  {d['max_upd']}")
tot = sum(d["n"] for d in agg.values()); exp = sum(d["exp"] for d in agg.values())
print(f"合计 {tot} 条，其中已过期 {exp} 条（{exp/max(1,tot)*100:.1f}%）")
