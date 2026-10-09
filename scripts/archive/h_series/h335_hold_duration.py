# -*- coding: utf-8 -*-
"""H335 持仓时长纪律：已实现盈亏 × 持仓时长分布（Menkveld 2013 对照）。

# 文献锚
   Menkveld (2013 JFM)：一家大型 HFT 做市商的逐笔分解——**<5 秒持仓 +€0.45/笔、
   >5 秒持仓 −€1.13/笔**，持仓时长是逆选择的核心开关（逆选择在持仓中累积）。
   我们实测中位持时 ≈46s、衰减宽限 300s ⇒ 大概率落在">5s 亏损区"。
   本脚本用成本分层法（h334 同款）算出每条腿的已实现净 bp 与持仓秒数，
   按时长桶分组：若短桶显著优于长桶 ⇒ 出口要往短端压（硬超时/加快衰减）。

# 用法: python scripts/h335_hold_duration.py [--hours 96] [--ago 0]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h335_hold_duration.json"
BUCKETS = [(0.0, 5.0, "<5s"), (5.0, 15.0, "5-15s"), (15.0, 30.0, "15-30s"),
           (30.0, 60.0, "30-60s"), (60.0, 120.0, "60-120s"),
           (120.0, 300.0, "120-300s"), (300.0, 1e18, ">300s")]


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
    ap.add_argument("--ago", type=float, default=0.0)
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

    # 成本分层（h334 同款）：每条腿 = 开仓层 + 减仓成交
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
                # [h342 符号修正] 用开仓层方向（此前误用平仓腿方向，符号整体反转）
                raw_bp = (px - L[1]) / L[1] * 1e4 * L[2] if L[1] > 0 else 0.0
                hold = float(ts.timestamp() - L[3].timestamp())
                legs.append({"net_bp": raw_bp - fee,
                             "notional": take * (L[1] + px), "hold_s": hold})
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

    print(f"\n{'时长桶':<10} {'n':>6} {'名义%':>7} {'加权bp/腿':>10} {'USD':>10}  中位")
    out = {}
    for lo, hi, lab in BUCKETS:
        sub = [L for L in legs if lo <= L["hold_s"] < hi]
        if not sub:
            continue
        med = sorted(L["net_bp"] for L in sub)[len(sub) // 2]
        usd = sum(L["net_bp"] * L["notional"] for L in sub) / 1e4
        out[lab] = {"n": len(sub), "w_bp": round(wmean(sub), 3),
                    "med_bp": round(med, 3), "usd": round(usd, 2)}
        print(f"{lab:<10} {len(sub):>6} "
              f"{sum(L['notional'] for L in sub)/max(sum(L['notional'] for L in legs),1)*100:>6.1f}% "
              f"{wmean(sub):>+10.3f} {usd:>+10.2f}  {med:+.2f}")

    # 中位持仓
    holds = sorted(L["hold_s"] for L in legs)
    print(f"\n持仓中位 {holds[len(holds)//2]:.0f}s   90 分位 {holds[int(len(holds)*0.9)]:.0f}s")

    OUT.write_text(json.dumps({"hours": a.hours, "ago": a.ago, "n": len(legs),
                               "buckets": out}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
