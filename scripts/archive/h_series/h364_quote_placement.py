# -*- coding: utf-8 -*-
"""H364 挂单位置模型：P1 薄流信号下，挂单距离 d 的成交率 × 每腿已实现 bp。

问题（目标③组合的最后一环）：事件研究给的是信号侧 E[Δmid|状态]（h355/h360），
实盘多一个"挂单位置"维度——d 太贴 ⇒ 成交多但逆选择重；d 太远 ⇒ 成交少。
本脚本用 1s 盘口/中价路径做触及成交模型（best bid 触及买单价即成交），
扫 d ∈ {0.25,0.5,1,2,3,5}bp × 出口（tp30/stop40/to120），输出：
  fill_rate、每腿已实现 bp（成交口径）、每次挂单期望 bp（fill_rate×每腿 bp）、
  每币每小时成交数（高频可行性）。

口径：信号=P1 薄流（h355：|r60|≥2bp 逆 300s 趋势≥15bp，flow≠against）。
买单价 = mid×(1−d/1e4)、卖单价 = mid×(1+d/1e4)。触及=窗口内 best_bid ≤ 买单价
（或 best_ask ≥ 卖单价）。成交价=挂单价，退出沿 1s 中价路径 tp30/stop40/to120。
（触及模型略乐观——不模拟队列优先权；排序对比仍有效。）

用法: python scripts/h364_quote_placement.py [--hours 168]
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
OUT = ROOT / "research_l1" / "out" / "h364_quote_placement.json"
DISTS = [0.25, 0.5, 1.0, 2.0, 3.0, 5.0]
TP, STOP, TO = 30.0, 40.0, 120


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
    # 每事件：{bids(1s), asks(1s), mids(1s), i0, sign}
    events = []

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
        bids = [float(r[1]) for r in recs]
        asks = [float(r[2]) for r in recs]
        mids = [(b + s) / 2.0 for b, s in zip(bids, asks)]
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

        last = -1e18
        for i in range(n):
            if ks[i] - last < 20:
                continue
            r60 = past(i, 60)
            r300 = past(i, 300)
            if r60 is None or r300 is None:
                continue
            if abs(r60) < 2.0 or abs(r300) < 15.0 or (r60 > 0) == (r300 > 0):
                continue
            last = ks[i]
            sign = 1.0 if r300 > 0 else -1.0
            o = ofi.get(ks[i] // 15)
            ofi_signed = o * sign if o is not None else None
            if ofi_signed is not None and ofi_signed <= -0.3:
                continue  # flow≠against
            events.append({"bids": bids, "asks": asks, "mids": mids,
                           "ks": ks, "i0": i, "sign": sign})

    print(f"P1 薄流事件 {len(events)}")
    if not events:
        return 1

    results = {}
    print(f"\n{'d(bp)':>7} {'尝试':>7} {'成交':>7} {'fill%':>7} "
          f"{'bp/成交腿':>10} {'bp/挂单':>9} {'成交/h/币':>10}")
    for d in DISTS:
        filled = 0
        pnl = []
        for ev in events:
            bids, asks, mids, ks, i0, sign = (ev["bids"], ev["asks"], ev["mids"],
                                              ev["ks"], ev["i0"], ev["sign"])
            m0 = mids[i0]
            if sign > 0:
                qpx = m0 * (1 - d / 1e4)
                hit = [j for j in range(i0 + 1, min(i0 + TO, len(bids)))
                       if bids[j] > 0 and bids[j] <= qpx]
            else:
                qpx = m0 * (1 + d / 1e4)
                hit = [j for j in range(i0 + 1, min(i0 + TO, len(asks)))
                       if asks[j] > 0 and asks[j] >= qpx]
            if not hit:
                continue
            filled += 1
            j0 = hit[0]
            # 退出：沿中价 tp30/stop40/timeout120（自成交时起）
            base = qpx
            exit_bp = None
            for j in range(j0, min(j0 + TO, len(mids))):
                ret = (mids[j] - base) / base * 1e4 * sign
                if ret >= TP:
                    exit_bp = TP
                    break
                if ret <= -STOP:
                    exit_bp = -STOP
                    break
                if ks[j] - ks[j0] >= TO:
                    exit_bp = ret
                    break
            if exit_bp is None:
                ret = (mids[min(j0 + TO, len(mids)) - 1] - base) / base * 1e4 * sign
                exit_bp = ret
            pnl.append(exit_bp)
        n_att = len(events)
        fr = filled / n_att
        bp_fill = sum(pnl) / max(filled, 1)
        bp_att = bp_fill * fr
        fills_h = filled / (a.hours * len(syms))
        results[str(d)] = {"attempts": n_att, "fills": filled,
                           "fill_rate": round(fr, 3), "bp_per_fill": round(bp_fill, 3),
                           "bp_per_attempt": round(bp_att, 4),
                           "fills_per_h_per_sym": round(fills_h, 1)}
        print(f"{d:>7} {n_att:>7} {filled:>7} {fr*100:>6.1f}% "
              f"{bp_fill:>+10.3f} {bp_att:>+9.4f} {fills_h:>10.1f}")

    OUT.write_text(json.dumps({"hours": a.hours, "symbols": syms, "n_events": len(events),
                               "tp": TP, "stop": STOP, "timeout": TO,
                               "by_distance": results}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n已存 {OUT}")
    print("注：触及模型（best bid/ask 触价即成交）略乐观，不模拟队列优先权；"
          "bp/成交腿含 tp30/stop40/to120 路径。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
