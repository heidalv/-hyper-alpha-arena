# -*- coding: utf-8 -*-
"""排查：①agent 时间戳与系统时钟是否一致；②param_search 为何 404；③timing 是否停跑；
④QAA 卡片族当前是否可用；⑤各 agent 的配置频率与实际间隔对照。"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import request as urlreq
from urllib.error import HTTPError

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
B = "http://127.0.0.1:8000"
CST = timezone(timedelta(hours=8))


def g(p: str):
    try:
        with urlreq.urlopen(B + p, timeout=30) as r:
            return r.status, json.loads(r.read())
    except HTTPError as e:
        return e.code, None
    except Exception as e:  # noqa: BLE001
        return type(e).__name__, None


print("=" * 96)
print("【1】时钟一致性：系统 now vs agent ts_ms")
print("=" * 96)
now = time.time()
print(f"  系统 time.time()      = {now:.3f}  => {datetime.fromtimestamp(now, CST):%Y-%m-%d %H:%M:%S} CST")
print(f"  系统 utcnow           = {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC")
print(f"  时区                  = {time.tzname}, TZ={os.environ.get('TZ')}")
st, d = g("/api/agents/latest/anomaly")
if isinstance(d, dict):
    t = d.get("ts_ms", 0) / 1000
    print(f"  anomaly ts_ms         = {t:.3f}  => {datetime.fromtimestamp(t, CST):%Y-%m-%d %H:%M:%S} CST")
    print(f"  ⇒ 与系统相差 {(t - now)/3600:+.2f} 小时")

print()
print("=" * 96)
print("【2】六个观察型 agent：注册 id / 最新记录 / 配置频率")
print("=" * 96)
from backend.services.agents.jobs import ensure_registered  # noqa: E402
from backend.services.agents.base import registered_agents, read_latest  # noqa: E402
from backend.services.agents.base import DATA_DIR  # noqa: E402

ensure_registered()
reg = sorted(registered_agents())
print(f"  已注册 agent id（{len(reg)} 个）: {reg}")
print(f"  latest 产物目录: {DATA_DIR}")
if DATA_DIR.exists():
    files = sorted(DATA_DIR.glob("latest_*.json"))
    print(f"  目录内 latest_*.json 共 {len(files)} 个")
    for f in files:
        mt = datetime.fromtimestamp(f.stat().st_mtime, CST)
        age_h = (time.time() - f.stat().st_mtime) / 3600
        print(f"    {f.name:34s} mtime={mt:%m-%d %H:%M:%S}  {age_h:8.1f} 小时前  {f.stat().st_size:7d}B")
else:
    print("  ！产物目录不存在")

print()
print("  逐个 agent：")
for aid in reg + ["param_search", "timing_weights"]:
    st, _ = g(f"/api/agents/latest/{aid}")
    got = read_latest(aid)
    if isinstance(got, dict):
        t = got.get("ts_ms", 0) / 1000
        age = (time.time() - t) / 3600 if t else float("nan")
        print(f"    {aid:16s} HTTP {st}  最近 ts={datetime.fromtimestamp(t, CST):%m-%d %H:%M:%S}"
              f"（{age:7.1f} 小时前） ok={got.get('ok')} 键={len(got)}")
    else:
        print(f"    {aid:16s} HTTP {st}  ✗ 无 latest 记录（断链候选）")

print()
print("=" * 96)
print("【3】QAA 卡片族当前可用性")
print("=" * 96)
st, d = g("/api/qaa/health")
print(f"  /api/qaa/health HTTP {st}: {json.dumps(d, ensure_ascii=False) if d else ''}")
for k in ("QAA_V3_ENABLED", "QAA_MODE", "QAA_ENABLED"):
    print(f"  env {k} = {os.getenv(k)!r}")
try:
    from backend.services.qaa.cards import ALL_CARDS
    print(f"  代码里注册的 QAA 卡片 = {len(ALL_CARDS)} 张: {sorted(ALL_CARDS)}")
except Exception as e:  # noqa: BLE001
    print(f"  导入 cards 失败: {type(e).__name__}: {e}")

print()
print("=" * 96)
print("【4】配置频率（jobs.py 的调度声明）")
print("=" * 96)
src = (Path(__file__).resolve().parents[1] / "backend" / "services" / "agents" / "jobs.py").read_text(encoding="utf-8")
import re  # noqa: E402
for ln in src.splitlines():
    if re.search(r"(interval|minutes|hours|cron|hour=|minute=|off_peak|非峰)", ln) and not ln.strip().startswith("#"):
        print("  " + ln.strip()[:150])
