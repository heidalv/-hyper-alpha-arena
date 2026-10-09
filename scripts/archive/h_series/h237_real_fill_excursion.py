# -*- coding: utf-8 -*-
"""H237 用**引擎的真实成交**量逆向幅度（不再用"触及即成交"当代理）。

# 为什么必须换口径（H235/H236 的教训）

H235/H236 用「价格触及我们的挂单价」当成交条件，得到：

    H=30s  毛利 +1.14bp  逆向 +4.40bp  净 −3.26bp
    H=60s  毛利 +1.14bp  逆向 +5.68bp  净 −4.55bp

而引擎**实测**（当前时代 7 小时，1,340 条 maker 腿）：

    单腿净 **−1.46 bp**（spread +0.18 / price −1.34 / fee 0）

**差 3 倍以上。** 原因：真实成交要求

    runner.py:774   qty = min(leg_qty, _avail * 0.30)    # _avail = 该 15s 桶的主动成交量
    且 seg_taker_sell > 0（买方主动量）

⇒ 价格"插一下"我们挂在最外侧的单，在真实市场里**大概率没有成交量** ⇒ 不成交。
而"触及即成交"模型把大量「插针即回」的良性情形**无条件**算成成交，
再对它们施加逆向选择惩罚 ⇒ **系统性高估逆选择成本**。

⇒ 正确顺序：**先用引擎真实成交当样本，量它之后的逆向幅度**；
再问"挂更宽能否避免这些成交"。本脚本做第一步。

# 口径

1. 从账本取**入场腿**（`flatten=false`），带 `fill_px` / `mid_px` / `side` / `notional` / `ts`；
2. 从 `asterdex_book_ticker` 取真实 mid 序列（15s 网格）；
3. 对每条入场腿，找它**之后**的窗口，计算：
     · `mae_bp` 最大不利偏移（对多头=价格下跌；对空头=上涨）
     · `mfe_bp` 最大有利偏移
     · `term_bp` H 时刻相对成交价的变化（带符号，对我们有利为正）
4. 输出 MAE 分布 + `term` 随 H 的曲线 ⇒ 真实做市腿的"逆向幅度 vs 持有时间"

# 判据

· 若 `term` 随 H **收敛或转正** ⇒ 持有更久更好（均值回归）
· 若 `term` 随 H **持续变负** ⇒ 逆选择是真的，应缩短持有
· 若 `mae` 的 P50 很小而 P90 很大 ⇒ 成本集中在小概率大偏移（尾部），
  与 H226「止损单腿成本逐日差 23 倍」一致

# 用法

    python scripts/h237_real_fill_excursion.py --hours 8
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h237_real_fill_excursion.json"
LANE = "mm_asterdex"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT"]
HS = [15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 900.0]


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


def q(v, p):
    if not v:
        return 0.0
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--grid", type=float, default=15.0)
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, ts, coalesce(meta_json->>'side',''),
                       coalesce(meta_json->>'fill_px','0'),
                       coalesce(meta_json->>'mid_px','0'),
                       coalesce(notional,0), coalesce(net_bp,0),
                       coalesce(spread_bp,0), coalesce(price_bp,0)
                FROM lane_ledger
                WHERE lane_id=%s AND (meta_json->'flatten')::text='false'
                  AND ts >= now() - (%s || ' hours')::interval
                  AND meta_json IS NOT NULL
                ORDER BY ts ASC
            """, (LANE, str(float(a.hours))))
            legs = cur.fetchall()

    if not legs:
        print("窗口内无入场腿")
        return 1

    with psycopg.connect(market_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, event_ts_ms, (bid_px + ask_px) / 2.0
                FROM asterdex_book_ticker
                WHERE ingest_ts >= now() - (%s || ' hours')::interval
                  AND symbol = ANY(%s) AND bid_px > 0 AND ask_px > bid_px
                ORDER BY symbol, event_ts_ms ASC
            """, (str(float(a.hours) + 1.0), CUR))
            rows = cur.fetchall()

    g = {}
    for sym, ms, mid in rows:
        g.setdefault(str(sym), {})[int(ms // 1000 // a.grid * a.grid)] = float(mid)
    series = {s: sorted(d.items()) for s, d in g.items()}
    idx = {s: {k: i for i, (k, _) in enumerate(v)} for s, v in series.items()}

    print("=" * 104)
    print("H237  用真实成交量逆向幅度")
    print("=" * 104)
    print(f"\n  窗口 {a.hours:g}h　入场腿 {len(legs)}　"
          f"tick 网格 {a.grid:g}s　币 {len(series)}")

    recs = []
    unmatched = 0
    for sym, ts, side, fpx, mpx, notl, nbp, sbp, pbp in legs:
        bare = str(sym).upper()
        tk = bare + "USDT" if not bare.endswith("USDT") else bare
        if tk not in series:
            unmatched += 1
            continue
        try:
            fp = float(fpx)
            notl = float(notl)
        except Exception:
            continue
        if fp <= 0 or side not in ("buy", "sell"):
            continue
        t_sec = int(ts.timestamp())
        kk = t_sec // int(a.grid) * int(a.grid)
        i0 = idx[tk].get(kk)
        if i0 is None:
            # 找最近的网格点
            vs = series[tk]
            lo, hi = 0, len(vs) - 1
            while lo < hi:
                mid = (lo + hi) // 2
                if vs[mid][0] < kk:
                    lo = mid + 1
                else:
                    hi = mid
            i0 = lo
        ks = [k for k, _ in series[tk]]
        px = [p for _, p in series[tk]]
        # 方向：多头(buy) ⇒ 不利 = 跌；空头(sell) ⇒ 不利 = 涨
        sign = 1.0 if side == "buy" else -1.0
        rec = {"sym": bare, "side": side, "notional": notl,
               "net_bp": float(nbp), "spread_bp": float(sbp), "price_bp": float(pbp)}
        for H in HS:
            j = i0
            end = ks[i0] + H
            lo_p, hi_p = px[i0], px[i0]
            last = px[i0]
            while j + 1 < len(ks) and ks[j + 1] <= end:
                j += 1
                lo_p = min(lo_p, px[j])
                hi_p = max(hi_p, px[j])
                last = px[j]
            if j == i0:
                continue
            # 对我们有利为正
            mae = sign * (fp - (lo_p if side == "buy" else hi_p)) / fp * 1e4
            mfe = sign * ((hi_p if side == "buy" else lo_p) - fp) / fp * 1e4
            term = sign * (last - fp) / fp * 1e4
            rec[f"mae_{int(H)}"] = mae
            rec[f"mfe_{int(H)}"] = mfe
            rec[f"term_{int(H)}"] = term
        recs.append(rec)

    if not recs:
        print("无法匹配到 tick 序列")
        return 1
    print(f"  匹配成功 {len(recs)} 条（未匹配 {unmatched}）")
    ns = st.mean([r["net_bp"] for r in recs])
    print(f"  这些腿的**实测**净额均值 = {ns:+.3f} bp"
          f"　（spread {st.mean([r['spread_bp'] for r in recs]):+.3f} "
          f"/ price {st.mean([r['price_bp'] for r in recs]):+.3f}）")

    # ── 一、MAE 分布（逆向幅度）──
    print(f"\n{'━'*104}\n  一、最大不利偏移（MAE，正数=对我们不利的幅度）\n{'━'*104}")
    print(f"\n  {'H(s)':>7}{'样本':>7}{'MAE中位':>10}{'MAE均值':>10}"
          f"{'MAE P75':>10}{'MAE P90':>10}{'MAE P99':>10}")
    for H in HS:
        v = [r[f"mae_{int(H)}"] for r in recs if f"mae_{int(H)}" in r]
        if not v:
            continue
        print(f"  {H:>7.0f}{len(v):>7}{st.median(v):>+10.3f}{st.mean(v):>+10.3f}"
              f"{q(v,75):>+10.3f}{q(v,90):>+10.3f}{q(v,99):>+10.3f}")

    # ── 二、term 曲线（带符号，对我们有利为正）──
    print(f"\n{'━'*104}\n  二、H 时刻的**带符号**盈亏（相对成交价，对我们有利为正）\n{'━'*104}")
    print(f"\n  {'H(s)':>7}{'term中位':>11}{'term均值':>11}{'term P10':>10}"
          f"{'term P90':>10}{'为正占比':>10}{'实测net_bp(参考)':>18}")
    prev = None
    for H in HS:
        v = [r[f"term_{int(H)}"] for r in recs if f"term_{int(H)}" in r]
        if not v:
            continue
        pos = sum(1 for x in v if x > 0) / len(v) * 100
        arrow = ""
        if prev is not None:
            arrow = "↓变差" if st.median(v) < prev else "↑变好"
        prev = st.median(v)
        print(f"  {H:>7.0f}{st.median(v):>+11.3f}{st.mean(v):>+11.3f}"
              f"{q(v,10):>+10.2f}{q(v,90):>+10.2f}{pos:>9.1f}%"
              f"{ns:>+14.3f} {arrow}")

    # ── 三、判据 ──
    print(f"\n{'━'*104}\n  三、判据：持有更久是变好还是变差\n{'━'*104}")
    v30 = [r["term_30"] for r in recs if "term_30" in r]
    v900 = [r["term_900"] for r in recs if "term_900" in r]
    if v30 and v900:
        m30, m900 = st.median(v30), st.median(v900)
        print(f"\n  H=30s  term 中位 {m30:+.3f} bp")
        print(f"  H=900s term 中位 {m900:+.3f} bp")
        if m900 > m30:
            print(f"  ⇒ **持有更久更好**（term 上升）⇒ 逆选择不是主导，"
                  f"缩短持有无益")
        else:
            print(f"  ⇒ **持有更久更差**（term 下降 {m900-m30:+.3f}）"
                  f"⇒ 逆选择随时间是真实成本")
        # √t 对比
        import math
        if m30:
            pred = m30 * math.sqrt(900.0 / 30.0)
            print(f"     若按 √t 增长，H=900 应为 {pred:+.3f}；"
                  f"实测 {m900:+.3f} ⇒ {'低于' if m900 > pred else '高于'}随机游走预测")

    # ── 四、逐币 ──
    print(f"\n{'━'*104}\n  四、逐币：实测净额 vs MAE/term\n{'━'*104}")
    by = {}
    for r in recs:
        by.setdefault(r["sym"], []).append(r)
    print(f"\n  {'币':<10}{'腿数':>7}{'实测净bp':>11}{'MAE60中位':>12}"
          f"{'term60中位':>12}{'term300中位':>13}")
    for s in sorted(by):
        v = by[s]
        m60 = [r["mae_60"] for r in v if "mae_60" in r]
        t60 = [r["term_60"] for r in v if "term_60" in r]
        t300 = [r["term_300"] for r in v if "term_300" in r]
        print(f"  {s:<10}{len(v):>7}{st.mean([r['net_bp'] for r in v]):>+11.3f}"
              f"{(st.median(m60) if m60 else 0):>+12.3f}"
              f"{(st.median(t60) if t60 else 0):>+12.3f}"
              f"{(st.median(t300) if t300 else 0):>+13.3f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "hours": a.hours, "n_legs": len(recs), "observed_net_bp": round(ns, 4),
        "horizons": HS,
        "mae_median": {str(int(H)): round(st.median(
            [r[f"mae_{int(H)}"] for r in recs if f"mae_{int(H)}" in r]), 4)
            for H in HS if any(f"mae_{int(H)}" in r for r in recs)},
        "term_median": {str(int(H)): round(st.median(
            [r[f"term_{int(H)}"] for r in recs if f"term_{int(H)}" in r]), 4)
            for H in HS if any(f"term_{int(H)}" in r for r in recs)},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
