# -*- coding: utf-8 -*-
"""时间分辨对照：进程 CPU 占用 vs 同步端点延迟（每 2s 一对样本）。

用途：判定"慢性 3~4s 慢请求"是否由**阵发性 GIL 占用**造成。
  - 若延迟峰值与 CPU 峰值同步 ⇒ GIL/计算争用（repo 文档的说法成立）
  - 若 CPU 平坦而延迟仍高 ⇒ 等待（锁/DB/池）
探测本身串行且每分钟仅 60 次，不给系统加压。
"""
from __future__ import annotations

import csv
import io
import statistics
import sys
import time
from pathlib import Path
from urllib import request as urlreq

import psutil

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "cpu_vs_latency.csv"
BASE = "http://127.0.0.1:8000"
PATHS = ["/api/paper/orders/14?limit=50", "/api/paper/balance/14"]
MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
INTERVAL = 2.0


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


def timed(path: str):
    t = time.perf_counter()
    try:
        with urlreq.urlopen(BASE + path, timeout=120) as r:
            n = len(r.read())
        return time.perf_counter() - t, n, "ok"
    except Exception as e:  # noqa: BLE001
        return time.perf_counter() - t, 0, type(e).__name__


proc = pick_backend()
print(f"监视后端 pid={proc.pid}，时长 {MINUTES:.0f} 分钟，间隔 {INTERVAL:.0f}s")
print(f"输出 {OUT}")

rows = []
end = time.time() + MINUTES * 60
prev = proc.cpu_times()
prev_t = time.perf_counter()
last_report = 0.0

with OUT.open("w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["ts", "cpu_pct", "threads", "d_orders_s", "d_balance_s",
                "bytes_orders", "status", "n_warn"])
    while time.time() < end:
        time.sleep(INTERVAL)
        now = time.perf_counter()
        ct = proc.cpu_times()
        cpu = (ct.user - prev.user) + (ct.system - prev.system)
        wall = now - prev_t
        pct = cpu / max(wall, 1e-6) * 100
        prev, prev_t = ct, now

        d1, n1, s1 = timed(PATHS[0])
        d2, n2, s2 = timed(PATHS[1])
        nthreads = proc.num_threads()
        w.writerow([time.strftime("%H:%M:%S"), f"{pct:.1f}", nthreads,
                    f"{d1:.3f}", f"{d2:.3f}", n1, f"{s1}/{s2}", 0])
        fh.flush()
        rows.append((pct, d1, d2, nthreads))

        if time.time() - last_report > 60:
            last_report = time.time()
            recent = rows[-20:]
            print(f"  {time.strftime('%H:%M:%S')} 近 {len(recent)} 样本："
                  f"CPU 中位={statistics.median(r[0] for r in recent):5.1f}% "
                  f"峰值={max(r[0] for r in recent):5.1f}%  "
                  f"orders 中位={statistics.median(r[1] for r in recent)*1000:6.0f}ms "
                  f"max={max(r[1] for r in recent)*1000:6.0f}ms  "
                  f"balance 中位={statistics.median(r[2] for r in recent)*1000:6.0f}ms")


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return num / (dx * dy) if dx and dy else float("nan")


print("\n" + "=" * 88)
print(f"汇总：{len(rows)} 个样本")
cpu_all = [r[0] for r in rows]
o_all = [r[1] * 1000 for r in rows]
b_all = [r[2] * 1000 for r in rows]
print(f"  CPU%: 中位={statistics.median(cpu_all):.1f} 均值={statistics.mean(cpu_all):.1f} "
      f"峰值={max(cpu_all):.1f}  >80%占比={sum(1 for c in cpu_all if c>80)/len(cpu_all):.0%}")
print(f"  orders  : 中位={statistics.median(o_all):.0f}ms 峰值={max(o_all):.0f}ms "
      f">1s占比={sum(1 for x in o_all if x>1000)/len(o_all):.0%}")
print(f"  balance : 中位={statistics.median(b_all):.0f}ms 峰值={max(b_all):.0f}ms "
      f">1s占比={sum(1 for x in b_all if x>1000)/len(b_all):.0%}")
print(f"\n  相关 orders vs 同窗 CPU : r = {pearson(cpu_all, o_all):+.3f}")
print(f"  相关 balance vs 同窗 CPU: r = {pearson(cpu_all, b_all):+.3f}")
lag_cpu = cpu_all[:-1]
print(f"  相关 orders vs 前窗 CPU : r = {pearson(lag_cpu, o_all[1:]):+.3f}")
print(f"  相关 threads vs orders  : r = {pearson([r[3] for r in rows], o_all):+.3f}")

hi = [(c, o) for c, o in zip(cpu_all, o_all) if c > 60]
lo = [(c, o) for c, o in zip(cpu_all, o_all) if c <= 60]
if hi and lo:
    print(f"\n  高 CPU(>60%) 时 orders 中位 = {statistics.median(o for _, o in hi):.0f}ms (n={len(hi)})")
    print(f"  低 CPU(≤60%) 时 orders 中位 = {statistics.median(o for _, o in lo):.0f}ms (n={len(lo)})")
