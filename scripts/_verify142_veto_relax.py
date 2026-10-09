"""轮142 验证：放宽后的风控官否决（只否反向）+ 是否开始有成交。"""
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text  # noqa: E402

from backend.database.connection import analytics_engine, engine  # noqa: E402

SINCE = "2026-09-20 16:00"
bl = Path("logs/backend.log").read_text(encoding="utf-8", errors="replace").splitlines()
aud = [ln for ln in bl if "[MidLongAudit]" in ln and ln[:19] >= SINCE]
print(f"审计原因分布（{SINCE}+）共 {len(aud)} 行：")
for k, v in Counter(ln.split("reason=")[-1][:52] for ln in aud).most_common(8):
    print(f"   {v:>4}  {k}")

ro = [ln for ln in bl if "RiskOfficer" in ln and ln[:19] >= SINCE]
print(f"\n风控官日志 {len(ro)} 行：")
for ln in ro[-6:]:
    print("   ", ln[:19], ln.split("] ", 1)[-1][:140])

with analytics_engine.connect() as c:
    rows = c.execute(text(
        "select allow, count(*), max(ts) from risk_officer_decisions "
        "where ts > now() - interval '40 minutes' group by allow")).fetchall()
    print("\n风控官近 40 分钟判定：",
          [(("allow" if x[0] else "veto"), x[1], str(x[2])[:19]) for x in rows])
    rows = c.execute(text(
        "select ts, symbol, side, allow, reason, checks_json from risk_officer_decisions "
        "where ts > now() - interval '40 minutes' order by id desc limit 5")).fetchall()
    import json as j
    for x in rows:
        ch = [y for y in j.loads(x[5] or "[]") if y.get("name") == "debate_posture"]
        val = ch[0].get("value") if ch else None
        print(f"   {str(x[0])[:19]} {x[1]:<7} {x[2]:<5} allow={x[3]} {str(x[4])[:34]:<34} debate={val}")

with engine.connect() as c:
    n = c.execute(text("select count(*) from paper_positions where opened_at >= :t"),
                  {"t": "2026-09-20 15:50"}).scalar()
    print(f"\n15:50 后开仓 = {n}")
    for r in c.execute(text(
            "select id, symbol, side, margin, opened_at from paper_positions "
            "where opened_at >= :t order by id desc limit 5"), {"t": "2026-09-20 15:50"}):
        print("   ", r)
