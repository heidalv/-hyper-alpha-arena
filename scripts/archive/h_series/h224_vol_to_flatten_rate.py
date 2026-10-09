# -*- coding: utf-8 -*-
"""H224 直接验证：「大波动 → 大亏」的传导是不是「波动 → 强平率 → taker 成本」。

# 链条假说

    H222/H223 已确认：
        全部亏损来自 flatten 腿（当前 4 币口径：2.0% 的腿吃掉 61.3% 的亏损）
        flatten 单腿 −12.8 ~ −17.6 bp，成分 = price(−5~−10) + fee(−4.0) + spread(−0.6~−1.2)

    若用户观察成立，剩下要证的一环是：
        **波动越大 ⇒ 强平越频繁** ⇒ 总成本 = 波动 × 强平率 × 单次成本

⇒ 这解释了三件事同时成立：
    · 「遇到大波动行情直接就大亏」（成本随波动上升）
    · 「利润稳定不住，大盘波动大一点就回吐」（没有波动时能赚）
    · 我做市腿在不同口径下都"接近打平"（做市本身不是问题）

# 口径（逐腿，不依赖任何外部数据）

`meta_json.seg_low/seg_high` 让每条 **maker** 腿自带"当时那一桶多宽"。
对每个 (symbol, 5 分钟窗口) 算**窗口中位桶宽**作为该窗口的波动代理，
再把 flatten 腿按时间落到窗口里 ⇒ 得到「每个波动档位的强平率」。

⚠️ flatten 腿没有 seg 字段 ⇒ 只用来**计数**，不用来算波动。

# 判据（跨窗口，本会话硬规矩）

1. 强平率是否**随波动单调上升**；
2. 该单调性是否在**逐日**都成立（不是被某一天带走的）；
3. 单次强平成本是否随波动上升（若只升频率、不升单价，修法不同）。

# 用法

    python scripts/h224_vol_to_flatten_rate.py --days 7
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h224_vol_to_flatten_rate.json"
NOW_SYMBOLS = ["ASTER", "XRP", "SOL", "HYPE"]


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


def q(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--only-current", action="store_true", default=True)
    ap.add_argument("--bin-minutes", type=int, default=5)
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, ts, (meta_json->'flatten')::text,
                       coalesce(meta_json->>'seg_low',''),
                       coalesce(meta_json->>'seg_high',''),
                       coalesce(meta_json->>'mid_px',''),
                       coalesce(notional,0), coalesce(net_bp,0),
                       coalesce(fee_bp,0), coalesce(price_bp,0)
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= now() - (%s || ' days')::interval
                  AND meta_json IS NOT NULL AND coalesce(notional,0) > 0
                ORDER BY ts ASC
            """, (LANE, str(int(a.days))))
            raw = cur.fetchall()

    # 分桶：每个 (symbol, 时间桶) 一条记录
    bins = {}
    for sym, ts, flat, lo, hi, mid, notl, nbp, fbp, pbp in raw:
        sym = str(sym)
        if a.only_current and sym not in NOW_SYMBOLS:
            continue
        key = (sym, ts.replace(second=0, microsecond=0,
                               minute=(ts.minute // a.bin_minutes) * a.bin_minutes))
        b = bins.setdefault(key, {"maker": [], "flat": [], "ts": ts})
        if str(flat).lower() == "true":
            b["flat"].append({"notional": float(notl), "net_bp": float(nbp),
                              "fee_bp": float(fbp), "price_bp": float(pbp),
                              "usd": float(notl) * float(nbp) / 1e4})
        else:
            try:
                lo_f, hi_f, mid_f = float(lo), float(hi), float(mid)
            except Exception:
                continue
            if min(lo_f, hi_f, mid_f) <= 0:
                continue
            b["maker"].append({"w_bp": abs(hi_f - lo_f) / mid_f * 1e4,
                               "notional": float(notl), "net_bp": float(nbp),
                               "usd": float(notl) * float(nbp) / 1e4})

    cells = []
    for (sym, _b), v in bins.items():
        if not v["maker"]:
            continue
        cells.append({
            "sym": sym, "ts": v["ts"],
            "vol": st.median([m["w_bp"] for m in v["maker"]]),
            "n_leg": len(v["maker"]) + len(v["flat"]),
            "n_maker": len(v["maker"]), "n_flat": len(v["flat"]),
            "maker_usd": sum(m["usd"] for m in v["maker"]),
            "flat_usd": sum(m["usd"] for m in v["flat"]),
            "flat_bp": (st.mean([m["net_bp"] for m in v["flat"]])
                        if v["flat"] else None),
        })

    if not cells:
        print("无可用数据（当前口径下没有 maker 腿带 seg 字段）")
        return 1

    print("=" * 104)
    print("H224  「大波动 → 大亏」的传导链验证")
    print("=" * 104)
    print(f"\n  窗口 {a.days} 天　时间桶 {a.bin_minutes} 分钟　"
          f"有效桶 {len(cells)}（仅当前 {len(NOW_SYMBOLS)} 币）")
    vols = [c["vol"] for c in cells]
    print(f"  桶波动（窗口中位桶宽 bp）：P10 {q(vols,10):.2f}　P50 {q(vols,50):.2f}　"
          f"P90 {q(vols,90):.2f}　P99 {q(vols,99):.2f}　max {max(vols):.2f}")
    nf = sum(c["n_flat"] for c in cells)
    nl = sum(c["n_leg"] for c in cells)
    print(f"  总腿 {nl}　其中 flatten {nf}（{nf/nl*100:.2f}%）")
    print(f"  maker 净额 ${sum(c['maker_usd'] for c in cells):+.2f}　"
          f"flatten 净额 ${sum(c['flat_usd'] for c in cells):+.2f}")

    # ── 一、按波动分档：强平率 + 单次成本 ──
    print(f"\n{'━'*104}\n  一、强平率是否随波动上升（这是链条最关键的一环）\n{'━'*104}")
    EDGES = [0, 1, 2, 3, 5, 8, 12, 20, 1e9]
    print(f"\n  {'波动档(bp)':>14}{'桶数':>7}{'maker腿':>9}{'flat腿':>8}"
          f"{'**强平率**':>11}{'maker净$':>11}{'flat净$':>11}{'flat单腿bp':>12}")
    tab = []
    for i in range(len(EDGES) - 1):
        lo, hi = EDGES[i], EDGES[i + 1]
        sel = [c for c in cells if lo <= c["vol"] < hi]
        if not sel:
            continue
        nm = sum(c["n_maker"] for c in sel)
        nfl = sum(c["n_flat"] for c in sel)
        rate = nfl / (nm + nfl) * 100 if (nm + nfl) else 0
        lbl = f"{lo:g}–{hi:g}" if hi < 1e9 else f"{lo:g}+"
        fbps = [c["flat_bp"] for c in sel if c["flat_bp"] is not None]
        print(f"  {lbl:>14}{len(sel):>7}{nm:>9}{nfl:>8}{rate:>10.2f}%"
              f"{sum(c['maker_usd'] for c in sel):>+11.2f}"
              f"{sum(c['flat_usd'] for c in sel):>+11.2f}"
              f"{(st.mean(fbps) if fbps else 0):>+12.2f}")
        tab.append({"band": lbl, "cells": len(sel), "maker": nm, "flat": nfl,
                    "rate_pct": round(rate, 3),
                    "maker_usd": round(sum(c["maker_usd"] for c in sel), 2),
                    "flat_usd": round(sum(c["flat_usd"] for c in sel), 2)})

    rates = [t["rate_pct"] for t in tab]
    if len(rates) >= 3:
        inc = sum(1 for i in range(1, len(rates)) if rates[i] >= rates[i - 1])
        print(f"\n  ⇒ 强平率单调不降的相邻档数 = {inc}/{len(rates)-1}")
        print(f"     最低档 {rates[0]:.2f}%　最高档 {rates[-1]:.2f}%　"
              f"比值 {rates[-1]/rates[0] if rates[0] else float('nan'):.1f}×")
        if rates[-1] > rates[0] * 1.5 and inc >= (len(rates) - 1) * 0.6:
            print(f"  ⇒ **链条成立**：波动越高，强平越频繁 ⇒ "
                  f"成本 = 波动 × 强平率 × 单次成本")
        else:
            print(f"  ⇒ **链条不成立**（强平率与波动无稳定单调关系）")

    # ── 二、逐日：单调性是否每天成立 ──
    print(f"\n{'━'*104}\n  二、逐日：波动最高四分位 vs 最低四分位的强平率\n{'━'*104}")
    days = {}
    for c in cells:
        days.setdefault(c["ts"].strftime("%Y-%m-%d"), []).append(c)
    print(f"\n  {'日期':<12}{'桶数':>7}{'低波动强平率':>14}{'高波动强平率':>14}"
          f"{'高/低':>9}{'低波动净$':>12}{'高波动净$':>12}")
    hi_wins = 0
    nd = 0
    for d in sorted(days):
        v = days[d]
        if len(v) < 8:
            continue
        v = sorted(v, key=lambda c: c["vol"])
        k = max(1, len(v) // 4)
        lo_q, hi_q = v[:k], v[-k:]
        def rate(g):
            nm = sum(c["n_maker"] for c in g)
            nfl = sum(c["n_flat"] for c in g)
            return nfl / (nm + nfl) * 100 if (nm + nfl) else 0.0
        def net(g):
            return sum(c["maker_usd"] + c["flat_usd"] for c in g)
        rl, rh = rate(lo_q), rate(hi_q)
        nd += 1
        if rh > rl:
            hi_wins += 1
        print(f"  {d:<12}{len(v):>7}{rl:>13.2f}%{rh:>13.2f}%"
              f"{(rh/rl if rl else float('nan')):>8.1f}×"
              f"{net(lo_q):>+12.2f}{net(hi_q):>+12.2f}")
    print(f"\n  ⇒ 高波动档强平率更高的天数 = **{hi_wins}/{nd}**")

    # ── 三、把总成本拆成「波动 × 强平率 × 单次成本」──
    print(f"\n{'━'*104}\n  三、成本传导的年化直觉：少一次强平值多少\n{'━'*104}")
    fl_all = [c for c in cells if c["flat_bp"] is not None]
    if fl_all:
        avg_bp = st.mean([c["flat_bp"] for c in fl_all])
        avg_notional = q([c["flat_usd"] / (c["flat_bp"] / 1e4)
                          for c in fl_all if c["flat_bp"]], 50)
        print(f"\n  flatten 单次均值 = {avg_bp:+.2f} bp　"
              f"中位名义 ≈ ${avg_notional:,.0f}")
        print(f"  ⇒ 单次强平成本 ≈ **${avg_bp/1e4*avg_notional:+.3f}**")
        print(f"  当前强平率 {nf/nl*100:.2f}%　总腿 {nl}")
        print(f"  ⇒ 若强平率**减半**，按当前口径可省 "
              f"${-avg_bp/1e4*avg_notional*nf/2:+.2f}")
        print(f"  ⇒ 若**完全不需强平**（仓位自然对冲），可省 "
              f"${-avg_bp/1e4*avg_notional*nf:+.2f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"cells": len(cells), "table": tab,
                               "flat_rate_pct": round(nf / nl * 100, 3),
                               "days": nd, "hi_wins": hi_wins},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
