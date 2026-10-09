# -*- coding: utf-8 -*-
"""[h842] 索引后的真实节拍 + 单币查询/聚合耗时拆解。"""
import io
import re
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
from backend.services.market_maker.attribution import _market_dsn  # noqa: E402
import psycopg  # noqa: E402

# ① 干净窗口的 worker 节拍
log = ROOT / "logs" / "mm_lane_worker.log"
lines = [l for l in log.read_text(encoding="utf-8", errors="replace").splitlines()
         if "ticks=" in l and "[mm-worker]" in l][-3:]
if len(lines) >= 2:
    out = []
    for l in lines:
        ts = re.search(r"^(\S+ \S+)", l)
        n = re.search(r"ticks=(\d+)", l)
        if ts and n:
            out.append((time.mktime(time.strptime(ts.group(1), "%Y-%m-%d %H:%M:%S")),
                        int(n.group(1))))
    if len(out) >= 2:
        (t0, n0), (t1, n1) = out[-2], out[-1]
        per = (t1 - t0) / max(1, n1 - n0) * 1000
        print(f"① worker 节拍(索引后): {per:.0f} ms/tick({n1 - n0} tick / {t1 - t0:.0f}s)"
              f" ⇒ {per / 33:.1f} ms/币")
        print(f"   (索引前实测 1383 ms/tick ⇒ 提升 {1383 / max(per, 1):.1f}×)")

# ② 单币同款查询(逐笔窗口)
with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute("SELECT DISTINCT symbol FROM asterdex_trades"
                " WHERE event_ts_ms > (extract(epoch from now())-60)*1000 LIMIT 12")
    syms = [r[0] for r in cur.fetchall()]
    hi = int(time.time() * 1000)
    lo = hi - 60000
    t0 = time.perf_counter()
    tot = 0
    for s in syms:
        cur.execute("SELECT event_ts_ms, price, qty, is_buyer_maker FROM asterdex_trades"
                    " WHERE symbol=%s AND event_ts_ms > %s AND event_ts_ms <= %s"
                    " ORDER BY event_ts_ms", (s, lo, hi))
        tot += len(cur.fetchall())
    per_q = (time.perf_counter() - t0) / max(1, len(syms)) * 1000
    print(f"② 单币逐笔查询(60s 窗口): {per_q:.1f} ms/币 "
          f"⇒ 33 币 ≈ {per_q * 33:.0f} ms/tick(共 {tot} 行)")
    # ③ 批量化:一条 ANY() 查询取全部币
    t0 = time.perf_counter()
    cur.execute("SELECT symbol, event_ts_ms, price, qty, is_buyer_maker FROM asterdex_trades"
                " WHERE symbol = ANY(%s) AND event_ts_ms > %s AND event_ts_ms <= %s"
                " ORDER BY symbol, event_ts_ms", (syms, lo, hi))
    rows = cur.fetchall()
    t_batch = (time.perf_counter() - t0) * 1000
    print(f"③ 批量版(一条 ANY 查询,{len(rows)} 行): {t_batch:.1f} ms "
          f"⇒ 相比逐币 {(t0 and (per_q * len(syms)) / max(t_batch, 0.01)):.0f}× 提速")
