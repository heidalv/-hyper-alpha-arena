# -*- coding: utf-8 -*-
"""H360b P4/P5 信号×出口组合（最优持有期补全）。

h360 只测了 P1；h361 显示 P4/P5 的 edge 随持有期增长（P4 f300 +1.11 > f30 +0.66、
P5 f300 +1.00 ≈ f30 +0.77）⇒ 需要独立的最优持有期结论。
复用 h360 的 simulate/_stat，事件生成与 h361b 同口径（flow=with）。

用法: python scripts/h360b_p45_exit_sim.py [--hours 168]
"""
from __future__ import annotations

import argparse
import bisect
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
OUT = ROOT / "research_l1" / "out" / "h360b_p45_exit_sim.json"

from h360_signal_exit_sim import POLICIES, read_env_dsn, simulate, _stat  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=168.0)
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,DOGEUSDT,XRPUSDT")
    a = ap.parse_args()

    import psycopg

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    ev_p4, ev_p5 = [], []

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

        vol60 = []
        for i in range(120, n):
            seg = mids[i - 120:i + 1]
            vol60.append(sum(abs((seg[t + 1] - seg[t]) / seg[t]) * 1e4
                             for t in range(len(seg) - 1)))
        vol_q30 = sorted(vol60)[int(len(vol60) * 0.3)] if vol60 else 1.0

        last = -1e18
        for i in range(n):
            if ks[i] - last < 20:
                continue
            r15 = past(i, 15)
            if r15 is None:
                continue
            cands = []
            seg = mids[max(0, i - 120):i + 1]
            hi, lo = max(seg), min(seg)
            near_hi = sum(1 for m in seg if m >= hi * (1 - 5e-6))
            near_lo = sum(1 for m in seg if m <= lo * (1 + 5e-6))
            if near_hi >= 2 and mids[i] >= hi * (1 - 5e-6):
                cands.append(("p4", 1.0))
            elif near_lo >= 2 and mids[i] <= lo * (1 + 5e-6):
                cands.append(("p4", -1.0))
            segv = mids[max(0, i - 60):i + 1]
            v60 = sum(abs((segv[t + 1] - segv[t]) / segv[t]) * 1e4
                      for t in range(len(segv) - 1))
            if v60 < vol_q30 and abs(r15) >= 2.0:
                cands.append(("p5", 1.0 if r15 > 0 else -1.0))
            if not cands:
                continue
            last = ks[i]
            o = ofi.get(ks[i] // 15)
            for pname, sign in cands:
                if o is None or o * sign < 0.3:   # flow=with 过滤
                    continue
                ev = {"mids": mids, "i0": i, "sign": sign}
                (ev_p4 if pname == "p4" else ev_p5).append(ev)

    print(f"P4 with 事件 {len(ev_p4)}；P5 with 事件 {len(ev_p5)}")
    if not ev_p4 and not ev_p5:
        return 1

    out = {}
    for pname, evs in (("P4_breakout", ev_p4), ("P5_squeeze", ev_p5)):
        print(f"\n══ {pname}（n={len(evs)}）══")
        print(f"{'策略':<20} {'均值bp':>9} {'中位bp':>9} {'t':>7} {'胜率':>7}")
        rec = {}
        for name, ptype, p in POLICIES:
            st = _stat(simulate(evs, (ptype, p)))
            if st:
                rec[name] = st
                print(f"{name:<20} {st['bp']:>+9.3f} {st['median']:>+9.3f} "
                      f"{st['t']:>+7.1f} {st['win_rate']:>7.3f}")
        out[pname] = rec

    OUT.write_text(json.dumps({"hours": a.hours, "symbols": syms,
                               "n_p4": len(ev_p4), "n_p5": len(ev_p5), "policies": out},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
