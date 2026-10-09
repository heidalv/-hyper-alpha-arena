# -*- coding: utf-8 -*-
"""H315 成交概率 vs 报价距离：maker 单挂在离中价多远最划算？

# 用户指令：继续深挖。文献框架（Albers）：KPI = fill概率 × post-fill 漂移。
# 问题：对每个报价距离 d（bp），未来 60s 内被对手方打到的概率 × 打到后的 60s
# markout（成交价 → 60s 后中价）——找到期望最优的挂单位置。
# 现行实盘：w_base=5bp 但"不穿越钳制"把宽度压到市场价差内（≈0.2~0.6bp）。
# 本脚本给出"如果没有钳制，最优距离是多少"的证据。

# 方法：1s mid 序列（book_ticker），60s 去重叠事件；每个事件两侧各挂
# d ∈ {0.5,1,2,3,4,5,6,8,10} bp 的假设单，看 60s 内价格是否打穿该价位（成交上界），
# 记 markout。输出：每距离 成交率 / 平均 markout / 期望（成交率×markout）。
# 口径：7 天、车道 3 币（DOGE/ETH/BNB）。

# 用法

    python scripts/h315_fill_distance.py --days 7
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h315_fill_distance.json"
CUR = ["DOGE", "ETH", "BNB"]
DS = [0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0]
HOLD = 60


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--directional", action="store_true",
                    help="只测逆势侧（与实盘 counter_trend 同构）")
    ap.add_argument("--thr", type=float, default=2.0, help="逆势信号门槛 bp")
    a = ap.parse_args()

    import psycopg

    # 每币 1s mid 序列
    agg = {d: {"fills": 0, "markout_sum": 0.0, "edge_sum": 0.0} for d in DS}
    for sym in CUR:
        with psycopg.connect(market_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (bucket) bucket, bid_px, ask_px
                    FROM (SELECT (event_ts_ms/1000) AS bucket, bid_px, ask_px
                          FROM asterdex_book_ticker
                          WHERE event_ts_ms >= (extract(epoch from now())*1000 - %s*86400*1000)::bigint
                            AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                    ORDER BY bucket, bid_px
                """, (a.days, sym + "USDT"))
                recs = cur.fetchall()
        d_map = {int(b): ((float(x) + float(y)) / 2.0) for b, x, y in recs}
        ks = sorted(d_map)
        mids = [d_map[k] for k in ks]
        n = len(ks)
        last = -1e18
        for i in range(n - HOLD):
            if ks[i] - last < 60.0:
                continue
            last = ks[i]
            mid0 = mids[i]
            if mid0 <= 0:
                continue
            seg = mids[i + 1:i + 1 + HOLD]
            if not seg:
                continue
            lo = min(seg)
            hi = max(seg)
            end_mid = mids[i + HOLD] if i + HOLD < n else seg[-1]
            # [h315b] --directional：只测逆势侧（r60≥thr → 只挂卖；r60≤−thr → 只挂买），
            # 与实盘 counter_trend 同构；否则双侧都测（无条件做市口径）
            r60 = None
            if a.directional:
                j = i
                while j >= 0 and ks[i] - ks[j] < 60.0:
                    j -= 1
                if j < 0 or mids[j] <= 0 or ks[i] - ks[j] < 54:
                    continue
                r60 = (mid0 - mids[j]) / mids[j] * 1e4
            for d in DS:
                bid_lvl = mid0 * (1 - d / 1e4)
                ask_lvl = mid0 * (1 + d / 1e4)
                if a.directional:
                    if r60 <= -a.thr and lo <= bid_lvl:   # 跌 → 只挂买
                        mo = (end_mid - bid_lvl) / bid_lvl * 1e4
                        agg[d]["fills"] += 1
                        agg[d]["markout_sum"] += mo
                    if r60 >= a.thr and hi >= ask_lvl:    # 涨 → 只挂卖
                        mo = (ask_lvl - end_mid) / ask_lvl * 1e4
                        agg[d]["fills"] += 1
                        agg[d]["markout_sum"] += mo
                    continue
                # 买价被打到（价格下穿我们的买价）
                if lo <= bid_lvl:
                    mo = (end_mid - bid_lvl) / bid_lvl * 1e4
                    agg[d]["fills"] += 1
                    agg[d]["markout_sum"] += mo
                # 卖价被打到
                if hi >= ask_lvl:
                    mo = (ask_lvl - end_mid) / ask_lvl * 1e4
                    agg[d]["fills"] += 1
                    agg[d]["markout_sum"] += mo
        print(f"  {sym:6} {len(ks)} 点", flush=True)

    print(f"\n  {'距离bp':>7} {'成交率':>8} {'平均markout':>11} {'期望bp/挂单':>11}")
    out = {}
    for d in DS:
        g = agg[d]
        rate = g["fills"] / 2.0 / (len(agg[d]) and 1)  # placeholder
        # 事件数统一：用第一个 d 的填充数反推不了事件数；重算事件数
    # 重新算事件数（每币 60s 去重叠）
    total_events = 0
    for sym in CUR:
        with psycopg.connect(market_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT count(*) FROM (SELECT DISTINCT ON (bucket) bucket
                      FROM (SELECT (event_ts_ms/1000) AS bucket FROM asterdex_book_ticker
                            WHERE event_ts_ms >= (extract(epoch from now())*1000 - %s*86400*1000)::bigint
                              AND symbol = %s) t) u
                """, (a.days, sym + "USDT"))
                total_events += int(cur.fetchone()[0])
    total_events = total_events // 60  # 60s 去重叠近似
    for d in DS:
        g = agg[d]
        n_quotes = total_events * 2  # 每事件两侧
        rate = g["fills"] / n_quotes if n_quotes else 0.0
        mo = g["markout_sum"] / g["fills"] if g["fills"] else 0.0
        edge = rate * mo
        out[d] = {"fill_rate": round(rate, 4), "markout_bp": round(mo, 3),
                  "edge_bp": round(edge, 4)}
        print(f"  {d:>7} {rate:>8.2%} {mo:>+11.3f} {edge:>+11.4f}")

    OUT.write_text(json.dumps({"n_events": total_events, "distances": out},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
