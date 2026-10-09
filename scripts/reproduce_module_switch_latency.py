# -*- coding: utf-8 -*-
"""端到端复现「切换模块后数据迟迟刷不出来」。

浏览器对同一 origin 只有 ~6 条 HTTP/1.1 连接，所以前端的 5~11 个并发请求会排队。
本脚本用 6 路连接池（与浏览器同口径）打「切换模块」那一刻的真实请求批次，测量
**从点击到数据齐全**的耗时；再叠加第二页/全局 TickerBar 的轮询，看是否出现 20s+。
"""
from __future__ import annotations

import io
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib import request as urlreq

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000/api"

# 模拟盘页 mount 那一批（useTradingData：balance/positions/orders/summary/sessions）
PAPER_PAGE = [
    "/paper/balance/14",
    "/paper/positions/14?status=open",
    "/paper/orders/14?limit=50",
    "/paper/summary/14",
    "/sessions",
]
# 全局布局 TickerBar（每页都在，3s 轮询）
TICKER = ["/market/ticker-bar?symbols=BTC,ETH,SOL,BNB,VIRTUAL,ASTER,XPL"]
# 其它页面同时在跑的轮询（切页时它们仍在后台组件里）
OTHERS = ["/auto-coin/active-symbols", "/full-auto/tier-status/fa_7e12e7a1b6"]


def hit(path: str):
    t0 = time.perf_counter()
    try:
        with urlreq.urlopen(BASE + path, timeout=120) as r:
            r.read()
        return path, time.perf_counter() - t0, True
    except Exception:  # noqa: BLE001
        return path, time.perf_counter() - t0, False


def run_batch(label: str, paths: list[str], conns: int = 6, rounds: int = 3):
    print(f"\n{'=' * 96}\n{label}（{len(paths)} 个请求，连接上限 {conns}）\n{'=' * 96}")
    totals = []
    for i in range(rounds):
        # 浏览器行为：最多 conns 条在飞，其余排队等待（这里用连接数=并发数建模）
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=conns) as ex:
            res = list(ex.map(hit, paths))
        wall = time.perf_counter() - t0
        totals.append(wall)
        bad = [p for p, _, ok in res if not ok]
        slow = sorted(((d, p) for p, d, _ in res), reverse=True)[:3]
        print(f"  第{i+1}轮  数据齐全耗时 = {wall:6.2f}s   失败={len(bad)}   "
              f"最慢三条: " + ", ".join(f"{p.split('/')[-1][:18]}={d:.1f}s" for d, p in slow))
    print(f"  → 中位 {statistics.median(totals):.2f}s  最大 {max(totals):.2f}s")
    return statistics.median(totals)


a = run_batch("【1】切到模拟盘：页面 5 个请求", PAPER_PAGE)
b = run_batch("【2】切到模拟盘 + 全局 TickerBar", PAPER_PAGE + TICKER)
c = run_batch("【3】两页叠加（模拟盘 5 + TickerBar + 其它页残留轮询 2）",
              PAPER_PAGE + TICKER + OTHERS)
d = run_batch("【4】极端：再加一轮其它页轮询（14 个请求抢 6 条连接）",
              PAPER_PAGE + TICKER + OTHERS + OTHERS + ["/paper/balance/14"] * 3)

print(f"\n{'=' * 96}\n结论\n{'=' * 96}")
print(f"  单页切换        : {a:.2f}s")
print(f"  + TickerBar     : {b:.2f}s  (×{b/max(a,0.01):.1f})")
print(f"  + 其它页轮询     : {c:.2f}s  (×{c/max(a,0.01):.1f})")
print(f"  14 请求抢 6 连接 : {d:.2f}s  (×{d/max(a,0.01):.1f})")
