# -*- coding: utf-8 -*-
"""[h847] save_states 内部三段分别计时:ensure_table / 自愈 open_positions / 落库。"""
import io
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

# ① ensure_table
from backend.services.market_maker.runner import ensure_table  # noqa: E402
t0 = time.perf_counter()
for _ in range(5):
    ensure_table()
print(f"① ensure_table: {(time.perf_counter() - t0) / 5 * 1000:.1f} ms")

# ② 自愈用的 open_positions(账本聚合)
from backend.services import lane_ledger  # noqa: E402
t0 = time.perf_counter()
for _ in range(3):
    _p = lane_ledger.open_positions(lane_id="mm_asterdex")
print(f"② lane_ledger.open_positions: {(time.perf_counter() - t0) / 3 * 1000:.1f} ms"
      f"(返回 {len(_p)} 个持仓)")

# ③ SessionLocal 建连(池化?)
from backend.core.tenant import system_identity  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402
t0 = time.perf_counter()
for _ in range(5):
    with system_identity():
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            db.fetchall()
print(f"③ SessionLocal 取连+SELECT 1: {(time.perf_counter() - t0) / 5 * 1000:.1f} ms")

# ④ _fresh_book_for(自愈里每个持仓调一次)
from backend.services.market_maker.runner import get_runner  # noqa: E402
r = get_runner("mm_asterdex")
if r is not None:
    t0 = time.perf_counter()
    for _ in range(20):
        r._fresh_book_for("BTC")
    print(f"④ _fresh_book_for: {(time.perf_counter() - t0) / 20 * 1000:.2f} ms")
