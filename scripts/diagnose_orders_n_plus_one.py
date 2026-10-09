# -*- coding: utf-8 -*-
"""定位 get_orders 的 3174 次/请求调用：完整函数名 + SQL 语句条数与耗时。

同时用 SQLAlchemy 事件统计每个引擎调用的**实际 SQL 条数**（N+1 检测）。
"""
from __future__ import annotations

import cProfile
import io
import json
import pstats
import statistics
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ACCOUNT_ID = 14

from sqlalchemy import event  # noqa: E402

from backend.database.connection import SessionLocal, engine  # noqa: E402
from backend.services.paper_trading_engine import paper_engine  # noqa: E402

# ── SQL 语句计数器 ──
stmts: list[tuple[str, float]] = []


@event.listens_for(engine, "before_cursor_execute")
def _bce(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault("_t0", []).append(time.perf_counter())


@event.listens_for(engine, "after_cursor_execute")
def _ace(conn, cursor, statement, parameters, context, executemany):
    t0 = conn.info["_t0"].pop()
    stmts.append((statement.strip().split("\n")[0][:110], time.perf_counter() - t0))


def measure(label: str, fn, n: int = 5):
    stmts.clear()
    db = SessionLocal()
    per_call = []
    for _ in range(n):
        before = len(stmts)
        t0 = time.perf_counter()
        res = fn(db)
        dt = (time.perf_counter() - t0) * 1000
        chunk = stmts[before:]
        per_call.append((dt, len(chunk), sum(d for _, d in chunk) * 1000,
                         len(json.dumps(res, default=str))))
    db.close()
    dts = [c[0] for c in per_call]
    sql_n = [c[1] for c in per_call]
    sql_ms = [c[2] for c in per_call]
    print(f"  {label:22s} 墙钟中位={statistics.median(dts):7.1f}ms  "
          f"SQL 条数={statistics.median(sql_n):5.0f}  "
          f"SQL 耗时中位={statistics.median(sql_ms):7.1f}ms  "
          f"载荷={per_call[0][3]/1024:6.1f}KB")
    return stmts.copy()


print(f"账号 {ACCOUNT_ID}\n")
print("=" * 100)
print("【1】每个引擎调用实际执行的 SQL 条数与耗时（N+1 检测）")
print("=" * 100)
snap = {}
snap["get_balance"] = measure("get_balance", lambda db: paper_engine.get_balance(db, ACCOUNT_ID))
snap["get_positions"] = measure("get_positions(open)", lambda db: paper_engine.get_positions(db, ACCOUNT_ID, "open"))
snap["get_orders"] = measure("get_orders(50)", lambda db: paper_engine.get_orders(db, ACCOUNT_ID, None, 50))
snap["get_summary"] = measure("get_summary", lambda db: paper_engine.get_summary(db, ACCOUNT_ID))

print()
print("=" * 100)
print("【2】get_orders 的 SQL 指纹（前 12 条 + 去重统计）")
print("=" * 100)
seen: dict[str, int] = {}
for s, _ in snap["get_orders"]:
    seen[s] = seen.get(s, 0) + 1
for s, c in sorted(seen.items(), key=lambda kv: -kv[1])[:12]:
    print(f"  ×{c:5d}  {s[:92]}")
print(f"  共 {len(snap['get_orders'])} 条语句，{len(seen)} 种不同语句")

print()
print("=" * 100)
print("【3】完整 cProfile（不截断）—— get_orders ×20 自身耗时 top12")
print("=" * 100)
db = SessionLocal()
pr = cProfile.Profile()
pr.enable()
for _ in range(20):
    paper_engine.get_orders(db, ACCOUNT_ID, None, 50)
pr.disable()
db.close()
st = pstats.Stats(pr).sort_stats("tottime")
buf = io.StringIO()
st.stream = buf
st.print_stats(12)
for ln in buf.getvalue().splitlines():
    if ln.strip():
        print("  " + ln.rstrip()[:200])
