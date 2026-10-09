"""轮131 副作用核查：辩论/风控官上线后，中线**还在不在开仓**（用户最在意"别又冻住"）。"""
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text  # noqa: E402

from backend.database.connection import analytics_engine, engine  # noqa: E402

print("=== 1) 今天 11:25 之后开的中线/长线仓（paper）===")
with engine.connect() as c:
    cols = [r[0] for r in c.execute(text(
        "select column_name from information_schema.columns where table_name='paper_positions'"))]
    want = [x for x in ("id", "symbol", "side", "timeframe_tier", "trade_nature", "entry_source",
                        "margin", "opened_at", "created_at", "status") if x in cols]
    tcol = "opened_at" if "opened_at" in cols else ("created_at" if "created_at" in cols else "updated_at")
    rows = c.execute(text(
        f"select {', '.join(want)} from paper_positions where {tcol} >= :t "
        f"order by {tcol} desc limit 15"),
        {"t": datetime(2026, 9, 20, 11, 25)}).fetchall()
    print(f"   （列：{want}；时间列={tcol}）")
if not rows:
    print("   （11:25 之后无新开仓）")
for r in rows:
    print("   " + " | ".join(str(x)[:24] for x in r))

print("\n=== 2) 最近 12 条论题刷新（辩论后的 accepted/rec_open 分布）===")
with analytics_engine.connect() as c:
    rows = c.execute(text(
        "select symbol, tier, updated_at, accepted, recommend_open, direction, llm_conviction "
        "from mlto_thesis order by updated_at desc limit 12")).fetchall()
for r in rows:
    print(f"   {r[0]:<8} {r[1]:<5} {str(r[2])[:19]} accepted={r[3]} rec_open={r[4]} dir={r[5]:<8} conv={r[6]}")

print("\n=== 3) 辩论对 conviction 的调整次数（近 2h 日志）===")
txt = Path("logs/brain_subprocess.log").read_text(encoding="utf-8", errors="replace")
adj = [ln for ln in txt.splitlines() if "辩论调整 conviction" in ln]
print(f"   调整次数 = {len(adj)}")
for ln in adj[-6:]:
    print("   ", ln.split("辩论调整", 1)[-1][:110])

print("\n=== 4) 六域信号对 conviction 的影子影响（近 2h）===")
with analytics_engine.connect() as c:
    rows = c.execute(text(
        "select count(*), count(*) filter (where would_change), "
        "avg(blended - conviction_before), min(blended - conviction_before), max(blended - conviction_before) "
        "from analyst_blend_shadow where ts >= now() - interval '2 hours'")).fetchone()
print(f"   影子样本={rows[0]}  会改变={rows[1]}  平均Δ={rows[2]}  最小Δ={rows[3]}  最大Δ={rows[4]}")
