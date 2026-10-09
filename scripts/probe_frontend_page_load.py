# -*- coding: utf-8 -*-
"""前端 dev 服务器（Next/Turbopack，:5273）模块切换耗时实测。

对照：同一模块「冷」（自上次代码变更后首次访问，需按需编译）vs「热」（已编译）。
若冷/热差距达数秒→用户"切模块 20s+"里包含 dev 编译时间。
"""
from __future__ import annotations

import io
import sys
import time
from urllib import request as urlreq

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:5273"
PAGES = [
    "/paper-trading",
    "/live-trading",
    "/agent-monitor",
    "/strategy",
    "/ops",
    "/dashboard",
    "/factors",
    "/",
]


def get(path: str, timeout: float = 180.0):
    t0 = time.perf_counter()
    try:
        with urlreq.urlopen(BASE + path, timeout=timeout) as r:
            body = r.read()
            return time.perf_counter() - t0, r.status, len(body)
    except Exception as e:  # noqa: BLE001
        return time.perf_counter() - t0, type(e).__name__, 0


print(f"前端 dev 服务器 {BASE}，每个模块请求 2 次\n")
print(f"  {'模块':18s} {'第1次(冷)':>12s} {'第2次(热)':>12s} {'HTML KB':>9s}")
print("  " + "-" * 58)
rows = []
for p in PAGES:
    d1, s1, n1 = get(p)
    d2, s2, n2 = get(p)
    rows.append((p, d1, d2))
    flag = "  ← 冷启动代价" if d1 > 2 * max(d2, 0.05) and d1 > 1.0 else ""
    print(f"  {p:18s} {d1*1000:10.0f}ms {d2*1000:10.0f}ms {n1/1024:8.1f}{flag}"
          f"   [{s1}/{s2}]")

print()
cold = sum(1 for _, a, b in rows if a > 1.0)
print(f"  冷加载 >1s 的模块: {cold}/{len(rows)}")
worst = sorted(rows, key=lambda r: -r[1])[:3]
print("  最慢冷加载:", ", ".join(f"{p}={a:.2f}s" for p, a, _ in worst))
