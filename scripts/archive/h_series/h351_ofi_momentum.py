# -*- coding: utf-8 -*-
"""H351 OFI 极端·跟随形态的数学模型（h350 P6 反转发现的正式化）。

# 发现（h350）：逆极端 OFI 做 −1.35bp@30s（t=−7.6，强证伪）⇒ 顺极端 OFI
   应为 +1.35bp@30s。本脚本做完整建模：
   条件期望 E[Δmid_{t→t+H} | OFI_t]（顺流方向符号化），阈值 θ 扫描，
   最优持有期 H，逐币频率（高频硬约束：≥2 次/h/币）。

# 用法: python scripts/h351_ofi_momentum.py [--hours 168]
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
OUT = ROOT / "research_l1" / "out" / "h351_ofi_momentum.json"
HORIZONS = [30, 60, 120]
THRESHOLDS = [0.4, 0.5, 0.6, 0.7, 0.8, 0.9]


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=168.0)
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,DOGEUSDT")
    a = ap.parse_args()

    import psycopg

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    rows = []
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

        def fwd(i, sec):
            j = bisect.bisect_right(ks, ks[i] + sec) - 1
            return (mids[j] - mids[i]) / mids[i] * 1e4 if j > i and ks[j] - ks[i] >= sec * 0.9 else None

        last = -1e18
        for i in range(n):
            if ks[i] - last < 20:
                continue
            o = ofi.get(ks[i] // 15)
            if o is None:
                continue
            last = ks[i]
            fws = {h: fwd(i, h) for h in HORIZONS}
            if not all(fws[h] is not None for h in HORIZONS):
                continue
            s = 1.0 if o > 0 else -1.0
            rows.append({"sym": bare, "ofi": o, "s": s,
                         **{f"f{h}": fws[h] * s for h in HORIZONS}})

    print(f"事件样本 {len(rows)}（20s 网格，5 币）")
    print(f"\n{'阈值θ':>6} {'n':>6} {'次/h/币':>8} " + "".join(f"{'f{h}s':>13}" for h in HORIZONS))
    out = {}
    for th in THRESHOLDS:
        sub = [r for r in rows if abs(r["ofi"]) >= th]
        if len(sub) < 100:
            continue
        freq = len(sub) / (a.hours * len(syms))
        cells = []
        rec = {"n": len(sub), "freq_per_h_per_sym": round(freq, 2)}
        for h in HORIZONS:
            xs = [r[f"f{h}"] for r in sub]
            m = sum(xs) / len(xs)
            var = sum((x - m) ** 2 for x in xs) / max(len(xs) - 1, 1)
            t = m / math.sqrt(var / len(xs)) if var > 0 else 0.0
            cells.append(f"{m:>+8.3f}({t:+.1f})")
            rec[f"f{h}"] = {"bp": round(m, 3), "t": round(t, 2)}
        out[str(th)] = rec
        print(f"{th:>6} {len(sub):>6} {freq:>8.1f} " + "".join(cells))

    OUT.write_text(json.dumps({"hours": a.hours, "symbols": syms, "sweep": out},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    print("数学形式：E[Δmid| |OFI|≥θ]（顺流符号化）。taker 成本 4.2bp 为主动口径门槛；")
    print("被动口径门槛 = 逆选择(~0.3bp)。高频硬约束：≥2 次/h/币。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
