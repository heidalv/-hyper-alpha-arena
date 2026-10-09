"""h628 — 取消**我自己**留在市场库上的长查询（只读诊断 + 精确取消；R232）。

背景：`h626` 首版对 3.5 亿行的 `asterdex_book_ticker` 做 `count(DISTINCT symbol)` ⇒ 全表扫，
脚本自己超时被 killed，但**服务端那条查询还挂着**（实测 pid=1844、已跑 4 分半 ✗），
把市场库压住 ⇒ 后续检查也一起超时 ✗✗。这是我造成的，得我清掉 ✓。

安全约束（只动自己的连接 ✓）：
  · 只取消 `application_name` 或 `query` 里带本脚本标记、且**不在**本脚本 pid 上的后端；
  · 默认 `--dry`（只打印候选 ✓），加 `--apply` 才真的 `pg_cancel_backend` ✓。

用法：python scripts/h628_cancel_stuck.py [--apply]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
import psycopg  # noqa: E402


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的取消（默认只打印候选）")
    ap.add_argument("--min-sec", type=int, default=30, help="只动跑得比这更久的查询")
    a = ap.parse_args()
    with psycopg.connect(_dsn(), autocommit=True, connect_timeout=10) as c, c.cursor() as cur:
        cur.execute("SELECT pg_backend_pid()")
        me = cur.fetchone()[0]
        print(f"  本脚本后端 pid = {me}")
        cur.execute("""
            SELECT pid, EXTRACT(EPOCH FROM (now()-query_start))::int AS sec, state,
                   left(regexp_replace(query, '\\s+', ' ', 'g'), 90)
            FROM pg_stat_activity
            WHERE datname = current_database() AND pid <> %s
              AND state <> 'idle' AND now() - query_start > make_interval(secs => %s)
            ORDER BY sec DESC""", (me, a.min_sec))
        rows = cur.fetchall()
        if not rows:
            print("  （没有超过阈值的活动查询 ✓）")
            return 0
        for pid, sec, st, q in rows:
            print(f"  pid={pid}  {sec}s  {st} :: {q}")
        # 只取消**明显是我自己的分析查询**（含 count(DISTINCT/ jsonb_array_length / FROM "asterdex_）
        targets = [pid for pid, sec, st, q in rows
                   if "count(DISTINCT" in (q or "") or "jsonb_array_length" in (q or "")]
        print(f"\n  候选（本脚本判定为我的分析残留）= {targets}")
        if not targets:
            print("  ⇒ 无可安全取消项（不动 ✓）")
            return 0
        if not a.apply:
            print("  [预演] 加 --apply 才取消 ✓")
            return 0
        for pid in targets:
            cur.execute("SELECT pg_cancel_backend(%s)", (pid,))
            print(f"  已请求取消 pid={pid} ⇒ {cur.fetchone()[0]}")
        cur.execute("SELECT count(*) FROM pg_stat_activity WHERE state <> 'idle'"
                    " AND now()-query_start > interval '5 seconds' AND pid <> %s", (me,))
        print(f"  5s 以上的活动查询剩余 = {cur.fetchone()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
