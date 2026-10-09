# -*- coding: utf-8 -*-
"""H378 全形态样本外检验补全：P2（VWAP 回归）与 P4（双触突破）前 84h vs 后 84h。

h373 已测 P1/P5；本脚本补 P2/P4（用同一套口径：flow=with/neutral/against × f30/f120）。
若两半窗 with 均正且 against 均负 ⇒ 形态跨 regime 稳健。

用法: python scripts/h378_pattern_oos_full.py [--hours 168]
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
OUT = ROOT / "research_l1" / "out" / "h378_p24_oos.json"


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


def collect(syms, ago_hours: float, hours: float):
    import psycopg
    rows = []
    for sym in syms:
        lo_ms = (ago_hours + hours) * 3600 * 1000
        hi_ms = ago_hours * 3600 * 1000
        with psycopg.connect(read_env_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT (event_ts_ms/1000)::bigint AS b,
                           (array_agg(bid_px ORDER BY event_ts_ms DESC))[1] AS bid,
                           (array_agg(ask_px ORDER BY event_ts_ms DESC))[1] AS ask
                    FROM asterdex_book_ticker
                    WHERE symbol=%s
                      AND event_ts_ms < (extract(epoch from now())*1000 - %s)::bigint
                      AND event_ts_ms >= (extract(epoch from now())*1000 - %s)::bigint
                      AND bid_px>0 AND ask_px>bid_px
                    GROUP BY b ORDER BY b
                """, (sym, hi_ms, lo_ms))
                recs = cur.fetchall()
                cur.execute("""
                    SELECT (event_ts_ms/1000)::bigint AS b, sum(price*qty), sum(qty)
                    FROM asterdex_trades
                    WHERE symbol=%s
                      AND event_ts_ms < (extract(epoch from now())*1000 - %s)::bigint
                      AND event_ts_ms >= (extract(epoch from now())*1000 - %s)::bigint
                    GROUP BY b ORDER BY b
                """, (sym, hi_ms, lo_ms))
                trecs = cur.fetchall()
        ks = [int(r[0]) for r in recs]
        mids = [float(r[1] + r[2]) / 2.0 for r in recs]
        n = len(ks)
        if n < 5000:
            continue
        tts = [int(r[0]) for r in trecs]
        vnum, vden = [0.0], [0.0]
        for r in trecs:
            vnum.append(vnum[-1] + float(r[1]))
            vden.append(vden[-1] + float(r[2]))
        bare = sym[:-4] if sym.endswith("USDT") else sym
        with psycopg.connect(read_env_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT timestamp, COALESCE(taker_buy_notional,0), COALESCE(taker_sell_notional,0)
                    FROM market_trades_aggregated
                    WHERE symbol=%s AND timestamp < (extract(epoch from now())*1000 - %s)::bigint
                      AND timestamp >= (extract(epoch from now())*1000 - %s)::bigint
                    ORDER BY timestamp
                """, (bare, hi_ms, lo_ms))
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
        for i in range(n):
            if ks[i] - last < 20:
                continue
            cands = []
            # P2：60s VWAP 偏离 ≥2bp → 回归
            i1 = bisect.bisect_right(tts, ks[i] - 60)
            i2 = bisect.bisect_right(tts, ks[i])
            den = vden[i2] - vden[i1]
            vwap = (vnum[i2] - vnum[i1]) / den if den > 0 else mids[i]
            dev = (mids[i] - vwap) / vwap * 1e4
            if abs(dev) >= 2.0:
                cands.append(("P2", -1.0 if dev > 0 else 1.0))
            # P4：120s 双触极值 + 现价贴极值 → 突破方向
            seg = mids[max(0, i - 120):i + 1]
            hi, lo = max(seg), min(seg)
            near_hi = sum(1 for m in seg if m >= hi * (1 - 5e-6))
            near_lo = sum(1 for m in seg if m <= lo * (1 + 5e-6))
            if near_hi >= 2 and mids[i] >= hi * (1 - 5e-6):
                cands.append(("P4", 1.0))
            elif near_lo >= 2 and mids[i] <= lo * (1 + 5e-6):
                cands.append(("P4", -1.0))
            if not cands:
                continue
            last = ks[i]
            o = ofi.get(ks[i] // 15)
            f30v = fwd(i, 30)
            f120v = fwd(i, 120)
            if f30v is None or f120v is None:
                continue
            for pname, sign in cands:
                ofi_signed = o * sign if o is not None else None
                flow = "with" if (ofi_signed is not None and ofi_signed >= 0.3) else \
                       ("against" if (ofi_signed is not None and ofi_signed <= -0.3)
                        else "neutral")
                rows.append({"p": pname, "flow": flow,
                             "f30": f30v * sign, "f120": f120v * sign})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=168.0)
    a = ap.parse_args()
    syms = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "DOGEUSDT"]
    half = a.hours / 2.0

    old = collect(syms, ago_hours=half, hours=half)
    new = collect(syms, ago_hours=0.0, hours=half)

    out = {}
    for label, rows in (("first_half", old), ("second_half", new)):
        print(f"\n══ {label}（n={len(rows)}）══")
        print(f"{'形态×流向':<14} {'n':>7} {'f30':>14} {'f120':>14}")
        rec = {}
        for pname in ("P2", "P4"):
            for flow in ("with", "neutral", "against"):
                sub = [r for r in rows if r["p"] == pname and r["flow"] == flow]
                st30 = _stat([r["f30"] for r in sub])
                st120 = _stat([r["f120"] for r in sub])
                key = f"{pname}_{flow}"
                rec[key] = {"n": len(sub), "f30": st30, "f120": st120}
                if st30:
                    print(f"{pname}×{flow:<9} {st30['n']:>7} "
                          f"{st30['bp']:>+8.3f}({st30['t']:+.1f}) "
                          f"{st120['bp']:>+8.3f}({st120['t']:+.1f})")
        out[label] = rec

    OUT.write_text(json.dumps({"hours": a.hours, "half": half, **out},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
