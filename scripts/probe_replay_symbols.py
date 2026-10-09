# -*- coding: utf-8 -*-
"""asterdex replay 源的 symbol 命名 + replay._load_series 传入格式核对。只读。"""
import sys
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

c = psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"),
                    autocommit=True)
cur = c.cursor()
cur.execute("""
    SELECT symbol, COUNT(*) FROM market_orderbook_snapshots
    WHERE exchange='asterdex' AND timestamp > (EXTRACT(EPOCH FROM now())*1000 - 12*3600*1000)::bigint
    GROUP BY 1 ORDER BY 2 DESC LIMIT 12
""")
print("近 12h asterdex symbols:")
for r in cur.fetchall():
    print("  ", r)

# replay._load_series 用什么符号查
import inspect
from backend.services.market_maker import replay
src = inspect.getsource(replay._load_series)
print("\n_load_series 源码片段：")
for line in src.splitlines():
    if "symbol" in line or "SELECT" in line:
        print("  ", line.strip()[:120])
