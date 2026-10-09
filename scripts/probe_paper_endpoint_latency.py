# -*- coding: utf-8 -*-
"""决定性实验：paper 端点的**引擎层**在隔离环境下快不快？

区分三种可能：
  A. 引擎本身慢（N+1 查询）⇒ 隔离下也是秒级；
  B. 资源争用（线程池/GIL/DB 连接池）⇒ 隔离下很快（毫秒级），生产里慢；
  C. 外部依赖（DC_ONLY 取价）⇒ 隔离下要等 DC。

同时体检：DB 连接池配置 + 历史 [DB LeakGuard] 告警。
只读（不发单、不写库）。
"""
from __future__ import annotations

import io
import re
import sys
import time
from collections import Counter
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ACC = 14

print("=" * 92)
print("① 引擎层隔离计时（连做 3 轮，看是否稳定）")
print("=" * 92)
try:
    from backend.database.connection import SessionLocal
    from backend.services.paper_trading_engine import paper_engine

    db = SessionLocal()
    db.execute(__import__("sqlalchemy").text("SET app.is_admin='on'"))
    try:
        for rnd in range(1, 4):
            print(f"  --- 第 {rnd} 轮 ---")
            for label, fn in (
                ("get_balance", lambda: paper_engine.get_balance(db, ACC)),
                ("get_positions(open)", lambda: paper_engine.get_positions(db, ACC, "open")),
                ("get_orders(50)", lambda: paper_engine.get_orders(db, ACC, None, 50)),
                ("get_summary", lambda: paper_engine.get_summary(db, ACC)),
            ):
                t0 = time.perf_counter()
                try:
                    out = fn()
                    n = len(out) if isinstance(out, (list, dict)) else "-"
                    ok = "OK"
                except Exception as exc:  # noqa: BLE001
                    n, ok = "-", f"{type(exc).__name__}: {str(exc)[:60]}"
                dt = (time.perf_counter() - t0) * 1000
                print(f"    {label:22s} {dt:9.1f} ms   n={n}  {ok}")
    finally:
        db.close()
except Exception as exc:  # noqa: BLE001
    print(f"  （失败: {type(exc).__name__}: {str(exc)[:160]}）")

print()
print("=" * 92)
print("② DB 连接池配置（争用假设的关键参数）")
print("=" * 92)
try:
    from backend.database import connection as C
    for name in ("engine", "market_engine", "analytics_engine"):
        eng = getattr(C, name, None)
        if eng is None:
            continue
        pool = getattr(eng, "pool", None)
        print(f"  {name}: url={str(eng.url)[:60]}")
        if pool is not None:
            for attr in ("size", "overflow", "_max_overflow", "timeout"):
                v = getattr(pool, attr, None)
                if v is not None:
                    print(f"      {attr} = {v}")
        try:
            st = eng.pool.status() if hasattr(eng.pool, "status") else ""
            print(f"      status = {st}")
        except Exception:  # noqa: BLE001
            pass
except Exception as exc:  # noqa: BLE001
    print(f"  （失败: {type(exc).__name__}: {str(exc)[:120]}）")

print()
print("=" * 92)
print("③ 历史 [DB LeakGuard] 告警（连接泄漏会耗尽池 ⇒ 所有请求排队）")
print("=" * 92)
lines = (ROOT / "logs" / "backend.log").read_text(encoding="utf-8", errors="replace").splitlines()
leaks = [ln for ln in lines if "LeakGuard" in ln]
print(f"  总数 = {len(leaks)}")
for ln in leaks[-6:]:
    print("   ", ln[:180])
c = Counter()
for ln in leaks:
    m = re.search(r"\[DB LeakGuard\]\s*(.{0,60})", ln)
    if m:
        c[m.group(1).strip()] += 1
print("  归类 top5:")
for k, v in c.most_common(5):
    print(f"    {v:4d}  {k}")
print()
print("④ 线程池/事件循环争用线索：uvicorn 线程与阻塞调用")
for kw in ("run_in_executor", "to_thread", "anyio", "threadpool"):
    n = sum(1 for ln in lines if kw in ln)
    print(f"  {kw:16s} {n}")
