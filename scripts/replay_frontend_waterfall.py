# -*- coding: utf-8 -*-
"""从真实访问日志还原「切换模块」时刻的请求瀑布。

关注：模拟盘页的 4 个数据端点（balance/positions/orders/summary）在一次切换中
是否成组出现、组内跨度多少秒、与上一波（accounts/config）之间是否隔了若干秒
（前端二级依赖 = 瀑布）。这就是用户"切模块后数据 20s+ 才正确"的真实现场。
"""
from __future__ import annotations

import io
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]

LINE = re.compile(r'(\d{1,3}(?:\.\d{1,3}){3}):(\d+) - "(\w+) (\S+) HTTP/1\.1" (\d{3})')
TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

rows: list[tuple[datetime, str, str]] = []
seen: set[tuple] = set()
for ln in (ROOT / "logs" / "backend.log").read_text(
        encoding="utf-8", errors="replace").splitlines():
    m = TS.match(ln)
    if not m:
        continue
    try:
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        continue
    a = LINE.search(ln)
    if not a:
        continue
    _ip, port, meth, path, _st = a.groups()
    if meth != "GET":
        continue
    key = (ts, port, path)
    if key in seen:
        continue
    seen.add(key)
    if "uvicorn.access" not in ln:       # 只取带时间戳的那一条
        continue
    rows.append((ts, port, path.split("?")[0]))

rows.sort()
PAPER = ("/api/paper/balance/", "/api/paper/positions/", "/api/paper/orders/", "/api/paper/summary/")


def is_paper(p):
    return p.startswith(PAPER)


# ── 找"一次切换"：4 个 paper 端点中 >=3 个落在 10s 窗口内 ──
groups: list[list[tuple[datetime, str, str]]] = []
used: set[int] = set()
for i, (ts, _port, p) in enumerate(rows):
    if i in used or not is_paper(p):
        continue
    g = [r for j, r in enumerate(rows)
         if j not in used and ts <= r[0] <= ts + timedelta(seconds=10)]
    kinds = {r[2].split("/")[3] for r in g if is_paper(r[2])}
    if len(kinds) >= 4:
        idx = [j for j, r in enumerate(rows) if r in g]
        used.update(idx)
        groups.append(g)

print(f"共识别出 {len(groups)} 次「模拟盘页数据齐发」事件（4 类端点齐现）\n")
span_list = []
for g in groups:
    start, end = g[0][0], g[-1][0]
    span = (end - start).total_seconds()
    span_list.append(span)
if span_list:
    import statistics
    print(f"组内跨度（第 1 个到最后一个请求完成）：中位 {statistics.median(span_list):.0f}s "
          f"最大 {max(span_list):.0f}s 最小 {min(span_list):.0f}s")
    b = Counter(int(s // 5) * 5 for s in span_list)
    print("跨度分档:", {f"{k}-{k+5}s": v for k, v in sorted(b.items())})

# ── 逐事件明细（前 8 个 + 跨度最大的 5 个） ──
def show(g, label):
    start, end = g[0][0], g[-1][0]
    print(f"\n── {label}  {start:%H:%M:%S} → {end:%H:%M:%S}  跨度={(end-start).total_seconds():.0f}s")
    prev = None
    for ts, port, p in g:
        gap = "" if prev is None else f"  (+{(ts-prev).total_seconds():.1f}s)"
        short = p.replace("/api/", "").replace("/14", "")
        print(f"     {ts:%H:%M:%S}  {short:44s} port={port}{gap}")
        prev = ts


for g in groups[:6]:
    show(g, "齐发事件")
for g in sorted(groups, key=lambda g: -(g[-1][0] - g[0][0]).total_seconds())[:4]:
    show(g, "跨度最大")

# ── 全局：paper 端点请求的时间间隔（同一端点相邻两次） ──
print()
print("=" * 96)
print("同一端点的相邻请求间隔（判断轮询是否真按 5s 走）")
print("=" * 96)
for ep in PAPER:
    ts_list = [ts for ts, _p, p in rows if p.startswith(ep)]
    gaps = [(b - a).total_seconds() for a, b in zip(ts_list, ts_list[1:])]
    gaps = [g for g in gaps if g < 120]
    if not gaps:
        continue
    import statistics
    print(f"  {ep:26s} n={len(ts_list):5d}  间隔中位={statistics.median(gaps):5.1f}s "
          f"p90={sorted(gaps)[int(len(gaps)*0.9)]:5.1f}s  >15s 占比="
          f"{sum(1 for g in gaps if g > 15)/len(gaps)*100:4.1f}%")
