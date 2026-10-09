"""h591 — 行情库体积与增长速度（只读，R117）。

动机：`asterdex_book_ticker` 已 3.43 亿行、写入 ~344 行/秒 ⇒ 需要知道
  · 两个库（alpha_market / alpha_arena）各占多少磁盘；
  · 哪张表最大、增长多快（按 1 小时增量估算日增）；
  · D: 剩余空间能撑多久（结合 `h591` 同轮的磁盘快照）。

用法：python scripts/h591_db_growth.py
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import datetime as dt
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

import psycopg  # noqa: E402

WATCH = ("asterdex_book_ticker", "asterdex_trades", "asterdex_depth_snapshots",
         "market_trades_aggregated", "lane_ledger")


def main() -> int:
    base = h.read_env_dsn()
    for label, dsn in (("alpha_market", base.replace("/alpha_arena", "/alpha_market")),
                       ("alpha_arena", base)):
        print("=" * 92)
        print(f"库 {label}")
        print("=" * 92)
        with psycopg.connect(dsn) as c, c.cursor() as cur:
            cur.execute("SELECT pg_size_pretty(pg_database_size(current_database())),"
                        " pg_database_size(current_database())")
            pretty, nbytes = cur.fetchone()
            print(f"  库大小 = {pretty} ({nbytes/1e9:.2f} GB)")
            for t in WATCH:
                try:
                    cur.execute("SELECT pg_size_pretty(pg_total_relation_size(%s)),"
                                " pg_total_relation_size(%s)", (t, t))
                    p, b = cur.fetchone()
                except Exception:  # noqa: BLE001
                    continue
                # 近 1 小时增量（用 id 递推，避免全表 count ✗ 太慢）
                grow = ""
                try:
                    if t == "lane_ledger":
                        cur.execute("SELECT count(*) FROM lane_ledger"
                                    " WHERE ts > now() - interval '1 hour'")
                        grow = f"  近1h +{cur.fetchone()[0]} 行"
                    else:
                        cur.execute(f"SELECT count(*) FROM {t}"
                                    f" WHERE ingest_ts > now() - interval '1 hour'")
                        grow = f"  近1h +{cur.fetchone()[0]} 行 ⇒ 日增 ≈{cur.fetchone() and 0 or 0}"
                        cur.execute(f"SELECT count(*) FROM {t}"
                                    f" WHERE ingest_ts > now() - interval '1 hour'")
                        n1 = cur.fetchone()[0]
                        grow = f"  近1h +{n1} 行 ⇒ 日增 ≈{n1*24:,} 行"
                except Exception:  # noqa: BLE001
                    grow = ""
                print(f"    {t:<30} {p:>12}{grow}")
    print("\n" + "=" * 92)
    print("D: 剩余空间请看同一轮的磁盘快照（h591 只报库侧）")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
