# -*- coding: utf-8 -*-
"""决定性检验：慢请求是否与 `[DB LeakGuard] idle-in-transaction` 同时发生？

假设 H3：后台任务长时间 `idle in transaction`（持有行锁）⇒ paper 端点的写/更新阻塞 ⇒ 3~33s。
若成立：LeakGuard 告警的时间点应与 SLOW 请求显著重合；反之则否。

对照假设 H4：慢请求与 **DC（数据中心）不可用/慢** 重合（DC_ONLY 下取价等待，超时 ~30s）。
"""
from __future__ import annotations

import io
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "logs" / "backend.log").read_text(
    encoding="utf-8", errors="replace").splitlines()
TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

slow_ts, leak_ts = [], []
for ln in lines:
    m = TS.match(ln)
    if not m:
        continue
    try:
        t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        continue
    if re.search(r"SLOW\s+[\d.]+s", ln):
        slow_ts.append(t)
    if "LeakGuard" in ln and "idle-in-transaction" in ln:
        leak_ts.append(t)

print(f"SLOW 请求 {len(slow_ts)} 条；LeakGuard(idle-in-tx) 告警 {len(leak_ts)} 条")


def near(a_list, b_list, win_s):
    """a 中每个时刻，b 中是否有 ±win_s 内的点。返回 (命中数, 总数)。"""
    import bisect
    bs = sorted(b_list)
    hit = 0
    for a in a_list:
        i = bisect.bisect_left(bs, a - timedelta(seconds=win_s))
        ok = False
        while i < len(bs) and bs[i] <= a + timedelta(seconds=win_s):
            ok = True
            break
        if ok:
            hit += 1
    return hit, len(a_list)


for win in (5, 15, 30, 60):
    h, n = near(slow_ts, leak_ts, win)
    print(f"  SLOW 中，±{win:3d}s 内有 LeakGuard 的：{h}/{n} = {h/max(1,n):.1%}")

# 反向：LeakGuard 时点是否也常伴随 SLOW
for win in (15, 30, 60):
    h, n = near(leak_ts, slow_ts, win)
    print(f"  LeakGuard 中，±{win:3d}s 内有 SLOW 的：{h}/{n} = {h/max(1,n):.1%}")

print("\n时间桶对照（15 分钟；只列任一侧非零的桶）：")
B = timedelta(minutes=15)
sb, lb = Counter(), Counter()
for t in slow_ts:
    sb[t.replace(minute=(t.minute // 15) * 15, second=0)] += 1
for t in leak_ts:
    lb[t.replace(minute=(t.minute // 15) * 15, second=0)] += 1
keys = sorted(set(sb) | set(lb))
print(f"  {'桶':10s} {'SLOW':>5s} {'LeakGuard':>10s}")
for k in keys:
    if sb[k] or lb[k]:
        print(f"  {k:%H:%M}      {sb[k]:5d} {lb[k]:10d}")

# 慢请求的耗时分布（看是否有超时特征值）
durs = []
for ln in lines:
    m = re.search(r"SLOW\s+([\d.]+)s", ln)
    if m:
        durs.append(float(m.group(1)))
if durs:
    print(f"\n耗时分布：n={len(durs)} min={min(durs):.1f} max={max(durs):.1f}")
    import statistics
    print(f"  中位数={statistics.median(durs):.1f}s  均值={statistics.mean(durs):.1f}s")
    buckets = Counter()
    for d in durs:
        buckets[int(d // 5) * 5] += 1
    print("  分档:", {f"{k}-{k+5}s": v for k, v in sorted(buckets.items())})
    hi = [d for d in durs if d >= 18]
    print(f"  ≥18s 的条数 = {len(hi)}（疑似超时特征）: {sorted(hi, reverse=True)[:12]}")
