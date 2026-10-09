# -*- coding: utf-8 -*-
"""断链核查：param_search 注册了为何从未产出？调度任务到底有没有被登记？

判定链：注册函数是否被调用 → 任务是否进了调度器 → 是否触发过 → 触发时是否报错。
"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import request as urlreq
from urllib.error import HTTPError

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CST = timezone(timedelta(hours=8))

print("=" * 96)
print("【1】今天是星期几（param_search 是周日 cron）")
print("=" * 96)
now = datetime.now(CST)
wd = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
print(f"  {now:%Y-%m-%d %H:%M} = {wd}")
last_sun = now - timedelta(days=(now.weekday() - 6) % 7 or 7)
print(f"  上一个周日 = {last_sun:%Y-%m-%d}")

print()
print("=" * 96)
print("【2】日志里 param_search 的痕迹（有没有触发过/报过错）")
print("=" * 96)
for name in ("backend.log", "brain_subprocess.log"):
    p = ROOT / "logs" / name
    if not p.exists():
        continue
    hits = []
    for i, ln in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines()):
        if "param_search" in ln or "param-search" in ln or "ParamSearch" in ln:
            hits.append((i + 1, ln.strip()[:160]))
    print(f"  {name}: 命中 {len(hits)} 行")
    for n, ln in hits[-6:]:
        print(f"    L{n}: {ln}")

print()
print("=" * 96)
print("【3】调度器里有没有这些任务（查 /api/full-auto/debug/scheduler-state 与 ops 任务）")
print("=" * 96)
B = "http://127.0.0.1:8000"


def g(p: str):
    try:
        with urlreq.urlopen(B + p, timeout=30) as r:
            return r.status, json.loads(r.read())
    except HTTPError as e:
        return e.code, None
    except Exception as e:  # noqa: BLE001
        return type(e).__name__, None


st, d = g("/api/full-auto/debug/scheduler-state")
print(f"  /api/full-auto/debug/scheduler-state HTTP {st}")
if isinstance(d, dict):
    txt = json.dumps(d, ensure_ascii=False)
    for key in ("agent_anomaly", "agent_signal_review", "agent_event_impact",
                "agent_param_search", "agent_execution_qa", "v3_agent_"):
        print(f"    含 '{key}': {key in txt}")
    # 打印任务清单（若存在）
    for k in ("tasks", "jobs", "scheduler", "registered"):
        if k in d:
            v = d[k]
            print(f"    {k}: {str(v)[:400]}")

print()
print("=" * 96)
print("【4】注册函数是否真的被调用（调用链 file:line）")
print("=" * 96)
ext = (ROOT / "backend" / "services" / "ops" / "v3_jobs_ext.py").read_text(encoding="utf-8")
for i, ln in enumerate(ext.splitlines(), 1):
    if "register_agent_jobs" in ln or "agents" in ln.lower():
        print(f"  v3_jobs_ext.py:{i}: {ln.strip()[:130]}")
# 谁调用 v3_jobs_ext
for f in ROOT.glob("backend/**/*.py"):
    try:
        for i, ln in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if "v3_jobs_ext" in ln and "import" in ln:
                print(f"  {f.relative_to(ROOT)}:{i}: {ln.strip()[:130]}")
    except Exception:  # noqa: BLE001
        pass

print()
print("=" * 96)
print("【5】AGENT 开关：agents_enabled() 与各 agent 的 effective_mode")
print("=" * 96)
from backend.services.agents.jobs import agents_enabled, ensure_registered  # noqa: E402
from backend.services.agents.base import registered_agents  # noqa: E402

print(f"  agents_enabled() = {agents_enabled()}")
ensure_registered()
st, d = g("/api/agents/status")
if isinstance(d, dict):
    for a in d.get("agents", []):
        print(f"    {a.get('agent'):16s} configured={a.get('configured_mode'):8s} "
              f"effective={str(a.get('effective_mode')):8s} "
              f"last={a.get('last_run_ms') or a.get('last_ms') or '-'} "
              f"desc={str(a.get('description'))[:40]}")
