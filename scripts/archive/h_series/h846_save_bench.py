# -*- coding: utf-8 -*-
"""[h846] save_states 两种写法对拍:逐条 execute vs executemany。"""
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
from backend.core.tenant import system_identity  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

SQL = ("INSERT INTO lane_runtime_state (lane_id, symbol, state_json, updated_ts)"
       " VALUES (:l, :s, CAST(:j AS JSONB), now())"
       " ON CONFLICT (lane_id, symbol) DO UPDATE SET"
       " state_json=EXCLUDED.state_json, updated_ts=now()")

syms = [f"BENCH{i}" for i in range(33)]
rows = [{"l": "bench_lane", "s": s, "j": json.dumps({"i": i, "q": 0.0})}
        for i, s in enumerate(syms)]

with system_identity():
    # ① 逐条 execute(旧写法)
    t0 = time.perf_counter()
    for _ in range(3):
        with SessionLocal() as db:
            for r in rows:
                db.execute(text(SQL), r)
            db.commit()
    t_one = (time.perf_counter() - t0) / 3 * 1000
    # ② executemany(新写法)
    t0 = time.perf_counter()
    for _ in range(3):
        with SessionLocal() as db:
            db.execute(text(SQL), rows)
            db.commit()
    t_many = (time.perf_counter() - t0) / 3 * 1000
    # ③ 单条多值 VALUES(手工拼)
    _vals = ",".join([f"(:l{i}, :s{i}, CAST(:j{i} AS JSONB), now())" for i in range(len(rows))])
    params = {}
    for i, r in enumerate(rows):
        params[f"l{i}"], params[f"s{i}"], params[f"j{i}"] = r["l"], r["s"], r["j"]
    SQL3 = ("INSERT INTO lane_runtime_state (lane_id, symbol, state_json, updated_ts)"
            f" VALUES {_vals} ON CONFLICT (lane_id, symbol) DO UPDATE SET"
            " state_json=EXCLUDED.state_json, updated_ts=now()")
    t0 = time.perf_counter()
    for _ in range(3):
        with SessionLocal() as db:
            db.execute(text(SQL3), params)
            db.commit()
    t_multi = (time.perf_counter() - t0) / 3 * 1000
print(f"① 逐条 execute 33 次: {t_one:.1f} ms")
print(f"② executemany:        {t_many:.1f} ms")
print(f"③ 单条多值 VALUES:    {t_multi:.1f} ms")
best = min((t_one, "逐条"), (t_many, "executemany"), (t_multi, "多值"))
print(f"⇒ 最快:{best[1]}({best[0]:.1f} ms)")
with system_identity():
    with SessionLocal() as db:
        db.execute(text("DELETE FROM lane_runtime_state WHERE lane_id='bench_lane'"))
        db.commit()
