# -*- coding: utf-8 -*-
"""断线审计 v4（补齐）：① 参数键 vs 数据类字段的错名/漏名；② 计划任务脚本存在性。只读。"""
import sys
import re
import json
import pathlib
import subprocess
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CORE = ROOT / "backend" / "services" / "market_maker" / "core.py"


def fields(cls):
    src = CORE.read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"^class {cls}\b", src, re.M)
    tail = src[m.end():]
    nxt = re.search(r"^(class |def |@)", tail, re.M)
    body = tail[:nxt.start()] if nxt else tail
    return set(re.findall(r"^    (\w+)\s*:\s*[^=\n]+=", body, re.M))


lim = fields("LaneRiskLimits")
qp = fields("QuoteParams")
c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]
params = set((m.get("params") or {}).keys())

print("== ① 注册表有、但两个数据类都没有的键（写了没人读）==")
orphan = sorted(params - lim - qp)
for k in orphan:
    print(f"  ⚠ {k}")
if not orphan:
    print("  （无）")

print("\n== ② 数据类有、但注册表没给值的字段（用默认值跑）==")
missing = sorted((lim | qp) - params)
for k in missing:
    print(f"  · {k}")

print("\n== ③ 计划任务 → 脚本存在性 ==")
r = subprocess.run(["schtasks", "/query", "/fo", "csv", "/v"], capture_output=True,
                   text=True, encoding="utf-8", errors="replace", timeout=120)
bad = []
for line in (r.stdout or "").splitlines():
    if "Hyper-Alpha-Arena" not in line:
        continue
    task = line.split('","')[1] if '","' in line else "?"
    if "DSH_HFT" not in task and "DSH_" not in task:
        continue
    for mm in re.finditer(r"[A-Za-z]:\\[^\"\s]+\.py", line):
        p = pathlib.Path(mm.group(0))
        if not p.exists():
            bad.append((task, str(p)))
if bad:
    for t, p in bad:
        print(f"  ⚠ {t} → 缺失 {p}")
else:
    print("  （全部任务引用的 .py 均存在）")
