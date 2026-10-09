# -*- coding: utf-8 -*-
"""H277 行情 tick 表保留期清理（根因修复）。

# 背景（2026-09-23 死机后"后端崩了/全部没数据"的根因）

  asterdex_book_ticker   1.86 亿行 / 39 GB
  asterdex_depth_snapshots 7100 万行 / 118 GB
  没有任何保留期 ⇒ 无限增长。看门狗探针/日摘要等做
  `max(event_ts_ms) ... WHERE symbol=ANY(...)` 时全表扫描 2~4 分钟，
  30 分钟一次的重叠实例把整个 DB 的 IO 打满 ⇒ 前端 API 全体 3s+ 超时。

# 本脚本

  · 分块删除 N 天前的行（默认 14 天），每块 50k 行 + 0.2s 停顿，避免长事务/锁暴涨；
  · 之后对每个表 VACUUM（回收空间，供 OS 还给文件系统/复用）；
  · 幂等：可重复跑；中断后下次从最早日期继续。
  · 用法：
      python scripts/h277_retention_purge.py --days 14
      python scripts/h277_retention_purge.py --days 14 --dry-run

# 调度

  schtasks 任务 DSH_HFT_RETENTION，每天 03:00（行情最淡时段）。
"""
from __future__ import annotations

import argparse
import sys
import time

ROOT = None
import pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TABLES = (
    # (表名, 时间列, 是否毫秒, 说明)
    ("asterdex_book_ticker", "event_ts_ms", True, "盘口 tick（39GB）"),
    ("asterdex_depth_snapshots", "event_ts_ms", True, "深度快照（118GB）"),
    ("asterdex_trades", "event_ts_ms", True, "逐笔成交（571MB）"),
)


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url.replace("/alpha_arena", "/alpha_market")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=14.0)
    ap.add_argument("--days-book", type=float, default=None, help="book_ticker 单独保留天数")
    ap.add_argument("--days-depth", type=float, default=None, help="depth_snapshots 单独保留天数")
    ap.add_argument("--days-trades", type=float, default=None, help="trades 单独保留天数")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch", type=int, default=50000)
    a = ap.parse_args()

    import psycopg2

    conn = psycopg2.connect(dsn())
    conn.autocommit = True
    cur = conn.cursor()
    per = {
        "asterdex_book_ticker": a.days_book if a.days_book is not None else a.days,
        "asterdex_depth_snapshots": a.days_depth if a.days_depth is not None else a.days,
        "asterdex_trades": a.days_trades if a.days_trades is not None else a.days,
    }
    print("H277 保留期清理（分表）" + ("  [DRY RUN]" if a.dry_run else ""))
    for table, col, is_ms, desc in TABLES:
        if not is_ms:
            continue
        days_t = per[table]
        cutoff_ms = int((time.time() - days_t * 86400) * 1000)
        print(f"\n== {table}（{desc}）== 保留 {days_t} 天（< {cutoff_ms}）")
        cur.execute(f"SELECT count(*) FROM {table} WHERE {col} < %s", (cutoff_ms,))
        n_old = cur.fetchone()[0]
        cur.execute(f"SELECT count(*) FROM {table}")
        n_tot = cur.fetchone()[0]
        print(f"    总 {n_tot} 行，待删 {n_old} 行")
        if a.dry_run:
            continue
        # 游标推进式删除：每批从上次进度继续（WHERE col >= 游标），
        # 避免"每批重扫已删前缀"的 O(n²) 退化（2026-09-25 实测 61M 行删 12 分钟仅 1.4M）。
        cur.execute(f"SELECT min({col}) FROM {table} WHERE {col} < %s", (cutoff_ms,))
        row = cur.fetchone()
        if row is None or row[0] is None:
            print("    无待删行")
            continue
        cursor_v = int(row[0])
        deleted = 0
        t0 = time.time()
        while True:
            cur.execute(
                f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table}"
                f" WHERE {col} >= %s AND {col} < %s ORDER BY {col} LIMIT %s)"
                f" RETURNING {col}", (cursor_v, cutoff_ms, a.batch))
            rows = cur.fetchall()
            if not rows:
                break
            n = len(rows)
            deleted += n
            cursor_v = max(int(r[0]) for r in rows)
            if deleted % (a.batch * 10) == 0:
                print(f"   已删 {deleted}/{n_old}  （{time.time()-t0:.0f}s）")
            time.sleep(0.2)
        print(f"  ✓ 删除 {deleted} 行，耗时 {time.time()-t0:.0f}s")
        if deleted:
            t0 = time.time()
            print(f"  VACUUM {table} …")
            cur.execute(f"VACUUM {table}")
            print(f"  ✓ VACUUM 完成 {time.time()-t0:.0f}s")
    print("\nH277 完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
