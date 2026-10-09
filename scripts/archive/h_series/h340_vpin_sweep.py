# -*- coding: utf-8 -*-
"""H340 VPIN 阈值扫描：各阈值下"只保留低 VPIN 入场"的已实现盈亏（h336 的连续版）。

# 目的
   h336 证明 VPIN 分层有单调的已实现梯度。本脚本扫阈值 T ∈ [0.55, 0.80]：
   对每个 T，保留"入场时 VPIN_20 ≤ T"的腿，输出其已实现加权 bp/腿、USD、中位、腿数。
   两条用途：① 找已实现最优阈值（线上现 0.65）；② 为 12h 试跑判决提供
   "若门有效，时代的每腿已实现应落在哪一档"的基准线。

# 用法: python scripts/h340_vpin_sweep.py [--hours 96]
"""
from __future__ import annotations

import argparse
import bisect
import json
import pathlib
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h340_vpin_sweep.json"
WIN = 20
THRESHOLDS = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 2.0]


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
    ap.add_argument("--ago", type=float, default=0.0, help="窗口前移小时数（跨时代稳健性）")
    a = ap.parse_args()

    import psycopg

    with psycopg.connect(read_env_dsn(False)) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT position_id, symbol, (meta_json->>'side') AS side,
                       (meta_json->>'qty')::float8, (meta_json->>'fill_px')::float8,
                       fee_bp, ts
                FROM lane_ledger
                WHERE lane_id='mm_asterdex' AND event='fill'
                  AND ts >= now() - make_interval(secs => %s)
                  AND ts <  now() - make_interval(secs => %s)
                  AND position_id IS NOT NULL AND position_id <> ''
                ORDER BY symbol, position_id, ts
            """, ((a.hours + a.ago) * 3600.0, a.ago * 3600.0))
            raw = cur.fetchall()

    syms = sorted({r[1] for r in raw})
    vpin = {}
    with psycopg.connect(read_env_dsn(True)) as c:
        with c.cursor() as cur:
            for s in syms:
                bs = s if s.endswith("USDT") else s + "USDT"
                bare = bs[:-4] if bs.endswith("USDT") else bs
                cur.execute("""
                    SELECT timestamp, COALESCE(taker_buy_notional,0), COALESCE(taker_sell_notional,0)
                    FROM market_trades_aggregated
                    WHERE symbol=%s AND timestamp >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                      AND timestamp <  (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                    ORDER BY timestamp
                """, (bare, a.hours + a.ago + 1, a.ago))
                orows = cur.fetchall()
                bmap = {}
                for ts_ms, bn, sn in orows:
                    tot = float(bn) + float(sn)
                    if tot > 0:
                        bmap[int(ts_ms) // 15000] = abs(float(bn) - float(sn)) / tot
                buckets = sorted(bmap)
                vals = {}
                for i, b in enumerate(buckets):
                    lo = max(0, i - WIN + 1)
                    vals[b] = sum(bmap[buckets[j]] for j in range(lo, i + 1)) / (i - lo + 1)
                vpin[s] = (buckets, vals)

    def vpin_at(s, t):
        buckets, vals = vpin[s]
        j = bisect.bisect_right(buckets, t // 15) - 1
        return vals[buckets[j]] if j >= 0 else None

    by_pos = defaultdict(list)
    for r in raw:
        by_pos[r[0]].append(r)
    legs = []
    for pos_id, fills in by_pos.items():
        fills.sort(key=lambda r: r[6])
        st_qty = 0.0
        layers = []   # [qty_rem, entry_px, 层方向sign, ts]
        for r in fills:
            side, qty, px, fee, ts = r[2], r[3] or 0.0, r[4] or 0.0, r[5] or 0.0, r[6]
            sgn = 1.0 if side == "buy" else -1.0
            dq = sgn * qty
            if abs(st_qty) < 1e-12:
                st_qty = dq
                if qty > 1e-12:
                    layers.append([qty, px, sgn, ts])
                continue
            if st_qty * dq > 0:
                st_qty += dq
                if qty > 1e-12:
                    layers.append([qty, px, sgn, ts])
                continue
            rem = qty
            while rem > 1e-12 and layers:
                L = layers[0]
                take = min(rem, L[0])
                # [h342 符号修正] 用开仓层方向
                raw_bp = (px - L[1]) / L[1] * 1e4 * L[2] if L[1] > 0 else 0.0
                vp = vpin_at(r[1], int(L[3].timestamp()))
                if vp is not None:
                    legs.append({"net_bp": raw_bp - fee,
                                 "notional": take * (L[1] + px), "vpin": vp})
                L[0] -= take
                rem -= take
                if L[0] <= 1e-12:
                    layers.pop(0)
            st_qty += dq
            if abs(st_qty) < 1e-12:
                st_qty = 0.0
            elif rem > 1e-12:
                layers.append([rem, px, sgn, ts])
    print(f"已配对腿 {len(legs)}")

    def wmean(sub):
        tot = sum(L["notional"] for L in sub) or 1.0
        return sum(L["net_bp"] * L["notional"] for L in sub) / tot

    print(f"\n{'阈值 T':<8} {'n':>6} {'名义%':>7} {'加权bp/腿':>10} {'USD':>10}  中位")
    out = {}
    for T in THRESHOLDS:
        sub = [L for L in legs if L["vpin"] <= T]
        if not sub:
            continue
        med = sorted(L["net_bp"] for L in sub)[len(sub) // 2]
        usd = sum(L["net_bp"] * L["notional"] for L in sub) / 1e4
        lab = f"{T:.2f}" if T < 2.0 else "off"
        out[lab] = {"n": len(sub), "w_bp": round(wmean(sub), 3),
                    "med_bp": round(med, 3), "usd": round(usd, 2)}
        print(f"{lab:<8} {len(sub):>6} "
              f"{sum(L['notional'] for L in sub)/max(sum(L['notional'] for L in legs),1)*100:>6.1f}% "
              f"{wmean(sub):>+10.3f} {usd:>+10.2f}  {med:+.2f}")

    OUT.write_text(json.dumps({"hours": a.hours, "n": len(legs), "sweep": out},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
