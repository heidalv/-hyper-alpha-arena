# -*- coding: utf-8 -*-
"""H220 大波动行情里到底哪一类腿在亏 —— 按「桶内波幅」分桶分解。

# 用户的问题（原话）

    「还是老问题，遇到大波动行情，直接就大亏」

F338 的腿量上限只砍掉了**尺寸**：$11,661 的腿变 $790。
但 $790 的腿在 −31bp 逆向里照样亏 $2.45，而大波动一天有几百腿。
⇒ 上限**降低不了「大波动行情亏钱」这件事**，只降低它的方差。

本脚本回答的是机制问题：**大波动行情里，亏损是哪一类腿产生的？**

# 为什么用 `meta_json` 里的 `seg_low/seg_high`

引擎在**决策时**就能看到那一桶的区间高低（`m["seg_low"] / m["seg_high"]`），
它们被写进了每条腿的 `meta_json` ⇒ **每条腿都自带"当时那一桶有多宽"**，
不需要任何外部数据源、不需要时间对齐、不受数据缺口影响。这是最干净的口径：

    桶内波幅 bp = |seg_high - seg_low| / mid_px × 1e4

# 三种腿的区分（这是关键）

    entry   : side 与当前仓位同向加仓（`abs(qty) ≈ 目标腿量`）
    reduce  : 减仓腿（`_reducing`，一次平掉整个仓位）
    flatten : 强平腿（`meta.flatten = true`，taker 过价）

三者的成本结构完全不同（maker 0 费 vs taker 4bp + 过价），
混在一起看会互相掩盖 —— 这正是我之前一直看错的地方。

# 用法

    python scripts/h220_vol_regime_legs.py --days 7
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h220_vol_regime_legs.json"


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
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT coalesce(meta_json->>'seg_low','') AS lo,
                       coalesce(meta_json->>'seg_high','') AS hi,
                       coalesce(meta_json->>'mid_px','') AS mid,
                       coalesce(meta_json->>'side','') AS side,
                       coalesce(meta_json->>'flatten','false') AS flat,
                       coalesce(meta_json->>'exit_reason','') AS xr,
                       coalesce(notional,0), coalesce(net_bp,0),
                       coalesce(spread_bp,0), coalesce(price_bp,0),
                       coalesce(fee_bp,0), coalesce(slippage_bp,0),
                       coalesce(symbol,''), ts,
                       coalesce(meta_json->>'qty','0')
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= now() - (%s || ' days')::interval
                  AND coalesce(notional,0) > 0 AND meta_json IS NOT NULL
                ORDER BY ts ASC
            """, (LANE, str(int(a.days))))
            raw = cur.fetchall()

    legs = []
    skipped = 0
    for (lo, hi, mid, side, flat, xr, notl, nbp, sbp, pbp, fbp, slp,
         sym, ts, qty) in raw:
        try:
            lo_f, hi_f, mid_f = float(lo), float(hi), float(mid)
        except Exception:
            skipped += 1
            continue
        if mid_f <= 0 or hi_f <= 0 or lo_f <= 0:
            skipped += 1
            continue
        w = abs(hi_f - lo_f) / mid_f * 1e4
        legs.append({
            "w_bp": w, "notional": float(notl), "net_bp": float(nbp),
            "spread_bp": float(sbp), "price_bp": float(pbp),
            "fee_bp": float(fbp), "slip_bp": float(slp),
            "usd": float(notl) * float(nbp) / 1e4,
            "flat": str(flat).lower() in ("true", "1"),
            "side": str(side), "xr": str(xr), "sym": str(sym),
            "ts": ts, "qty": abs(float(qty or 0)),
        })

    if not legs:
        print("没有可用腿（meta_json 缺 seg_low/seg_high？）")
        return 1

    print("=" * 104)
    print("H220  大波动行情里的亏损来自哪一类腿")
    print("=" * 104)
    ws = [x["w_bp"] for x in legs]
    print(f"\n  窗口 {a.days} 天　可用腿 {len(legs)}（跳过 {skipped} 条缺 seg 字段的）")
    print(f"  桶内波幅 bp：P10 {q(ws,10):.1f}　P50 {q(ws,50):.1f}　"
          f"P90 {q(ws,90):.1f}　P99 {q(ws,99):.1f}　max {max(ws):.1f}")

    # ── 一、按波幅分桶看总量 ──
    print(f"\n{'━'*104}\n  一、按桶内波幅分桶：总量与结构\n{'━'*104}")
    EDGES = [0, 3, 6, 10, 15, 25, 40, 1e9]
    print(f"\n  {'波幅区间bp':>14}{'腿数':>7}{'占比':>7}{'名义合计$':>13}"
          f"{'净额$':>11}{'加权net_bp':>12}{'spread':>9}{'price':>9}{'fee':>8}")
    for i in range(len(EDGES) - 1):
        lo, hi = EDGES[i], EDGES[i + 1]
        sel = [x for x in legs if lo <= x["w_bp"] < hi]
        if not sel:
            continue
        nn = sum(x["notional"] for x in sel)
        uu = sum(x["usd"] for x in sel)
        wbp = uu / nn * 1e4 if nn else 0
        lbl = f"{lo:g}–{hi:g}" if hi < 1e9 else f"{lo:g}+"
        print(f"  {lbl:>14}{len(sel):>7}{len(sel)/len(legs)*100:>6.1f}%{nn:>13,.0f}"
              f"{uu:>+11.2f}{wbp:>+12.3f}"
              f"{st.mean([x['spread_bp'] for x in sel]):>+9.3f}"
              f"{st.mean([x['price_bp'] for x in sel]):>+9.3f}"
              f"{st.mean([x['fee_bp'] for x in sel]):>+8.3f}")

    # ── 二、高波动 vs 低波动：按腿的**类型**拆开 ──
    print(f"\n{'━'*104}\n  二、高波动桶（≥15bp）里，三类腿各亏多少\n{'━'*104}")
    HI = [x for x in legs if x["w_bp"] >= 15]
    LO = [x for x in legs if x["w_bp"] < 15]
    print(f"\n  高波动腿 {len(HI)}（{len(HI)/len(legs)*100:.1f}%）　"
          f"低波动腿 {len(LO)}（{len(LO)/len(legs)*100:.1f}%）")
    print(f"  高波动净额 ${sum(x['usd'] for x in HI):+.2f}　"
          f"低波动净额 ${sum(x['usd'] for x in LO):+.2f}")

    def group(xs, name):
        fl = [x for x in xs if x["flat"]]
        mk = [x for x in xs if not x["flat"]]
        nn = sum(x["notional"] for x in xs)
        out = {"name": name, "n": len(xs), "notional": nn,
               "usd": sum(x["usd"] for x in xs)}
        print(f"\n  ── {name}（{len(xs)} 腿，名义 ${nn:,.0f}）"
              f"　净额 **${out['usd']:+.2f}**")
        for lbl, sub in (("maker 腿", mk), ("flatten 腿", fl)):
            if not sub:
                continue
            sn = sum(x["notional"] for x in sub)
            su = sum(x["usd"] for x in sub)
            print(f"     {lbl:10} {len(sub):>5} 腿　名义 ${sn:>10,.0f}　"
                  f"净额 ${su:>+9.2f}　加权 {su/sn*1e4 if sn else 0:>+8.3f} bp　"
                  f"均值 spread {st.mean([x['spread_bp'] for x in sub]):>+7.3f} "
                  f"price {st.mean([x['price_bp'] for x in sub]):>+7.3f} "
                  f"fee {st.mean([x['fee_bp'] for x in sub]):>+6.2f}")
        out["maker_usd"] = sum(x["usd"] for x in mk)
        out["flat_usd"] = sum(x["usd"] for x in fl)
        out["flat_n"] = len(fl)
        return out

    g_hi = group(HI, "高波动（桶内 ≥15bp）")
    g_lo = group(LO, "低波动（桶内 <15bp）")

    # ── 三、flatten 腿的过价成本（大波动里最贵的部分）──
    print(f"\n{'━'*104}\n  三、flatten（强平）腿的成本结构\n{'━'*104}")
    for lbl, xs in (("高波动", HI), ("低波动", LO)):
        fl = [x for x in xs if x["flat"]]
        if not fl:
            print(f"\n  {lbl}：无 flatten 腿")
            continue
        nn = sum(x["notional"] for x in fl)
        su = sum(x["usd"] for x in fl)
        print(f"\n  {lbl}：{len(fl)} 条 flatten　名义 ${nn:,.0f}　净额 ${su:+.2f}　"
              f"加权 {su/nn*1e4 if nn else 0:+.2f} bp")
        print(f"    成分均值：spread {st.mean([x['spread_bp'] for x in fl]):+.3f}　"
              f"price {st.mean([x['price_bp'] for x in fl]):+.3f}　"
              f"fee {st.mean([x['fee_bp'] for x in fl]):+.3f}　"
              f"slippage {st.mean([x['slip_bp'] for x in fl]):+.3f}")
        print(f"    单腿均值 ${su/len(fl):+.3f}　中位 ${st.median([x['usd'] for x in fl]):+.3f}")

    # ── 四、出场原因 × 波动（谁在什么时候把仓位打掉）──
    print(f"\n{'━'*104}\n  四、出场原因 × 波动区间（flatten 腿归因）\n{'━'*104}")
    print(f"\n  {'exit_reason':<22}{'腿数':>7}{'高波动中':>10}{'净额$':>12}"
          f"{'加权bp':>10}{'中位波幅bp':>13}")
    xs_all = [x for x in legs if x["xr"]]
    by = {}
    for x in xs_all:
        by.setdefault(x["xr"], []).append(x)
    for k in sorted(by, key=lambda k: -abs(sum(v["usd"] for v in by[k]))):
        v = by[k]
        nn = sum(z["notional"] for z in v)
        su = sum(z["usd"] for z in v)
        nhi = sum(1 for z in v if z["w_bp"] >= 15)
        print(f"  {k:<22}{len(v):>7}{nhi:>10}{su:>+12.2f}"
              f"{su/nn*1e4 if nn else 0:>+10.3f}"
              f"{st.median([z['w_bp'] for z in v]):>13.1f}")

    # ── 五、逐日：高波动腿的净额是否稳定为负 ──
    print(f"\n{'━'*104}\n  五、逐日：高波动腿净额（跨窗口判据）\n{'━'*104}")
    days = {}
    for x in legs:
        days.setdefault(x["ts"].strftime("%Y-%m-%d"), []).append(x)
    print(f"\n  {'日期':<12}{'高波动腿':>9}{'高净额$':>11}{'低波动腿':>9}{'低净额$':>11}"
          f"{'高波动占比':>11}")
    neg = 0
    tot_d = 0
    for d in sorted(days):
        v = days[d]
        hh = [z for z in v if z["w_bp"] >= 15]
        ll = [z for z in v if z["w_bp"] < 15]
        hu = sum(z["usd"] for z in hh)
        lu = sum(z["usd"] for z in ll)
        tot_d += 1
        if hu < 0:
            neg += 1
        print(f"  {d:<12}{len(hh):>9}{hu:>+11.2f}{len(ll):>9}{lu:>+11.2f}"
              f"{len(hh)/len(v)*100 if v else 0:>10.1f}%")
    print(f"\n  ⇒ 高波动腿为负的天数 = **{neg}/{tot_d}**")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "legs": len(legs), "hi_n": len(HI), "lo_n": len(LO),
        "hi_usd": round(g_hi["usd"], 2), "lo_usd": round(g_lo["usd"], 2),
        "hi_maker_usd": round(g_hi["maker_usd"], 2),
        "hi_flat_usd": round(g_hi["flat_usd"], 2), "hi_flat_n": g_hi["flat_n"],
        "lo_maker_usd": round(g_lo["maker_usd"], 2),
        "lo_flat_usd": round(g_lo["flat_usd"], 2), "lo_flat_n": g_lo["flat_n"],
        "bands": EDGES,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
