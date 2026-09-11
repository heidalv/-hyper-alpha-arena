# -*- coding: utf-8 -*-
"""浮盈锁形态对比（Y14）：固定锁 vs 按峰值比例锁，分 tier，总 USD 口径。

基线 = 实际总 USD（含部分平仓实盈）；加锁 = 沿真实路径执行真实部分平仓，
锁触发时以锁价全平剩余。锁形态：
  F05: 固定 peak-0.15（激活 0.5%）
  P50: 比例 锁 peak×0.50（激活 0.5%）
  P60: 比例 锁 peak×0.60（激活 0.5%）
  P70: 比例 锁 peak×0.70（激活 0.5%）
  LAD: 阶梯 0.5→0.3 / 1.0→0.6 / 2.0→1.3 / 3.0→2.0 / 5.0→3.5
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET_URL = os.getenv("MARKET_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
OUT = ROOT / "data" / "lock_shape.json"

FEE_SIDE = 0.0005
SLIP_SIDE = 0.0005


def load_trades(days=30):
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        poss = [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, side, timeframe_tier, entry_price, close_price, sl_price,
                   size, original_size, peak_pnl_pct, unrealized_pnl, partial_realized_pnl,
                   partial_fee_paid, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '{int(days)} days'
            order by opened_at
        """)).fetchall()]
        evs = [dict(r._mapping) for r in c.execute(text("""
            select position_id, quantity, price, created_at
            from position_exit_events
            where event_type='partial_exit_event' and created_at >= now() - interval '40 days'
            order by position_id, created_at
        """)).fetchall()]
    return poss, evs


def load_klines(symbols):
    h1 = defaultdict(list)
    eng = create_engine(MARKET_URL)
    with eng.connect() as c:
        c.execute(text("set statement_timeout='900000'"))
        for exch in ("asterdex", "binance"):
            for s, ts, o, h, l, cl in c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price
                from crypto_klines where period='1h' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": list(symbols)}).fetchall():
                h1[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))
    return h1


def pick(series, sym, ts):
    for ex in ("asterdex", "binance"):
        v = series.get((ex, sym))
        if v and len(v) > 200 and v[0][0] <= ts <= v[-1][0] + 86400:
            return v
    return None


def lock_level(shape, peak_pct):
    """返回锁定价位（价格 % 浮盈）；None = 未激活。"""
    if shape == "BASE":
        return None
    if shape == "F05":
        return (peak_pct - 0.15) if peak_pct >= 0.5 else None
    if shape.startswith("P"):
        frac = float(shape[1:]) / 100.0
        return (peak_pct * frac) if peak_pct >= 0.5 else None
    if shape == "LAD":
        for act, lock in ((5.0, 3.5), (3.0, 2.0), (2.0, 1.3), (1.0, 0.6), (0.5, 0.3)):
            if peak_pct >= act:
                return lock
        return None
    return None


