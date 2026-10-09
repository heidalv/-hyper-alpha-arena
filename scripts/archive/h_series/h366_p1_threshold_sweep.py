# -*- coding: utf-8 -*-
"""H366 P1 触发参数扫描：r60 回调深度 × r300 趋势强度 × flow≠against。

现行 P1 触发：|r60|≥2bp 且与 r300 反向、|r300|≥15bp。本脚本扫描
  r60_thr ∈ {2,3,4,5} bp × r300_thr ∈ {10,15,20,30,40} bp
输出每格 f120/f300（sign×bp, t）与事件频率（n/h/币）——为 #3 之后的
P1 闸参数精调提供依据（哪个格子 edge×频率 最优）。

用法: python scripts/h366_p1_threshold_sweep.py [--hours 168]
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
OUT = ROOT / "research_l1" / "out" / "h366_p1_sweep.json"
R60_THRS = [2.0, 3.0, 4.0, 5.0]
R300_THRS = [10.0, 15.0, 20.0, 30.0, 40.0]


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
    # 每事件：{r60, r300, sign, flow, f120, f300}
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
        ks = [int(r[0]) for r in recs]
        mids = [float(r[1] + r[2]) / 2.0 for r in recs]
        n = len(ks)
        if n < 5000:
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
        for i in range(n):
            if ks[i] - last < 20:
                continue
            r60 = past(i, 60)
            r300 = past(i, 300)
            if r60 is None or r300 is None:
                continue
            if abs(r60) < 2.0 or abs(r300) < 10.0 or (r60 > 0) == (r300 > 0):
                continue
            last = ks[i]
            sign = 1.0 if r300 > 0 else -1.0
            o = ofi.get(ks[i] // 15)
            ofi_signed = o * sign if o is not None else None
            if ofi_signed is not None and ofi_signed <= -0.3:
                continue  # flow≠against（#3 语义）
            f120v = fwd(i, 120)
            f300v = fwd(i, 300)
            if f120v is None or f300v is None:
                continue
            all_rows.append({"r60": abs(r60), "r300": abs(r300), "sign": sign,
                             "f120": f120v * sign, "f300": f300v * sign})

    print(f"事件 {len(all_rows)}（flow≠against）")
    out = {}
    print(f"\n{'r60\\r300':<10} " + "".join(f"{t:>22}" for t in R300_THRS))
    for r60t in R60_THRS:
        line = f"{r60t:<10} "
        for r300t in R300_THRS:
            sub = [r for r in all_rows if r["r60"] >= r60t and r["r300"] >= r300t]
            st120 = _stat([r["f120"] for r in sub])
            st300 = _stat([r["f300"] for r in sub])
            freq = len(sub) / (a.hours * len(syms))
            key = f"r60_{r60t}_r300_{r300t}"
            out[key] = {"n": len(sub), "freq_per_h_per_sym": round(freq, 2),
                        "f120": st120, "f300": st300}
            if st120:
                line += f" {st120['bp']:>+7.2f}({st120['t']:+.1f}){freq:>4.1f}/h "
            else:
                line += f" {'—':>22} "
        print(line)
    print("格式：f120bp(t) 频率/h/币")
    OUT.write_text(json.dumps({"hours": a.hours, "symbols": syms, "grid": out},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
