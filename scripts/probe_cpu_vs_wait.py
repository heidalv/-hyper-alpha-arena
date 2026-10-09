# -*- coding: utf-8 -*-
"""决定性判据：20 路并发下，后端是「在算」还是「在等」？

- 若进程 CPU ≈ 1 个核被打满 ⇒ 计算/GIL 争用（GIL 排队说成立）
- 若进程 CPU 很低而请求仍然慢 ⇒ 等待（DB 锁 / 连接池 / 线程池令牌）
同时抓 PG 侧 `pg_stat_activity` 的 state / wait_event，看等待发生在哪。
"""
from __future__ import annotations

import io
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib import request as urlreq

import psutil

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "http://127.0.0.1:8000"
PATH = "/api/paper/summary/14"
N = 20


def find_pid() -> int | None:
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cl = " ".join(p.info["cmdline"] or [])
        except Exception:
            continue
        if "run_uvicorn_dev" in cl and p.info["name"] == "python.exe":
            # 取线程数最多的那个（真实 worker，而非 launcher 包装）
            return p.info["pid"]
    return None


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
print(f"后端进程 pid={proc.pid} 线程数={proc.num_threads()}")

# ── 基线 CPU（空载 3s） ──
proc.cpu_percent(None)
t0 = time.perf_counter()
c0 = proc.cpu_times()
time.sleep(3.0)
c1 = proc.cpu_times()
wall = time.perf_counter() - t0
idle_cpu = (c1.user - c0.user) + (c1.system - c0.system)
print(f"空载 {wall:.1f}s：CPU 时间 {idle_cpu:.2f}s ⇒ 占用 {idle_cpu/wall*100:.0f}% 单核"
      f"；线程数={proc.num_threads()}")

# ── PG 采样线程 ──
pg_samples: list[dict] = []
stop = threading.Event()


def pg_sampler():
    try:
        import psycopg2
        from backend.database.connection import DATABASE_URL
        url = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")
        conn = psycopg2.connect(url)
        conn.set_session(readonly=True, autocommit=True)
        cur = conn.cursor()
        while not stop.is_set():
            cur.execute("""
                SELECT state, coalesce(wait_event_type,'-'), coalesce(wait_event,'-'),
                       count(*)
                  FROM pg_stat_activity
                 WHERE datname = current_database() AND pid <> pg_backend_pid()
                 GROUP BY 1,2,3 ORDER BY 4 DESC
            """)
            rows = cur.fetchall()
            pg_samples.append({"t": time.time(),
                               "rows": [tuple(r) for r in rows],
                               "total": sum(r[3] for r in rows)})
            time.sleep(0.4)
        conn.close()
    except Exception as e:  # noqa: BLE001
        pg_samples.append({"error": f"{type(e).__name__}: {e}"})


th = threading.Thread(target=pg_sampler, daemon=True)
th.start()
time.sleep(1.0)

# ── 加压 ──
def hit(_):
    t = time.perf_counter()
    try:
        with urlreq.urlopen(BASE + PATH, timeout=120) as r:
            r.read()
        return time.perf_counter() - t, 200
    except Exception as e:  # noqa: BLE001
        return time.perf_counter() - t, type(e).__name__


cw0 = proc.cpu_times()
tw0 = time.perf_counter()
with ThreadPoolExecutor(max_workers=N) as ex:
    res = list(ex.map(hit, range(N)))
tw1 = time.perf_counter()
cw1 = proc.cpu_times()
stop.set()
th.join(timeout=3)

lat = sorted(d for d, _ in res)
wall_load = tw1 - tw0
cpu_load = (cw1.user - cw0.user) + (cw1.system - cw0.system)
codes = {}
for _, c in res:
    codes[c] = codes.get(c, 0) + 1

print(f"\n并发 {N} 路 → {PATH}")
print(f"  状态码: {codes}")
print(f"  单请求延迟: 中位={statistics.median(lat)*1000:.0f}ms "
      f"最小={lat[0]*1000:.0f}ms 最大={lat[-1]*1000:.0f}ms")
print(f"  整批墙钟={wall_load:.2f}s；进程 CPU 时间={cpu_load:.2f}s "
      f"⇒ 平均占用 {cpu_load/wall_load*100:.0f}% 单核")
print(f"  每请求进程 CPU = {cpu_load/N*1000:.0f}ms  vs  每请求墙钟 "
      f"{statistics.median(lat)*1000:.0f}ms")
print(f"  线程数: 加压前 {proc.num_threads()}（当前 {proc.num_threads()}）")

print("\nPG 侧采样（alpha_arena，出现次数最多的前 8 组）：")
if pg_samples and "error" in pg_samples[0]:
    print("  ! 采样失败:", pg_samples[0]["error"])
else:
    agg: dict[tuple, list[int]] = {}
    for s in pg_samples:
        if "rows" not in s:
            continue
        for r in s["rows"]:
            agg.setdefault(r[:3], []).append(r[3])
    top = sorted(agg.items(), key=lambda kv: -max(kv[1]))[:8]
    print(f"  {'state':22s} {'wait_type':14s} {'wait_event':24s} {'峰值':>5s} {'样本数':>6s}")
    for k, v in top:
        print(f"  {k[0] or '-':22s} {k[1]:14s} {k[2]:24s} {max(v):5d} {len(v):6d}")
    tot = [s["total"] for s in pg_samples if "total" in s]
    if tot:
        print(f"  连接总数: 中位={statistics.median(tot):.0f} 峰值={max(tot)}")
