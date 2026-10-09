# -*- coding: utf-8 -*-
"""H192b 选币评分接口慢在哪 —— 轻量诊断（不跑 ANALYZE，避免再等 10 分钟）。

# 现象（用户截图）

HFT 面板「选币评分」与「硬性拒绝」两张卡片都显示
    `刷新失败: signal is aborted without reason`
实测 `GET /api/hft/universe/score` = **119.5s**（HTTP 200），
而前端 `HFT_TIMEOUT_MS = 90_000` ⇒ 必然 abort。

# 三条候选查询（`coin_select_hft._compute_hft_stats_uncached`）

  ① `asterdex_book_ticker`   近 2h → count + **两个 percentile_disc**
  ② `asterdex_depth_snapshots` 近 1h → max(event_ts_ms)
  ③ `asterdex_trades`        **全历史** count(*) 分组   ← 没有任何时间过滤

# 为什么上一版脚本会卡 10 分钟

它跑了 `EXPLAIN (ANALYZE, BUFFERS, TIMING)` —— 那是**真执行一遍**，
而 ③ 是 1 亿行表的全表聚合，比接口本身还慢。
⇒ 本版只用 `EXPLAIN`（不带 ANALYZE，不执行）+ 分开计时。
"""
from __future__ import annotations

import pathlib
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


TABLES = ("asterdex_book_ticker", "asterdex_depth_snapshots", "asterdex_trades")


def main() -> int:
    import psycopg

    now_ms = int(time.time() * 1000)
    print("=" * 92)
    print("H192b  选币评分接口慢在哪（轻量诊断）")
    print("=" * 92)

    with psycopg.connect(dsn_market(), autocommit=True) as c:
        with c.cursor() as cur:
            # ── 1. 索引：瞬时，先看有没有可用的 ──
            print("\n  ① 索引（决定 percentile / count 能不能走索引）")
            for t in TABLES:
                cur.execute("SELECT indexdef FROM pg_indexes WHERE tablename=%s", (t,))
                idx = [r[0] for r in cur.fetchall()]
                cur.execute(
                    "SELECT reltuples::bigint FROM pg_class WHERE relname=%s", (t,))
                est = cur.fetchone()
                print(f"\n    {t}   (≈{(est[0] if est else 0):,} 行，pg_class 估算)")
                if not idx:
                    print("        **无索引**")
                for d in idx:
                    print(f"        {d}")

            # ── 2. 执行计划（不执行，瞬时）──
            print("\n  ② 执行计划要点（EXPLAIN，不 ANALYZE ⇒ 不执行）")
            plans = {
                "① 近2h 两个 percentile_disc": (
                    "EXPLAIN SELECT symbol, count(*) n, "
                    " percentile_disc(0.5) WITHIN GROUP (ORDER BY "
                    "   (ask_px-bid_px)/nullif((ask_px+bid_px)/2,0)*1e4), "
                    " percentile_disc(0.25) WITHIN GROUP (ORDER BY "
                    "   (ask_px-bid_px)/nullif((ask_px+bid_px)/2,0)*1e4), "
                    " max(event_ts_ms) "
                    "FROM asterdex_book_ticker WHERE event_ts_ms >= %s GROUP BY symbol",
                    (now_ms - 2 * 3600 * 1000,)),
                "③ 全历史 count(*)": (
                    "EXPLAIN SELECT symbol, count(*) FROM asterdex_trades GROUP BY symbol",
                    ()),
            }
            for label, (sql, params) in plans.items():
                print(f"\n    ── {label} ──")
                cur.execute(sql, params)
                for (line,) in cur.fetchall():
                    print("      " + line.strip())

            # ── 3. 分开计时：先做最便宜的，最贵的放最后 ──
            def timed(label, sql, params=(), cap_s=120.0):
                c.cursor().execute("SET statement_timeout = %d" % int(cap_s * 1000))
                t0 = time.time()
                try:
                    cur.execute(sql, params)
                    n = len(cur.fetchall())
                    dt = time.time() - t0
                    print(f"    {label:<40}{dt:>8.2f}s  rows={n}")
                    return dt
                except Exception as e:
                    dt = time.time() - t0
                    name = type(e).__name__
                    print(f"    {label:<40}{dt:>8.2f}s  {name}: {str(e)[:60]}")
                    return dt

            print("\n  ③ 逐条计时（statement_timeout=120s，防止再卡死）")
            timed("② 近1h 深度 max(ts)",
                  "SELECT symbol, max(event_ts_ms) FROM asterdex_depth_snapshots "
                  "WHERE event_ts_ms >= %s GROUP BY symbol",
                  (now_ms - 3600 * 1000,))
            timed("①a 近2h 只 count+max",
                  "SELECT symbol, count(*), max(event_ts_ms) FROM asterdex_book_ticker "
                  "WHERE event_ts_ms >= %s GROUP BY symbol",
                  (now_ms - 2 * 3600 * 1000,))
            timed("①b 近2h 一个 percentile_disc(0.5)",
                  "SELECT symbol, percentile_disc(0.5) WITHIN GROUP (ORDER BY "
                  "  (ask_px-bid_px)/nullif((ask_px+bid_px)/2,0)*1e4) "
                  "FROM asterdex_book_ticker WHERE event_ts_ms >= %s GROUP BY symbol",
                  (now_ms - 2 * 3600 * 1000,))
            timed("③ 全历史 count(*) 分组",
                  "SELECT symbol, count(*) FROM asterdex_trades GROUP BY symbol")

    print("\n  判读：哪一条最贵，就是它需要索引 / 缩小窗口 / 预聚合。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
