# -*- coding: utf-8 -*-
"""H336 VPIN 门在**已实现**口径下的影子测验：按入场时 VPIN_20 分层的腿级盈亏。

# 为什么需要它
   h333 证明 VPIN_20 高 ⇒ 成交 markout 差（mk30 −0.48 vs −0.01bp）。
   但 markout 不等于现金（h334 已证明 markout 与已实现可以反号）。
   VPIN 门已上线（阈值 0.70），本脚本用成本分层法（h335 同款）算已实现净 bp，
   按**入场时刻**的 VPIN_20 分层：若低 VPIN 腿已实现为正、高 VPIN 为负 ⇒
   门的上线有真金白银依据；若分层无差别 ⇒ 门只能砍量不改善盈亏，需重新评估。

# 用法: python scripts/h336_vpin_realized.py [--hours 96]
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
OUT = ROOT / "research_l1" / "out" / "h336_vpin_realized.json"
WIN = 20


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
                  AND position_id IS NOT NULL AND position_id <> ''
                ORDER BY symbol, position_id, ts
            """, (a.hours * 3600.0,))
            raw = cur.fetchall()

    # VPIN_20 序列（每币）
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
                    ORDER BY timestamp
                """, (bare, a.hours + 1))
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
        if j < 0:
            return None
        return vals[buckets[j]]

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
    print(f"已配对腿 {len(legs)}（有 VPIN 标签）")

    def wmean(sub):
        tot = sum(L["notional"] for L in sub) or 1.0
        return sum(L["net_bp"] * L["notional"] for L in sub) / tot

    vals = sorted(L["vpin"] for L in legs)
    n = len(legs)
    t1, t2 = vals[n // 3], vals[2 * n // 3]
    print(f"VPIN_20 三分位：{t1:.3f} / {t2:.3f}\n")
    print(f"{'VPIN 层':<14} {'n':>6} {'名义%':>7} {'加权bp/腿':>10} {'USD':>10}  中位")
    out = {}
    for lab, lo, hi in (("低", -1.0, t1), ("中", t1, t2), ("高", t2, 2.0)):
        sub = [L for L in legs if lo <= L["vpin"] < hi]
        if not sub:
            continue
        med = sorted(L["net_bp"] for L in sub)[len(sub) // 2]
        usd = sum(L["net_bp"] * L["notional"] for L in sub) / 1e4
        out[lab] = {"n": len(sub), "w_bp": round(wmean(sub), 3),
                    "med_bp": round(med, 3), "usd": round(usd, 2)}
        print(f"{lab:<14} {len(sub):>6} "
              f"{sum(L['notional'] for L in sub)/max(sum(L['notional'] for L in legs),1)*100:>6.1f}% "
              f"{wmean(sub):>+10.3f} {usd:>+10.2f}  {med:+.2f}")

    OUT.write_text(json.dumps({"hours": a.hours, "n": n, "tiers": out, "t1": round(t1, 3), "t2": round(t2, 3)},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
