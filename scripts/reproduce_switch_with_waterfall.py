# -*- coding: utf-8 -*-
"""按真实瀑布重放「切到模拟盘」（修正上一版：漏了账户列表这一级依赖）。

真实前端路径（frontend-next/src/app/paper-trading/page.tsx:28-47）：
  波1  GET /api/account/list           （useAccounts，staleTime 30s，过期才重取）
  波2  波1 完成后才发：balance / positions / orders / summary（enabled: !!accountId）
  旁路 全局 TickerBar 每 3s 打 ticker-bar；其它页残留轮询仍在跑
连接口径：浏览器同源 HTTP/1.1 约 6 条并发。
"""
from __future__ import annotations

import io
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib import request as urlreq

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = "http://127.0.0.1:8000"

ACCOUNTS = "/api/account/list"
WAVE2 = [
    "/api/paper/balance/14",
    "/api/paper/positions/14?status=open",
    "/api/paper/orders/14?limit=50",
    "/api/paper/summary/14",
]
SIDE = [
    "/api/market/ticker-bar?symbols=BTC,ETH,SOL,BNB,VIRTUAL,ASTER,XPL",
    "/api/ops/errors?limit=1",
]
POOL = ThreadPoolExecutor(max_workers=6)   # 浏览器 6 连接


def hit(path: str, timeout: float = 120.0):
    t0 = time.perf_counter()
    try:
        with urlreq.urlopen(BASE + path, timeout=timeout) as r:
            r.read()
        return path, time.perf_counter() - t0, True
    except Exception as e:  # noqa: BLE001
        return path, time.perf_counter() - t0, f"{type(e).__name__}"


def switch(with_side: bool, rounds: int = 5):
    """一次真实切页：波1 → 波2（波2 内部并发）；旁路轮询同时占用连接。"""
    out = []
    for i in range(rounds):
        side_futs = []
        if with_side:
            side_futs = [POOL.submit(hit, p) for p in SIDE]
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(hit, [ACCOUNTS]))                # 波1
            t1 = time.perf_counter()
            w2 = list(ex.map(hit, WAVE2))                # 波2（4 个并发）
            t2 = time.perf_counter()
        for f in side_futs:
            f.result()
        acct = None
        out.append({
            "wave1": t1 - t0,
            "wave2": t2 - t1,
            "total": t2 - t0,
            "w2_slow": max(d for _, d, _ in w2),
            "fails": sum(1 for _, _, ok in w2 if ok is not True),
        })
    return out


def report(label, rows):
    print(f"\n{'=' * 96}\n{label}\n{'=' * 96}")
    for i, r in enumerate(rows, 1):
        print(f"  第{i}次  波1(账户)={r['wave1']*1000:6.0f}ms  "
              f"波2(4类数据)={r['wave2']*1000:6.0f}ms  "
              f"**数据齐全={r['total']*1000:6.0f}ms**  "
              f"波2最慢单条={r['w2_slow']*1000:6.0f}ms  失败={r['fails']}")
    tot = [r["total"] for r in rows]
    print(f"  → 中位 {statistics.median(tot)*1000:.0f}ms  最大 {max(tot)*1000:.0f}ms")
    return statistics.median(tot)


a = report("【A】纯切页（波1→波2），无旁路轮询", switch(with_side=False))
b = report("【B】切页 + 全局 TickerBar/ops 轮询同时抢连接（真实情形）", switch(with_side=True))

print(f"\n{'=' * 96}\n对照\n{'=' * 96}")
print(f"  无旁路 : 中位 {a*1000:.0f}ms")
print(f"  有旁路 : 中位 {b*1000:.0f}ms  (×{b/max(a,0.001):.2f})")
print(f"  上一版（错误地把 5 个请求一次性并发）测得中位 200ms —— 未建模波1，故低估")
