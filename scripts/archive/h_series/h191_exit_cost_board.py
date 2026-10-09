# -*- coding: utf-8 -*-
"""H191 出库成本看板 —— 每 N 分钟一行，盯住"要不要穿价强平"。

# 为什么需要它

2026-09-21 实测（14 小时账本，257 笔 taker 强平）：

  · **257/257 笔全部穿价付费，0 笔赚价差**
  · 出场穿价中位 **+1.14bp**，p95 +6.15bp
  · 加 taker 费 4bp ⇒ **每次强制出库成本 ≈ 5.1bp**
  · 合计 **−$93.53**，占总亏损 83%

而 maker 腿 7,381 笔 taker 费恒为 0、`price_bp` 为正 ⇒ **被动出库这条路是赚钱的**。
问题只是"有多少仓位最终没能走被动出库"。

⇒ 唯一值得盯的指标：**穿价强平在所有出库里的占比**。
它下降 = 改善；它不动 = 改动无效。

# 用法

    # 打印最近 2 小时，按 10 分钟分桶
    python scripts/h191_exit_cost_board.py

    # 持续盯（每 5 分钟刷新一行，共 12 行 = 1 小时）
    python scripts/h191_exit_cost_board.py --watch --minutes 5 --rounds 12

    # 只看某个改动的对比（自动找改动时刻附近的分界）
    python scripts/h191_exit_cost_board.py --since "2026-09-21 17:35"
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


SQL = """
WITH leg AS (
  SELECT ts, notional, net_bp, price_bp, fee_bp, spread_bp,
         net_bp * notional / 10000.0 AS usd,
         (meta_json->>'flatten' IN ('true','True')) AS is_flat,
         (meta_json->>'fill_px')::float AS fpx,
         (meta_json->>'mid_px')::float  AS mpx,
         lower(meta_json->>'side')      AS side
  FROM lane_ledger
  WHERE lane_id = %(lane)s AND ts >= %(a)s AND ts < %(b)s
)
SELECT
  count(*) FILTER (WHERE NOT is_flat)                          AS maker_n,
  coalesce(sum(usd) FILTER (WHERE NOT is_flat), 0)             AS maker_usd,
  count(*) FILTER (WHERE is_flat)                              AS flat_n,
  coalesce(sum(usd) FILTER (WHERE is_flat), 0)                 AS flat_usd,
  coalesce(sum(usd), 0)                                        AS total_usd,
  count(*) FILTER (WHERE is_flat AND fpx IS NOT NULL AND mpx IS NOT NULL
                     AND (CASE WHEN side='buy' THEN 1 ELSE -1 END)
                         * (fpx - mpx) / mpx * 1e4 > 0)        AS paid_cross_n,
  coalesce(percentile_cont(0.5) WITHIN GROUP (
      ORDER BY (CASE WHEN side='buy' THEN 1 ELSE -1 END)
               * (fpx - mpx) / mpx * 1e4)
    FILTER (WHERE is_flat AND fpx IS NOT NULL AND mpx IS NOT NULL), 0) AS med_cross_bp,
  coalesce(sum(fee_bp * notional) FILTER (WHERE is_flat), 0) / 10000.0 AS flat_fee_usd
