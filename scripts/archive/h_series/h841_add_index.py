# -*- coding: utf-8 -*-
"""[h841] 建索引 + 前后对比(最高价值的零风险优化)。"""
import io
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
from backend.services.market_maker.attribution import _market_dsn  # noqa: E402
import psycopg  # noqa: E402

Q = ("SELECT symbol, min(price), max(price),"
     " sum(CASE WHEN is_buyer_maker THEN qty*price ELSE 0 END),"
     " sum(CASE WHEN NOT is_buyer_maker THEN qty*price ELSE 0 END)"
     " FROM asterdex_trades WHERE event_ts_ms > %s GROUP BY symbol")

with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
    lo = int((time.time() - 20) * 1000)
    t0 = time.perf_counter()
    for _ in range(5):
        cur.execute(Q, (lo,))
        cur.fetchall()
    before = (time.perf_counter() - t0) / 5 * 1000
    print(f"建索引前: {before:.0f} ms/次")
    cur.execute("SELECT count(*) FROM pg_indexes WHERE indexname='ix_adx_trades_ts'")
    if cur.fetchone()[0] == 0:
        print("建索引 CREATE INDEX CONCURRENTLY ix_adx_trades_ts ...")
        t0 = time.perf_counter()
        cur.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_adx_trades_ts"
                    " ON asterdex_trades (event_ts_ms)")
        print(f"  完成,耗时 {time.perf_counter() - t0:.1f}s")
    t0 = time.perf_counter()
    for _ in range(10):
        cur.execute(Q, (lo,))
        cur.fetchall()
    after = (time.perf_counter() - t0) / 10 * 1000
    print(f"建索引后: {after:.0f} ms/次  ⇒ 提速 {before / max(after, 0.01):.0f}×")
    # 顺带看看冷热(第二次跑同一窗口)
    cur.execute("VACUUM (ANALYZE) asterdex_trades")
    print("已 VACUUM ANALYZE(清死元组 + 更新统计)")
