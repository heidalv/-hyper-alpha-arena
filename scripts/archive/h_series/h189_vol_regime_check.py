# -*- coding: utf-8 -*-
"""H189 成交频次下降归因：是市场变抖，还是引擎变哑？

# 现象（用户指出，已用量化确认）

按 tick 对齐的成交率 `Δfills/Δtick`：

    12:33–14:54   2.66 ~ 3.11     正常
    15:03–15:57   2.28 -> 1.79    开始掉
    16:03–16:25   1.74            掉一半
    16:33–16:57   0.93            最低
    17:03–17:22   1.38            略回

与 `vol_pause`（引擎自身的波动闸拦截数/ tick）**严格反向**：

    10–14 时   fills/tick 2.59–2.86   vol_pause/tick 0.05–0.34
    16 时      fills/tick 1.15        vol_pause/tick 1.86
    17 时      fills/tick 1.40        vol_pause/tick 1.79

⇒ 高度怀疑是**市场波动抬升**触发波动闸，而不是引擎退化。

# 本脚本要回答的问题

`vol_pause` 抬升有两种可能，**必须区分**，因为对策完全相反：

  A. **真实市场变抖** ⇒ 闸门在做它该做的事，不该调参数；
     频次下降是"用少做换少亏"，属正确行为。
  B. **波动读数被污染**（例如重启后 `mid_hist` 被断点补齐机制插进伪收益，
     或把跨停机的大跳当成一次波动）⇒ 闸门误触发，**必须修**。

判据：直接量**原始盘口数据**的 15 秒桶内极差（与引擎 `SEG_BUCKET_MS=15000` 同口径），
按小时看它有没有真的抬升。若原始数据也抬升 ⇒ 是 A。

# 两个必须避开的测量陷阱（本脚本已踩过并修正）

1. **不许把多个币混在一个均价里** —— ASTER≈0.76、XRP≈1.47、SOL≈200，
   混算出来的"极差"约 29230bp，是纯粹的伪影。
   ⇒ 必须 `GROUP BY symbol`。
2. **桶内 `mid` 常常一个点都没动**（相邻快照重复）⇒ 极差中位数是 0，
   看着像"没有波动"。⇒ 必须看 **p90 分位**，不能用中位数。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SYMS = ["ASTER", "XRP", "SOL"]
VENUE = "asterdex"


def dsn_market() -> str:
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
    import psycopg

    print("=" * 88)
    print("H189  波动是否真的抬升（原始盘口，15s 桶内极差）")
    print("=" * 88)

    with psycopg.connect(dsn_market()) as c:
        with c.cursor() as cur:
            # ① 先看数据密度：采样变稀会直接影响引擎的 mid_hist 质量
            cur.execute(
                "SELECT symbol, count(*),"
                " min(to_timestamp(timestamp/1000.0) AT TIME ZONE 'Asia/Shanghai'),"
                " max(to_timestamp(timestamp/1000.0) AT TIME ZONE 'Asia/Shanghai')"
                " FROM market_orderbook_snapshots"
                " WHERE exchange=%s AND symbol = ANY(%s)"
                "   AND timestamp > (extract(epoch FROM now()-interval '14 hours')*1000)"
                " GROUP BY symbol ORDER BY symbol", (VENUE, SYMS))
            print("\n  ① 采样密度（近 14h）")
            print(f"    {'symbol':<8}{'rows':>8}{'first':>21}{'last':>21}")
            for sym, n, t0, t1 in cur.fetchall():
                print(f"    {sym:<8}{n:>8}{str(t0)[11:]:>21}{str(t1)[11:]:>21}")

            # ② 每小时 × 每币：15s 桶内极差（bp），取 p90
            cur.execute(
                """
                WITH s AS (
                  SELECT symbol, timestamp, (best_bid+best_ask)/2.0 AS mid
                  FROM market_orderbook_snapshots
                  WHERE exchange=%s AND symbol = ANY(%s)
                    AND best_bid > 0 AND best_ask > best_bid
                    AND timestamp > (extract(epoch FROM now()-interval '14 hours')*1000)
                ), b AS (
                  SELECT symbol,
                         to_char(to_timestamp(timestamp/1000.0) AT TIME ZONE 'Asia/Shanghai','HH24') AS h,
                         (timestamp/15000)::bigint AS bucket,
                         avg(mid) AS m, max(mid) AS hi, min(mid) AS lo
                  FROM s GROUP BY symbol, 2, 3
                ), r AS (
                  SELECT h, symbol,
                         COALESCE((hi - lo) / NULLIF(m, 0) * 10000, 0) AS range_bp
                  FROM b
                )
                SELECT h, symbol,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY range_bp) AS p50,
                       percentile_cont(0.9) WITHIN GROUP (ORDER BY range_bp) AS p90
                FROM r GROUP BY h, symbol ORDER BY h, symbol
                """, (VENUE, SYMS))
            rows = cur.fetchall()

    byh: dict = {}
    for h, sym, p50, p90 in rows:
        byh.setdefault(h, {})[sym] = (float(p50 or 0), float(p90 or 0))

    print("\n  ② 各币 15s 桶内极差 p90（bp）—— 波动是否抬升看这一行")
    print(f"    {'hour':<7}{'ASTER':>10}{'SOL':>10}{'XRP':>10}{'mean':>9}   {'':<2}")
    print("    " + "-" * 62)
    for h in sorted(byh):
        d = byh[h]
        vals = [d.get(s, (0, 0))[1] for s in SYMS]
        mean = sum(vals) / len(vals)
        if mean <= 0:
            continue
        print(f"    {h}:00  {d.get('ASTER',(0,0))[1]:>9.2f}{d.get('SOL',(0,0))[1]:>10.2f}"
              f"{d.get('XRP',(0,0))[1]:>10.2f}{mean:>9.2f}   {'#' * int(mean / 1.5)}")

    print("\n  ③ 判读")
    print("    · 若 15–17 时的 p90 明显高于 10–14 时 ⇒ 是**真实波动抬升**（情形 A）")
    print("      ⇒ 波动闸在做它该做的事，频次下降属正确行为，**不该调参数**")
    print("    · 若原始数据没抬升而 vol_pause 抬升 ⇒ 是**读数被污染**（情形 B）")
    print("      ⇒ 查 mid_hist 断点补齐（F279）是否插入了伪收益，**必须修**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
