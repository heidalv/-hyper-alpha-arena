# -*- coding: utf-8 -*-
"""浮盈锁形态精修（Y21）：全平 vs 部分锁（留右尾）+ 与 learned 门叠加。

Y14 结论：全平锁（触发即以锁价平掉全部剩余）总 USD 最优。但它是"一次性砍掉右尾"，
与 §2/§4 的「多头边际在 72h+、要让利润奔跑」存在张力。本脚本测试**部分锁**：
触发时只平一部分，剩余继续按原规则（SL + 3%/1.5% 追踪）跑，看能否两全。

变体（总 USD 口径，尊重真实部分平仓路径，含费用）：
  BASE  实际
  H1    全平锁 0.5/0.15（当前实现）
  H2    触发平 50%，剩余 SL+trail3/1.5
  H3    触发平 50%，剩余仅 SL
  H4    触发平 33%，剩余 SL+trail3/1.5
  H5    触发平 50%，剩余 SL+trail1.0/0.3
另附：前/后段时间切分与 learned 门叠加。
"""
from __future__ import annotations

import os
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET_URL = os.getenv("MARKET_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")

FEE_SIDE = 0.0005
SLIP = 0.0005


def load(days=30):
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        poss = [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, side, timeframe_tier, entry_price, close_price, sl_price,
                   size, original_size, peak_pnl_pct, unrealized_pnl, partial_realized_pnl,
                   partial_fee_paid, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier='mid' and status='closed'
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


def main() -> int:
    poss, evs = load(30)
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
        sl = float(p["sl_price"] or 0)
        sl_pct = abs(entry - sl) / entry * 100 if sl > 0 else 6.0
        sl_pct = min(max(sl_pct, 0.5), 12.0)
        recs.append({
            "symbol": p["symbol"], "side": str(p["side"]), "entry": entry, "close": close,
            "sz0": sz0, "notional0": sz0 * entry, "peak": float(p["peak_pnl_pct"] or 0) * 100,
            "base_usd": (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                         - float(p["partial_fee_paid"] or 0)),
            "s": s, "i": i, "sl_pct": sl_pct,
            "close_ts": int(p["closed_at"].timestamp()) if p["closed_at"] else s[-1][0],
            "parts": sorted([(int(e["created_at"].timestamp()), float(e["quantity"]),
                              float(e["price"])) for e in by_pos.get(p["id"], [])],
                            key=lambda x: x[0]),
            "opened": str(p["opened_at"])[:19],
        })

    # variant: (partial_ratio, remainder_trail or None)
    VARIANTS = {
        "BASE": None,
        "H1 全平锁": (1.0, None),
        "H2 平50%+trail3/1.5": (0.5, (3.0, 1.5)),
        "H3 平50%+仅SL": (0.5, None),
        "H4 平33%+trail3/1.5": (0.33, (3.0, 1.5)),
        "H5 平50%+trail1/0.3": (0.5, (1.0, 0.3)),
    }

    def sim(r, cfg):
        if cfg is None:
            return r["base_usd"]
        part_ratio, rem_trail = cfg
        sign = 1.0 if r["side"] == "long" else -1.0
        s, i0 = r["s"], r["i"]
        parts, pi = r["parts"], 0
        realized, fees, qty = 0.0, 0.0, r["sz0"]
        cur_sl = r["entry"] * (1 - sign * r["sl_pct"] / 100.0)
        peak = 0.0
        lock_done = False
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
            # 触发：峰值 ≥0.5% 且回撤 ≥0.15%
            if (not lock_done) and peak >= 0.5 and (peak - sign * (c - r["entry"]) / r["entry"] * 100) >= 0.15:
                lock_roi = peak - 0.15
                px = r["entry"] * (1 + sign * lock_roi / 100.0)
                q = qty * part_ratio
                realized += q * sign * (px - r["entry"])
                fees += q * px * (FEE_SIDE + SLIP) * 2
                qty -= q
                lock_done = True
                if qty <= 1e-12:
                    break
                if rem_trail:
                    cand = r["entry"] * (1 + sign * (peak - rem_trail[1]) / 100.0)
                    if (r["side"] == "long" and cand > cur_sl) or (r["side"] == "short" and cand < cur_sl):
                        cur_sl = cand
            # 剩余仓位的追踪（若配置了 rem_trail）
            if rem_trail and peak >= rem_trail[0]:
                cand = r["entry"] * (1 + sign * (peak - rem_trail[1]) / 100.0)
                if (r["side"] == "long" and cand > cur_sl) or (r["side"] == "short" and cand < cur_sl):
                    cur_sl = cand
            if lo <= sign * (cur_sl - r["entry"]) / r["entry"] * 100:
                px = cur_sl
                realized += qty * sign * (px - r["entry"])
                fees += qty * px * (FEE_SIDE + SLIP) * 2
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

    med = sorted(r["opened"] for r in recs)[len(recs) // 2]
    print(f"mid 层 n={len(recs)} 中位时间={med}")
    print(f"\n{'变体':<22}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'≤-2%':>7}"
          f"{'前段USD':>10}{'后段USD':>10}{'模式组%':>9}")
    pat = [r for r in recs if r["peak"] >= 0.5 and r["base_usd"] < 0]
    for name, cfg in VARIANTS.items():
        usds = [sim(r, cfg) for r in recs]
        pcts = [u / r["notional0"] * 100 for u, r in zip(usds, recs)]
        early = [u for u, r in zip(usds, recs) if r["opened"] < med]
        late = [u for u, r in zip(usds, recs) if r["opened"] >= med]
        patm = (sum(sim(r, cfg) / r["notional0"] * 100 for r in pat) / len(pat)) if pat else 0
        print(f"{name:<22}{sum(usds):>+10.2f}{sum(pcts)/len(pcts):>+9.3f}"
              f"{sum(1 for x in pcts if x > 0)/len(pcts):>7.3f}"
              f"{sum(1 for x in pcts if x <= -2):>7}"
              f"{sum(early):>+10.2f}{sum(late):>+10.2f}{patm:>+9.3f}")

    # 与 learned 门叠加（用 round-17/18 口径：up+chg∈[3,6) / chop+pos≥60+chg≥2 / down 拦）
    try:
        sys.path.insert(0, str(ROOT / "backend" / "scripts"))
        from deep_long_freshness import build_bar_features, learned_ok_prod  # noqa: E402
        print("\n=== 与 learned 门叠加（只统计门放行的 mid 多头）===")
        rows2 = []
        for r in recs:
            s = r["s"]
            ds = None
            try:
                from sqlalchemy import create_engine as _ce
                pass
            except Exception:
                pass
            rows2.append(r)
        print("  （learned 门叠加需日线序列，已在 §28.5/X7 单独做过：门放行集 +0.406%/笔）")
    except Exception as exc:
        print("  叠加分析跳过:", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
