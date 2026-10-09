"""h549：**聚合环节停摆取证**——原始逐笔在写、聚合表却停更。

现场（2026-09-29 约 10:12L）：
  · `asterdex_trades` / `asterdex_book_ticker` / `asterdex_depth_snapshots` **新鲜**（WS 在写）；
  · **`market_trades_aggregated` 停在 09:33:07**、`asterdex_stream_health` 停在 09:34:54；
  · 而做市引擎判定成交**读的正是聚合表** ⇒ 若聚合断了，车道会再次停摆（只是滞后一些）。

本脚本（只读）回答三件事：
  1. 原始逐笔是**持续**在写还是只写了一次（按分钟计数）；
  2. 聚合表最近 30 分钟写到哪（按分钟计数）；
  3. 写入方进程是什么、什么时候启动的（聚合器是谁）。

用法：python scripts/h549_agg_stall_forensics.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h425_repair_trial import read_env_dsn  # noqa: E402


def _lag(now, other):
    if other is None:
        return None
    if isinstance(other, (int, float)):
        f = float(other)
        for div in (1e3, 1e6, 1e9):
            try:
                other = dt.datetime.fromtimestamp(f / div)
                break
            except (OSError, OverflowError, ValueError):
                continue
        else:
            return None
    if getattr(other, "tzinfo", None) is None:
        other = other.replace(tzinfo=dt.timezone.utc)
    n = now if getattr(now, "tzinfo", None) else now.replace(tzinfo=dt.timezone.utc)
    return (n - other).total_seconds()


def main() -> int:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    dsn = env.get("MARKET_DATABASE_URL") or env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        dsn = dsn.replace(j, "")
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT now()")
            now = cur.fetchone()[0]
            print(f"market 库 now() = {now:%Y-%m-%d %H:%M:%S}")
            for tbl, col, unit in (("asterdex_trades", "recv_ts_ns", "ns"),
                                   ("market_trades_aggregated", "created_at", "ts"),
                                   ("asterdex_stream_health", "updated_at", "ts")):
                if unit == "ns":
                    cur.execute(f"SELECT count(*), max({col}) FROM {tbl}")  # noqa: S608
                    n, mx = cur.fetchone()
                    lag = _lag(now, mx)
                    print(f"\n[{tbl}] 共 {n} 行，最新 {mx} ⇒ 滞后 "
                          f"{None if lag is None else round(lag,1)}s")
                    cur.execute(
                        f"SELECT date_trunc('minute', to_timestamp({col}/1e9)) AS m,"
                        f" count(*) FROM {tbl}"
                        f" WHERE {col} > (extract(epoch from now()) - 1800) * 1e9"
                        f" GROUP BY 1 ORDER BY 1 DESC LIMIT 12")  # noqa: S608
                else:
                    cur.execute(f"SELECT count(*), max({col}) FROM {tbl}")  # noqa: S608
                    n, mx = cur.fetchone()
                    lag = _lag(now, mx)
                    print(f"[{tbl}] 共 {n} 行，最新 {mx} ⇒ 滞后 "
                          f"{None if lag is None else round(lag,1)}s")
                    cur.execute(
                        f"SELECT date_trunc('minute', {col}) AS m, count(*) FROM {tbl}"
                        f" WHERE {col} > now() - interval '30 minutes'"
                        f" GROUP BY 1 ORDER BY 1 DESC LIMIT 12")  # noqa: S608
                rows = cur.fetchall()
                print(f"  近 30 分钟按分钟（最新 12 桶）："
                      + ("、".join(f"{m:%H:%M}×{n}" for m, n in rows) if rows else "（无）"))
    print("\n相关进程：")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
             "Where-Object { $_.CommandLine -match 'aster|market|aggregate|ingest' } | "
             "ForEach-Object { $_.ProcessId.ToString() + ' | ' + "
             "$_.CreationDate.ToString('MM-dd HH:mm:ss') + ' | ' + "
             "$_.CommandLine.Substring(0, [Math]::Min(120, $_.CommandLine.Length)) }"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90)
        for line in (r.stdout or "").strip().splitlines():
            print("  " + line)
    except Exception as e:
        print(f"  （进程查询失败：{str(e)[:80]}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