def main() -> int:
    poss, evs = load_trades(30)
    by_pos = defaultdict(list)
    for e in evs:
        if e["quantity"] and e["price"]:
            by_pos[e["position_id"]].append(e)
    h1 = load_klines({p["symbol"] for p in poss})

    recs = []
    for p in poss:
        entry = float(p["entry_price"] or 0)
        close = float(p["close_price"] or 0)
        sz0 = float(p["original_size"] or p["size"] or 0)
        if entry <= 0 or close <= 0 or sz0 <= 0:
            continue
        ts = int(p["opened_at"].timestamp())
        s = pick(h1, p["symbol"], ts)
        if not s:
            continue
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i == 0 or abs(s[i][0] - ts) > 7200:
            continue
        notional0 = sz0 * entry
        total_usd = (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                     - float(p["partial_fee_paid"] or 0))
        sl = float(p["sl_price"] or 0)
        sl_pct = abs(entry - sl) / entry * 100 if sl > 0 else 6.0
        sl_pct = min(max(sl_pct, 0.5), 12.0)
        recs.append({
            "id": p["id"], "symbol": p["symbol"], "side": str(p["side"]),
            "tier": str(p["timeframe_tier"]), "entry": entry, "close": close, "sz0": sz0,
            "notional0": notional0, "peak": float(p["peak_pnl_pct"] or 0) * 100,
            "base_usd": total_usd, "base_pct": total_usd / notional0 * 100,
            "s": s, "i": i, "sl_pct": sl_pct,
            "close_ts": int(p["closed_at"].timestamp()) if p["closed_at"] else s[-1][0],
            "parts": sorted([(int(e["created_at"].timestamp()), float(e["quantity"]),
                              float(e["price"])) for e in by_pos.get(p["id"], [])],
                            key=lambda x: x[0]),
            "fee_usd": float(p["partial_fee_paid"] or 0),
        })

    def sim(r, shape):
        if shape == "BASE":
            return r["base_usd"]
        sign = 1.0 if r["side"] == "long" else -1.0
        s, i0 = r["s"], r["i"]
        parts = r["parts"]
        pi = 0
        realized = 0.0
        fees = 0.0
        qty = r["sz0"]
        cur_sl = r["entry"] * (1 - sign * r["sl_pct"] / 100.0)
        peak = 0.0
        for k in range(i0, len(s)):
            ts, _o, h, l, c = s[k]
            while pi < len(parts) and parts[pi][0] <= ts:
                _t, q, px = parts[pi]
                q = min(q, qty)
                realized += q * sign * (px - r["entry"])
                fees += q * px * (FEE_SIDE + SLIP_SIDE) * 2
                qty -= q
                pi += 1
            if qty <= 1e-12:
                break
            hi = sign * (h - r["entry"]) / r["entry"] * 100
            lo = sign * (l - r["entry"]) / r["entry"] * 100
            peak = max(peak, hi)
            lvl = lock_level(shape, peak)
            if lvl is not None:
                cand = r["entry"] * (1 + sign * lvl / 100.0)
                if (r["side"] == "long" and cand > cur_sl) or (r["side"] == "short" and cand < cur_sl):
                    cur_sl = cand
            if lo <= sign * (cur_sl - r["entry"]) / r["entry"] * 100:
                px = cur_sl
                realized += qty * sign * (px - r["entry"])
                fees += qty * px * (FEE_SIDE + SLIP_SIDE) * 2
                qty = 0.0
                break
            if ts >= r["close_ts"]:
                realized += qty * sign * (r["close"] - r["entry"])
                fees += qty * r["close"] * (FEE_SIDE + SLIP_SIDE) * 2
                qty = 0.0
                break
        if qty > 1e-12:
            realized += qty * sign * (s[-1][4] - r["entry"])
            fees += qty * s[-1][4] * (FEE_SIDE + SLIP_SIDE) * 2
        return realized - fees

    shapes = ["BASE", "F05", "P50", "P60", "P70", "LAD"]
    res = {}
    for tier in ("mid", "long", "ALL"):
        sub = recs if tier == "ALL" else [r for r in recs if r["tier"] == tier]
        if not sub:
            continue
        print(f"\n=== tier={tier} n={len(sub)} ===")
        print(f"{'形态':<8}{'均值%':>9}{'中位%':>8}{'胜率':>7}{'总USD':>10}{'≤-2%':>7}{'模式组':>9}")
        pat = [r for r in sub if r["peak"] >= 0.5 and r["base_usd"] < 0]
        for shape in shapes:
            usds = [sim(r, shape) for r in sub]
            pcts = [u / r["notional0"] * 100 for u, r in zip(usds, sub)]
            pat_mean = (sum(sim(r, shape) / r["notional0"] * 100 for r in pat) / len(pat)
                        if pat else 0)
            res[f"{tier}|{shape}"] = {
                "n": len(sub), "mean": round(sum(pcts) / len(pcts), 3),
                "median": round(st.median(pcts), 3),
                "win": round(sum(1 for x in pcts if x > 0) / len(pcts), 3),
                "usd": round(sum(usds), 2),
                "bad": sum(1 for x in pcts if x <= -2),
                "pat_mean": round(pat_mean, 3),
            }
            v = res[f"{tier}|{shape}"]
            print(f"{shape:<8}{v['mean']:>+9.3f}{v['median']:>+8.3f}{v['win']:>7.3f}"
                  f"{v['usd']:>+10.2f}{v['bad']:>7}{v['pat_mean']:>+9.3f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(), "res": res,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
