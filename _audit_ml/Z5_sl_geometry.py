# -*- coding: utf-8 -*-
"""风险几何 × 门 的交互（Z5）：门放行子集上收紧 SL 是否有效？

背景：§30.3 在**全量**上测过 SL 宽度（越紧越差），但那是未过滤的旧信号流。
门放行的是动量延续型入场——这类仓位要么快速走开、要么快速失败，理论上
**更紧的 SL** 可能反而更好（Y24 已证「锁」不行，但那是止盈侧；本轮测止损侧）。

方法：沿真实路径（尊重真实部分平仓），SL ∈ {1.5,2,3,4,6}%，总 USD 口径，
分「门放行 / 门拦截 / 全量」，并做逐月切分 + 最优候选的 bootstrap。
"""
from __future__ import annotations

import os
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from deep_long_freshness import build_bar_features, load_klines, pick, learned_ok_prod  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET_URL = os.getenv("MARKET_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
FEE_SIDE = 0.0005
SLIP = 0.0005


def load(days=75):
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
            where event_type='partial_exit_event' and created_at >= now() - interval '90 days'
            order by position_id, created_at
        """)).fetchall()]
    return poss, evs


def main() -> int:
    poss, evs = load(75)
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
        recs.append({
            "symbol": sym, "side": str(p["side"]), "entry": entry, "close": close,
            "sz0": sz0, "notional0": sz0 * entry,
            "base_usd": (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                         - float(p["partial_fee_paid"] or 0)),
            "s": s, "i": i,
            "close_ts": int(p["closed_at"].timestamp()) if p["closed_at"] else s[-1][0],
            "parts": sorted([(int(e["created_at"].timestamp()), float(e["quantity"]),
                              float(e["price"])) for e in by_pos.get(p["id"], [])],
                            key=lambda x: x[0]),
            "allow": learned_ok_prod(reg_arr[i], pos_arr[i], chg_arr[i]),
            "mon": str(p["opened_at"])[:7], "opened": str(p["opened_at"])[:19],
        })

    def sim(r, sl_pct):
        sign = 1.0 if r["side"] == "long" else -1.0
        s, i0 = r["s"], r["i"]
        parts, pi = r["parts"], 0
        realized, fees, qty = 0.0, 0.0, r["sz0"]
        cur_sl = r["entry"] * (1 - sign * sl_pct / 100.0)
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
            lo = sign * (l - r["entry"]) / r["entry"] * 100
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

    print(f"mid 层 n={len(recs)}（近 75 天）")
    for label, sub in (("门放行", [r for r in recs if r["allow"]]),
                       ("门拦截", [r for r in recs if not r["allow"]]),
                       ("全量", recs)):
        if not sub:
            continue
        print(f"\n=== {label} n={len(sub)} ===")
        print(f"{'SL%':>6}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'≤-2%':>7}  逐月USD")
        for sl in (1.5, 2.0, 3.0, 4.0, 6.0):
            usds = [sim(r, sl) for r in sub]
            pcts = [u / r["notional0"] * 100 for u, r in zip(usds, sub)]
            bym = defaultdict(float)
            for u, r in zip(usds, sub):
                bym[r["mon"]] += u
            bym_s = " ".join(f"{m[5:]}:{v:+.0f}" for m, v in sorted(bym.items()))
            print(f"{sl:>6.1f}{sum(usds):>+10.2f}{sum(pcts)/len(pcts):>+9.3f}"
                  f"{sum(1 for x in pcts if x > 0)/len(pcts):>7.3f}"
                  f"{sum(1 for x in pcts if x <= -2):>7}  {bym_s}")
        # 实际
        base = [r["base_usd"] for r in sub]
        bym = defaultdict(float)
        for u, r in zip(base, sub):
            bym[r["mon"]] += u
        bym_s = " ".join(f"{m[5:]}:{v:+.0f}" for m, v in sorted(bym.items()))
        print(f"{'实际':>6}{sum(base):>+10.2f}   {'':<16}  {bym_s}")

    # 最优候选 vs 实际 的 bootstrap（门放行子集）
    gated = [r for r in recs if r["allow"]]
    if gated:
        print("\n=== bootstrap：门放行子集 SL2% vs 实际（pct 差）===")
        diffs = [sim(r, 2.0) / r["notional0"] * 100 - r["base_usd"] / r["notional0"] * 100
                 for r in gated]
        rnd = random.Random(20260910)
        n = len(diffs)
        boots = sorted(sum(diffs[rnd.randrange(n)] for _ in range(n)) / n for _ in range(4000))
        obs = sum(diffs) / n
        lo, hi = boots[int(0.025 * 4000)], boots[int(0.975 * 4000)]
        print(f"  观测差={obs:+.4f}% CI[{lo:+.4f},{hi:+.4f}] "
              f"{'显著' if lo > 0 or hi < 0 else '不显著(跨0)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
