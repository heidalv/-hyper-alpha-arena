# -*- coding: utf-8 -*-
"""H355 P1 条件化数学模型：E[Δmid | 回调深度 d, 趋势强度 τ, OFI 流向] × 持有期。

# 目标（目标①+②：为基准形态 P1 建立数学模型）
    P1 = 顺势回调（|r60|≥2bp 且与 300s 趋势同向的反向回调）——已知实盘 +3.75bp/腿。
    本脚本把 P1 的期望收益写成状态的条件期望：
        E[Δmid(h) | d∈桶, τ∈桶, OFI 流向]   h ∈ {30,60,120,300}s
    并输出每格的样本数/均值/t 值/最优持有期（30s-5min 域内 argmax）。
    附加：OFI 交互检验——回调若是"流向驱动"（OFI 逆势），h351 的流延续规律
    （E[Δmid|OFI]≈+0.7bp）预示回调继续 → 应回避；OFI 顺势（薄流回调）→ 均值回归更强。

# 事件口径（与 h350 P1 一致，无未来函数）
    sign = 与 300s 趋势同向（涨势中买回调、跌势中卖反弹）
    收益 = sign × 未来 Δmid(h) bp

# 用法: python scripts/h355_p1_conditional_model.py [--hours 168]
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import pathlib
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h355_p1_conditional.json"
HORIZONS = [30, 60, 120, 300]
DEPTH_BUCKETS = [(2.0, 3.0, "d2-3"), (3.0, 5.0, "d3-5"), (5.0, 8.0, "d5-8"), (8.0, 1e9, "d8+")]
TREND_BUCKETS = [(15.0, 30.0, "t15-30"), (30.0, 60.0, "t30-60"), (60.0, 1e9, "t60+")]


def read_env_dsn() -> str:
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


def _stat(xs: list[float]):
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    t = m / math.sqrt(var / n) if var > 0 else 0.0
    return {"n": n, "bp": round(m, 3), "t": round(t, 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=168.0)
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,DOGEUSDT")
    a = ap.parse_args()

    import psycopg

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    all_rows = []

    for sym in syms:
        with psycopg.connect(read_env_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT (event_ts_ms/1000)::bigint AS b,
                           (array_agg(bid_px ORDER BY event_ts_ms DESC))[1] AS bid,
                           (array_agg(ask_px ORDER BY event_ts_ms DESC))[1] AS ask
                    FROM asterdex_book_ticker
                    WHERE symbol=%s AND event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                      AND bid_px>0 AND ask_px>bid_px
                    GROUP BY b ORDER BY b
                """, (sym, a.hours))
                recs = cur.fetchall()
                cur.execute("""
                    SELECT (event_ts_ms/1000)::bigint AS b, sum(price*qty), sum(qty)
                    FROM asterdex_trades
                    WHERE symbol=%s AND event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                    GROUP BY b ORDER BY b
                """, (sym, a.hours))
                trecs = cur.fetchall()
        ks = [int(r[0]) for r in recs]
        mids = [float(r[1] + r[2]) / 2.0 for r in recs]
        n = len(ks)
        if n < 5000:
            print(f"{sym}: 样本不足 ({n})，跳过")
            continue
        bare = sym[:-4] if sym.endswith("USDT") else sym
        with psycopg.connect(read_env_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT timestamp, COALESCE(taker_buy_notional,0), COALESCE(taker_sell_notional,0)
                    FROM market_trades_aggregated
                    WHERE symbol=%s AND timestamp >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                    ORDER BY timestamp
                """, (bare, a.hours))
                orows = cur.fetchall()
        ofi = {}
        for ts_ms, bn, sn in orows:
            tot = float(bn) + float(sn)
            if tot > 0:
                ofi[int(ts_ms) // 15000] = (float(bn) - float(sn)) / tot

        def past(i, sec):
            j = bisect.bisect_left(ks, ks[i] - sec)
            return (mids[i] - mids[j]) / mids[j] * 1e4 \
                if j < i and ks[i] - ks[j] >= sec * 0.9 and mids[j] > 0 else None

        def fwd(i, sec):
            j = bisect.bisect_right(ks, ks[i] + sec) - 1
            return (mids[j] - mids[i]) / mids[i] * 1e4 \
                if j > i and ks[j] - ks[i] >= sec * 0.9 else None

        last = -1e18
        n_ev = 0
        for i in range(n):
            if ks[i] - last < 20:
                continue
            r60 = past(i, 60)
            r300 = past(i, 300)
            if r60 is None or r300 is None:
                continue
            # P1 触发：|r60|≥2bp 且 r60 与 300s 趋势反向（= 顺势回调）
            if abs(r60) < 2.0 or abs(r300) < 15.0:
                continue
            if (r60 > 0) == (r300 > 0):
                continue  # 60s 与 300s 同向 = 不是回调
            last = ks[i]
            sign = 1.0 if r300 > 0 else -1.0          # 交易方向 = 与趋势同向
            d = abs(r60)                               # 回调深度 bp
            t_ = abs(r300)                             # 趋势强度 bp
            # 当前 OFI（15-60s 前段，事件前 15s 起的最近桶——与 h351 的 t=11-35s 口径相邻）
            o15 = ofi.get(ks[i] // 15)
            o_prev = ofi.get(ks[i] // 15 - 1)
            o = o15 if o15 is not None else o_prev
            ofi_signed = (o * sign) if o is not None else None
            # 流向交互：OFI 与趋势同向 = 薄流回调（均值回归候选）；逆势 = 流驱动回调（延续风险）
            flow = None
            if ofi_signed is not None:
                flow = "with" if abs(ofi_signed) >= 0.3 and ofi_signed > 0 else \
                       ("against" if abs(ofi_signed) >= 0.3 else "neutral")
            fws = {h: fwd(i, h) for h in HORIZONS}
            if not all(fws[h] is not None for h in HORIZONS):
                continue
            n_ev += 1
            all_rows.append({
                "sym": bare, "sign": sign, "d": d, "t": t_,
                "flow": flow, "ofi_signed": ofi_signed,
                **{f"f{h}": fws[h] * sign for h in HORIZONS},
            })
        print(f"{bare}: P1 事件 {n_ev} 次 / {a.hours:.0f}h = {n_ev/a.hours:.1f} 次/h")

    print(f"\n总事件 {len(all_rows)}")
    if not all_rows:
        return 1

    # ── 1. 边际表（每特征单独） ────────────────────────────────────────────
    def marginal(key, buckets):
        print(f"\n── 边际 E[Δmid|{key}]（sign×bp）──")
        print(f"{'桶':<10} {'n':>6} " + "".join(f"{f'f{h}':>14}" for h in HORIZONS))
        out = {}
        for lo, hi, name in buckets:
            xs = {h: [r[f"f{h}"] for r in all_rows if lo <= r[key] < hi] for h in HORIZONS}
            cells = []
            rec = {"n": len(xs[HORIZONS[0]])}
            for h in HORIZONS:
                st = _stat(xs[h])
                if st:
                    cells.append(f"{st['bp']:>+8.3f}({st['t']:+.1f})")
                    rec[f"f{h}"] = st
            out[name] = rec
            if cells:
                print(f"{name:<10} {rec['n']:>6} " + "".join(cells))
        return out

    m_depth = marginal("d", DEPTH_BUCKETS)
    m_trend = marginal("t", TREND_BUCKETS)

    # ── 2. OFI 流向交互（P1 × h351 流延续）───────────────────────────────
    print("\n── P1 × OFI 流向交互 ──")
    print(f"{'流向':<10} {'n':>6} " + "".join(f"{f'f{h}':>14}" for h in HORIZONS))
    m_flow = {}
    for flow in ("with", "neutral", "against"):
        xs = {h: [r[f"f{h}"] for r in all_rows if r["flow"] == flow] for h in HORIZONS}
        cells = []
        rec = {"n": len(xs[HORIZONS[0]])}
        for h in HORIZONS:
            st = _stat(xs[h])
            if st:
                cells.append(f"{st['bp']:>+8.3f}({st['t']:+.1f})")
                rec[f"f{h}"] = st
        m_flow[flow] = rec
        if cells:
            print(f"{flow:<10} {rec['n']:>6} " + "".join(cells))

    # ── 3. 深度×趋势 联合表（最优持有期）───────────────────────────────────
    print("\n── 深度×趋势 联合（f120 bp(t)，[ ]=最优持有期 s）──")
    joint = {}
    for lo_d, hi_d, nd in DEPTH_BUCKETS:
        row = {}
        for lo_t, hi_t, nt in TREND_BUCKETS:
            sub = [r for r in all_rows if lo_d <= r["d"] < hi_d and lo_t <= r["t"] < hi_t]
            if len(sub) < 30:
                row[nt] = {"n": len(sub)}
                continue
            byh = {}
            for h in HORIZONS:
                xs = [r[f"f{h}"] for r in sub]
                byh[h] = (sum(xs) / len(xs),
                          sum(xs) / len(xs) / math.sqrt(sum((x - sum(xs)/len(xs))**2
                                                            for x in xs) / (len(xs)-1) / len(xs))
                          if len(xs) > 1 else 0.0)
            best = max(byh, key=lambda h: byh[h][0])
            row[nt] = {"n": len(sub),
                       "f120": {"bp": round(byh[120][0], 3), "t": round(byh[120][1], 2)},
                       "best_h": best, "best_bp": round(byh[best][0], 3)}
            print(f"  {nd:>5}×{nt:<8} n={len(sub):>4}  "
                  f"f120={byh[120][0]:+8.3f}({byh[120][1]:+.1f})  "
                  f"最优持有 {best}s {byh[best][0]:+.3f}bp")
        joint[nd] = row

    # ── 4. 分币种 + 频率 ─────────────────────────────────────────────────
    print("\n── 分币种（f120）与频率 ──")
    by_sym = {}
    for sym in sorted({r["sym"] for r in all_rows}):
        sub = [r for r in all_rows if r["sym"] == sym]
        xs = [r["f120"] for r in sub]
        st = _stat(xs) if xs else None
        by_sym[sym] = {"n": len(sub), "freq_per_h": round(len(sub) / a.hours, 2),
                       "f120": st}
        print(f"  {sym:<8} n={len(sub):>4} {len(sub)/a.hours:>6.1f} 次/h  "
              f"f120={st['bp']:>+8.3f}bp(t={st['t']:+.1f})" if st else f"  {sym}: 无")

    out = {"hours": a.hours, "symbols": syms, "n_total": len(all_rows),
           "marginal_depth": m_depth, "marginal_trend": m_trend,
           "flow_interaction": m_flow, "joint_depth_trend": joint, "by_symbol": by_sym}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
