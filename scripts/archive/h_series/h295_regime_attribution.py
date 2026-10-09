# -*- coding: utf-8 -*-
"""H295 盈亏 regime 归因：赢的小时 vs 亏的小时，差在什么客观量上。

# 问题：策略升级后，早晨时段仍在亏。H281 已证明"信号层面"日盘不弱，
# 那亏损到底是时段本身、波动 regime、还是趋势持续度？用 3 天账本 × tick 统计回答。

# 口径：lane_ledger 逐小时净额（3 天）× 同小时 tick 统计（成交中价实现波动、
# |5 分钟净移动| 中位、成交笔数强度）。输出按三个维度切片的每小时均值净额。

# 用法

    python scripts/h295_regime_attribution.py --days 3
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h295_regime_attribution.json"
LANE = "mm_asterdex"


def market_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    base = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        base = base.replace(j, "")
    return base.rsplit("/", 1)[0] + "/alpha_market"


def arena_dsn() -> str:
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
    return url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=3.0)
    a = ap.parse_args()

    import psycopg

    # 1) 账本逐小时净额
    hourly = {}
    with psycopg.connect(arena_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT date_trunc('hour', ts) AS h, count(*),
                       round((sum(net_bp*notional/1e4))::numeric,2)
                FROM lane_ledger WHERE lane_id=%s
                  AND ts >= now() - (%s || ' days')::interval
                GROUP BY 1 ORDER BY 1
            """, (LANE, a.days))
            for h, n, usd in cur.fetchall():
                hourly[h] = {"n": int(n), "net_usd": float(usd)}

    # 2) tick 统计（合并 4 币成交中价）逐小时：实现波动 / |5min 净移动|中位 / 强度
    stats = defaultdict(list)
    with psycopg.connect(market_dsn()) as c:
        with c.cursor() as cur:
            for sym in ("SOLUSDT", "DOGEUSDT", "ETHUSDT", "BNBUSDT"):
                cur.execute("""
                    SELECT event_ts_ms, price FROM asterdex_trades
                    WHERE event_ts_ms >= (extract(epoch from now())*1000 - %s*24*3600*1000)::bigint
                      AND symbol = %s ORDER BY event_ts_ms
                """, (a.days, sym))
                tr = [(int(r[0]), float(r[1])) for r in cur.fetchall()]
                if not tr:
                    continue
                # 逐笔收益（相邻成交价，过滤跨 60s 的大跳）
                import math
                for i in range(1, len(tr)):
                    t0, p0 = tr[i - 1]
                    t1, p1 = tr[i]
                    if t1 - t0 > 60_000 or p0 <= 0:
                        continue
                    r = abs((p1 - p0) / p0) * 1e4
                    stats[t1 // 3_600_000].append(("abs_r", r))
    # |5min 净移动| 中位：用 5 分钟首尾价差
    with psycopg.connect(market_dsn()) as c:
        with c.cursor() as cur:
            for sym in ("SOLUSDT", "DOGEUSDT", "ETHUSDT", "BNBUSDT"):
                cur.execute("""
                    SELECT event_ts_ms, price FROM asterdex_trades
                    WHERE event_ts_ms >= (extract(epoch from now())*1000 - %s*24*3600*1000)::bigint
                      AND symbol = %s ORDER BY event_ts_ms
                """, (a.days, sym))
                tr = [(int(r[0]), float(r[1])) for r in cur.fetchall()]
                buckets = {}
                for t, p in tr:
                    b = (t // 300_000)
                    if b not in buckets:
                        buckets[b] = [p, p]
                    buckets[b][1] = p
                for b, (p0, p1) in buckets.items():
                    if p0 > 0:
                        stats[b * 300_000 // 3_600_000].append(
                            ("m300", abs((p1 - p0) / p0) * 1e4))

    print("=" * 96)
    print("H295  盈亏 regime 归因（3 天账本 × tick）")
    print("=" * 96)
    rows = []
    for h, d in sorted(hourly.items()):
        ms = h.timestamp() // 3600
        abs_r = [v for k, v in stats.get(int(ms), []) if k == "abs_r"]
        m300 = [v for k, v in stats.get(int(ms), []) if k == "m300"]
        vol = sum(abs_r) if abs_r else None
        m300_med = sorted(m300)[len(m300) // 2] if m300 else None
        rows.append({"hour": h, "net_usd": d["net_usd"], "n": d["n"],
                     "vol_hour_bp": vol, "m300_med_bp": m300_med,
                     "hod": h.hour})
    print(f"\n  {'时段':>16} {'n':>6} {'净额$':>9} {'小时波动bp':>10} {'|5min|中位bp':>12}")
    for r in rows:
        print(f"  {str(r['hour']):>16} {r['n']:>6} {r['net_usd']:>9.2f} "
              f"{str(r['vol_hour_bp'] and round(r['vol_hour_bp'],1)):>10} "
              f"{str(r['m300_med_bp'] and round(r['m300_med_bp'],2)):>12}")

    # 按小时切
    by_hod = defaultdict(lambda: [0.0, 0])
    for r in rows:
        by_hod[r["hod"]][0] += r["net_usd"]
        by_hod[r["hod"]][1] += r["n"]
    print("\n  按日内小时聚合：")
    for hod in sorted(by_hod):
        usd, n = by_hod[hod]
        print(f"    {hod:02d}:00  n={n:>5}  净={usd:+.2f}$  每腿={usd/max(n,1)*100:+.2f}¢")

    # 按波动分位切
    rows_v = [r for r in rows if r["vol_hour_bp"]]
    rows_v.sort(key=lambda r: r["vol_hour_bp"])
    k = max(1, len(rows_v) // 3)
    for name, seg in (("低波动", rows_v[:k]), ("中波动", rows_v[k:2 * k]), ("高波动", rows_v[2 * k:])):
        usd = sum(r["net_usd"] for r in seg)
        n = sum(r["n"] for r in seg)
        print(f"\n  {name}（{len(seg)} 小时）: 净={usd:+.2f}$  腿={n}  每腿={usd/max(n,1)*100:+.2f}¢")

    # 按 |5min| 中位分位切
    rows_m = [r for r in rows if r["m300_med_bp"]]
    rows_m.sort(key=lambda r: r["m300_med_bp"])
    k2 = max(1, len(rows_m) // 3)
    for name, seg in (("小趋势", rows_m[:k2]), ("中趋势", rows_m[k2:2 * k2]), ("大趋势", rows_m[2 * k2:])):
        usd = sum(r["net_usd"] for r in seg)
        n = sum(r["n"] for r in seg)
        print(f"\n  {name}（{len(seg)} 小时）: 净={usd:+.2f}$  腿={n}  每腿={usd/max(n,1)*100:+.2f}¢")

    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n  已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
