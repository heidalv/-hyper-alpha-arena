"""轮146 验证：方案 B（探针豁免辩论否决）在活链路的效果。"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text  # noqa: E402

from backend.database.connection import analytics_engine, engine  # noqa: E402

SINCE = "2026-09-21 00:"
bl = Path("logs/backend.log").read_text(encoding="utf-8", errors="replace").splitlines()
aud = [ln for ln in bl if "[MidLongAudit]" in ln and ln[:16] >= SINCE]
print(f"审计 {len(aud)} 行（{SINCE}+）:")
for k, v in Counter(ln.split("reason=")[-1][:58] for ln in aud).most_common(8):
    print(f"   {v:>4}  {k}")

print("\n风控官近 5 条判定（看 debate_posture 是否记为探针豁免）:")
with analytics_engine.connect() as c:
    rows = c.execute(text(
        "select ts, symbol, side, allow, reason, checks_json from risk_officer_decisions "
        "order by id desc limit 6")).fetchall()
for r in rows:
    ch = [x for x in json.loads(r[5] or "[]") if x.get("name") == "debate_posture"]
    info = (ch[0].get("skipped") or ch[0].get("value") or ch[0].get("error")) if ch else None
    print(f"   {str(r[0])[:19]} {r[1]:<8} {r[2]:<5} allow={r[3]} {str(r[4])[:30]:<30} | {str(info)[:80]}")

with engine.connect() as c:
    n = c.execute(text("select count(*) from paper_positions where opened_at >= :t"),
                  {"t": "2026-09-21 00:00"}).scalar()
    print(f"\n09-21 开仓 = {n} 笔")
    for r in c.execute(text(
            "select id, symbol, side, margin, opened_at from paper_positions "
            "where opened_at >= :t order by id desc limit 5"), {"t": "2026-09-21 00:00"}):
        print("   ", r)
