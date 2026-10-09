# -*- coding: utf-8 -*-
"""决定性切分：那一个核是「后台循环」烧的，还是「API 请求」烧的？

Phase A：60s 完全不发请求 → 进程 CPU 基线（只含后台线程）
Phase B：60s 按用户实测速率（~100 请求/分，与访问日志分布同形）发请求 → 进程 CPU
ΔCPU = API 实际占用。同时对每个端点记录延迟。

若 A ≈ 1 核 且 ΔCPU 很小 ⇒ CPU 饱和来自后台循环，API 只是被 GIL 连累的受害者。
若 A 很小 且 ΔCPU ≈ 1 核 ⇒ 是 API 处理器自身（ORM/序列化）烧的核。
"""
from __future__ import annotations

import io
import statistics
import sys
import threading
import time
from urllib import request as urlreq

import psutil

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = "http://127.0.0.1:8000"
DUR = 60.0

# 与访问日志实测占比同形（ticker 25%、paper 四件套 50%、config 9%、ops 5.5%、auto-coin 2.8%）
MIX = [
    "/api/market/ticker-bar?symbols=BTC,ETH,SOL,BNB,VIRTUAL,ASTER,XPL",
    "/api/paper/positions/14?status=open",
    "/api/paper/summary/14",
    "/api/paper/balance/14",
    "/api/paper/orders/14?limit=50",
    "/api/config/default-exchange",
    "/api/ops/errors?limit=1",
    "/api/auto-coin/active-symbols",
]


def pick_backend() -> psutil.Process:
    cands = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cl = " ".join(p.info["cmdline"] or [])
            if "run_uvicorn_dev" in cl and p.info["name"] == "python.exe":
                cands.append(p)
        except Exception:
            continue
    if not cands:
        raise SystemExit("未找到后端进程")
    cands.sort(key=lambda p: p.num_threads(), reverse=True)
    return cands[0]


proc = pick_backend()
print(f"后端 pid={proc.pid}\n")


def cpu_span(duration: float, send: bool, rate_per_min: float = 0.0):
    """在 duration 秒内测量进程 CPU；send=True 时按速率请求。"""
    c0 = proc.cpu_times()
    t0 = time.perf_counter()
    lat: dict[str, list[float]] = {}
    if send:
        interval = 60.0 / rate_per_min
        i = 0
        while time.perf_counter() - t0 < duration:
            path = MIX[i % len(MIX)]
            i += 1
            t = time.perf_counter()
            try:
                with urlreq.urlopen(BASE + path, timeout=60) as r:
                    r.read()
                lat.setdefault(path.split("?")[0], []).append(time.perf_counter() - t)
            except Exception:  # noqa: BLE001
                pass
            sleep = interval - (time.perf_counter() - t)
            if sleep > 0:
                time.sleep(sleep)
    else:
        time.sleep(duration)
    span = time.perf_counter() - t0
    c1 = proc.cpu_times()
    cpu = (c1.user - c0.user) + (c1.system - c0.system)
    return cpu, span, lat


print("Phase A：60s 不发任何请求（后台循环基线）…")
a_cpu, a_span, _ = cpu_span(DUR, send=False)
print(f"  → CPU {a_cpu:.2f}s / {a_span:.1f}s = {a_cpu/a_span*100:.0f}% 单核\n")

print("Phase B：60s 按 ~100 请求/分 发请求（与用户实测同形）…")
b_cpu, b_span, lat = cpu_span(DUR, send=True, rate_per_min=100)
print(f"  → CPU {b_cpu:.2f}s / {b_span:.1f}s = {b_cpu/b_span*100:.0f}% 单核")
n_req = sum(len(v) for v in lat.values())
print(f"  → 发出 {n_req} 个请求；API 增量 CPU = {b_cpu - a_cpu:.2f}s "
      f"⇒ 每请求 {(b_cpu-a_cpu)/max(1,n_req)*1000:.0f}ms CPU\n")

print("=" * 92)
print("【延迟】用户同速率下的每端点延迟")
print("=" * 92)
print(f"  {'端点':44s} {'n':>4s} {'中位':>9s} {'p90':>9s} {'最大':>9s}")
all_lat = []
for p, ds in sorted(lat.items(), key=lambda kv: -statistics.median(kv[1])):
    ds_sorted = sorted(ds)
    all_lat += ds
    print(f"  {p[:44]:44s} {len(ds):4d} {statistics.median(ds)*1000:8.0f}ms "
          f"{ds_sorted[min(len(ds)-1, int(len(ds)*0.9))]*1000:8.0f}ms "
          f"{max(ds)*1000:8.0f}ms")
print(f"\n  全端点合计 n={len(all_lat)} 中位={statistics.median(all_lat)*1000:.0f}ms "
      f"最大={max(all_lat)*1000:.0f}ms")

print()
print("=" * 92)
print("【裁决】")
print("=" * 92)
print(f"  空载 CPU          = {a_cpu/a_span*100:6.0f}% 单核")
print(f"  负载 CPU          = {b_cpu/b_span*100:6.0f}% 单核")
print(f"  API 增量          = {(b_cpu-a_cpu)/b_span*100:6.0f}% 单核 "
      f"（占总量 {(b_cpu-a_cpu)/max(b_cpu,1e-9)*100:.0f}%）")
print(f"  空载占总量        = {a_cpu/max(b_cpu,1e-9)*100:6.0f}%")
if a_cpu / max(b_cpu, 1e-9) > 0.7:
    print("  ⇒ 核被【后台循环】占用为主，API 请求是 GIL 连累的受害者")
elif (b_cpu - a_cpu) / max(b_cpu, 1e-9) > 0.5:
    print("  ⇒ 核主要被【API 处理器】自身占用（ORM/序列化开销）")
else:
    print("  ⇒ 两者相当，需分别治理")
