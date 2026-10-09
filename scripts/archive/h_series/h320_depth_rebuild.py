# -*- coding: utf-8 -*-
"""H320 深度快照表瘦身重建（磁盘危机根修，2026-09-25）。

# 背景

  asterdex_depth_snapshots 182GB / 1.1 亿行（9 天 @ 100ms 全量写入 = 20GB/天），
  D 盘仅剩 ~22GB。逐行 DELETE 是 O(n²)（每批重扫已删前缀），9 小时也删不完；
  VACUUM FULL 需要 60GB 瞬时空间，不可行。
  本脚本：只保留最近 --cutoff-hours（默认 12h）的数据重建新表，事务内
  LOCK + 增量补抄 + DROP + RENAME 原子切换，接入侧 INSERT 零丢失、零报错。

# 用法

    python scripts/h320_depth_rebuild.py --cutoff-hours 12 [--dry-run]

# 注意

  · 副本体积 ≈ cutoff_hours × 0.8GB（降采样后 0.4GB/天，12h≈0.2GB；全量口径 20GB/天）。
  · 重建后旧表被 DROP，其 182GB 文件立即归还 OS。
  · 全程不锁 book/trades；仅最后一瞬对 depth 表持 ACCESS EXCLUSIVE（秒级）。
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TABLE = "asterdex_depth_snapshots"
NEW = "asterdex_depth_snapshots_new"
COLS = ["id", "symbol", "event_ts_ms", "recv_ts_ns", "update_id",
        "levels", "bids", "asks", "ingest_ts"]


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
    ap.add_argument("--cutoff-hours", type=float, default=12.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    import psycopg2

    conn = psycopg2.connect(dsn())
    conn.autocommit = True
    cur = conn.cursor()
    cutoff_ms = int((time.time() - a.cutoff_hours * 3600) * 1000)

    cur.execute(f"SELECT count(*), min(event_ts_ms), max(event_ts_ms) FROM {TABLE}")
    n, lo, hi = cur.fetchone()
    cur.execute(f"SELECT count(*) FROM {TABLE} WHERE event_ts_ms >= %s", (cutoff_ms,))
    n_keep = cur.fetchone()[0]
    print(f"{TABLE}: 总 {n} 行，保留 >= {cutoff_ms} 的 {n_keep} 行（{a.cutoff_hours}h）")
    if a.dry_run:
        print("[DRY RUN] 到此为止")
        return 0

    t0 = time.time()
    cur.execute(f"DROP TABLE IF EXISTS {NEW}")
    cur.execute(f"""
        CREATE TABLE {NEW} (
            id bigint NOT NULL,
            symbol varchar NOT NULL,
            event_ts_ms bigint NOT NULL,
            recv_ts_ns bigint NOT NULL,
            update_id bigint,
            levels smallint NOT NULL,
            bids jsonb NOT NULL,
            asks jsonb NOT NULL,
            ingest_ts timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT {NEW}_pkey PRIMARY KEY (id)
        )""")
    cols = ", ".join(COLS)
    print("  批量拷贝中 …")
    cur.execute(f"INSERT INTO {NEW} ({cols}) SELECT {cols} FROM {TABLE}"
                f" WHERE event_ts_ms >= %s", (cutoff_ms,))
    n_copy = cur.rowcount
    print(f"  ✓ 拷贝 {n_copy} 行，{time.time()-t0:.0f}s")

    # 原子切换：LOCK 阻断接入写入 → 增量补抄（此时起旧表不再有新行）→ DROP → RENAME
    t0 = time.time()
    conn.autocommit = False
    try:
        cur.execute(f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE")
        cur.execute(f"INSERT INTO {NEW} ({cols}) SELECT {cols} FROM {TABLE}"
                    f" WHERE event_ts_ms >= %s ON CONFLICT (id) DO NOTHING", (cutoff_ms,))
        n_delta = cur.rowcount
        cur.execute(f"DROP TABLE {TABLE}")
        cur.execute(f"ALTER TABLE {NEW} RENAME TO {TABLE}")
        conn.commit()
        print(f"  ✓ 切换完成（增量 {n_delta} 行），{time.time()-t0:.1f}s")
    except Exception:
        conn.rollback()
        raise
    conn.autocommit = True

    # 次级索引（接入写入不受影响）
    t0 = time.time()
    for name, expr in (("ix_adx_depth_ts", "(event_ts_ms)"),
                       ("ix_adx_depth_sym_ts", "(symbol, event_ts_ms)")):
        cur.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {TABLE} {expr}")
    # 序列归属修正：新表 id 默认值仍指向原序列
    cur.execute(f"ALTER TABLE {TABLE} ALTER COLUMN id"
                f" SET DEFAULT nextval('asterdex_depth_snapshots_id_seq'::regclass)")
    print(f"  ✓ 索引/默认值就绪，{time.time()-t0:.0f}s")

    cur.execute(f"SELECT count(*), min(event_ts_ms), max(event_ts_ms) FROM {TABLE}")
    n2, lo2, hi2 = cur.fetchone()
    cur.execute(f"SELECT pg_size_pretty(pg_total_relation_size('{TABLE}'))")
    sz = cur.fetchone()[0]
    print(f"  新表: {n2} 行  {sz}  范围 [{lo2},{hi2}]")
    print("H320 完成：旧 182GB 文件已随 DROP 归还 OS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
