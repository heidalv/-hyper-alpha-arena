"""诊断：为什么 `DISTINCT ON ... ORDER BY symbol, event_ts_ms DESC` 要 130 秒？

对比三种写法：
  A `DISTINCT ON (symbol) ... ORDER BY symbol, event_ts_ms DESC`（F281 首版，实测 130s ✗✗）
  B 逆向 `ORDER BY symbol DESC, event_ts_ms DESC` DESC 扫描技巧
  C 逐符号 `ORDER BY event_ts_ms DESC LIMIT 1`（10 次小查询，各自走索引）
  D LATERAL join

并打印 `asterdex_book_ticker` 的索引情况。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

SYMS = ['ASTERUSDT', 'XRPUSDT', 'SOLUSDT', 'DOGEUSDT', 'UNIUSDT',
        'PENDLEUSDT', 'SEIUSDT', 'VIRTUALUSDT', '1000SHIBUSDT', 'ARBUSDT']


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main():
    import psycopg2
    import psycopg2.extras

    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print("=" * 88)
    print("asterdex_book_ticker 的索引")
    print("=" * 88)
    cur.execute("SELECT indexname, indexdef FROM pg_indexes"
                " WHERE tablename='asterdex_book_ticker'")
    idx = cur.fetchall()
    for r in idx:
        print(f"  {r['indexname']}\n      {r['indexdef']}")
    print(f"\n  行数：", end="")
    cur.execute("SELECT reltuples::bigint n FROM pg_class"
                " WHERE relname='asterdex_book_ticker'")
    print(cur.fetchone()["n"])

    def t(label, sql, params=None):
        t0 = time.time()
        try:
            cur.execute(sql, params)
            rows = cur.fetchall()
            dt = time.time() - t0
            print(f"  {label:<46} {dt*1000:>10,.0f} ms   {len(rows)} 行")
            return rows, dt
        except Exception as e:
            print(f"  {label:<46} FAILED: {e}")
            return None, None

    print("\n" + "=" * 88)
    print("三种写法的耗时对比")
    print("=" * 88)
    t("A DISTINCT ON + ORDER BY symbol,ts DESC（F281 首版）",
      "SELECT DISTINCT ON (symbol) symbol, bid_px::float b, ask_px::float a"
      "  FROM asterdex_book_ticker"
      " WHERE symbol = ANY(%(ss)s) AND bid_px>0 AND ask_px>bid_px"
      " ORDER BY symbol, event_ts_ms DESC", {"ss": SYMS})

    t("B 逆向扫描 ORDER BY symbol DESC, ts DESC",
      "SELECT DISTINCT ON (symbol) symbol, bid_px::float b, ask_px::float a"
      "  FROM asterdex_book_ticker"
      " WHERE symbol = ANY(%(ss)s) AND bid_px>0 AND ask_px>bid_px"
      " ORDER BY symbol DESC, event_ts_ms DESC", {"ss": SYMS})

    t("C 逐符号 LIMIT 1（走 (symbol,ts) 索引）",
      "SELECT DISTINCT ON (symbol) symbol, bid_px::float b, ask_px::float a"
      "  FROM asterdex_book_ticker"
      " WHERE symbol = ANY(%(ss)s) AND bid_px>0 AND ask_px>bid_px"
      " ORDER BY symbol, event_ts_ms DESC", {"ss": SYMS})

    # 真正逐符号
    t0 = time.time()
    n = 0
    for s in SYMS:
        cur.execute("SELECT bid_px::float b, ask_px::float a FROM asterdex_book_ticker"
                    " WHERE symbol=%s AND bid_px>0 AND ask_px>bid_px"
                    " ORDER BY event_ts_ms DESC LIMIT 1", (s,))
        if cur.fetchone():
            n += 1
    print(f"  {'C2 逐符号 LIMIT 1（10 次独立查询）':<46} {(time.time()-t0)*1000:>10,.0f} ms   {n} 行")

    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
