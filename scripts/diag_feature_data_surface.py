"""查清 `market_trades_aggregated` 有哪些列可用于构"成交类特征"。

论文（Albers et al. 2502.18625v2 §7.2）的特征分四组：
    1. Price Dynamics   : 多尺度收益 / 振幅 / VWAP 偏离
    2. Trade Volume     : max/avg 单笔量、买/卖笔数、买/卖总量
    3. Momentum         : 收益自协方差、收益累加、**成交强度**（相邻成交平均间隔）
    4. LOB State        : 最优档流动性、$500K 冲击成本、top-of-book 存活时间、距上次变价时长

本脚本只回答一件事：**用我们已有的表，这四组各能复现多少？**
不构模型、不测 IC —— 先看清数据面，避免又造出一个"用错字段"的结论。

用法：
    .venv\\Scripts\\python.exe scripts\\diag_feature_data_surface.py
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

for t in ("market_trades_aggregated", "market_orderbook_snapshots",
          "asterdex_book_ticker", "asterdex_depth_snapshots", "asterdex_trades"):
    print(f"=== {t} ===")
    cur.execute(
        "SELECT column_name, data_type FROM information_schema.columns"
        " WHERE table_name = %s ORDER BY ordinal_position", (t,))
    cols = cur.fetchall()
    print("   " + ", ".join(c["column_name"] for c in cols))
    # 一行样例
    try:
        cur.execute(f'SELECT * FROM "{t}" ORDER BY 1 DESC LIMIT 1')
        r = cur.fetchone()
        if r:
            for k, v in list(dict(r).items())[:16]:
                print(f"     {k:<24} {str(v)[:70]}")
    except Exception as e:
        print("     样例失败:", e)
    # 时间粒度
    try:
        cur.execute(
            f'SELECT timestamp FROM "{t}" WHERE symbol IS NOT NULL'
            " ORDER BY timestamp DESC LIMIT 4")
        ts = [int(x["timestamp"]) for x in cur.fetchall()]
        if len(ts) >= 2:
            print("     时间间隔(ms):", [ts[i] - ts[i + 1] for i in range(len(ts) - 1)])
    except Exception as e:
        print("     粒度检查失败:", e)
    print()

cur.close()
cn.close()
