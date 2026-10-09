# -*- coding: utf-8 -*-
"""H192 选币评分接口慢在哪 —— 逐条查询计时。

# 现象（用户截图）

HFT 面板「选币评分」与「硬性拒绝」两个卡片都显示
    `刷新失败: signal is aborted without reason`
即前端 90s 超时后 `AbortController.abort()`。
实测 `GET /api/hft/universe/score` 耗时 **119.5s**（HTTP 200）。

# 待查的三条查询（`coin_select_hft._compute_hft_stats_uncached`）

  ① `asterdex_book_ticker`  近 2h 分组 → count + **两个 percentile_disc**
  ② `asterdex_depth_snapshots` 近 1h 分组 → max(event_ts_ms)
  ③ `asterdex_trades`       **全历史** count(*) 分组

`percentile_disc ... WITHIN GROUP (ORDER BY ...)` 是**排序型聚合**：
大表上若没有可用索引，Postgres 只能全表扫 + 排序。

本脚本只做只读测量（EXPLAIN ANALYZE + 计时），不改任何数据。
"""
from __future__ import annotations

import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]


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

    now_ms = int(time.time() * 1000)
    since2h = now_ms - 2 * 3600 * 1000
    since1h = now_ms - 3600 * 1000

    print("=" * 92)
    print("H192  选币评分接口慢在哪（只读测量）")
    print("=" * 92)

    with psycopg.connect(dsn_market(), autocommit=True) as c:
        with c.cursor() as cur:
            print("\n  ① 表规模与索引")
            for t in ("asterdex_book_ticker", "asterdex_depth_snapshots", "asterdex_trades"):
                try:
                    cur.execute(f"SELECT count(*) FROM {t}")
                    n = cur.fetchone()[0]
                except Exception as e:
                    print(f"    {t:<30} count 失败: {str(e)[:60]}")
                    continue
                cur.execute(
                    "SELECT indexdef FROM pg_indexes WHERE tablename = %s", (t,))
                idx = [r[0] for r in cur.fetchall()]
                print(f"    {t:<30} rows={n:>12,}")
                for d in idx:
                    print(f"        {d}")
                if not idx:
                    print("        (无索引)")

            def timed(label: str, sql: str, params: tuple = ()) -> float:
                t0 = time.time()
                try:
                    cur.execute(sql, params)
                    rows = cur.fetchall()
                    dt = time.time() - t0
                    print(f"    {label:<34} {dt:>8.2f}s   rows={len(rows)}")
                    return dt
                except Exception as e:
                    dt = time.time() - t0
                    print(f"    {label:<34} {dt:>8.2f}s   FAILED: {str(e)[:70]}")
                    return dt

            print("\n  ② 逐条计时（原始 SQL，与生产同参）")
            t1 = timed("① 近2h 点差+更新数(两个percentile)",
                       "SELECT symbol, count(*) n, "
                       "  percentile_disc(0.5) WITHIN GROUP (ORDER BY "
                       "    (ask_px - bid_px) / nullif((ask_px + bid_px)/2, 0) * 1e4), "
                       "  percentile_disc(0.25) WITHIN GROUP (ORDER BY "
                       "    (ask_px - bid_px) / nullif((ask_px + bid_px)/2, 0) * 1e4), "
                       "  max(event_ts_ms) "
                       "FROM asterdex_book_ticker WHERE event_ts_ms >= %s "
                       "GROUP BY symbol", (since2h,))

            t2 = timed("② 近1h 深度状态",
                       "SELECT symbol, max(event_ts_ms) FROM asterdex_depth_snapshots "
                       "WHERE event_ts_ms >= %s GROUP BY symbol", (since1h,))

            t3 = timed("③ 全历史成交笔数",
                       "SELECT symbol, count(*) FROM asterdex_trades GROUP BY symbol")

            print(f"\n    合计 {t1 + t2 + t3:.2f}s")

            print("\n  ③ 拆分 ①：去掉 percentile 后还剩多少")
            timed("①a 只 count + max（无 percentile）",
                  "SELECT symbol, count(*) n, max(event_ts_ms) "
                  "FROM asterdex_book_ticker WHERE event_ts_ms >= %s "
                  "GROUP BY symbol", (since2h,))
            timed("①b 只一个 percentile(0.5)",
                  "SELECT symbol, percentile_disc(0.5) WITHIN GROUP (ORDER BY "
                  "  (ask_px - bid_px) / nullif((ask_px + bid_px)/2, 0) * 1e4) "
                  "FROM asterdex_book_ticker WHERE event_ts_ms >= %s "
                  "GROUP BY symbol", (since2h,))

            print("\n  ④ 执行计划（只取 ①，看是否有 Seq Scan + Sort）")
            cur.execute(
                "EXPLAIN (ANALYZE, BUFFERS, TIMING) "
                "SELECT symbol, count(*) n, "
                "  percentile_disc(0.5) WITHIN GROUP (ORDER BY "
                "    (ask_px - bid_px) / nullif((ask_px + bid_px)/2, 0) * 1e4) "
                "FROM asterdex_book_ticker WHERE event_ts_ms >= %s "
                "GROUP BY symbol", (since2h,))
            for (line,) in cur.fetchall():
                print("      " + line)

    print("\n  判读：哪一条占比最大，就是它需要索引/改写。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
