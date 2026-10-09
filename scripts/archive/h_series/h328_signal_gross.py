# -*- coding: utf-8 -*-
"""H328 信号毛边际筛查（taker 口径，无执行选择偏差）。

# 为什么需要它
   h327 实测：现行信号（r60 反转 + 5min 顺势闸 + OFI）**毛边际只有 +0.24~+0.39bp**：
     被动入场毛 −0.729bp（含 ~1bp 逆选择）
     主动入场毛 +0.239bp（真实边际，无选择偏差）
     主动入场净 −3.772bp（4bp taker 费 = 边际的 17 倍）
   ⇒ 调参已到尽头：**任何执行路径都要求信号毛边际 ≫ 成本**。本脚本在同一口径下
     横向筛查候选信号，给出"每笔毛 bp + t 值"，为下一步选信号提供依据。

# 口径（与 h327 taker 通道一致）
   事件：book_ticker 1s 中价，每 60s 一个事件点（币各自）
   信号方向 sign ∈ {+1 做多, −1 做空}；毛边际 = sign × 未来 H 秒中价收益(bp)
   不做任何费用/滑点扣减（那是执行层的事），只回答"信号本身有没有肉"。

# 用法: python scripts/h328_signal_gross.py [--hours 96] [--symbols ETHUSDT,BNBUSDT]
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
OUT = ROOT / "research_l1" / "out" / "h328_signal_gross.json"
HORIZONS = [30, 60, 120, 300]


def read_env_dsn(market: bool) -> str:
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
    return url.replace("/alpha_arena", "/alpha_market") if market else url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=96.0)
    ap.add_argument("--symbols", default="ETHUSDT,BNBUSDT")
    ap.add_argument("--sweep", action="store_true", help="阈值敏感性扫描（h329）")
    a = ap.parse_args()

    import psycopg2

    cur_syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    cm = psycopg2.connect(read_env_dsn(True))
    cm.autocommit = True
    curm = cm.cursor()

    series = {}
    for sym in cur_syms:
        curm.execute("""
            SELECT (event_ts_ms/1000)::bigint AS b,
                   (array_agg(bid_px ORDER BY event_ts_ms DESC))[1] AS bid,
                   (array_agg(ask_px ORDER BY event_ts_ms DESC))[1] AS ask
            FROM asterdex_book_ticker
            WHERE symbol=%s AND event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
              AND bid_px>0 AND ask_px>bid_px
            GROUP BY b ORDER BY b
        """, (sym, a.hours))
        rows = curm.fetchall()
        ks = [int(r[0]) for r in rows]
        mids = [(float(r[1]) + float(r[2])) / 2.0 for r in rows]
        series[sym] = (ks, mids)
        print(f"  {sym}: {len(ks)} 个 1s 点", flush=True)

    # OFI 15s（裸符号）
    ofi_map = {}
    for sym in cur_syms:
        bare = sym[:-4] if sym.endswith("USDT") else sym
        curm.execute("""
            SELECT timestamp, sum(COALESCE(taker_buy_notional,0)), sum(COALESCE(taker_sell_notional,0))
            FROM market_trades_aggregated
            WHERE symbol=%s AND timestamp >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
            GROUP BY timestamp
        """, (bare, a.hours))
        d = {}
        for ts_ms, bu, se in curm.fetchall():
            tot = float(bu or 0) + float(se or 0)
            if tot > 0:
                d[int(ts_ms) // 15000] = (float(bu or 0) - float(se or 0)) / tot
        ofi_map[sym] = d
        print(f"  {sym} OFI 桶 {len(d)}", flush=True)

    def past(ks, mids, i, sec):
        j = bisect.bisect_left(ks, ks[i] - sec)
        if j >= i or ks[i] - ks[j] < sec * 0.9 or mids[j] <= 0:
            return None
        return (mids[i] - mids[j]) / mids[j] * 1e4

    def fwd(ks, mids, i, sec):
        j = bisect.bisect_right(ks, ks[i] + sec) - 1
        if j <= i or ks[j] - ks[i] < sec * 0.9 or mids[i] <= 0:
            return None
        return (mids[j] - mids[i]) / mids[i] * 1e4

    # 候选信号：名字 → 在事件点返回 sign(+1/−1/None)
    def sig_r60_fade(ctx):
        r = ctx["r60"]
        return None if r is None or abs(r) < 2.0 else (-1.0 if r > 0 else 1.0)

    def sig_r60_mom(ctx):
        r = ctx["r60"]
        return None if r is None or abs(r) < 2.0 else (1.0 if r > 0 else -1.0)

    def sig_trend_r60(ctx):
        """现行复合：r60 反转 + 与 5min 趋势同向（h324 结论）"""
        r, t = ctx["r60"], ctx["r300"]
        if r is None or t is None or abs(r) < 2.0 or abs(t) < 20.0:
            return None
        s = -1.0 if r > 0 else 1.0
        return s if (s > 0) == (t > 0) else None

    def sig_r300_mom(ctx):
        t = ctx["r300"]
        return None if t is None or abs(t) < 20.0 else (1.0 if t > 0 else -1.0)

    def sig_r300_fade(ctx):
        t = ctx["r300"]
        return None if t is None or abs(t) < 20.0 else (-1.0 if t > 0 else 1.0)

    def sig_r900_mom(ctx):
        t = ctx["r900"]
        return None if t is None or abs(t) < 40.0 else (1.0 if t > 0 else -1.0)

    def sig_r900_fade(ctx):
        t = ctx["r900"]
        return None if t is None or abs(t) < 40.0 else (-1.0 if t > 0 else 1.0)

    def sig_ofi_mom(ctx):
        o = ctx["ofi"]
        return None if o is None or abs(o) < 0.3 else (1.0 if o > 0 else -1.0)

    def sig_ofi_fade(ctx):
        o = ctx["ofi"]
        return None if o is None or abs(o) < 0.3 else (-1.0 if o > 0 else 1.0)

    def sig_breakout(ctx):
        """突破 5min 区间（顺势追）"""
        hi, lo = ctx["hi300"], ctx["lo300"]
        if hi is None or lo is None:
            return None
        if ctx["px"] >= hi:
            return 1.0
        if ctx["px"] <= lo:
            return -1.0
        return None

    def sig_lowvol_r60(ctx):
        """低波动里的 r60 反转（H281：低波动反转更强）"""
        r, v = ctx["r60"], ctx["vol300"]
        if r is None or v is None or abs(r) < 2.0:
            return None
        if v > 30.0:      # 300s 累计 |ret| 超 30bp 视为高波动 → 不做
            return None
        return -1.0 if r > 0 else 1.0

    SIGNALS = {
        "r60_fade(现行)": sig_r60_fade,
        "r60_mom": sig_r60_mom,
        "trend+r60(复合)": sig_trend_r60,
        "r300_mom": sig_r300_mom,
        "r300_fade": sig_r300_fade,
        "r900_mom": sig_r900_mom,
        "r900_fade": sig_r900_fade,
        "ofi_mom": sig_ofi_mom,
        "ofi_fade": sig_ofi_fade,
        "breakout300": sig_breakout,
        "lowvol_r60": sig_lowvol_r60,
    }
    if a.sweep:
        # [h329] 阈值敏感性：更强信号的毛边际能否越过 taker 门槛（4.2bp）？
        for th in (8.0, 15.0, 25.0, 40.0, 60.0):
            SIGNALS[f"r900_fade@{th:.0f}bp"] = (
                lambda ctx, th=th: (None if ctx["r900"] is None or abs(ctx["r900"]) < th
                                    else (-1.0 if ctx["r900"] > 0 else 1.0)))
        for th in (5.0, 10.0, 20.0, 30.0):
            SIGNALS[f"r300_fade@{th:.0f}bp"] = (
                lambda ctx, th=th: (None if ctx["r300"] is None or abs(ctx["r300"]) < th
                                    else (-1.0 if ctx["r300"] > 0 else 1.0)))
        for th in (0.5, 0.7, 0.85):
            SIGNALS[f"ofi_mom@{th:.2f}"] = (
                lambda ctx, th=th: (None if ctx["ofi"] is None or abs(ctx["ofi"]) < th
                                    else (1.0 if ctx["ofi"] > 0 else -1.0)))
    stats = {name: {h: [] for h in HORIZONS} for name in SIGNALS}

    for sym, (ks, mids) in series.items():
        n = len(ks)
        step = 0
        for i in range(n):
            if ks[i] - step < 60:
                continue
            step = ks[i]
            px = mids[i]
            ctx = {"px": px, "r60": past(ks, mids, i, 60), "r300": past(ks, mids, i, 300),
                   "r900": past(ks, mids, i, 900)}
            if ctx["r60"] is None:
                continue
            b = ks[i] // 15
            ctx["ofi"] = ofi_map.get(sym, {}).get(b - 1)
            # 300s 高低点 + 波动
            j3 = bisect.bisect_left(ks, ks[i] - 300)
            if ks[i] - ks[j3] >= 270:
                seg = mids[j3:i + 1]
                ctx["hi300"], ctx["lo300"] = max(seg), min(seg)
                ctx["vol300"] = sum(abs((seg[t + 1] - seg[t]) / seg[t]) * 1e4
                                    for t in range(len(seg) - 1))
            else:
                ctx["hi300"] = ctx["lo300"] = ctx["vol300"] = None
            fw = {h: fwd(ks, mids, i, h) for h in HORIZONS}
            for name, fn in SIGNALS.items():
                s = fn(ctx)
                if s is None:
                    continue
                for h in HORIZONS:
                    if fw[h] is not None:
                        stats[name][h].append(s * fw[h])

    print(f"\n{'信号':<18} " + " ".join(f"{h}s".rjust(16) for h in HORIZONS))
    print(f"{'':<18} " + " ".join(f"{'毛bp(t)':>16}" for _ in HORIZONS))
    out = {}
    for name in SIGNALS:
        cells = []
        row = {}
        for h in HORIZONS:
            xs = stats[name][h]
            if len(xs) < 50:
                cells.append(f"{'-':>16}")
                row[h] = None
                continue
            m = sum(xs) / len(xs)
            var = sum((x - m) ** 2 for x in xs) / max(len(xs) - 1, 1)
            t = m / math.sqrt(var / len(xs)) if var > 0 else 0.0
            cells.append(f"{m:>+10.3f}({t:>+4.1f})")
            row[h] = {"mean_bp": round(m, 4), "t": round(t, 2), "n": len(xs)}
        print(f"{name:<18} " + " ".join(cells))
        out[name] = row

    OUT.write_text(json.dumps({"hours": a.hours, "symbols": cur_syms, "signals": out},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    print("\n判据：taker 通道需毛边际 > 4.2bp；被动通道需毛边际 > 逆选择(~1bp)。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
