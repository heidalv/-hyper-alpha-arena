# -*- coding: utf-8 -*-
"""队列现状：所有 trial 键 + 活跃状态 + h410 队列推进器消费的文件。只读。"""
import sys
import json
import pathlib
import datetime as dt
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]

now = dt.datetime.now(dt.timezone.utc)
print("== 全部 trial 键（活跃/已决）==")
for k in sorted(m):
    if "trial" in k:
        v = m[k]
        if not isinstance(v, dict):
            print(f"  {k}: {v}")
            continue
        st = v.get("started_at", "")
        ju = v.get("judge_at", "")
        ve = v.get("verdict", "(pending)")
        active = bool(st and ju and st < now.isoformat() < ju)
        print(f"  {k}: verdict={ve} active={'YES' if active else 'no'} "
              f"start={st[:16]} judge={ju[:16]}")

print("\n== 队列相关键 ==")
for k in sorted(m):
    if any(x in k for x in ("queue", "next_deploy", "h4xx", "pending")):
        print(f"  {k}: {json.dumps(m[k], ensure_ascii=False)[:300]}")

print("\n== h410 队列推进器状态文件 ==")
p = ROOT / "research_l1" / "out"
for f in sorted(p.glob("h410*")) if p.exists() else []:
    print("  ", f.name, f.stat().st_size, "B")

print("\n== 现有试跑脚本（防撞车清单）==")
for f in sorted((ROOT / "scripts").glob("h3[5-9]*_trial.py")) + sorted((ROOT / "scripts").glob("h4[0-2]*trial*.py")):
    print("  ", f.name)
