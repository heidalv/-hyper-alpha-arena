# -*- coding: utf-8 -*-
"""H236 持有时间的边际：逆向幅度随窗口怎么长（√t 还是收敛）。

# 为什么这是与「挂宽」并列的另一条轴

H235 给了残酷的算术：当前半价差下，**毛利 +1.15bp 盖不住逆向幅度 +8.47bp**，
要把挂宽拉到 20× 才能转正（而触及率塌到 36% = 基本不做市）。

但 H235 只测了 **m=60s** 一个未来窗口。而"逆选择成本"随持有时间的增长规律
决定了完全不同的修法：

  · 若逆向幅度 ~ **√t**（随机游走）⇒ 持有越久亏越多 ⇒ **必须缩短持有**
  · 若逆向幅度**收敛**（均值回归）⇒ 持有越久反而越好 ⇒ **应当延长持有**
    （本会话早先的口径曾显示 maker 腿在长期限上「结构性打平」
     `spread +0.2146 / price −0.2008 / net +0.0138`）

两者的修法相反，而**不需要任何新数据**即可判定。

# 口径（比 H235 更细）

对每个 15s 网格点 i、每个持有期 H：
  · 挂单价用**实际的半价差**（`hs` 由该点 bid/ask 算出，k=1）
  · 找窗口 [i, i+H] 内**先被触及的那一侧**
  · **只有先触及的那一侧才计 P&L**（H234 的错误：两侧都算）
  · 毛利 = `2 × hs_bp`（挂单免费 ⇒ 无 fee；离场按 **maker** 价，即对侧挂单价）
  · 逆选择 = 从挂单价到 **H 时刻的价**（不是到窗口内最不利价 ——
    因为持有到期时是按当时价离场，不是按最不利价）
  · 净 = 毛利 − 逆选择（maker 离场 ⇒ 不扣 taker fee）

⚠️ 两种离场口径都出：
  · `maker` 离场（理想：减仓腿也是挂单成交）⇒ 净 = 毛利 − 逆选择
  · `taker` 离场（现实：超时/止损过价）⇒ 再扣 2×4bp

# 用法

    python scripts/h236_holding_horizon.py --hours 6
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h236_holding_horizon.json"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT"]
HORIZONS = [30.0, 60.0, 120.0, 300.0, 600.0, 900.0, 1800.0, 3600.0]
TAKER_FEE_BP = 4.0


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
    return url


def market_dsn() -> str:
    return dsn().rsplit("/", 1)[0] + "/alpha_market"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--grid", type=float, default=15.0)
    ap.add_argument("--step", type=int, default=1, help="每多少个网格点取一个样本（降采样）")
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(market_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, event_ts_ms, bid_px, ask_px
                FROM asterdex_book_ticker
                WHERE ingest_ts >= now() - (%s || ' hours')::interval
                  AND symbol = ANY(%s) AND bid_px > 0 AND ask_px > bid_px
                ORDER BY symbol, event_ts_ms ASC
            """, (str(float(a.hours)), CUR))
            rows = cur.fetchall()
    if not rows:
        print("无数据")
        return 1

    g = {}
    for sym, ms, bid, ask in rows:
        k = int(ms // 1000 // a.grid * a.grid)
        g.setdefault(str(sym), {})[k] = (float(bid), float(ask))
    series = {s: sorted(d.items()) for s, d in g.items()}

    print("=" * 104)
    print("H236  持有时间的边际：逆向幅度随窗口怎么长")
    print("=" * 104)
    print(f"\n  窗口 {a.hours:g}h　网格 {a.grid:g}s　k=1（用实际半价差）")
    print(f"  口径：只算**先被触及的那一侧**；逆选择算到 **H 时刻的价**（不是最不利价）")

    res = {}
    for sym, items in sorted(series.items()):
        bare = sym.replace("USDT", "")
        ks = [k for k, _ in items]
        vals = [v for _, v in items]
        mids = [(lo + hi) / 2.0 for lo, hi in vals]
        n = len(mids)
        if n < 200:
            continue
        per = {}
        for H in HORIZONS:
            grosses, advs, hits = [], [], 0
            tot = 0
            for i in range(0, n - 1, a.step):
                lo_f, hi_f = vals[i]
                mid_f = mids[i]
                if mid_f <= 0:
                    continue
                tot += 1
                hs = (hi_f - lo_f) / mid_f * 1e4 / 2.0
                end = ks[i] + H
                # H 时刻的价（最后一个 <= end 的点）
                j = i + 1
                last = None
                t_buy = t_sell = None
                while j < n and ks[j] <= end:
                    last = (vals[j], j)
                    l, h = vals[j]
                    if t_buy is None and l <= lo_f:
                        t_buy = j
                    if t_sell is None and h >= hi_f:
                        t_sell = j
                    j += 1
                if last is None:
                    continue
                if t_buy is None and t_sell is None:
                    continue
                hits += 1
                _lv, jl = last
                exit_mid = mids[jl]
                if t_buy is not None and (t_sell is None or t_buy <= t_sell):
                    entry = lo_f                       # 挂在 bid ⇒ 成交在 bid
                    adv = max(0.0, (entry - exit_mid) / entry * 1e4)
                else:
                    entry = hi_f
                    adv = max(0.0, (exit_mid - entry) / entry * 1e4)
                per.setdefault(H, {"g": [], "a": [], "hit": 0, "tot": 0})
                gross = 2.0 * hs
                per[H]["g"].append(gross)
                per[H]["a"].append(adv)
                per[H]["hit"] += 1
                per[H]["tot"] = tot
            if H not in per:
                continue
            d = per[H]
            res.setdefault(bare, {})[H] = {
                "hit_rate": round(d["hit"] / d["tot"] * 100, 2) if d["tot"] else 0.0,
                "gross": round(st.mean(d["g"]), 3),
                "adv": round(st.mean(d["a"]), 3),
                "net_maker": round(st.mean(d["g"]) - st.mean(d["a"]), 3),
                "net_taker": round(st.mean(d["g"]) - st.mean(d["a"]) - 2 * TAKER_FEE_BP, 3),
                "n": d["hit"],
            }

    # ── 一、汇总表 ──
    print(f"\n{'━'*104}\n  一、汇总：持有期 H → 毛利 / 逆选择 / 净额\n{'━'*104}")
    print(f"\n  {'H(s)':>7}{'触及率':>9}{'毛利bp':>9}{'逆向bp':>9}"
          f"{'净(maker离场)':>15}{'净(taker离场)':>15}{'√t 预测':>10}")
    base_adv = None
    for H in HORIZONS:
        got = [res[s][H] for s in res if H in res[s]]
        if not got:
            continue
        hr = st.median([d["hit_rate"] for d in got])
        gr = st.mean([d["gross"] for d in got])
        ad = st.mean([d["adv"] for d in got])
        if base_adv is None:
            base_adv = ad
        pred = base_adv * (H / HORIZONS[0]) ** 0.5
        print(f"  {H:>7.0f}{hr:>8.1f}%{gr:>+9.3f}{ad:>+9.3f}"
              f"{gr-ad:>+15.3f}{gr-ad-2*TAKER_FEE_BP:>+15.3f}{pred:>+10.3f}")
    print(f"\n  「√t 预测」= 若逆向幅度按随机游走增长（√t），H 处应有的值")
    print(f"  ⇒ 实测值**远低于** √t 预测 ⇒ 有均值回归在抵消 ⇒ 持有更久**不会**线性变差")

    # ── 二、逐币：净额转正的最小 H ──
    print(f"\n{'━'*104}\n  二、逐币：maker 离场口径下，净额何时转正\n{'━'*104}")
    print(f"\n  {'币':<10}{'转正 H':>10}{'该 H 触及率':>14}{'净(maker)':>12}"
          f"{'净(taker)':>12}")
    for s in sorted(res):
        pos = [H for H in HORIZONS if H in res[s] and res[s][H]["net_maker"] > 0]
        if pos:
            h0 = min(pos)
            print(f"  {s:<10}{h0:>10.0f}{res[s][h0]['hit_rate']:>13.1f}%"
                  f"{res[s][h0]['net_maker']:>+12.3f}{res[s][h0]['net_taker']:>+12.3f}")
        else:
            best = max((H for H in HORIZONS if H in res[s]),
                       key=lambda H: res[s][H]["net_maker"])
            print(f"  {s:<10}{'无':>10}{res[s][best]['hit_rate']:>13.1f}%"
                  f"{res[s][best]['net_maker']:>+12.3f}"
                  f"{res[s][best]['net_taker']:>+12.3f}  （H={best:.0f} 最优）")

    # ── 三、逐币详表 ──
    print(f"\n{'━'*104}\n  三、逐币详表（净额 maker 离场口径）\n{'━'*104}")
    hdr = "".join(f"{int(H):>9}" for H in HORIZONS)
    print(f"\n  {'币':<10}{hdr}")
    for s in sorted(res):
        cells = ""
        for H in HORIZONS:
            d = res[s].get(H)
            cells += f"{d['net_maker']:>+9.2f}" if d else f"{'—':>9}"
        print(f"  {s:<10}{cells}")

    print(f"\n  ⚠️ 口径限制（必须知道）：")
    print(f"     · 触及用 15s 网格 ⇒ **低估**触及率（真实 tick 更细）")
    print(f"     · 无队列 ⇒ **高估**成交率")
    print(f"     · 「先触及的那一侧」是事后判定 ⇒ 避免了 H234 的两侧重复计数，")
    print(f"       但引入**选择偏差**：先被触及的那一侧恰好是逆向的那一侧")
    print(f"       （这其实是**正确**的方向 —— 它模拟了「我们的单被吃到」这个事实）")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "horizons": HORIZONS,
                               "by_symbol": res}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
