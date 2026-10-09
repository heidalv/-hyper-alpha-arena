# -*- coding: utf-8 -*-
"""修复 h429 判定丢失（并行判定任务的读改写竞争）：把 JSON 里的判定合并回注册表。"""
import sys
import json
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

vd = json.loads((ROOT / "research_l1" / "out" / "h429_verdict.json")
                .read_text(encoding="utf-8"))

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]
t = dict(m.get("h429_trial") or {})
t["verdict"] = vd.get("verdict")
t["judged_at"] = vd.get("judged_at")
t["why"] = vd.get("why")
m["h429_trial"] = t
cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now() WHERE lane_id='mm_asterdex'",
            (json.dumps(m, ensure_ascii=False, default=str),))
c.commit()
print("h429 判定已合并：", t.get("verdict"), t.get("why"))

# 复核其他四项是否也已丢失（防漏）
for k in ("h392_trial", "h425_trial", "h426_trial", "h427_trial"):
    print(k, "verdict =", (m.get(k) or {}).get("verdict"))
