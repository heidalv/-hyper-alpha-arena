"""查清可用于"队列消耗制"成交建模的最细粒度数据有哪些。

论文（Albers et al. 2502.18625v2）第 612–618 行的成交机制：
    挂在某档位的单子成交，条件是
        「自挂单以来累计的对手方主动成交量」 > 「该档位我们前面的挂量 LA」
    —— **不需要价格穿过我们的档位**。

我们现有 fill 模型（`core.fill_side`，`MM_F60_PENETRATION_BP=0`）要求
`seg_low < bid`（价格穿过），且假设 LA=0。两个假设都与论文相反。
要改成论文口径，先得知道手上有哪些粒度。

用法：
    .venv\\Scripts\\python.exe scripts\\diag_tick_data_inventory.py
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

print("=== alpha_market 里与成交/盘口相关的表 ===")
cur.execute(
    "SELECT tablename FROM pg_tables WHERE schemaname='public'"
    " AND (tablename LIKE '%%trade%%' OR tablename LIKE '%%tick%%'"
    "      OR tablename LIKE '%%book%%' OR tablename LIKE '%%depth%%')"
    " ORDER BY tablename"
)
tabs = [r["tablename"] for r in cur.fetchall()]
for t in tabs:
    print("   ", t)

for t in tabs:
    print(f"\n=== {t} ===")
    try:
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns"
            " WHERE table_name = %s ORDER BY ordinal_position", (t,)
        )
        cols = cur.fetchall()
        print("    列:", ", ".join(f"{c['column_name']}" for c in cols))
        # 行数（可能很慢，用 reltuples 估算）
        cur.execute(
            "SELECT reltuples::bigint AS n FROM pg_class WHERE relname = %s", (t,)
        )
        est = cur.fetchone()
        print("    估算行数:", est["n"] if est else "?")
        # 采样一行
        cur.execute(f'SELECT * FROM "{t}" ORDER BY 1 DESC LIMIT 1')
        row = cur.fetchone()
        if row:
            d = dict(row)
            for k, v in list(d.items())[:14]:
                s = repr(v)
                print(f"      {k:<26} {s[:90]}")
    except Exception as e:
        print("    查询失败:", e)

print("\n=== asterdex_trades 若有：时间粒度与主动方字段 ===")
try:
    cur.execute(
        "SELECT COUNT(*) AS n, MIN(event_ts_ms) AS mn, MAX(event_ts_ms) AS mx"
        "  FROM asterdex_trades"
    )
    r = cur.fetchone()
    print("    行数 %s  跨度 %s .. %s" % (r["n"], r["mn"], r["mx"]))
    cur.execute("SELECT * FROM asterdex_trades ORDER BY event_ts_ms DESC LIMIT 3")
    for row in cur.fetchall():
        print("    ", {k: (str(v)[:40] if v is not None else None) for k, v in dict(row).items()})
except Exception as e:
    print("    asterdex_trades 不可用:", e)

print("\n=== market_trades_aggregated 的时间粒度（是否只有 15s 桶）===")
try:
    cur.execute(
        "SELECT timestamp, COUNT(*) OVER () AS total FROM market_trades_aggregated"
        " WHERE exchange='asterdex' AND symbol='ASTERUSDT'"
        " ORDER BY timestamp DESC LIMIT 8"
    )
    for row in cur.fetchall():
        print("    ts =", row["timestamp"], " total =", row["total"])
    cur.execute(
        "SELECT timestamp FROM market_trades_aggregated"
        " WHERE exchange='asterdex' AND symbol='ASTERUSDT'"
        " ORDER BY timestamp DESC LIMIT 4"
    )
    ts = [r["timestamp"] for r in cur.fetchall()]
    if len(ts) >= 2:
        diffs = [ts[i] - ts[i + 1] for i in range(len(ts) - 1)]
        print("    相邻间隔(ms):", diffs, " ⇒ 粒度 ≈", min(diffs) if diffs else "?")
except Exception as e:
    print("    查询失败:", e)

cur.close()
cn.close()
