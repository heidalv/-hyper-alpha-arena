# -*- coding: utf-8 -*-
"""H247 挂深反事实 v2：用**引擎的撤单节奏**（修正 H246 的最大偏差）。

# H246 的结果与它最大的偏差

H246（无队列、窗口最长 300s）给出：

    Δ=0.2bp  成交率 99.9%  条件净额 −1.35bp   ← 与实测 −0.0368 吻合（模拟 −0.0353）
    Δ=2.0bp  成交率 99.9%  条件净额 +0.41bp
    Δ=8.0bp  成交率 98.2%  条件净额 +6.65bp
    Δ=15bp   成交率 81.7%  条件净额 +13.53bp

⇒ 表面结论："挂得越深越好，因为 Δ 涨而 term 稳定在 −1.2bp"。

**但有一个会致命的偏差**：H246 假设挂单在**整个 300s 窗口**内一直有效，
而**引擎每个 tick（实测 ~19s）就撤单重挂**（`_quote_hist` / `quote_ts`）。
在 19s 内动 8bp 的概率，用实测逐 tick 分布估只有约 **1%**（P99=19~24bp）。

⇒ **深度挂单的真实成交率会被严重高估。** 而成交率正是这个权衡的另一半。

# 本脚本的修正

**挂单只存活一个 tick（`--tick-sec`，默认 19s）**，窗口内被触及才算成交。
这是引擎的真实语义（每 tick 撤旧挂新）。

同时给出**两个口径**，因为"成交后何时离场"决定了 term 怎么算：

  · `exit@tick`：下次重挂时以中价离场（理想，maker）
  · `exit@H`：成交后持有 H 秒再以中价离场

# 判据

    「每 tick 期望 = 成交率 × (Δ + term)」
⇒ 这是唯一可比的量（挂深提高单次收益、降低成交率）。
若所有 Δ 的每 tick 期望都 ≤ 当前值 ⇒ **挂深这条路也走不通**，
那就只剩"减少成交次数"（突发事件闸 / σ 闸）这一条路。

# 用法

    python scripts/h247_deep_quote_v2.py --hours 24 --tick-sec 19
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h247_deep_quote_v2.json"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT"]
DEPTHS = [0.2, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 25.0]
TAKER_FEE_BP = 4.0


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
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--tick-sec", type=float, default=19.0,
                    help="挂单存活期 = 引擎 tick 周期（撤旧挂新）")
    ap.add_argument("--stride", type=int, default=19,
                    help="每多少个 1s 网格点取一个样本")
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(market_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, event_ts_ms, (bid_px + ask_px) / 2.0
                FROM asterdex_book_ticker
                WHERE ingest_ts >= now() - (%s || ' hours')::interval
                  AND symbol = ANY(%s) AND bid_px > 0 AND ask_px > bid_px
                ORDER BY symbol, event_ts_ms ASC
            """, (str(float(a.hours) + 0.2), CUR))
            rows = cur.fetchall()
    if not rows:
        print("无数据")
        return 1

    g = {}
    for sym, ms, mid in rows:
        g.setdefault(str(sym), {})[int(ms / 1000.0)] = float(mid)
    series = {}
    for s, d in g.items():
        ks = sorted(d)
        series[s] = (ks, [d[k] for k in ks])

    print("=" * 104)
    print("H247  挂深反事实 v2（挂单只存活一个 tick —— 引擎的真实语义）")
    print("=" * 104)
    print(f"\n  窗口 {a.hours:g}h　**挂单存活 {a.tick_sec:g}s**（撤旧挂新）"
          f"　采样步长 {a.stride}")
    for s, (ks, px) in sorted(series.items()):
        print(f"  {s:12} {len(ks):>7} 点")

    # tick 内的 |移动| 分布（这是成交率的物理上限）
    allmv = []
    for s, (ks, px) in series.items():
        for i in range(0, len(px) - 1, a.stride):
            j = i + 1
            while j + 1 < len(px) and ks[j + 1] - ks[i] < a.tick_sec:
                j += 1
            if j <= i or px[i] <= 0:
                continue
            allmv.append(abs(px[j] - px[i]) / px[i] * 1e4)
    allmv.sort()
    if allmv:
        n = len(allmv)
        print(f"\n  **{a.tick_sec:g}s 内 |移动| 分布**（{n} 样本，决定成交率上限）：")
        for p in (50, 75, 90, 95, 99, 99.9):
            print(f"    P{p:<5} {allmv[min(n-1, int(n*p/100))]:>8.3f} bp")
        print(f"    max   {allmv[-1]:>8.3f} bp")

    res = {}
    for sym, (ks, px) in sorted(series.items()):
        n = len(ks)
        per = {}
        for D in DEPTHS:
            hits, tot, terms = 0, 0, []
            for i in range(0, n - 2, a.stride):
                mid = px[i]
                if mid <= 0:
                    continue
                tot += 1
                bid_q = mid * (1.0 - D / 1e4)
                ask_q = mid * (1.0 + D / 1e4)
                # 挂单存活窗 = [t, t+tick_sec]
                end = ks[i] + a.tick_sec
                j = i + 1
                t_buy = t_sell = None
                while j < n and ks[j] <= end:
                    if t_buy is None and px[j] <= bid_q:
                        t_buy = j
                    if t_sell is None and px[j] >= ask_q:
                        t_sell = j
                    j += 1
                if t_buy is None and t_sell is None:
                    continue
                hits += 1
                if t_buy is not None and (t_sell is None or t_buy <= t_sell):
                    side, ti, entry = "buy", t_buy, bid_q
                else:
                    side, ti, entry = "sell", t_sell, ask_q
                sign = 1.0 if side == "buy" else -1.0
                # 离场：窗末以中价离场（理想 maker 口径）
                k2 = min(ti, n - 1)
                j2 = ti
                while j2 + 1 < n and ks[j2 + 1] <= end:
                    j2 += 1
                k2 = j2
                terms.append(sign * (px[k2] - entry) / entry * 1e4)
            per[D] = {"hit_rate": hits / tot * 100 if tot else 0.0, "n": hits,
                      "tot": tot,
                      "cond": (D + st.mean(terms)) if terms else None,
                      "exp": ((D + st.mean(terms)) * hits / tot) if terms else None}
        res[sym] = per

    print(f"\n{'━'*104}\n  一、逐币：成交率 / 条件净额 / **每 tick 期望**\n{'━'*104}")
    for sym in sorted(res):
        print(f"\n  ── {sym}")
        print(f"     {'Δ(bp)':>7}{'成交率':>9}{'样本':>7}{'条件净额bp':>13}"
              f"{'**每tick期望bp**':>17}{'折每腿$':>11}")
        for D in DEPTHS:
            d = res[sym][D]
            if d["cond"] is None:
                continue
            # 每腿美元：名义 ≈ 中位腿量 $250
            usd = d["exp"] / 1e4 * 250.0
            print(f"     {D:>7.1f}{d['hit_rate']:>8.2f}%{d['n']:>7}"
                  f"{d['cond']:>+13.3f}{d['exp']:>+17.3f}{usd:>+11.4f}")

    print(f"\n{'━'*104}\n  二、汇总（四币平均，腿数加权）\n{'━'*104}")
    print(f"\n  假设单腿名义 $250（实测中位）\n")
    print(f"  {'Δ(bp)':>7}{'成交率':>9}{'条件净额bp':>13}"
          f"{'每tick期望bp':>15}{'**折每腿$**':>13}{'vs Δ=0.2':>11}")
    base_usd = None
    rows_out = []
    for D in DEPTHS:
        hs, conds, ns = [], [], 0
        for sym in res:
            d = res[sym][D]
            if d["cond"] is None:
                continue
            hs.append(d["hit_rate"])
            conds.append(d["cond"])
            ns += d["n"]
        if not conds:
            continue
        hr = st.mean(hs) / 100.0
        cond = st.mean(conds)
        exp = cond * hr
        usd = exp / 1e4 * 250.0
        if base_usd is None:
            base_usd = usd
        print(f"  {D:>7.1f}{st.mean(hs):>8.2f}%{cond:>+13.3f}{exp:>+15.3f}"
              f"{usd:>+13.4f}{usd-base_usd:>+11.4f}")
        rows_out.append({"depth_bp": D, "hit_pct": round(st.mean(hs), 2),
                         "cond_bp": round(cond, 4), "exp_bp": round(exp, 4),
                         "usd_per_leg": round(usd, 5), "n": ns})

    print(f"\n{'━'*104}\n  三、结论\n{'━'*104}")
    if rows_out:
        best = max(rows_out, key=lambda r: r["usd_per_leg"])
        first = rows_out[0]
        print(f"\n  当前口径（Δ≈0.2bp）每腿 **${first['usd_per_leg']:+.5f}**")
        print(f"  最优 Δ（{best['depth_bp']:g}bp）每腿 **${best['usd_per_leg']:+.5f}**"
              f"（成交率 {best['hit_pct']:.1f}%）")
        print(f"  ⇒ 改善 ${best['usd_per_leg']-first['usd_per_leg']:+.5f}/腿")
        if best["usd_per_leg"] > 0:
            print(f"\n  ⇒ **挂深可以转正** ⇒ 值得在线验证（但注意口径限制）")
        else:
            print(f"\n  ⇒ **挂深也转不正**（最优仍为负）⇒ 这条路的收益上限不足以覆盖成本")
        print(f"\n  ⚠️ 口径限制（必须知道）：")
        print(f"     · **无队列**：触及即成交 ⇒ 深度挂单的成交率仍被**高估**")
        print(f"     · 离场按「窗末中价」= 理想 maker 口径；若被迫 taker 则再扣 2×{TAKER_FEE_BP}bp")
        print(f"     · 未建模：挂深 ⇒ 库存积累更慢 ⇒ 敞口路径改变")
        print(f"     ⇒ 这是**上界估计**。若上界都不转正，那条路就不必试了。")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "tick_sec": a.tick_sec,
                               "depths": DEPTHS, "summary": rows_out,
                               "tick_move_pct": {str(p): round(
                                   allmv[min(len(allmv)-1, int(len(allmv)*p/100))], 4)
                                   for p in (50, 75, 90, 95, 99, 99.9)} if allmv else {}},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
