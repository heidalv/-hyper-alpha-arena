# -*- coding: utf-8 -*-
"""配置清点与证据对齐：每个在线变更 → 证据 → 判定状态 → 建议（保留/回滚）。只读。"""
import sys
import json
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]
p = m.get("params") or {}

# 所有试跑键 → 改了哪些参数、判定状态
rows = []
for k, v in m.items():
    if not k.endswith("_trial") or not isinstance(v, dict):
        continue
    to = v.get("to")
    if isinstance(to, dict):
        fields = list(to.keys())
    elif "field" in v:
        fields = [str(v["field"]).split(".")[-1]]
    else:
        fields = []
    rows.append({
        "trial": k,
        "fields": fields,
        "verdict": v.get("verdict") or "(未判)",
        "started": str(v.get("started_at") or "")[:16],
        "judged": str(v.get("judged_at") or "")[:16],
        "why": str(v.get("why") or "")[:44],
        "bypassed": bool(v.get("guards_bypassed")),
    })

print(f"== 在线试跑清点（{len(rows)} 项）==")
print(f"{'试跑':<12}{'参数':<34}{'判定':<14}{'起':<17}{'依据':<10}")
for r in sorted(rows, key=lambda x: x["started"]):
    f = ",".join(r["fields"])[:32]
    print(f"{r['trial']:<12}{f:<34}{r['verdict']:<14}{r['started']:<17}"
          f"{'force' if r['bypassed'] else 'guards':<10} {r['why']}")

print("\n== 仍处于试跑值（=未获 PASS 的在线变更）==")
for r in rows:
    if r["verdict"] in ("PASS",):
        continue
    for f in r["fields"]:
        cur_v = p.get(f)
        print(f"  {f:<28} 当前={str(cur_v):<10} 试跑={r['trial']:<12} "
              f"判定={r['verdict']:<14}{r['why']}")

print("\n== 核心参数快照 ==")
for k in sorted(p.keys()):
    print(f"  {k:<28} {p[k]}")
