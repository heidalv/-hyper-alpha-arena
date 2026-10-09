"""排查 `_live_mids()` 返回空的原因：`asterdex_depth_snapshots` 的真实 schema 与 symbol 格式。

用法：
    .venv\\Scripts\\python.exe scripts\\diag_depth_schema.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
dsn = os.getenv("DATABASE_URL") or ""
for p in ("+psycopg2", "+psycopg", "+asyncpg"):
    dsn = dsn.replace(p, "")
head, _, _ = dsn.rpartition("/")
MARKET = head + "/alpha_market"

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

cn = psycopg2.connect(MARKET)
cn.autocommit = True
cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

print("[1] asterdex_depth_snapshots 列定义")
cur.execute(
    "SELECT column_name, data_type, udt_name FROM information_schema.columns"
    " WHERE table_name = 'asterdex_depth_snapshots' ORDER BY ordinal_position"
)
for r in cur.fetchall():
    print("    %-22s %-28s %s" % (r["column_name"], r["data_type"], r["udt_name"]))

print("\n[2] symbol 样例")
cur.execute(
    "SELECT DISTINCT symbol FROM asterdex_depth_snapshots"
    " WHERE event_ts_ms > (extract(epoch from now())*1000)::bigint - 600000"
    " ORDER BY symbol LIMIT 40"
)
syms = [r["symbol"] for r in cur.fetchall()]
print("    近 10 分钟在采:", " ".join(syms) if syms else "(空)")

print("\n[3] 取一行看 bids/asks 的实际结构")
cur.execute(
    "SELECT symbol, event_ts_ms, bids, asks FROM asterdex_depth_snapshots"
    " ORDER BY event_ts_ms DESC LIMIT 1"
)
row = cur.fetchone()
if row:
    print("    symbol=%s  event_ts_ms=%s" % (row["symbol"], row["event_ts_ms"]))
    b, a = row["bids"], row["asks"]
    print("    bids type=%s  len=%s  first=%s" % (type(b).__name__, len(b) if b is not None else None,
                                                  (b[0] if b else None)))
    print("    asks type=%s  len=%s  first=%s" % (type(a).__name__, len(a) if a is not None else None,
                                                  (a[0] if a else None)))
    print("    bids repr (前 80 字符): %s" % repr(b)[:80])
    print("    asks repr (前 80 字符): %s" % repr(a)[:80])

print("\n[4] 试各种取最优价写法（用真实 symbol）")
if row:
    s = row["symbol"]
    for label, sql in [
        ("bids->0->>0", "SELECT (bids->0->>0)::float, (asks->0->>0)::float FROM asterdex_depth_snapshots WHERE symbol=%s ORDER BY event_ts_ms DESC LIMIT 1"),
        ("bids->0->0", "SELECT (bids->0->0)::float, (asks->0->0)::float FROM asterdex_depth_snapshots WHERE symbol=%s ORDER BY event_ts_ms DESC LIMIT 1"),
    ]:
        try:
            cur.execute(sql, (s,))
            print("    %-14s -> %s" % (label, cur.fetchone()))
        except Exception as e:
            print("    %-14s -> ERR %s" % (label, e))

print("\n[5] book_ticker 的 symbol 与取值")
try:
    cur.execute("SELECT symbol, bid_px, ask_px, event_ts_ms FROM asterdex_book_ticker ORDER BY event_ts_ms DESC LIMIT 3")
    for r in cur.fetchall():
        print("    ", dict(r))
except Exception as e:
    print("    ERR", e)

cur.close()
cn.close()
