# -*- coding: utf-8 -*-
"""组合效果验证（Y23）：learned 门 + mid 浮盈锁，叠加到近 30 天真实成交。

回答"这套修复到底能把这 30 天变成什么样"：
  1. 实际（全量、无门无锁）；
  2. 仅 learned 门（round-17b 口径：up+chg∈[3,6) / chop+pos≥60&chg≥2 / down 拦）；
  3. 仅浮盈锁（H1 全平锁 0.5/0.15）；
  4. **门 + 锁**（当前生产配置）；
  5. 按 tier 拆分（mid 走 ExitPolicy 锁；long 无锁，仅受门约束）。
"""
from __future__ import annotations

import os
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from deep_long_freshness import build_bar_features, load_klines, pick, learned_ok_prod  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
FEE_SIDE = 0.0005
SLIP = 0.0005


def main() -> int:
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        poss = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, side, timeframe_tier, trade_nature, entry_price, close_price,
                   sl_price, size, original_size, peak_pnl_pct, unrealized_pnl,
                   partial_realized_pnl, partial_fee_paid, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '30 days'
            order by opened_at
        """)).fetchall()]
        evs = [dict(r._mapping) for r in c.execute(text("""
            select position_id, quantity, price, created_at
            from position_exit_events
            where event_type='partial_exit_event' and created_at >= now() - interval '40 days'
            order by position_id, created_at
        """)).fetchall()]
    by_pos = defaultdict(list)
    for e in evs:
        if e["quantity"] and e["price"]:
            by_pos[e["position_id"]].append(e)

    h1, d1 = load_klines({p["symbol"] for p in poss})
    feats = {}
    for sym in {p["symbol"] for p in poss}:
        s = pick(h1, sym)
        ds = pick(d1, sym)
        if s is None or ds is None or len(s) < 300 or len(ds) < 70:
            continue
        feats[sym] = (s, build_bar_features(s, ds))

    recs = []
    for p in poss:
        sym = p["symbol"]
        if sym not in feats:
            continue
        s, (reg_arr, pos_arr, chg_arr) = feats[sym]
        entry = float(p["entry_price"] or 0)
        close = float(p["close_price"] or 0)
        sz0 = float(p["original_size"] or p["size"] or 0)
        if entry <= 0 or close <= 0 or sz0 <= 0:
            continue
        ts = int(p["opened_at"].timestamp())
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i == 0 or abs(s[i][0] - ts) > 7200:
            continue
        sl = float(p["sl_price"] or 0)
        sl_pct = abs(entry - sl) / entry * 100 if sl > 0 else 6.0
        sl_pct = min(max(sl_pct, 0.5), 12.0)
        recs.append({
            "symbol": sym, "side": str(p["side"]), "tier": str(p["timeframe_tier"]),
            "entry": entry, "close": close, "sz0": sz0, "notional0": sz0 * entry,
            "peak": float(p["peak_pnl_pct"] or 0) * 100,
            "base_usd": (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                         - float(p["partial_fee_paid"] or 0)),
            "s": s, "i": i, "sl_pct": sl_pct,
            "close_ts": int(p["closed_at"].timestamp()) if p["closed_at"] else s[-1][0],
            "parts": sorted([(int(e["created_at"].timestamp()), float(e["quantity"]),
                              float(e["price"])) for e in by_pos.get(p["id"], [])],
                            key=lambda x: x[0]),
            "allow": learned_ok_prod(reg_arr[i], pos_arr[i], chg_arr[i]),
            "regime": reg_arr[i], "chg": round(chg_arr[i], 2), "pos": round(pos_arr[i], 1),
        })

    def sim_lock(r):
        """H1 全平锁（0.5/0.15），尊重真实部分平仓；返回 USD。"""
        sign = 1.0 if r["side"] == "long" else -1.0
        s, i0 = r["s"], r["i"]
        parts, pi = r["parts"], 0
        realized, fees, qty = 0.0, 0.0, r["sz0"]
        cur_sl = r["entry"] * (1 - sign * r["sl_pct"] / 100.0)
        peak = 0.0
        for k in range(i0, len(s)):
            ts, _o, h, l, c = s[k]
            while pi < len(parts) and parts[pi][0] <= ts:
                _t, q, px = parts[pi]
                q = min(q, qty)
                realized += q * sign * (px - r["entry"])
                fees += q * px * (FEE_SIDE + SLIP) * 2
                qty -= q
                pi += 1
            if qty <= 1e-12:
                break
            hi = sign * (h - r["entry"]) / r["entry"] * 100
            lo = sign * (l - r["entry"]) / r["entry"] * 100
            peak = max(peak, hi)
            if peak >= 0.5:
                cur_roi = sign * (c - r["entry"]) / r["entry"] * 100
                if peak - cur_roi >= 0.15:
                    px = r["entry"] * (1 + sign * (peak - 0.15) / 100.0)
                    realized += qty * sign * (px - r["entry"])
                    fees += qty * px * (FEE_SIDE + SLIP) * 2
                    qty = 0.0
                    break
            if lo <= sign * (cur_sl - r["entry"]) / r["entry"] * 100:
                realized += qty * sign * (cur_sl - r["entry"])
                fees += qty * cur_sl * (FEE_SIDE + SLIP) * 2
                qty = 0.0
                break
            if ts >= r["close_ts"]:
                realized += qty * sign * (r["close"] - r["entry"])
                fees += qty * r["close"] * (FEE_SIDE + SLIP) * 2
                qty = 0.0
                break
        if qty > 1e-12:
            realized += qty * sign * (s[-1][4] - r["entry"])
            fees += qty * s[-1][4] * (FEE_SIDE + SLIP) * 2
        return realized - fees

    def agg(rows_, label):
        if not rows_:
            print(f"  {label:<28} 无样本")
            return
        n = len(rows_)
        usd = sum(r["usd"] for r in rows_)
        pct = [r["usd"] / r["notional0"] * 100 for r in rows_]
        print(f"  {label:<28} n={n:>3} 总USD={usd:>+8.2f} 均值={sum(pct)/n:>+7.3f}% "
              f"胜率={sum(1 for x in pct if x > 0)/n:>5.3f} ≤-2%={sum(1 for x in pct if x <= -2):>2}")

    for tier in ("mid", "long", "ALL"):
        sub = recs if tier == "ALL" else [r for r in recs if r["tier"] == tier]
        if not sub:
            continue
        print(f"\n=== tier={tier} ===")
        # 1) 实际
        for r in sub:
            r["usd"] = r["base_usd"]
        agg(sub, "① 实际（全量/无门/无锁）")
        # 2) 仅门
        gated = [r for r in sub if r["allow"]]
        for r in gated:
            r["usd"] = r["base_usd"]
        agg(gated, "② 仅 learned 门")
        # 3) 仅锁
        for r in sub:
            r["usd"] = sim_lock(r) if r["tier"] == "mid" else r["base_usd"]
        agg(sub, "③ 仅浮盈锁（mid）")
        # 4) 门 + 锁
        for r in gated:
            r["usd"] = sim_lock(r) if r["tier"] == "mid" else r["base_usd"]
        agg(gated, "④ 门 + 锁（当前生产配置）")
        # 5) 门 + 锁 的模式组
        pat = [r for r in gated if r["peak"] >= 0.5 and r["usd"] < 0]
        agg(pat, "   └ 其中「浮盈→亏损」")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
