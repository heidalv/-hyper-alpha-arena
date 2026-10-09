# -*- coding: utf-8 -*-
"""H358 P2 条件化数学模型：E[Δmid | VWAP 偏离深度, OFI 流向] × 持有期。

P2 = 价格偏离 60s 成交 VWAP ≥2bp → 向 VWAP 回归（h350 无条件 +0.57bp@120s，t=2.2）。
本脚本把 P2 的期望收益写成状态条件期望：
  E[Δmid(h) | 偏离深度桶, OFI 与交易方向同/逆]  h ∈ {30,60,120,300}s
假设（由 h355/h351 推出）：偏离若由流驱动（OFI 背离 VWAP）⇒ 回归与流对抗 ⇒ 弱/负；
偏离若薄流（OFI 已回归 VWAP）⇒ 回归顺流 ⇒ 强。
若假设成立：实盘 P2 闸应加"薄流确认"（= 试跑 #4 候选）。

事件口径（无未来函数）：sign = 向 VWAP 回归方向（dev>0 ⇒ 空、dev<0 ⇒ 多）。
收益 = sign × 未来 Δmid(h) bp。OFI 相对交易方向：flow_with = ofi*sign ≥ 0.3。

用法: python scripts/h358_p2_vwap_conditional.py [--hours 168]
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h358_p2_conditional.json"
HORIZONS = [30, 60, 120, 300]
DEV_BUCKETS = [(2.0, 3.0, "v2-3"), (3.0, 5.0, "v3-5"), (5.0, 8.0, "v5-8"), (8.0, 1e9, "v8+")]


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


def _stat(xs):
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
            continue
        bare = sym[:-4] if sym.endswith("USDT") else sym
        tts = [int(r[0]) for r in trecs]
        vnum, vden = [0.0], [0.0]
        for r in trecs:
            vnum.append(vnum[-1] + float(r[1]))
            vden.append(vden[-1] + float(r[2]))
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

        def fwd(i, sec):
            j = bisect.bisect_right(ks, ks[i] + sec) - 1
            return (mids[j] - mids[i]) / mids[i] * 1e4 \
                if j > i and ks[j] - ks[i] >= sec * 0.9 else None

        last = -1e18
        n_ev = 0
        for i in range(n):
            if ks[i] - last < 20:
                continue
            # 60s 成交 VWAP 偏离
            i1 = bisect.bisect_right(tts, ks[i] - 60)
            i2 = bisect.bisect_right(tts, ks[i])
            den = vden[i2] - vden[i1]
            vwap = (vnum[i2] - vnum[i1]) / den if den > 0 else mids[i]
            dev = (mids[i] - vwap) / vwap * 1e4
            if abs(dev) < 2.0:
                continue
            last = ks[i]
            sign = -1.0 if dev > 0 else 1.0        # 回归方向
            o = ofi.get(ks[i] // 15)
            ofi_signed = (o * sign) if o is not None else None
            flow = None
            if ofi_signed is not None:
                flow = "with" if ofi_signed >= 0.3 else \
                       ("against" if ofi_signed <= -0.3 else "neutral")
            fws = {h: fwd(i, h) for h in HORIZONS}
            if not all(fws[h] is not None for h in HORIZONS):
                continue
            n_ev += 1
            all_rows.append({"sym": bare, "dev": abs(dev), "flow": flow,
                             **{f"f{h}": fws[h] * sign for h in HORIZONS}})
        print(f"{bare}: P2 事件 {n_ev} 次 / {a.hours:.0f}h = {n_ev/a.hours:.1f} 次/h")

    print(f"\n总事件 {len(all_rows)}")
    if not all_rows:
        return 1

    print(f"\n── 边际 E[Δmid|dev]（sign×bp）──")
    print(f"{'桶':<8} {'n':>6} " + "".join(f"{f'f{h}':>14}" for h in HORIZONS))
    m_dev = {}
    for lo, hi, name in DEV_BUCKETS:
        xs = {h: [r[f"f{h}"] for r in all_rows if lo <= r["dev"] < hi] for h in HORIZONS}
        cells = []
        rec = {"n": len(xs[HORIZONS[0]])}
        for h in HORIZONS:
            st = _stat(xs[h])
            if st:
                cells.append(f"{st['bp']:>+8.3f}({st['t']:+.1f})")
                rec[f"f{h}"] = st
        m_dev[name] = rec
        if cells:
            print(f"{name:<8} {rec['n']:>6} " + "".join(cells))

    print("\n── P2 × OFI 流向交互 ──")
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

    print("\n── 深度×流向（f120 bp(t)）──")
    joint = {}
    for lo, hi, nd in DEV_BUCKETS:
        row = {}
        for flow in ("with", "neutral", "against"):
            xs = [r["f120"] for r in all_rows
                  if lo <= r["dev"] < hi and r["flow"] == flow]
            st = _stat(xs)
            row[flow] = st
            print(f"  {nd:>5}×{flow:<8} n={len(xs):>5}  "
                  f"f120={st['bp']:>+8.3f}(t={st['t']:+.1f})" if st else
                  f"  {nd:>5}×{flow:<8} n={len(xs):>5}  样本不足")
        joint[nd] = row

    print("\n── 分币种（f120）──")
    by_sym = {}
    for sym in sorted({r["sym"] for r in all_rows}):
        xs = [r["f120"] for r in all_rows if r["sym"] == sym]
        st = _stat(xs) if xs else None
        by_sym[sym] = {"n": len(xs), "freq_per_h": round(len(xs) / a.hours, 2), "f120": st}
        print(f"  {sym:<8} n={len(xs):>5} {len(xs)/a.hours:>6.1f} 次/h  "
              f"f120={st['bp']:>+8.3f}bp(t={st['t']:+.1f})" if st else f"  {sym}: 无")

    OUT.write_text(json.dumps({"hours": a.hours, "symbols": syms,
                               "n_total": len(all_rows), "marginal_dev": m_dev,
                               "flow_interaction": m_flow, "joint": joint,
                               "by_symbol": by_sym}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
