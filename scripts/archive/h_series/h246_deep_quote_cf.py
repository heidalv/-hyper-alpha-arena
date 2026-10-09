# -*- coding: utf-8 -*-
"""H246 挂到盘口之外的反事实（生产里做不了，因为被钳制挡住）。

# 为什么必须做这个

H245 证明了生产的**不穿越钳制**把挂宽钉死在 `[best_bid, best_ask]` 内
⇒ `spread_mult > 1.0` 完全无效。**但"更宽到底好不好的"这个问题仍然没被回答** ——
只是被钳制挡住了，无法在生产里测。

而它可以用 tick 数据**精确模拟**：

    在某时刻 t 挂 `mid ± Δ`，看未来 H 秒内是否被触及（触及即成交）
    成交后，看**带符号**的价格移动（对我们有利为正）
    ⇒ 期望 = 成交概率 × E[有利移动] − 成交概率 × Δ 的机会成本 …

更干净的口径：**只看"成交了的那一批"的条件期望**：

    条件净额(Δ, H) = Δ − E[成交后 H 秒的不利偏移 | 被触及]

因为挂单免费（Aster maker 0 fee），Δ 就是我们赚的价差，
而"成交后的不利偏移"就是逆选择成本。

# 这个模拟与之前几次失败尝试的区别（重要）

| 之前的尝试 | 错在哪 |
|---|---|
| H235/H236 | 用「mid 触及」当成交，但**没有队列/成交量约束** ⇒ 高估成交率 |
| H238/H240 | 拿账本 `spread_bp` 当 Δ 的基础，而**账本读数受选择效应污染** |
| **H246（本脚本）** | **不依赖任何账本量**：Δ 是我们自己设的，价格路径是市场的。**自足。** |

⇒ 而且它**同时给出成交率**（这是唯一的代价），所以可以直接看权衡。

# 关键洞察：为什么"更宽"在理论上可能有效

生产的逆选择成本是 **−1.18bp**，而我们只赚 **+0.19bp** ⇒ 缺口 6.4 倍。
但那个 −1.18bp 是在 Δ≈0.2bp（贴着中价）时测的 —— **几乎必然被逆向选择**，
因为价格每 tick 动 4bp 而我们在 0.2bp 处。

⇒ **若挂到 4~8bp 外，只有"真的要走 4~8bp"的成交才会碰到我们**
⇒ 那批成交的逆向选择特征**完全不同**。这是唯一可能改变算术的地方。

# 用法

    python scripts/h246_deep_quote_cf.py --hours 24
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h246_deep_quote.json"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT"]
DEPTHS = [0.2, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0]
HOLDS = [15.0, 30.0, 60.0, 300.0]
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
    ap.add_argument("--grid", type=float, default=1.0, help="价格网格（秒）")
    ap.add_argument("--stride", type=int, default=20,
                    help="每多少个网格点取一个样本（控制计算量）")
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
    print("H246  挂到盘口之外的反事实（Δ 由我们设定，价格路径来自市场）")
    print("=" * 104)
    print(f"\n  窗口 {a.hours:g}h　价格网格 {a.grid:g}s　步长 {a.stride}")
    for s, (ks, px) in sorted(series.items()):
        print(f"  {s:12} {len(ks):>7} 点")

    maxH = max(HOLDS)
    res = {}
    for sym, (ks, px) in sorted(series.items()):
        n = len(ks)
        per = {}
        for D in DEPTHS:
            hits = 0
            tot = 0
            adv = {H: [] for H in HOLDS}
            for i in range(0, n - 2, a.stride):
                mid = px[i]
                if mid <= 0:
                    continue
                tot += 1
                # 两侧都挂：取**先被触及**的那一侧
                bid_q = mid * (1.0 - D / 1e4)
                ask_q = mid * (1.0 + D / 1e4)
                t_buy = t_sell = None
                j = i + 1
                end = ks[i] + maxH
                while j < n and ks[j] <= end:
                    if t_buy is None and px[j] <= bid_q:
                        t_buy = j
                    if t_sell is None and px[j] >= ask_q:
                        t_sell = j
                    if t_buy is not None or t_sell is not None:
                        # 继续扫完（要为所有 H 算 fwd），但记录首次触及已够
                        pass
                    j += 1
                if t_buy is None and t_sell is None:
                    continue
                hits += 1
                if t_buy is not None and (t_sell is None or t_buy <= t_sell):
                    side, ti, entry = "buy", t_buy, bid_q
                else:
                    side, ti, entry = "sell", t_sell, ask_q
                sign = 1.0 if side == "buy" else -1.0
                for H in HOLDS:
                    k2 = ti
                    while k2 + 1 < n and ks[k2 + 1] - ks[i] < H:
                        k2 += 1
                    if k2 <= i:
                        continue
                    # 从成交点起算的**带符号**移动（有利为正）
                    term = sign * (px[k2] - entry) / entry * 1e4
                    adv[H].append(term)
            per[D] = {"hit_rate": hits / tot * 100 if tot else 0.0, "n": hits,
                      "tot": tot,
                      "term": {H: (st.mean(adv[H]) if adv[H] else None)
                               for H in HOLDS}}
        res[sym] = per

    # ── 一、逐币逐深度：成交率 + 条件净额 ──
    for sym in sorted(res):
        print(f"\n{'━'*104}\n  {sym}\n{'━'*104}")
        hdr = "".join(f"{('term@'+str(int(H))+'s'):>14}" for H in HOLDS)
        print(f"  {'Δ(bp)':>7}{'成交率':>9}{'样本':>8}{hdr}")
        for D in DEPTHS:
            d = res[sym][D]
            cells = ""
            for H in HOLDS:
                t = d["term"][H]
                cells += f"{t:>+14.3f}" if t is not None else f"{'—':>14}"
            print(f"  {D:>7.1f}{d['hit_rate']:>8.2f}%{d['n']:>8}{cells}")
        print(f"  ⇒ `term@Hs` = 从**成交价**起算、H 秒后的带符号移动（有利为正）")
        print(f"     净额 ≈ Δ + term@H（挂单免费；若离场走 taker 再 −2×{TAKER_FEE_BP}bp）")

    # ── 二、汇总：期望净额（含机会成本）──
    print(f"\n{'━'*104}\n  二、汇总：条件净额（= Δ + term@H）与「每 tick 期望」\n{'━'*104}")
    print(f"\n  「条件净额」= 成交后平均赚多少；「每 tick 期望」= 条件净额 × 成交率")
    print(f"  ⇒ **后者才是可比的**（挂宽会降低成交率）\n")
    for H in HOLDS:
        print(f"  ── H = {H:g}s")
        print(f"     {'Δ(bp)':>7}{'成交率':>9}{'条件净额bp':>13}"
              f"{'每tick期望bp':>14}{'扣2×4bp后':>12}")
        for D in DEPTHS:
            hs, ns, terms = [], 0, []
            for sym in res:
                d = res[sym][D]
                t = d["term"][H]
                hs.append(d["hit_rate"])
                if t is not None:
                    terms.append(t)
                    ns += d["n"]
            if not terms:
                continue
            hr = st.mean(hs) / 100.0
            cond = D + st.mean(terms)
            print(f"     {D:>7.1f}{st.mean(hs):>8.2f}%{cond:>+13.3f}"
                  f"{cond*hr:>+14.3f}{cond*hr - 2*TAKER_FEE_BP*hr:>+12.3f}")
        print()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "depths": DEPTHS, "holds": HOLDS,
                               "by_symbol": {
                                   s: {str(D): {"hit_rate": v[D]["hit_rate"],
                                                "n": v[D]["n"],
                                                "term": {str(H): v[D]["term"][H]
                                                         for H in HOLDS}}
                                       for D in DEPTHS}
                                   for s, v in res.items()}},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
