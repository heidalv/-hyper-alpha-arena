"""查清我们手上有没有**多交易所**数据（决定能否做跨所对比）。

用户问：「现在都是在 Aster 上，币安上做会是怎样？HL 上或者其他所？」
要回答它有两种路径：
  A. 用真实数据直接测（需要该所的数据）
  B. 用费率/微观结构的**算术外推**（需要准确的费率表 + 我们的 edge 分解）

本脚本只回答"路 A 可行吗"：数一下 `alpha_market` 里每个 exchange 的数据量。

用法：
    .venv\\Scripts\\python.exe scripts\\diag_multivenue_coverage.py
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

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

cn = psycopg2.connect(head + "/alpha_market")
cn.autocommit = True
cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

print("=== market_* 表的 exchange 取值（跨所对比的数据基础）===\n")
for t in ("market_orderbook_snapshots", "market_trades_aggregated", "ticker_snapshots"):
    print(f"--- {t} ---")
    try:
        cur.execute(
            f"SELECT exchange, COUNT(*) n, COUNT(DISTINCT symbol) syms,"
            f"       MIN(timestamp) mn, MAX(timestamp) mx"
            f"  FROM {t} GROUP BY exchange ORDER BY n DESC")
        rows = cur.fetchall()
        if not rows:
            print("    （空）")
        for r in rows:
            span_h = ((int(r["mx"]) - int(r["mn"])) / 3600000.0) if r["mn"] and r["mx"] else 0
            print("    %-14s rows=%-12d symbols=%-5d span=%.0fh"
                  % (r["exchange"], r["n"], r["syms"], span_h))
    except Exception as e:
        print("    ERR", str(e)[:80])
    print()

print("=== 其它可能含多所/资金费的表 ===")
cur.execute(
    "SELECT tablename FROM pg_tables WHERE schemaname='public'"
    " AND (tablename LIKE '%%funding%%' OR tablename LIKE '%%hyper%%'"
    "      OR tablename LIKE '%%binance%%' OR tablename LIKE '%%venue%%'"
    "      OR tablename LIKE '%%bybit%%' OR tablename LIKE '%%okx%%')"
    " ORDER BY tablename")
tabs = [r["tablename"] for r in cur.fetchall()]
print("   " + (", ".join(tabs) if tabs else "（无）"))

print("\n=== 各 exchange 的可比标的（用于跨所配对）===")
cur.execute(
    "SELECT exchange, COUNT(DISTINCT symbol) n FROM market_trades_aggregated"
    " WHERE timestamp > (extract(epoch from now())*1000)::bigint - 86400000"
    " GROUP BY exchange ORDER BY n DESC")
for r in cur.fetchall():
    print("    %-14s %d symbols" % (r["exchange"], r["n"]))

cur.close()
cn.close()
