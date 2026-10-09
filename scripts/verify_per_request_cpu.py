# -*- coding: utf-8 -*-
"""复核「每请求 CPU 成本」：A/B/A 交替 + 单端点定速。

上一版把混合负载与"后台循环阵发"混在一起（测得 188ms/请求，竟大于请求墙钟），
本版用**前后两个空载窗夹逼**同一次负载窗，逐端点定速测量，消除阵发混淆。

CPU_i = 负载窗 CPU - (前一空载窗 CPU + 后一空载窗 CPU)/2
每请求 CPU = CPU_i / 请求数
"""
from __future__ import annotations

import io
import statistics
import sys
import time
from urllib import request as urlreq

import psutil

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = "http://127.0.0.1:8000"

ENDPOINTS = [
    ("orders(50)", "/api/paper/orders/14?limit=50", 20, 20.0),
    ("balance", "/api/paper/balance/14", 20, 20.0),
    ("positions", "/api/paper/positions/14?status=open", 20, 20.0),
    ("summary", "/api/paper/summary/14", 20, 20.0),
    ("ticker-bar(7)", "/api/market/ticker-bar?symbols=BTC,ETH,SOL,BNB,VIRTUAL,ASTER,XPL", 20, 20.0),
    ("config/default-exchange", "/api/config/default-exchange", 20, 20.0),
]
IDLE = 20.0


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
print(f"后端 pid={proc.pid}；每个端点：空载{IDLE:.0f}s → 定速20请求/20s → 空载{IDLE:.0f}s\n")


def cpu_of(fn, duration: float):
    c0 = proc.cpu_times()
    t0 = time.perf_counter()
    out = fn()
    span = time.perf_counter() - t0
    c1 = proc.cpu_times()
    return ((c1.user - c0.user) + (c1.system - c0.system)), span, out


print(f"  {'端点':26s} {'前空载':>8s} {'后空载':>8s} {'负载窗':>8s} "
      f"{'API增量':>8s} {'每请求CPU':>10s} {'墙钟中位':>9s}")
print("  " + "-" * 88)
results = []
for name, path, n_req, dur in ENDPOINTS:
    idle_cpu, idle_span, _ = cpu_of(lambda: time.sleep(IDLE), IDLE)
    idle1 = idle_cpu / idle_span

    def load():
        lat = []
        interval = dur / n_req
        for _ in range(n_req):
            t = time.perf_counter()
            try:
                with urlreq.urlopen(BASE + path, timeout=60) as r:
                    r.read()
                lat.append(time.perf_counter() - t)
            except Exception:  # noqa: BLE001
                pass
            nap = interval - (time.perf_counter() - t)
            if nap > 0:
                time.sleep(nap)
        return lat

    load_cpu, load_span, lat = cpu_of(load, dur)
    idle_cpu2, idle_span2, _ = cpu_of(lambda: time.sleep(IDLE), IDLE)
    idle2 = idle_cpu2 / idle_span2

    base = (idle1 + idle2) / 2
    api_cpu = max(0.0, load_cpu - base * load_span)
    per_req = api_cpu / max(1, len(lat)) * 1000
    results.append((name, idle1, idle2, load_cpu / load_span, api_cpu / load_span,
                    per_req, statistics.median(lat) * 1000))
    print(f"  {name:26s} {idle1*100:7.0f}% {idle2*100:7.0f}% "
          f"{load_cpu/load_span*100:7.0f}% {api_cpu/load_span*100:7.0f}% "
          f"{per_req:9.0f}ms {statistics.median(lat)*1000:8.0f}ms")

print()
print("=" * 96)
print("复核结论")
print("=" * 96)
idles = [r[1] for r in results] + [r[2] for r in results]
print(f"  12 个空载窗的 CPU 占用：中位 {statistics.median(idles)*100:.0f}% "
      f"最小 {min(idles)*100:.0f}% 最大 {max(idles)*100:.0f}%  "
      f"⇒ 背景负载{'稳定' if max(idles)-min(idles) < 0.3 else '阵发明显'}")
print()
print(f"  {'端点':26s} {'每请求 CPU':>11s} {'墙钟中位':>9s} {'比值':>7s}")
for name, *_r, per_req, med in [(r[0], *r[1:]) for r in results]:
    pass
for r in results:
    name, idle1, idle2, loadp, apip, per_req, med = r
    ratio = per_req / max(med, 0.001)
    flag = "  ← CPU 大于墙钟，异常" if ratio > 1.05 else ""
    print(f"  {name:26s} {per_req:9.0f}ms {med:8.0f}ms ×{ratio:5.2f}{flag}")
