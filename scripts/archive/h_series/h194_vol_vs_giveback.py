# -*- coding: utf-8 -*-
"""H194 波动 → 回吐：把每 15 分钟的盈亏和同期大盘波动对齐。

# 用户观察

"利润稳定不住，大盘波动大一点就回吐了"

# 为什么值得单独量

本会话已确立两件事：
  · 盈利路径存在且为正：maker 自然出库 **+$0.121/周期**（28 周期，中位 +$0.019）
  · 亏损几乎全部来自 flatten：**−$2.594/周期**（45 周期），且 **257/257 笔全部穿价**

若"波动一大就回吐"成立，则 **flatten 率应随波动单调上升** ——
这是可以直接测的相关性，不需要模型。

# 口径

  · 盈亏：`lane_ledger` 按 15 分钟桶，`sum(net_bp × notional / 1e4)`
  · 波动：`market_orderbook_snapshots`（引擎真正用的那张表）同一桶内
    相邻快照中价移动的 **p90**（bp）。用 p90 而非中位数 —— 中位数
    常被同一快照的重复值压到 0（已踩过）。
  · 用同一张表是刻意的：引擎按它决策，所以它量的是**引擎看到的波动**。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
BUCKET_MIN = 15
HOURS = 14


def dsn(which: str = "alpha_arena") -> str:
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
    base, _, _old = url.rpartition("/")
    return f"{base}/{which}"


def main() -> int:
    import psycopg

    print("=" * 100)
    print(f"H194  波动 vs 回吐（{BUCKET_MIN} 分钟桶，近 {HOURS} 小时）")
    print("=" * 100)

    # ── 1. 每个桶的盈亏 + flatten 拆分 ──
    with psycopg.connect(dsn("alpha_arena")) as c:
        with c.cursor() as cur:
            cur.execute(f"""
                SELECT to_timestamp(floor(extract(epoch FROM ts) / {BUCKET_MIN*60})
                                    * {BUCKET_MIN*60}) AS b,
                       count(*) FILTER (WHERE meta_json->>'flatten' NOT IN ('true','True')) AS mk_n,
                       coalesce(sum(net_bp*notional/1e4)
                                FILTER (WHERE meta_json->>'flatten' NOT IN ('true','True')),0) AS mk_usd,
                       count(*) FILTER (WHERE meta_json->>'flatten' IN ('true','True')) AS fl_n,
                       coalesce(sum(net_bp*notional/1e4)
                                FILTER (WHERE meta_json->>'flatten' IN ('true','True')),0) AS fl_usd,
                       coalesce(sum(net_bp*notional/1e4),0) AS tot_usd,
                       coalesce(sum(notional),0) AS notional
                FROM lane_ledger
                WHERE lane_id=%s AND ts > now() - interval '{HOURS} hours'
                GROUP BY 1 ORDER BY 1
            """, (LANE,))
            led = {r[0]: r for r in cur.fetchall()}

    if not led:
        print("  账本无数据")
        return 0

    # ── 2. 每个桶的波动（引擎用的那张表）──
    with psycopg.connect(dsn("alpha_market")) as c:
        with c.cursor() as cur:
            cur.execute(f"""
                WITH s AS (
                  SELECT to_timestamp(
                           floor((timestamp::double precision / 1000.0) / {BUCKET_MIN*60})
                           * {BUCKET_MIN*60}) AS b,
                         symbol, timestamp/1000.0 AS t,
                         (best_bid+best_ask)/2.0 AS mid
                  FROM market_orderbook_snapshots
                  WHERE exchange='asterdex' AND best_bid>0 AND best_ask>best_bid
                    AND timestamp > (extract(epoch FROM now() - interval '{HOURS} hours')*1000)
                ), d AS (
                  SELECT b,
                         abs(mid - lag(mid) OVER (PARTITION BY symbol ORDER BY t))
                           / nullif(lag(mid) OVER (PARTITION BY symbol ORDER BY t),0) * 1e4 AS mv
                  FROM s
                )
                SELECT b, percentile_cont(0.9) WITHIN GROUP (ORDER BY mv) AS p90, count(*) AS n
                FROM d WHERE mv IS NOT NULL GROUP BY b ORDER BY b
            """)
            vol = {r[0]: (float(r[1] or 0.0), int(r[2] or 0)) for r in cur.fetchall()}

    keys = sorted(set(led) & set(vol))
    if not keys:
        print("  账本与波动没有重叠的时间桶（检查两张表的时间范围）")
        return 0

    rows = []
    for k in keys:
        mk_n, mk_usd, fl_n, fl_usd, tot_usd, notional = led[k][1:]
        p90, vn = vol[k]
        fl_rate = (fl_n / (mk_n + fl_n)) if (mk_n + fl_n) else 0.0
        rows.append({"b": k, "mk_n": int(mk_n), "mk_usd": float(mk_usd),
                     "fl_n": int(fl_n), "fl_usd": float(fl_usd),
                     "tot": float(tot_usd), "notional": float(notional),
                     "p90": p90, "fl_rate": fl_rate, "vn": vn})

    print(f"\n  每桶明细（时间 / 波动p90 / maker笔数 / 强平笔数 / 强平率 / 净额$）")
    print(f"  {'time':<7}{'p90bp':>8}{'maker_n':>9}{'flat_n':>8}{'flat率':>8}"
          f"{'maker$':>10}{'flat$':>10}{'净额$':>10}")
    print("  " + "-" * 78)
    for r in rows:
        print(f"  {r['b'].strftime('%H:%M'):<7}{r['p90']:>8.2f}{r['mk_n']:>9}{r['fl_n']:>8}"
              f"{r['fl_rate']*100:>7.1f}%{r['mk_usd']:>10.3f}{r['fl_usd']:>10.3f}"
              f"{r['tot']:>10.3f}")

    # ── 3. 按波动分档聚合 ──
    print(f"\n  ── 按波动分档（这是判据）──")
    print(f"  {'波动档 p90':<16}{'桶数':>6}{'maker笔':>9}{'强平笔':>8}{'强平率':>8}"
          f"{'maker$':>10}{'flat$':>10}{'净额$':>10}{'每笔$':>10}")
    print("  " + "-" * 92)
    bands = [(0, 4, "低 <4bp"), (4, 7, "中 4-7bp"), (7, 10, "高 7-10bp"),
             (10, 999, "极高 >10bp")]
    for lo, hi, lab in bands:
        sel = [r for r in rows if lo <= r["p90"] < hi]
        if not sel:
            print(f"  {lab:<16}{0:>6}")
            continue
        mkn = sum(r["mk_n"] for r in sel)
        fln = sum(r["fl_n"] for r in sel)
        mku = sum(r["mk_usd"] for r in sel)
        flu = sum(r["fl_usd"] for r in sel)
        tot = mku + flu
        n = mkn + fln
        fr = (fln / n) if n else 0.0
        print(f"  {lab:<16}{len(sel):>6}{mkn:>9}{fln:>8}{fr*100:>7.1f}%"
              f"{mku:>10.3f}{flu:>10.3f}{tot:>10.3f}{(tot/n if n else 0):>10.4f}")

    # ── 4. 相关系数 ──
    def pearson(xs, ys):
        n = len(xs)
        if n < 3:
            return None
        mx, my = sum(xs)/n, sum(ys)/n
        num = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
        dx = sum((x-mx)**2 for x in xs) ** 0.5
        dy = sum((y-my)**2 for y in ys) ** 0.5
        return (num/(dx*dy)) if dx > 0 and dy > 0 else None

    xs = [r["p90"] for r in rows]
    print(f"\n  ── 相关系数（{len(rows)} 个桶）──")
    for lab, ys in (("强平率", [r["fl_rate"] for r in rows]),
                    ("每桶净额$", [r["tot"] for r in rows]),
                    ("强平金额$", [r["fl_usd"] for r in rows]),
                    ("maker金额$", [r["mk_usd"] for r in rows])):
        c = pearson(xs, ys)
        if c is None:
            print(f"    {lab:<12} 样本不足")
            continue
        strength = ("强" if abs(c) >= 0.6 else "中" if abs(c) >= 0.3 else "弱")
        print(f"    corr(波动, {lab:<10}) = {c:+.3f}   ({strength})")

    tot_all = sum(r["tot"] for r in rows)
    print(f"\n  合计净额 = {tot_all:+.3f} USD   （{len(rows)} 桶，近 {HOURS}h）")
    print("\n  判读：若 corr(波动, 强平率) 明显为正、且高档净额显著更负 ⇒")
    print("        \"波动一大就回吐\"成立，机制是**波动 → 库存卡住 → taker 强平**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
