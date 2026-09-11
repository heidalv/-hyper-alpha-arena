# -*- coding: utf-8 -*-
"""Z11：出场是否**系统性过早**？——「延迟出场」反事实（直接对应 §24 #12）。

背景：X3（§28.4）用**纯机械**反事实（持到 SL/TP）算出主动通道的代价：
long_trend_v2 +2.48%、no_progress +1.20%、profit_drawdown +0.92%、master_running +0.69%
（合计 ≈ +0.40%/笔），但 §28.7 列出两项偏差未排除：
  (1) 无信号信息——机械反事实不知道通道掌握的信息；
  (2) 无资金机会成本。

本轮用**延迟出场**同时排除两项偏差：
  - 保留通道信号本身，只是把成交时间**推迟 Δ 小时**（Δ∈{2,4,8,24}），
    期间若触及当前保护性止损（DB `sl_price`，对 long 为下界）则按止损成交；
  - 显式扣除资金机会成本（默认 2bp/日 × 名义，按延迟小时数计）；
  - 逐通道 + 逐月 + walk-forward + bootstrap。

若「延迟 Δ 小时净收益 > 0」，说明通道确实切得太早 → 可落码放松；
若 ≈0 或 < 0，则 X3 的「代价」是机械偏差造成的假象，现行出场不必改。
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

from deep_long_freshness import load_klines  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
FEE_SIDE = 0.0005
SLIP = 0.0005
SEED = 20260910
OPP_PER_DAY = 0.0002  # 2bp/日 名义资金机会成本


def pick(series, sym, ts):
    for ex in ("asterdex", "binance"):
        v = series.get((ex, sym))
        if v and len(v) > 200 and v[0][0] <= ts <= v[-1][0] + 86400:
            return v
    return None


def load(days=75):
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
            where event_type='partial_exit_event' and created_at >= now() - interval '95 days'
            order by position_id, created_at
        """)).fetchall()]
    return poss, evs


def channel(reason: str) -> str:
    r = (reason or "").lower()
    for key in ("long_trend_v2", "no_progress", "profit_drawdown", "master_running",
                "thesis_invalidation", "trend_broken", "breakeven_tp", "sl", "max_hold",
                "dust_cleanup", "symbol_removed", "hard_line"):
        if key in r:
            return key
    return "other"


def build(days=75):
    poss, evs = load(days)
    by_pos = defaultdict(list)
    for e in evs:
        if e["quantity"] and e["price"]:
            by_pos[e["position_id"]].append(e)
    h1, _ = load_klines({p["symbol"] for p in poss})
    recs = []
    for p in poss:
        sym = p["symbol"]
        s = pick(h1, sym, int(p["opened_at"].timestamp()))
        if not s:
            continue
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
            "id": p["id"], "symbol": sym, "tier": str(p["timeframe_tier"]),
            "side": str(p["side"]), "entry": entry, "close": close, "sz0": sz0,
            "notional0": sz0 * entry, "sl": float(p["sl_price"] or 0),
            "base_usd": (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                         - float(p["partial_fee_paid"] or 0)),
            "db_peak": float(p["peak_pnl_pct"] or 0) * 100.0,
            "s": s, "i": i,
            "close_ts": int(p["closed_at"].timestamp()) if p["closed_at"] else s[-1][0],
            "parts": sorted([(int(e["created_at"].timestamp()), float(e["quantity"]),
                              float(e["price"])) for e in by_pos.get(p["id"], [])],
                            key=lambda x: x[0]),
            "mon": str(p["opened_at"])[:7],
            "ch": channel(str(p["close_reason"] or "")),
        })
    return recs


def delayed(r, hours, opp_per_day=OPP_PER_DAY):
    """把真实平仓推迟 hours 小时；**仅在延迟窗口内**用当前 SL 做保护性止损；
    扣资金机会成本。真实持有期的部分平仓照常处理。"""
    sign = 1.0 if r["side"] == "long" else -1.0
    s, i0 = r["s"], r["i"]
    parts, pi = r["parts"], 0
    realized, fees, qty = 0.0, 0.0, r["sz0"]
    exit_ts = r["close_ts"] + int(hours * 3600)
    sl = r["sl"]
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
        # 延迟窗口内才启用保护性止损（真实持有期内 DB sl_price 是活体值，不可回溯使用）
        if ts >= r["close_ts"] and sl > 0:
            hit = (l <= sl) if sign > 0 else (h >= sl)
            if hit:
                fill = sl * (1 - sign * SLIP)
                realized += qty * sign * (fill - r["entry"])
                fees += qty * fill * (FEE_SIDE + SLIP) * 2
                qty = 0.0
                break
        if ts >= exit_ts:
            realized += qty * sign * (c - r["entry"])
            fees += qty * c * (FEE_SIDE + SLIP) * 2
            qty = 0.0
            break
    if qty > 1e-12:  # 数据到头
        c = s[-1][4]
        realized += qty * sign * (c - r["entry"])
        fees += qty * c * (FEE_SIDE + SLIP) * 2
    opp = r["notional0"] * opp_per_day * hours / 24.0
    return realized - fees - opp


