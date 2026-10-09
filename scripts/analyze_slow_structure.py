# -*- coding: utf-8 -*-
"""SLOW 行结构分析：端点 × 耗时指纹 × 是否同秒成簇。

若是"全局停顿"（事件循环阻塞/DB 挂起），慢请求会**同秒成簇**且端点混杂；
若是"每端点固定成本"，则同一端点的耗时值高度一致、时间上彼此独立。
"""
from __future__ import annotations

import io
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "logs" / "backend.log").read_text(
    encoding="utf-8", errors="replace").splitlines()

TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
# 形如: ... SLOW 3.4s GET /api/paper/balance/14 ...
pat = re.compile(r"SLOW\s+([\d.]+)s\s+(\w+)\s+(\S+)")

rows = []          # (ts, dur, method, path)
for ln in lines:
    m = TS.match(ln)
    if not m:
        continue
    s = pat.search(ln)
    if not s:
        continue
    rows.append((datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"),
                 float(s.group(1)), s.group(2), s.group(3)))

print(f"解析到 SLOW 行 {len(rows)} 条\n")

# ---- 1. 端点归一化（把 id 折叠） ----
def norm(p: str) -> str:
    p = re.sub(r"/\d+", "/{id}", p)
    p = re.sub(r"/[0-9a-f]{8}-[0-9a-f-]{27,}", "/{uuid}", p)
    return p

by_ep = defaultdict(list)
for ts, d, meth, path in rows:
    by_ep[norm(path)].append(d)

print("按端点（耗时中位数降序；n>=3）：")
print(f"  {'端点':52s} {'n':>4s} {'中位':>7s} {'最小':>7s} {'最大':>7s}")
for ep, ds in sorted(by_ep.items(), key=lambda kv: -statistics.median(kv[1])):
    if len(ds) < 3:
        continue
    print(f"  {ep[:52]:52s} {len(ds):4d} {statistics.median(ds):6.2f}s "
          f"{min(ds):6.2f}s {max(ds):6.2f}s")

# ---- 2. 同秒成簇检测 ----
sec = Counter(ts for ts, *_ in rows)
clusters = {t: c for t, c in sec.items() if c >= 2}
print(f"\n同秒并发慢请求：{len(clusters)} 个同秒点，涉及 {sum(clusters.values())} 条"
      f"（占 {sum(clusters.values())/max(1,len(rows)):.0%}）")
for t, c in sorted(clusters.items(), key=lambda kv: -kv[1])[:8]:
    eps = [norm(p) for ts, d, m, p in rows if ts == t]
    print(f"  {t:%H:%M:%S} ×{c}: {', '.join(sorted(set(eps)))[:150]}")

# ---- 3. 单条孤立 vs 成簇的耗时对比 ----
solo = [d for ts, d, *_ in rows if sec[ts] == 1]
clust = [d for ts, d, *_ in rows if sec[ts] >= 2]
if solo and clust:
    print(f"\n孤立慢请求 n={len(solo)} 中位={statistics.median(solo):.2f}s "
          f"均值={statistics.mean(solo):.2f}s")
    print(f"成簇慢请求 n={len(clust)} 中位={statistics.median(clust):.2f}s "
          f"均值={statistics.mean(clust):.2f}s")

# ---- 4. paper 端点 vs 其它，分档对比 ----
print("\npaper/* 与其它端点的耗时分档：")
for label, sel in (("paper/*", lambda p: "/api/paper/" in p),
                   ("其它", lambda p: "/api/paper/" not in p)):
    ds = [d for ts, d, m, p in rows if sel(p)]
    if not ds:
        continue
    b = Counter(int(d // 5) * 5 for d in ds)
    print(f"  {label:9s} n={len(ds):4d} 中位={statistics.median(ds):5.2f}s "
          f"分档={dict(sorted(b.items()))}")

# ---- 5. 原始样例（便于人工核对） ----
print("\n原始样例（前 6 条 + 最慢 6 条）：")
for ts, d, m, p in rows[:6]:
    print(f"  {ts:%H:%M:%S} {d:6.2f}s {m:4s} {p}")
for ts, d, m, p in sorted(rows, key=lambda r: -r[1])[:6]:
    print(f"  {ts:%H:%M:%S} {d:6.2f}s {m:4s} {p}")

# ---- 6. 所有不含 id 的慢端点 / 非 GET ----
odd = [r for r in rows if r[2] != "GET"]
print(f"\n非 GET 的慢请求：{len(odd)} 条")
for ts, d, m, p in odd[:10]:
    print(f"  {ts:%H:%M:%S} {d:6.2f}s {m:4s} {p}")
