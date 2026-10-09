# -*- coding: utf-8 -*-
"""H361b P4/P5 数学模型的补全：分币种 × 持有期 × 跨形态重叠。

h361 给出 P4/P5 在 flow=with 条件下 f30/f60/f120/f300 的全局均值；本脚本补：
  1. 分币种 × 持有期：E[Δmid(h)|P4/P5, flow=with]（试跑 #6/#7 的选币依据）；
  2. 最优持有期：P4/P5 的 edge 是否随 h 增长（h361 显示 P4 f300 +1.11 > f30 +0.66）；
  3. 跨形态重叠：P1/P4/P5 在 ±20s 网格内共同触发的比例（实盘每 tick 只能选一个形态，
     需要优先级设计）。

用法: python scripts/h361b_p45_per_symbol.py [--hours 168]
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
OUT = ROOT / "research_l1" / "out" / "h361b_p45_symbol.json"
HORIZONS = [30, 60, 120, 300]


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
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,DOGEUSDT,"
                                           "XRPUSDT,SUIUSDT,NEARUSDT,ARBUSDT,ADAUSDT,ENAUSDT")
    a = ap.parse_args()

    import psycopg

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    rows = []          # 每事件一行：{sym, p, flow, f30..f300, ts}
    events = {}        # sym -> {ts_grid -> set(patterns fired)} 用于重叠

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

        vol60 = []
        for i in range(120, n):
            seg = mids[i - 120:i + 1]
            vol60.append(sum(abs((seg[t + 1] - seg[t]) / seg[t]) * 1e4
                             for t in range(len(seg) - 1)))
        vol_q30 = sorted(vol60)[int(len(vol60) * 0.3)] if vol60 else 1.0

        ev_sym = events.setdefault(bare, {})
        last = -1e18
        for i in range(n):
            if ks[i] - last < 20:
                continue
            r15 = past(i, 15)
            r60 = past(i, 60)
            r300 = past(i, 300)
            if r15 is None:
                continue
            fired = []
            # P1
            if r60 is not None and r300 is not None and abs(r60) >= 2.0 \
                    and abs(r300) >= 15.0 and (r60 > 0) != (r300 > 0):
                fired.append(("P1", 1.0 if r300 > 0 else -1.0))
            # P4
            seg = mids[max(0, i - 120):i + 1]
            hi, lo = max(seg), min(seg)
            near_hi = sum(1 for m in seg if m >= hi * (1 - 5e-6))
            near_lo = sum(1 for m in seg if m <= lo * (1 + 5e-6))
            if near_hi >= 2 and mids[i] >= hi * (1 - 5e-6):
                fired.append(("P4", 1.0))
            elif near_lo >= 2 and mids[i] <= lo * (1 + 5e-6):
                fired.append(("P4", -1.0))
            # P5
            segv = mids[max(0, i - 60):i + 1]
            v60 = sum(abs((segv[t + 1] - segv[t]) / segv[t]) * 1e4
                      for t in range(len(segv) - 1))
            if v60 < vol_q30 and abs(r15) >= 2.0:
                fired.append(("P5", 1.0 if r15 > 0 else -1.0))
            if not fired:
                continue
            last = ks[i]
            o = ofi.get(ks[i] // 15)
            fws = {h: fwd(i, h) for h in HORIZONS}
            if not all(fws[h] is not None for h in HORIZONS):
                continue
            # 重叠记录（本网格触发的形态集合）
            ev_sym.setdefault(ks[i], set()).update(p for p, _ in fired)
            for pname, sign in fired:
                ofi_signed = o * sign if o is not None else None
                flow = "with" if (ofi_signed is not None and ofi_signed >= 0.3) else \
                       ("against" if (ofi_signed is not None and ofi_signed <= -0.3)
                        else "neutral")
                rows.append({"sym": bare, "p": pname, "flow": flow,
                             **{f"f{h}": fws[h] * sign for h in HORIZONS}})

    print(f"事件总数 {len(rows)}")
    out = {"per_symbol": {}, "overlap": {}}
    for pname in ("P4", "P5"):
        print(f"\n══ {pname} flow=with 分币种（E[Δmid(h)] sign×bp）══")
        print(f"{'币':<8} {'n':>6} {'次/h':>7} " + "".join(f"{f'f{h}':>14}" for h in HORIZONS))
        rec = {}
        for sym in sorted({r["sym"] for r in rows}):
            sub = [r for r in rows if r["p"] == pname and r["sym"] == sym
                   and r["flow"] == "with"]
            if not sub:
                continue
            cells = []
            cell = {"n": len(sub), "freq_per_h": round(len(sub) / a.hours, 2)}
            for h in HORIZONS:
                st = _stat([r[f"f{h}"] for r in sub])
                if st:
                    cells.append(f"{st['bp']:>+8.3f}({st['t']:+.1f})")
                    cell[f"f{h}"] = st
            rec[sym] = cell
            print(f"{sym:<8} {cell['n']:>6} {cell['freq_per_h']:>7} " + "".join(cells))
        out["per_symbol"][pname] = rec

    # 跨形态重叠（P1/P4/P5 在同一网格共同触发）
    print("\n── 跨形态重叠（±20s 网格内共同触发）──")
    total_ticks = sum(len(v) for v in events.values())
    combos = {}
    for sym, evs in events.items():
        for ts, ps in evs.items():
            key = tuple(sorted(ps))
            combos[key] = combos.get(key, 0) + 1
    for key in sorted(combos, key=lambda k: -combos[k]):
        print(f"  {list(key)}: {combos[key]} 次")
    overlap = {str(k): v for k, v in combos.items()}
    out["overlap"] = {"total_ticks": total_ticks, **overlap}

    OUT.write_text(json.dumps({"hours": a.hours, "symbols": syms, **out},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
