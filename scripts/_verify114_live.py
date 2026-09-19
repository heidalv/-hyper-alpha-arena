# -*- coding: utf-8 -*-
"""轮114 收尾：确认 (1) boot 与 HEAD 同步 (2) 通用串已绝迹 (3) 冷却拦截现在带具体原因。"""
import io
import json
import sys
import urllib.request
from datetime import datetime

sys.path.insert(0, ".")
from sqlalchemy import text
from backend.database.connection import AnalyticsSessionLocal

OUT = io.open("reports/_verify114_live.txt", "w", encoding="utf-8")


def w(*a):
    OUT.write(" ".join(str(x) for x in a) + "\n")


h = json.loads(urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=20).read().decode())
b = h["boot_fingerprint"]
w("boot_git_hash =", b["boot_git_hash"], "| code_git_head =", b["code_git_head"], "| matches =", b["matches_disk"])
boot_local = datetime.fromtimestamp(float(b["boot_at_unix"]))
w("boot(local) =", boot_local)

db = AnalyticsSessionLocal()
db.execute(text("select set_config('app.is_admin','on',false)"))

w("\n== boot 之后的 open_execute_false（应全部带具体原因）==")
for r in db.execute(text("""
    SELECT ts, payload_json::json->>'symbol', payload_json::json->>'tier',
           payload_json::json->>'reason', LEFT(COALESCE(payload_json::json->>'reason_detail',''),70)
    FROM mlto_thesis_events
    WHERE event_type='open_execute_false' AND ts >= :b
    ORDER BY ts DESC LIMIT 25"""), {"b": boot_local}).fetchall():
    w("   ", r)

w("\n== boot 之后 通用串 / 总数 ==")
w("   ", db.execute(text("""
    SELECT COUNT(*) FILTER (WHERE payload_json::json->>'reason' = 'evaluate_and_execute_returned_false'),
           COUNT(*)
    FROM mlto_thesis_events WHERE event_type='open_execute_false' AND ts >= :b"""),
    {"b": boot_local}).fetchone())

w("\n== 修复前后 24h 对照（通用串占比）==")
w("   ", db.execute(text("""
    SELECT COUNT(*) FILTER (WHERE payload_json::json->>'reason' = 'evaluate_and_execute_returned_false') AS generic,
           COUNT(*) AS total
    FROM mlto_thesis_events
    WHERE event_type='open_execute_false' AND ts >= now() - interval '24 hours'""")).fetchone())

w("\n== 冷却表现状（cooldowns）==")
try:
    _s = json.loads(io.open("data/proposal_block_streaks.json", encoding="utf-8").read())
    for k, v in (_s.get("cooldowns") or {}).items():
        w("   ", k, v)
except Exception as e:
    w("   (读取失败)", e)

w("\n== 最近 48h 的 midlong_cooldown_block 事件（既有 reentry_cooldown 仍在拦）==")
for r in db.execute(text("""
    SELECT ts, LEFT(payload_json::json->>'reason', 90)
    FROM mlto_thesis_events
    WHERE event_type='open_execute_false' AND ts >= now() - interval '48 hours'
      AND payload_json::json->>'reason' LIKE '%cooldown%'
    ORDER BY ts DESC LIMIT 15""")).fetchall():
    w("   ", r)

db.close()
OUT.close()
print("written reports/_verify114_live.txt")
