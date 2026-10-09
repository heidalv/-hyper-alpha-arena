# -*- coding: utf-8 -*-
"""H361 形态库扩展：P3/P4/P5 × OFI 流向条件化事件研究。

统一流定律（h355/h358）：30s-5min 形态的期望收益由"当前 OFI 与交易方向是否同向"
决定。P1/P2 已被验证；本脚本把同一条件化应用到 h350 里未入选的 P3/P4/P5，
看是否存在"条件化后转正"的隐藏形态。

  P3 spike_fade   |r15|≥3bp 尖峰 → 反向（fade）
  P4 double_touch 120s 内两次触及 60s 极值 → 突破方向
  P5 squeeze_break 60s 波动低于当日 30 分位 且 |r15|≥2bp → 动量延续

flow 定义（与 h355/h358 一致）：ofi_signed = ofi × sign（交易方向），
with ≥ +0.3 / against ≤ −0.3 / 其余 neutral。
入选标准（预注册）：|t|≥2.5 且 频率 ≥ 2 次/h/币。

用法: python scripts/h361_pattern_flow_conditional.py [--hours 168]
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
OUT = ROOT / "research_l1" / "out" / "h361_p345_flow.json"
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

        # 60s 波动 30 分位（P5 基准）
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
            # P3 spike_fade
            if abs(r15) >= 3.0:
                cands.append(("P3_spike_fade", -1.0 if r15 > 0 else 1.0))
            # P4 double_touch
            seg = mids[max(0, i - 120):i + 1]
            hi, lo = max(seg), min(seg)
            near_hi = sum(1 for m in seg if m >= hi * (1 - 5e-6))
            near_lo = sum(1 for m in seg if m <= lo * (1 + 5e-6))
            if near_hi >= 2 and mids[i] >= hi * (1 - 5e-6):
                cands.append(("P4_double_touch", 1.0))
            elif near_lo >= 2 and mids[i] <= lo * (1 + 5e-6):
                cands.append(("P4_double_touch", -1.0))
            # P5 squeeze_break
            segv = mids[max(0, i - 60):i + 1]
            v60 = sum(abs((segv[t + 1] - segv[t]) / segv[t]) * 1e4
                      for t in range(len(segv) - 1))
            if v60 < vol_q30 and abs(r15) >= 2.0:
                cands.append(("P5_squeeze_break", 1.0 if r15 > 0 else -1.0))
            if not cands:
                continue
            last = ks[i]
            o = ofi.get(ks[i] // 15)
            fws = {h: fwd(i, h) for h in HORIZONS}
            if not all(fws[h] is not None for h in HORIZONS):
                continue
            for pname, sign in cands:
                ofi_signed = o * sign if o is not None else None
                flow = "with" if (ofi_signed is not None and ofi_signed >= 0.3) else \
                       ("against" if (ofi_signed is not None and ofi_signed <= -0.3)
                        else "neutral")
                all_rows.append({"p": pname, "flow": flow,
                                 **{f"f{h}": fws[h] * sign for h in HORIZONS}})

    print(f"总事件 {len(all_rows)}")
    out = {}
    for pname in ("P3_spike_fade", "P4_double_touch", "P5_squeeze_break"):
        rows = [r for r in all_rows if r["p"] == pname]
        print(f"\n── {pname}（n={len(rows)}）──")
        print(f"{'条件':<10} {'n':>7} " + "".join(f"{f'f{h}':>14}" for h in HORIZONS))
        rec = {}
        for key, fn in (("uncond", lambda r: True), ("with", lambda r: r["flow"] == "with"),
                        ("neutral", lambda r: r["flow"] == "neutral"),
                        ("against", lambda r: r["flow"] == "against")):
            sub = [r for r in rows if fn(r)]
            cells = []
            cell = {"n": len(sub)}
            for h in HORIZONS:
                st = _stat([r[f"f{h}"] for r in sub])
                if st:
                    cells.append(f"{st['bp']:>+8.3f}({st['t']:+.1f})")
                    cell[f"f{h}"] = st
            rec[key] = cell
            if cells:
                print(f"{key:<10} {cell['n']:>7} " + "".join(cells))
        out[pname] = rec

    OUT.write_text(json.dumps({"hours": a.hours, "symbols": syms, "patterns": out},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
