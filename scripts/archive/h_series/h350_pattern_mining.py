# -*- coding: utf-8 -*-
"""H350 形态/事件研究：30s-5min 时域的市场规律挖掘（高频导向）。

# 中心思想（用户指令）：在 30 秒至 5 分钟、高频的基础上找到**可重复的正期望形态**。
   对每个候选形态做事件研究：触发时刻 t，按形态规定的方向 sign，测未来
   30/60/120 秒的中价收益（bp）。输出：均值 bp、t 值、样本数、每小时触发次数。
   入选标准：|t|≥2.5 且 频率 ≥ 2 次/小时/币（高频硬约束）。
   已证伪或已有结论的形态（r60 反转≈0、15min 级）不再列入。

# 候选形态（全部 30s-5min 可计算，无未来函数）
   P1 pullback    |r60|≥2bp 反转 + 与 300s 趋势(≥15bp)同向（已知 +3.75bp/腿，基准）
   P2 vwap_revert 价格偏离 60s 成交VWAP ≥2bp → 回归
   P3 spike_fade  |r15|≥3bp 尖峰 → 反向
   P4 double_touch 120s 内两次触及 60s 极值未破 → 突破方向
   P5 squeeze_break 60s 波动低于当日 30 分位 且 r15≥2bp → 动量延续
   P6 ofi_fade    |OFI_15s|≥0.7 → 逆流（过度反应回摆）

# 用法: python scripts/h350_pattern_mining.py [--hours 168] [--symbols BTCUSDT,ETHUSDT,BNBUSDT]
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
OUT = ROOT / "research_l1" / "out" / "h350_patterns.json"
HORIZONS = [30, 60, 120]


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
        tts = [int(r[0]) for r in trecs]
        vnum, vden = [0.0], [0.0]
        for r in trecs:
            vnum.append(vnum[-1] + float(r[1]))
            vden.append(vden[-1] + float(r[2]))
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
            return (mids[i] - mids[j]) / mids[j] * 1e4 if j < i and ks[i] - ks[j] >= sec * 0.9 and mids[j] > 0 else None

        def fwd(i, sec):
            j = bisect.bisect_right(ks, ks[i] + sec) - 1
            return (mids[j] - mids[i]) / mids[i] * 1e4 if j > i and ks[j] - ks[i] >= sec * 0.9 else None

        # 日波动 30 分位（squeeze 基准）：用 60s 波动滚动，取当日 30 分位近似=全窗 30 分位
        vol60 = []
        for i in range(120, n):
            seg = mids[i - 120:i + 1]
            v = sum(abs((seg[t + 1] - seg[t]) / seg[t]) * 1e4 for t in range(len(seg) - 1))
            vol60.append(v)
        vol_q30 = sorted(vol60)[int(len(vol60) * 0.3)] if vol60 else 1.0

        last = -1e18
        for i in range(n):
            if ks[i] - last < 20:   # 20s 事件网格（高频挖掘，去重）
                continue
            r15 = past(i, 15)
            r60 = past(i, 60)
            r300 = past(i, 300)
            if r15 is None or r60 is None or r300 is None:
                continue
            last = ks[i]
            sign = None
            # P1 pullback
            if abs(r60) >= 2.0 and abs(r300) >= 15.0:
                s = -1.0 if r60 > 0 else 1.0
                if (s > 0) == (r300 > 0):
                    sign = s
            if sign is None:
                continue
            # P2 vwap_revert：60s VWAP 偏离 ≥2bp
            i1 = bisect.bisect_right(tts, ks[i] - 60)
            i2 = bisect.bisect_right(tts, ks[i])
            den = vden[i2] - vden[i1]
            vwap = (vnum[i2] - vnum[i1]) / den if den > 0 else mids[i]
            dev = (mids[i] - vwap) / vwap * 1e4
            p2 = -1.0 if dev > 0 else 1.0 if abs(dev) >= 2.0 else None
            # P3 spike_fade：|r15|≥3bp → 反向
            p3 = (-1.0 if r15 > 0 else 1.0) if abs(r15) >= 3.0 else None
            # P4 double_touch：120s 内两次触及 60s 极值
            seg = mids[max(0, i - 120):i + 1]
            hi, lo = max(seg), min(seg)
            near_hi = sum(1 for m in seg if m >= hi * (1 - 5e-6))
            near_lo = sum(1 for m in seg if m <= lo * (1 + 5e-6))
            p4 = None
            if near_hi >= 2 and mids[i] >= hi * (1 - 5e-6):
                p4 = 1.0
            elif near_lo >= 2 and mids[i] <= lo * (1 + 5e-6):
                p4 = -1.0
            # P5 squeeze_break：60s 波动低 + 15s 动 ≥2bp → 延续
            segv = mids[max(0, i - 60):i + 1]
            v60 = sum(abs((segv[t + 1] - segv[t]) / segv[t]) * 1e4 for t in range(len(segv) - 1))
            p5 = (1.0 if r15 > 0 else -1.0) if (v60 < vol_q30 and abs(r15) >= 2.0) else None
            # P6 ofi_fade：|OFI|≥0.7 → 逆流
            o = ofi.get(ks[i] // 15)
            p6 = (-1.0 if o > 0 else 1.0) if (o is not None and abs(o) >= 0.7) else None
            for pname, ps in (("P1_pullback", sign), ("P2_vwap_revert", p2),
                              ("P3_spike_fade", p3), ("P4_double_touch", p4),
                              ("P5_squeeze_break", p5), ("P6_ofi_fade", p6)):
                if ps is None:
                    continue
                fws = {h: fwd(i, h) for h in HORIZONS}
                if all(fws[h] is not None for h in HORIZONS):
                    all_rows.append({"sym": bare, "pattern": pname, "s": ps,
                                     **{f"f{h}": fws[h] * ps for h in HORIZONS}})

    print(f"事件样本 {len(all_rows)}")
    from collections import defaultdict
    pat = defaultdict(list)
    for r in all_rows:
        pat[r["pattern"]].append(r)
    print(f"\n{'形态':<16} {'n':>6} {'次/h':>7} " + "".join(f"{'f{h}s':>12}" for h in HORIZONS))
    out = {}
    for pname in sorted(pat):
        rows = pat[pname]
        n = len(rows)
        freq = n / (a.hours * len(syms))
        cells = []
        rec = {"n": n, "freq_per_h_per_sym": round(freq, 2)}
        for h in HORIZONS:
            xs = [r[f"f{h}"] for r in rows]
            m = sum(xs) / n
            var = sum((x - m) ** 2 for x in xs) / max(n - 1, 1)
            t = m / math.sqrt(var / n) if var > 0 else 0.0
            cells.append(f"{m:>+8.3f}({t:+.1f})")
            rec[f"f{h}"] = {"bp": round(m, 3), "t": round(t, 2)}
        out[pname] = rec
        print(f"{pname:<16} {n:>6} {freq:>7.1f} " + "".join(cells))

    OUT.write_text(json.dumps({"hours": a.hours, "symbols": syms, "patterns": out},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    print("入选标准：|t|≥2.5 且 频率≥2次/h/币。P1 为已知基准（+3.75bp/腿已实现口径）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