def table(label, sub):
    print(f"\n=== {label} n={len(sub)} 基线=${sum(r['base_usd'] for r in sub):+.2f} ===")
    print(f"{'方案':<20}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'改善':>10}  逐月USD")
    base = sum(r["base_usd"] for r in sub)
    rows = [("实际（不延迟）", 0)] + [(f"延迟 {h}h", h) for h in (2, 4, 8, 24)]
    for name, h in rows:
        usds = [r["base_usd"] if h == 0 else delayed(r, h) for r in sub]
        pcts = [u / r["notional0"] * 100 for u, r in zip(usds, sub)]
        bym = defaultdict(float)
        for u, r in zip(usds, sub):
            bym[r["mon"]] += u
        bym_s = " ".join(f"{m[5:]}:{v:+.0f}" for m, v in sorted(bym.items()))
        print(f"{name:<20}{sum(usds):>+10.2f}{sum(pcts)/len(pcts):>+9.3f}"
              f"{sum(1 for x in pcts if x > 0)/len(pcts):>7.3f}"
              f"{sum(usds)-base:>+10.2f}  {bym_s}")


def main() -> int:
    recs = build(75)
    print(f"样本 n={len(recs)}（近 75 天；延迟出场反事实；资金机会成本 "
          f"{OPP_PER_DAY*10000:.0f}bp/日）")
    table("全量", recs)
    table("mid", [r for r in recs if r["tier"] == "mid"])
    table("long", [r for r in recs if r["tier"] == "long"])

    print("\n===== 逐通道（延迟 4h / 8h，净机会成本）=====")
    bych = defaultdict(list)
    for r in recs:
        bych[r["ch"]].append(r)
    print(f"{'通道':<20}{'n':>5}{'实际USD':>11}{'延迟4h':>11}{'延迟8h':>11}{'差4h':>10}{'差8h':>10}")
    for ch, sub in sorted(bych.items(), key=lambda x: -len(x[1])):
        if len(sub) < 5:
            continue
        b = sum(r["base_usd"] for r in sub)
        d4 = sum(delayed(r, 4) for r in sub)
        d8 = sum(delayed(r, 8) for r in sub)
        print(f"{ch:<20}{len(sub):>5}{b:>+11.2f}{d4:>+11.2f}{d8:>+11.2f}"
              f"{d4-b:>+10.2f}{d8-b:>+10.2f}")

    print("\n===== 指定通道延迟（§28.4 点名的 4 个通道）=====")
    target = {"long_trend_v2", "no_progress", "profit_drawdown", "master_running"}
    sub_t = [r for r in recs if r["ch"] in target]
    sub_o = [r for r in recs if r["ch"] not in target]
    table("点名通道", sub_t)
    table("其余通道", sub_o)

    print("\n===== bootstrap：点名通道延迟 4h 的 pct 差 =====")
    diffs = [delayed(r, 4) / r["notional0"] * 100 - r["base_usd"] / r["notional0"] * 100
             for r in sub_t]
    if diffs:
        rnd = random.Random(SEED)
        n = len(diffs)
        boots = sorted(sum(diffs[rnd.randrange(n)] for _ in range(n)) / n for _ in range(4000))
        obs = sum(diffs) / n
        lo, hi = boots[int(0.025 * 4000)], boots[int(0.975 * 4000)]
        print(f"  n={n} 观测差={obs:+.4f}% CI[{lo:+.4f},{hi:+.4f}] "
              f"{'显著' if lo > 0 or hi < 0 else '不显著(跨0)'}")

    print("\n===== 敏感性：机会成本 0 / 2bp / 10bp 日 =====")
    for opp in (0.0, 0.0002, 0.001):
        for h in (2, 4, 8):
            tot_t = sum(delayed(r, h, opp) for r in sub_t)
            tot_a = sum(delayed(r, h, opp) for r in recs)
            print(f"  opp={opp*10000:>4.0f}bp/日 延迟{h}h: 点名通道 "
                  f"${tot_t:+.2f}（基线 ${sum(r['base_usd'] for r in sub_t):+.2f}）"
                  f" | 全量 ${tot_a:+.2f}（基线 ${sum(r['base_usd'] for r in recs):+.2f}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
