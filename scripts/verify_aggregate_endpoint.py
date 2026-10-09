# -*- coding: utf-8 -*-
"""聚合端点收尾核查：①对拍差异到底是"时间性"还是"口径漂移"；②稳态延迟对照。

背景：首次 HTTP 对拍中 positions/orders 全等，而 balance/summary 不等 ——
两者都含按实时价重算的字段（uPnL/权益），若差异只出现在这些字段 ⇒ 时间性差异；
若出现在结构/计数类字段 ⇒ 口径漂移（必须修）。
"""
from __future__ import annotations

import io
import json
import statistics
import sys
import time
from urllib import request as urlreq

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
B = "http://127.0.0.1:8000"


def get(p: str, timeout: float = 90.0):
    t0 = time.perf_counter()
    with urlreq.urlopen(B + p, timeout=timeout) as r:
        return r.status, json.loads(r.read()), time.perf_counter() - t0


print("=" * 96)
print("【1】对拍差异定位（同一秒内连续取，看差在哪些字段）")
print("=" * 96)
_, d, _ = get("/api/paper/dashboard/14?status=open")
_, bal, _ = get("/api/paper/balance/14")
_, summ, _ = get("/api/paper/summary/14")

for name, agg_v, one_v in (("balance", d["balance"], bal), ("summary", d["summary"], summ)):
    diffs = {}
    for k in sorted(set(agg_v) | set(one_v)):
        if agg_v.get(k) != one_v.get(k):
            diffs[k] = (agg_v.get(k), one_v.get(k))
    print(f"\n  {name}: 不同字段 {len(diffs)} 个 / 共 {len(set(agg_v) | set(one_v))} 个")
    for k, (a, b) in list(diffs.items())[:12]:
        try:
            rel = abs(float(a) - float(b)) / max(abs(float(b)), 1e-9)
            print(f"    {k:28s} 聚合={a!r:>22} 单端点={b!r:>22}  相对差={rel:.2e}")
        except (TypeError, ValueError):
            print(f"    {k:28s} 聚合={a!r} 单端点={b!r}")

print()
print("=" * 96)
print("【2】再取一次聚合后立刻取单端点（间隔≈0）——若仍不等则是口径问题")
print("=" * 96)
_, d2, _ = get("/api/paper/dashboard/14?status=open")
_, bal2, _ = get("/api/paper/balance/14")
same_bal = json.dumps(d2["balance"], sort_keys=True, default=str) == json.dumps(bal2, sort_keys=True, default=str)
_, summ2, _ = get("/api/paper/summary/14")
same_summ = json.dumps(d2["summary"], sort_keys=True, default=str) == json.dumps(summ2, sort_keys=True, default=str)
print(f"  balance 全等 = {same_bal}   summary 全等 = {same_summ}")

print()
print("=" * 96)
print("【3】稳态延迟：聚合 1 次 vs 四个单端点各 1 次（各测 6 轮，串行）")
print("=" * 96)
agg_ms, singles_ms = [], []
for _ in range(6):
    _, _, dt = get("/api/paper/dashboard/14?status=open")
    agg_ms.append(dt * 1000)
    t = 0.0
    for p in ("/api/paper/balance/14", "/api/paper/positions/14?status=open",
              "/api/paper/orders/14?limit=50", "/api/paper/summary/14"):
        _, _, dt2 = get(p)
        t += dt2 * 1000
        time.sleep(0.02)
    singles_ms.append(t)

print(f"  聚合 1 次      : 中位 {statistics.median(agg_ms):7.0f}ms  {[round(x) for x in agg_ms]}")
print(f"  四个单端点合计 : 中位 {statistics.median(singles_ms):7.0f}ms  {[round(x) for x in singles_ms]}")
print(f"  ⇒ 墙钟节省 {(1 - statistics.median(agg_ms)/max(statistics.median(singles_ms),1))*100:.0f}%；"
      f"请求数 4 → 1")
