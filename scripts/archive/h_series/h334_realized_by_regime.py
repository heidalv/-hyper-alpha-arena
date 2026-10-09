# -*- coding: utf-8 -*-
"""H334 按入场 regime 分类的**已实现**盈亏（把 markout 证据兑现成真金白银证据）。

# 目的
   h323 的 markout（+4.4~+5.3bp 中位数）是"成交后 30s 的中价漂移"，不是现金。
   本脚本用移动库存法（h321 同款 FIFO 配对）算出每条腿的**已实现净 bp**，
   再按入场时刻的 5 分钟趋势分类：
     强趋势·顺势（|trend|≥20 且方向与趋势同向）
     强趋势·逆势（|trend|≥20 且方向相反）
     平缓（|trend|<20）
   若"强趋势·顺势"类的已实现也是强正 ⇒ 可部署规则的最后一块证据齐了。

# 用法: python scripts/h334_realized_by_regime.py [--hours 96]
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
OUT = ROOT / "research_l1" / "out" / "h334_realized_regime.json"
TREND_BP, TREND_LB = 20.0, 300.0


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
    ap.add_argument("--ago", type=float, default=0.0, help="窗口前移小时数（交叉验证）")
    a = ap.parse_args()

    import psycopg

    with psycopg.connect(read_env_dsn(False)) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT position_id, symbol, (meta_json->>'side') AS side,
                       (meta_json->>'qty')::float8, (meta_json->>'fill_px')::float8,
                       COALESCE(meta_json->>'exit_path','') AS ep, fee_bp, ts
                FROM lane_ledger
                WHERE lane_id='mm_asterdex' AND event='fill'
                  AND ts >= now() - make_interval(secs => %s)
                  AND ts <  now() - make_interval(secs => %s)
                  AND position_id IS NOT NULL AND position_id <> ''
                ORDER BY symbol, position_id, ts
            """, ((a.hours + a.ago) * 3600.0, a.ago * 3600.0))
            raw = cur.fetchall()
    print(f"账本行 {len(raw)}")

    # 15s 桶中价（趋势口径，与线上 trend_move_bp(mid_hist,20) 一致）
    syms = sorted({r[1] for r in raw})
    m15 = {}
    with psycopg.connect(read_env_dsn(True)) as c:
        with c.cursor() as cur:
            for s in syms:
                bs = s if s.endswith("USDT") else s + "USDT"
                cur.execute("""
                    SELECT ((event_ts_ms/1000)/15)::bigint AS b,
                           (array_agg(bid_px ORDER BY event_ts_ms DESC))[1] AS bid,
                           (array_agg(ask_px ORDER BY event_ts_ms DESC))[1] AS ask
                    FROM asterdex_book_ticker
                    WHERE symbol=%s AND event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                      AND event_ts_ms <  (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                      AND bid_px>0 AND ask_px>bid_px
                    GROUP BY b ORDER BY b
                """, (bs, a.hours + a.ago + 1, a.ago))
                rows = cur.fetchall()
                m15[s] = ([int(r[0]) for r in rows],
                          [(float(r[1]) + float(r[2])) / 2.0 for r in rows])

    def trend_at(s, t):
        ks, ms = m15[s]
        j = bisect.bisect_right(ks, t // 15) - 1
        if j < 0 or (t // 15) - ks[j] > 2:
            return None
        j0 = bisect.bisect_right(ks, t // 15 - TREND_LB // 15) - 1
        if j0 < 0 or ms[j0] <= 0:
            return None
        return (ms[j] - ms[j0]) / ms[j0] * 1e4

    def _tag(sym, ts, side):
        tr = trend_at(sym, int(ts.timestamp()))
        if tr is None:
            return "样本不足"
        if abs(tr) < TREND_BP:
            return "平缓"
        with_trend = (side == "buy" and tr > 0) or (side == "sell" and tr < 0)
        return "强趋势·顺势" if with_trend else "强趋势·逆势"

    # 移动库存成本分层（修正版）：每层记开仓时的 regime 标签，减仓 FIFO 消耗层，
    # 已实现盈亏归到各层所属 regime。被动平仓腿（无 exit_path）按库存符号判定
    # 是加仓还是减仓，而不是一律当开仓（h321 v1 的同一个错，会产生 −37bp 的配对假象）。
    by_pos = defaultdict(list)
    for r in raw:
        by_pos[r[0]].append(r)
    legs = []
    for pos_id, fills in by_pos.items():
        fills.sort(key=lambda r: r[7])
        st_qty = 0.0
        layers = []   # [qty_rem, entry_px, 层方向sign, regime_tag, sym, ts]
        for r in fills:
            side, qty, px, fee, ts = r[2], r[3] or 0.0, r[4] or 0.0, r[6] or 0.0, r[7]
            sgn = 1.0 if side == "buy" else -1.0
            dq = sgn * qty
            if abs(st_qty) < 1e-12:
                st_qty = dq
                if qty > 1e-12:
                    layers.append([qty, px, sgn, _tag(r[1], ts, side), r[1], ts])
                continue
            same_dir = st_qty * dq > 0
            if same_dir:
                st_qty += dq
                if qty > 1e-12:
                    layers.append([qty, px, sgn, _tag(r[1], ts, side), r[1], ts])
                continue
            # 减仓：FIFO 消耗层
            rem = qty
            while rem > 1e-12 and layers:
                L = layers[0]
                take = min(rem, L[0])
                # [h342 符号修正] 已实现用**开仓层的方向**：多头层 (px−entry) 为正；
                # 此前误用平仓腿方向，符号整体反转（100买/101卖 被记成 −100bp ✗）
                raw_bp = (px - L[1]) / L[1] * 1e4 * L[2] if L[1] > 0 else 0.0
                legs.append({"tag": L[3], "net_bp": raw_bp - fee,
                             "notional": take * (L[1] + px)})
                L[0] -= take
                rem -= take
                if L[0] <= 1e-12:
                    layers.pop(0)
            st_qty += dq
            if abs(st_qty) < 1e-12:
                st_qty = 0.0
            elif rem > 1e-12:   # 穿仓：剩余部分反向开仓
                layers.append([rem, px, sgn, _tag(r[1], ts, side), r[1], ts])
    print(f"已配对腿 {len(legs)}")

    cls = {"强趋势·顺势": [], "强趋势·逆势": [], "平缓": [], "样本不足": []}
    for L in legs:
        if L["tag"] in cls:
            cls[L["tag"]].append(L)

    def wmean(sub):
        tot = sum(L["notional"] for L in sub) or 1.0
        return sum(L["net_bp"] * L["notional"] for L in sub) / tot

    print(f"\n{'类别':<14} {'n':>6} {'名义%':>7} {'已实现加权bp/腿':>14} {'已实现USD':>11}  中位")
    out = {}
    for k in ("强趋势·顺势", "强趋势·逆势", "平缓", "样本不足"):
        sub = cls[k]
        if not sub:
            continue
        med = sorted(L["net_bp"] for L in sub)[len(sub) // 2]
        usd = sum(L["net_bp"] * L["notional"] for L in sub) / 1e4
        out[k] = {"n": len(sub), "w_bp": round(wmean(sub), 3),
                  "med_bp": round(med, 3), "usd": round(usd, 2)}
        print(f"{k:<14} {len(sub):>6} {sum(L['notional'] for L in sub)/max(sum(L['notional'] for L in legs),1)*100:>6.1f}% "
              f"{wmean(sub):>+14.3f} {usd:>+11.2f}  {med:+.2f}")

    OUT.write_text(json.dumps({"hours": a.hours, "n": len(legs), "by_class": out},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
