# -*- coding: utf-8 -*-
"""主脑 / 主线 agent 的**实测**分析频率（用日志与运行态接口，不看配置声明）。

覆盖：
- 主脑：MLTO brain 子进程（--tier mid|long）、论题 thesis 更新
- 主线：midlong_executor / FactorRouteAB 决策循环、midlong_direction_audit
- 车道：swing / trend / scalp
- 调度器口径：/api/full-auto/tick-intervals（运行时真实间隔）
"""
from __future__ import annotations

import io
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import request as urlreq
from urllib.error import HTTPError

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
CST = timezone(timedelta(hours=8))
B = "http://127.0.0.1:8000"

TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def hourly(path: Path, keyword: str, label: str, tail_hours: int = 24):
    """统计关键字在日志里的按小时分布 + 最近一次时间。"""
    if not path.exists():
        print(f"  {label:34s} 日志不存在: {path.name}")
        return
    per_hour: Counter = Counter()
    last = None
    last_line = ""
    total = 0
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if keyword not in ln:
            continue
        m = TS.match(ln)
        if not m:
            continue
        try:
            t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        total += 1
        per_hour[t.replace(minute=0, second=0)] += 1
        if last is None or t > last:
            last, last_line = t, ln.strip()[:120]
    if not total:
        print(f"  {label:34s} 无命中（关键字 {keyword!r}）")
        return
    cutoff = datetime.now() - timedelta(hours=tail_hours)
    recent = {k: v for k, v in per_hour.items() if k >= cutoff}
    span = f"{min(per_hour):%m-%d %H:%M} ~ {max(per_hour):%m-%d %H:%M}"
    n_recent = sum(recent.values())
    hours = max(len(recent), 1)
    print(f"  {label:34s} 总 {total:6d} 次 | 近{tail_hours}h {n_recent:5d} 次 "
          f"≈ {n_recent/hours:6.1f} 次/小时 | 最近 {last:%m-%d %H:%M:%S}")
    if n_recent:
        top = sorted(recent.items(), key=lambda kv: -kv[1])[:4]
        print(f"      近{tail_hours}h 峰值小时: " + ", ".join(f"{k:%H}:00={v}" for k, v in top))


print("=" * 104)
print("【1】日志实测频率（backend.log / brain_subprocess.log）")
print("=" * 104)
blog = ROOT / "logs" / "backend.log"
brain = ROOT / "logs" / "brain_subprocess.log"
hourly(blog, "[MidLong]", "主线 midlong_executor")
hourly(blog, "[FactorRouteAB]", "主线 因子路线 A/B 决策")
hourly(blog, "[MidLongAudit]", "主线 方向审计")
hourly(blog, "[AutoCoinSelector]", "选币（喂给主线）")
hourly(blog, "[Agent:", "观察型 Agent（含 score/experiment）")
hourly(blog, "[agents]", "agents 调度器日志")
hourly(brain, "===", "主脑 brain 子进程标记")
hourly(brain, "tier=mid", "主脑 mid 层")
hourly(brain, "tier=long", "主脑 long 层")
hourly(brain, "thesis", "主脑 论题")

print()
print("=" * 104)
print("【2】主脑子进程：最近若干次运行的参数与时间（brain_subprocess.log 尾部）")
print("=" * 104)
if brain.exists():
    lines = brain.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"  文件共 {len(lines)} 行；最后 12 行：")
    for ln in lines[-12:]:
        print("   " + ln.strip()[:150])

print()
print("=" * 104)
print("【3】运行态口径：/api/full-auto/tick-intervals（前端「调度监控」用的就是这个）")
print("=" * 104)


def g(p: str):
    try:
        with urlreq.urlopen(B + p, timeout=30) as r:
            return r.status, json.loads(r.read())
    except HTTPError as e:
        return e.code, None
    except Exception as e:  # noqa: BLE001
        return type(e).__name__, None


st, d = g("/api/full-auto/tick-intervals")
print(f"  HTTP {st}")
if isinstance(d, dict):
    print("  " + json.dumps(d, ensure_ascii=False)[:900])

print()
print("=" * 104)
print("【4】MLTO 论题（主脑产物）刷新节奏：thesis/summary 里的时间字段")
print("=" * 104)
st, d = g("/api/mlto/sessions/fa_7e12e7a1b6/thesis/summary?slim=1")
if isinstance(d, dict):
    print(f"  HTTP {st}  keys={sorted(d.keys())}")
    th = d.get("theses")
    if isinstance(th, list):
        print(f"  论题条数 = {len(th)}")
        for t in th[:4]:
            if isinstance(t, dict):
                keep = {k: t.get(k) for k in ("tier", "symbol", "as_of", "ts_ms", "updated_at",
                                              "direction", "confidence") if k in t}
                print(f"   {json.dumps(keep, ensure_ascii=False)[:180]}")
    m = d.get("metrics")
    if m:
        print(f"  metrics = {json.dumps(m, ensure_ascii=False)[:300]}")
