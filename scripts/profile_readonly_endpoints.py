# -*- coding: utf-8 -*-
"""分解只读端点的 CPU 去向：SQL vs ORM vs 领域逻辑 vs JSON 序列化。

回答："一个只读 summary 请求为什么要 58ms CPU / orders 要 363ms 墙钟？"
方法：cProfile + SQLAlchemy 语句计时（按请求路径的口径开会话）。
"""
from __future__ import annotations

import cProfile
import io
import json
import os
import pstats
import statistics
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ACCOUNT_ID = 14
N = 20

from backend.database.connection import SessionLocal  # noqa: E402
from backend.services.paper_trading_engine import paper_engine  # noqa: E402

CASES = [
    ("get_balance", lambda db: paper_engine.get_balance(db, ACCOUNT_ID)),
    ("get_positions(open)", lambda db: paper_engine.get_positions(db, ACCOUNT_ID, "open")),
    ("get_orders(50)", lambda db: paper_engine.get_orders(db, ACCOUNT_ID, None, 50)),
    ("get_summary", lambda db: paper_engine.get_summary(db, ACCOUNT_ID)),
]


def timed_case(fn):
    """单次调用：分离开会话/取数/JSON 三段耗时。"""
    t0 = time.perf_counter()
    db = SessionLocal()
    t1 = time.perf_counter()
    res = fn(db)
    t2 = time.perf_counter()
    payload = json.dumps(res, default=str)
    t3 = time.perf_counter()
    db.close()
    t4 = time.perf_counter()
    return {
        "session_open_ms": (t1 - t0) * 1000,
        "query_ms": (t2 - t1) * 1000,
        "json_ms": (t3 - t2) * 1000,
        "close_ms": (t4 - t3) * 1000,
        "total_ms": (t4 - t0) * 1000,
        "bytes": len(payload),
    }


print(f"账号 {ACCOUNT_ID}，每项 {N} 次\n")
print("=" * 100)
print("【1】分段耗时（毫秒，中位/最大）")
print("=" * 100)
print(f"  {'用例':22s} {'开会话':>9s} {'取数':>9s} {'JSON':>9s} {'关闭':>9s} "
      f"{'合计':>9s} {'载荷KB':>8s}")

for name, fn in CASES:
    rows = [timed_case(fn) for _ in range(N)]
    med = lambda k: statistics.median(r[k] for r in rows)  # noqa: E731
    mx = lambda k: max(r[k] for r in rows)  # noqa: E731
    print(f"  {name:22s} {med('session_open_ms'):4.1f}/{mx('session_open_ms'):<5.0f}"
          f" {med('query_ms'):4.1f}/{mx('query_ms'):<5.0f}"
          f" {med('json_ms'):4.1f}/{mx('json_ms'):<5.0f}"
          f" {med('close_ms'):4.1f}/{mx('close_ms'):<5.0f}"
          f" {med('total_ms'):4.1f}/{mx('total_ms'):<5.0f}"
          f" {med('bytes')/1024:7.1f}")

print()
print("=" * 100)
print("【2】cProfile：CPU 去向 top20（tottime，单函数自身耗时）")
print("=" * 100)

for name, fn in CASES:
    db = SessionLocal()
    pr = cProfile.Profile()
    pr.enable()
    for _ in range(N):
        fn(db)
    pr.disable()
    db.close()
    st = pstats.Stats(pr)
    st.sort_stats("tottime")
    buf = io.StringIO()
    st.stream = buf
    st.print_stats(20)
    print(f"\n──── {name} ×{N} ────")
    lines = buf.getvalue().splitlines()
    keep = False
    shown = 0
    for ln in lines:
        if "ncalls" in ln and "tottime" in ln:
            keep = True
            print("  " + ln.strip()[:96])
            continue
        if keep:
            s = ln.strip()
            if not s or s.startswith("{"):
                continue
            print("  " + s[:96])
            shown += 1
            if shown >= 18:
                break
