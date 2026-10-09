"""轮130 运行时验证：活主脑是否真的在按周期辩论（日志 + 落库）。"""
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("等待活主脑跑几轮（最多 300s）…")
log = Path("logs/brain_subprocess.log")
found = []
for _ in range(30):
    time.sleep(10)
    txt = log.read_text(encoding="utf-8", errors="replace")
    lines = [ln for ln in txt.splitlines() if "辩论" in ln]
    found = lines
    if len(lines) >= 2:
        break

print(f"\n日志里 [MidLongBrain] 辩论 行数 = {len(found)}")
for ln in found[-6:]:
    m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", ln)
    print(f"  {m.group(1) if m else '?'} {ln.split('辩论', 1)[-1][:190]}")

from sqlalchemy import text  # noqa: E402

from backend.database.connection import analytics_engine  # noqa: E402

with analytics_engine.connect() as c:
    n = c.execute(text("select count(*) from mlto_debate_log")).scalar()
    print(f"\nmlto_debate_log 行数 = {n}")
    rows = c.execute(text(
        "select ts, side, content_json from mlto_debate_log order by id desc limit 2")).fetchall()
    for ts, side, cj in rows:
        print(f"  {ts} {side}: {str(cj)[:260]}")

# 论题事件里是否带上了辩论字段（主脑 append_event）
try:
    from backend.database.connection import analytics_engine as ae
    with ae.connect() as c:
        cols = [r[0] for r in c.execute(text(
            "select column_name from information_schema.columns where table_name='mlto_thesis_events'"))]
        print("\nmlto_thesis_events 列 =", cols)
        tcol = "created_at" if "created_at" in cols else ("ts" if "ts" in cols else cols[0])
        rows = c.execute(text(
            f"select {tcol}, symbol, event, payload_json from mlto_thesis_events "
            f"where payload_json like '%debate%' order by {tcol} desc limit 3")).fetchall()
        print(f"带 debate 字段的论题事件 = {len(rows)} 条（最近 3）")
        for r in rows:
            print(f"  {str(r[0])[:19]} {r[1]} {r[2]} | {str(r[3])[:220]}")
except Exception as exc:  # noqa: BLE001
    print(f"  [warn] {type(exc).__name__}: {str(exc)[:120]}")
