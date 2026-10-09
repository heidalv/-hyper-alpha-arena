"""把 `_live_mids` 的宽 except 里被吞掉的真实异常打出来。"""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

print("--- step 1: import ---")
try:
    from backend.services.market_maker.board import to_bare_symbol, to_book_symbol
    print("   OK  to_bare_symbol('ASTERUSDT') =", to_bare_symbol("ASTERUSDT"))
    print("   OK  to_book_symbol('ASTER')     =", to_book_symbol("ASTER"))
except Exception:
    print("   FAIL"); traceback.print_exc(); raise SystemExit(1)

print("--- step 2: psycopg2 ---")
try:
    import psycopg2
    print("   OK")
except Exception:
    print("   FAIL"); traceback.print_exc(); raise SystemExit(1)

print("--- step 3: DSN ---")
url = os.environ.get("DATABASE_URL", "")
print("   raw =", url[:60])
for d in ("+psycopg2", "+psycopg", "+asyncpg"):
    url = url.replace(d, "")
head, sep, tail = url.rpartition("/")
print("   head =", head[-40:], " sep =", repr(sep), " tail =", tail)

print("--- step 4: connect ---")
try:
    cn = psycopg2.connect(head + "/alpha_market")
    cn.autocommit = True
    cur = cn.cursor()
    print("   OK connected")
except Exception:
    print("   FAIL"); traceback.print_exc(); raise SystemExit(1)

print("--- step 5: query ASTER ---")
now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
try:
    cur.execute(
        "SELECT (bids->0->>0)::float, (asks->0->>0)::float"
        "  FROM asterdex_depth_snapshots"
        " WHERE symbol = %s AND event_ts_ms > %s"
        " ORDER BY event_ts_ms DESC LIMIT 1",
        (to_book_symbol("ASTER"), now_ms - 600_000),
    )
    print("   row =", cur.fetchone())
except Exception:
    print("   FAIL"); traceback.print_exc()

print("--- step 6: book_ticker fallback for ASTER ---")
try:
    cur.execute(
        "SELECT (bid_px + ask_px) / 2 FROM asterdex_book_ticker"
        " WHERE symbol = %s AND event_ts_ms > %s"
        " ORDER BY event_ts_ms DESC LIMIT 1",
        (f"{to_bare_symbol('ASTER')}USDT", now_ms - 600_000),
    )
    print("   row =", cur.fetchone())
except Exception:
    print("   FAIL"); traceback.print_exc()

cn.close()
print("\n--- 直接调用 _live_mids（把日志级别调到 INFO 看告警）---")
import logging
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
from backend.api.hft_routes import _live_mids
print("   result =", _live_mids(["ASTER"]))
