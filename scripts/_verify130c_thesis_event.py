"""轮130 验证（补）：辩论裁决是否随论题事件落库（mlto_thesis_events.payload_json.debate）。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text  # noqa: E402

from backend.database.connection import analytics_engine  # noqa: E402

with analytics_engine.connect() as c:
    rows = c.execute(text(
        "select ts, thesis_id, event_type, payload_json from mlto_thesis_events "
        "where payload_json like '%\"debate\"%' order by ts desc limit 5")).fetchall()

print(f"带 debate 字段的论题事件 = {len(rows)} 条")
for ts, tid, etype, pj in rows:
    p = json.loads(pj) if pj else {}
    d = p.get("debate") or {}
    print(f"  {str(ts)[:19]} {str(tid)[:24]} {etype}")
    print(f"      verdict={d.get('verdict')} 主周期={d.get('primary_horizon')}:{d.get('primary_verdict')} "
          f"分周期={d.get('horizon_verdicts')} 冲突={d.get('horizon_conflict')} "
          f"risk_min={d.get('risk_min')} llm={d.get('llm')}")
    for e in (d.get("evidence") or [])[:2]:
        print(f"      证据: {str(e)[:110]}")