FROM leg
"""


def fetch_range(a: datetime, b: datetime):
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute(SQL_RANGE, {"lane": LANE, "a": a, "b": b})
            return cur.fetchone()


def row_line(label: str, r) -> str:
    maker_n, maker_usd, flat_n, flat_usd, total_usd, paid_n, med_cross, flat_fee = r
    maker_n = int(maker_n or 0)
    flat_n = int(flat_n or 0)
    total_n = maker_n + flat_n
    share = (100.0 * flat_n / total_n) if total_n else 0.0
    paid_share = (100.0 * int(paid_n or 0) / flat_n) if flat_n else 0.0
    verdict = ""
    if flat_n and paid_share >= 99.0:
        verdict = "  <-- 全部穿价"
    return (f"  {label:<14}{maker_n:>6}{float(maker_usd or 0):>9.3f}"
            f"{flat_n:>7}{float(flat_usd or 0):>10.3f}{share:>7.1f}%"
            f"{paid_share:>7.1f}%{float(med_cross or 0):>8.2f}"
            f"{float(flat_fee or 0):>10.3f}{float(total_usd or 0):>10.3f}{verdict}")


HEADER = (f"  {'window':<14}{'maker_n':>6}{'maker$':>9}{'flat_n':>7}"
          f"{'flat$':>10}{'flat占比':>8}{'穿价%':>8}{'中位bp':>8}"
          f"{'taker费$':>10}{'合计$':>10}")
RULE = "  " + "-" * 100


def report(t0: datetime, t1: datetime, *,
           buckets: int = 6, since: datetime | None = None) -> None:
    print(HEADER)
    print(RULE)
    span = (t1 - t0) / buckets
    for i in range(buckets):
        a = t0 + span * i
        b = t0 + span * (i + 1)
        # 每个桶单独查：SQL 里按 ts > t0 取，再用上个桶的值做差不准
        # （腿的归属会跨桶），所以直接对桶区间查一次。
        r = fetch_range(a, b)
        print(row_line(a.strftime("%H:%M"), r))
    print(RULE)
    print(row_line("TOTAL", fetch_range(t0, t1)))
    if since:
        print()
        # ⚠️ 必须用**等长**窗口比较：`改动后` 若只过了 2 分钟，
        # 它的绝对金额天然远小于"改动前 60m"，看起来像"改善"其实是没数据。
        # 所以后窗取 `min(已过时长, 60m)`，并把实际时长印出来。
        elapsed = (t1 - since).total_seconds() / 60.0
        span_min = max(1.0, min(elapsed, 60.0))
        print(f"  ── 改动时刻 {since:%H:%M} 前后对比（各 {span_min:.0f} 分钟等长窗口）──")
        print(HEADER)
        print(RULE)
        print(row_line(f"前{span_min:.0f}m", fetch_range(
            since - timedelta(minutes=span_min), since)))
        print(row_line(f"后{span_min:.0f}m", fetch_range(
            since, since + timedelta(minutes=span_min))))
        if span_min < 60.0:
            print(f"  ⚠️ 改动后只过了 {elapsed:.0f} 分钟 ⇒ **样本不足，不可下结论**")
            print(f"     （7 笔/小时的强平率下，至少要 1-2 小时才能看出差异）")


SQL_RANGE = """
WITH leg AS (
  SELECT ts, notional, net_bp, fee_bp,
         net_bp * notional / 10000.0 AS usd,
         (meta_json->>'flatten' IN ('true','True')) AS is_flat,
         (meta_json->>'fill_px')::float AS fpx,
         (meta_json->>'mid_px')::float  AS mpx,
         lower(meta_json->>'side')      AS side
  FROM lane_ledger
  WHERE lane_id = %(lane)s AND ts >= %(a)s AND ts < %(b)s
)
SELECT
  count(*) FILTER (WHERE NOT is_flat)                      AS maker_n,
  coalesce(sum(usd) FILTER (WHERE NOT is_flat), 0)         AS maker_usd,
  count(*) FILTER (WHERE is_flat)                          AS flat_n,
  coalesce(sum(usd) FILTER (WHERE is_flat), 0)             AS flat_usd,
  coalesce(sum(usd), 0)                                    AS total_usd,
  count(*) FILTER (WHERE is_flat AND fpx IS NOT NULL AND mpx IS NOT NULL
                     AND (CASE WHEN side='buy' THEN 1 ELSE -1 END)
                         * (fpx - mpx) / mpx * 1e4 > 0)    AS paid_cross_n,
  coalesce(percentile_cont(0.5) WITHIN GROUP (
      ORDER BY (CASE WHEN side='buy' THEN 1 ELSE -1 END)
               * (fpx - mpx) / mpx * 1e4)
    FILTER (WHERE is_flat AND fpx IS NOT NULL AND mpx IS NOT NULL), 0) AS med_cross_bp,
  coalesce(sum(fee_bp * notional) FILTER (WHERE is_flat), 0) / 10000.0 AS flat_fee_usd
FROM leg
"""


def fetch_range(a: datetime, b: datetime):
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute(SQL_RANGE, {"lane": LANE, "a": a, "b": b})
            return cur.fetchone()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=2.0, help="回看多少小时")
    ap.add_argument("--buckets", type=int, default=6)
    ap.add_argument("--since", default="", help="改动时刻 HH:MM，做前后对比")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--minutes", type=float, default=5.0)
    ap.add_argument("--rounds", type=int, default=12)
    a = ap.parse_args()

    print("=" * 104)
    print("H191  出库成本看板   （唯一要盯的指标：穿价强平占比）")
    print("=" * 104)
    print("  基线（2026-09-21 14h 实测）：257 笔强平 **257/257 全部穿价**，"
          "中位 +1.14bp，taker 费 −$50.9，合计 **−$93.5**")

    since = None
    if a.since:
        hh, mm = a.since.split(":")
        today = datetime.now().replace(hour=int(hh), minute=int(mm),
                                       second=0, microsecond=0)
        since = today

    if not a.watch:
        t1 = datetime.now()
        t0 = t1 - timedelta(hours=a.hours)
        print()
        report(t0, t1, buckets=a.buckets, since=since)
        return 0

    for i in range(a.rounds):
        t1 = datetime.now()
        t0 = t1 - timedelta(minutes=a.minutes)
        print()
        print(f"  ── 第 {i+1}/{a.rounds} 轮  {t1:%H:%M:%S}（窗口 {a.minutes:.0f} 分钟）──")
        print(HEADER)
        print(RULE)
        print(row_line(t0.strftime("%H:%M"), fetch_range(t0, t1)))
        if i < a.rounds - 1:
            import time
            time.sleep(a.minutes * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
