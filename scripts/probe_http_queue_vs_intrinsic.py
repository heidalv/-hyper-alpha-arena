# -*- coding: utf-8 -*-
"""HTTP 层实测：区分「排队等待」与「单请求固有成本」。

判据：
  - 若某端点在**串行(空载)**下就已经 3~4s ⇒ 固有成本（查询/计算本身慢）
  - 若串行很快、**并发**下才退化 ⇒ 资源排队（anyio 线程池令牌 / DB 连接 / 锁）
对照：async 端点 vs 同步 def 端点（只有同步 def 才吃 anyio 线程池）。
"""
from __future__ import annotations

import io
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib import request as urlreq

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000"
EPS = [
    ("/api/health", "异步/白名单，对照组"),
    ("/api/paper/balance/14", "同步 def —— 慢榜第 2"),
    ("/api/paper/positions/14?status=open", "同步 def —— 慢榜第 1（带 5s TTL 缓存）"),
    ("/api/paper/orders/14?limit=50", "同步 def"),
    ("/api/paper/summary/14", "同步 def"),
    ("/api/market/ticker-bar?symbols=BTC,ETH,SOL", "慢榜第 3"),
    ("/api/full-auto/tier-status/fa_7e12e7a1b6", "同步 def"),
]

SYM = {"open": 1}
ACCOUNT_ID = 14


def one(path, timeout=60):
    t0 = time.perf_counter()
    try:
        with urlreq.urlopen(BASE + path, timeout=timeout) as r:
            r.read()
            code = r.status
    except Exception as e:  # noqa: BLE001
        return time.perf_counter() - t0, f"ERR {type(e).__name__}"
    return time.perf_counter() - t0, code


def summarize(ds):
    ds = sorted(ds)
    if not ds:
        return "n/a"
    n = len(ds)
    p50 = statistics.median(ds)
    p95 = ds[min(n - 1, int(n * 0.95))]
    return (f"n={n:3d} 中位={p50*1000:7.1f}ms p95={p95*1000:7.1f}ms "
            f"最小={ds[0]*1000:7.1f}ms 最大={ds[-1]*1000:7.1f}ms")


print("=" * 96)
print("【A】串行 5 次（近似空载）—— 若这里就慢，说明是单请求固有成本")
print("=" * 96)
serial = {}
for path, note in EPS:
    ds = []
    for _ in range(5):
        d, code = one(path)
        ds.append(d)
        if code != 200:
            print(f"  ! {path} -> {code}")
    serial[path] = ds
    print(f"  {path[:46]:48s} {summarize(ds)}   # {note}")

print()
print("=" * 96)
print("【B】并发 20（模拟前端多页同时轮询）—— 若显著劣化，说明是资源排队")
print("=" * 96)
conc = {}
for path, note in EPS:
    with ThreadPoolExecutor(max_workers=20) as ex:
        ds = [d for d, _ in ex.map(lambda _: one(path), range(20))]
    conc[path] = ds
    print(f"  {path[:46]:48s} {summarize(ds)}")

print()
print("=" * 96)
print("【C】放大倍数（并发中位 / 串行中位）")
print("=" * 96)
for path, _ in EPS:
    s = statistics.median(serial[path]) * 1000
    c = statistics.median(conc[path]) * 1000
    flag = "  ← 排队特征" if c > max(3 * s, s + 1000) else ""
    print(f"  {path[:46]:48s} 串行={s:7.1f}ms 并发={c:7.1f}ms ×{c/max(s,0.001):5.1f}{flag}")

print()
print("=" * 96)
print("【D】混合并发：同时打 5 类同步端点 ×8 轮（更接近真实前端）")
print("=" * 96)
mix = [p for p, _ in EPS[1:]]
tasks = mix * 8


def run(path):
    return path, one(path)


with ThreadPoolExecutor(max_workers=40) as ex:
    res = list(ex.map(run, tasks))
byp = {}
for p, (d, code) in res:
    byp.setdefault(p, []).append(d)
for p in mix:
    print(f"  {p[:46]:48s} {summarize(byp.get(p, []))}")
