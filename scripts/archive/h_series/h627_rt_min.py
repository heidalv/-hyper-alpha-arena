"""h627 — **最快的一次实时性判定**（只读；单条索引查询，不做任何扫表 ✗）。

为什么另写一个：`h626` 因覆盖面的 count 查询把市场库压住 ⇒ 自己超时 ✗（首版对 3.5 亿行
做 count(DISTINCT)，第二次仍被前一次的残留长查询拖住 ✗）。本脚本只做三件事：
  1. 每张表一条 `max(event_ts_ms)`（走索引 ⇒ 毫秒级 ✓）；
  2. `asterdex_stream_health` 的 `updated_at`（3 行小表 ✓）；
  3. **当前长查询**（`pg_stat_activity`，state<>'idle' 且 >5s ✓）——若我上一轮的查询还挂着，
     这一步会直接把它列出来 ✓（并给出 `pg_cancel_backend` 的建议 pid ✓）。

用法：python scripts/h627_rt_min.py
"""
from __future__ import annotations

import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
import psycopg  # noqa: E402

TABLES = ("asterdex_trades", "asterdex_book_ticker", "asterdex_depth_snapshots",
          "asterdex_stream_health")


def _dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("MARKET_DATABASE_URL") or env.get("DATABASE_URL") or ""
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main() -> int:
    print("=" * 80)
    print("h627 — 实时性快判（单条索引查询）")
    print("=" * 80)
    with psycopg.connect(_dsn(), autocommit=True,
                         connect_timeout=10) as c, c.cursor() as cur:
        cur.execute("SET statement_timeout = '8000'")   # 单条查询最多 8s ⇒ 不会再挂住 ✓
        for t in TABLES:
            col = "updated_at" if t.endswith("stream_health") else "event_ts_ms"
            try:
                if col == "updated_at":
                    cur.execute(f'SELECT EXTRACT(EPOCH FROM (now() - max(updated_at)))::float8 FROM "{t}"')
                else:
                    cur.execute(f'SELECT EXTRACT(EPOCH FROM (now() - to_timestamp(max("{col}")/1000.0)))::float8'
                                f' FROM "{t}"')
                lag = cur.fetchone()[0]
                lag = float(lag) if lag is not None else -1.0
                print(f"  {t:<26} 滞后 {lag:8.1f}s  {'✓ 实时' if lag <= 60 else '✗ 陈旧'}")
            except Exception as e:  # noqa: BLE001
                print(f"  {t:<26} 查询失败/超时：{type(e).__name__}: {str(e)[:60]}")
        print("\n  当前长查询（>5s，可能是压住数据库的元凶 ✗）：")
        try:
            cur.execute("SELECT pid, now()-query_start AS dur, state, left(query, 70)"
                        " FROM pg_stat_activity WHERE state <> 'idle' AND datname = current_database()"
                        " AND now()-query_start > interval '5 seconds' ORDER BY dur DESC LIMIT 6")
            rows = cur.fetchall()
            if not rows:
                print("    （无 ✓）")
            for pid, dur, st, q in rows:
                print(f"    pid={pid} 时长={dur} state={st} :: {q}")
            print("    ⇒ 若是本机脚本的残留：`SELECT pg_cancel_backend(<pid>);` ✓")
        except Exception as e:  # noqa: BLE001
            print(f"    查询失败：{type(e).__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
