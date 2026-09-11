# -*- coding: utf-8 -*-
"""Z22：hub 择时税的正确度量——真实出场路径 + 生产口径门。

§27.2 的「hub 税 +1.34%/笔」用**机械新出场结构**（SL6/追踪3-1.5/168h）计算，
而 §33.5 已证机械反事实严重偏离真实出场（延迟/替换真实出场都更差）。
本轮用**真实出场路径**重算：
  - 信号 bar = 入场前窗口内满足**生产口径门**（`learned_ok_prod`）的 1h bar；
  - 反事实 = 在信号 bar 收盘价入场（同一 size），**沿用真实的部分平仓与真实平仓时点**，
    期间若触及该 tier 的初始 SL（mid 4.5% / long 6.5%）则按 SL 成交，并计资金费；
  - 策略：P_last（最近一次信号）/ P_first（窗口内首次信号）/ P_fresh（实际入场 bar 收盘）。
另给出**择时解剖**：hub 滞后小时数 + 入场价相对信号 bar 收盘的漂移。
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

from deep_long_freshness import build_bar_features, learned_ok_prod, load_klines  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
FEE_SIDE = 0.0005
SLIP = 0.0005
FUND_8H = 0.0001
SEED = 20260910
LOOKBACK_BARS = 336  # 14 天
SL_PCT = {"mid": 4.5, "long": 6.5}


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


def build(days=75):
    poss, evs = load(days)
    by_pos = defaultdict(list)
    for e in evs:
        if e["quantity"] and e["price"]:
            by_pos[e["position_id"]].append(e)
    h1, d1 = load_klines({p["symbol"] for p in poss})
    # 按时间覆盖挑序列（默认 pick 只按长度，可能选到过期序列）
    cache = {}
    for p in poss:
        sym = p["symbol"]
        if sym in cache:
            continue
        s = pick(h1, sym, int(p["opened_at"].timestamp()))
        ds = pick(d1, sym, int(p["opened_at"].timestamp()))
        if s and ds and len(s) >= 300 and len(ds) >= 70:
            cache[sym] = (s, build_bar_features(s, ds))
        else:
            cache[sym] = None

    recs = []
    for p in poss:
        sym = p["symbol"]
        cc = cache.get(sym)
        if not cc:
            continue
        s, (reg, pos, chg) = cc
        entry = float(p["entry_price"] or 0)
        close = float(p["close_price"] or 0)
        sz0 = float(p["original_size"] or p["size"] or 0)
        if entry <= 0 or close <= 0 or sz0 <= 0:
            continue
        ts = int(p["opened_at"].timestamp())
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i < 25 or abs(s[i][0] - ts) > 7200:
            continue
        # 窗口内满足生产门的 bar
        sig = [k for k in range(max(0, i - LOOKBACK_BARS), i)
               if learned_ok_prod(reg[k], pos[k], chg[k])]
        recs.append({
            "id": p["id"], "symbol": sym, "tier": str(p["timeframe_tier"]),
            "side": str(p["side"]), "entry": entry, "close": close, "sz0": sz0,
            "notional0": sz0 * entry,
            "base_usd": (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                         - float(p["partial_fee_paid"] or 0)),
            "db_peak": float(p["peak_pnl_pct"] or 0) * 100.0,
            "s": s, "i": i, "sig": sig,
            "fresh_ok": bool(learned_ok_prod(reg[i], pos[i], chg[i])),
            "close_ts": int(p["closed_at"].timestamp()) if p["closed_at"] else s[-1][0],
            "parts": sorted([(int(e["created_at"].timestamp()), float(e["quantity"]),
                              float(e["price"])) for e in by_pos.get(p["id"], [])],
                            key=lambda x: x[0]),
            "mon": str(p["opened_at"])[:7],
            "reason": str(p["close_reason"] or "")[:26],
        })
    return recs


def sim_shifted(r, j, p_entry):
    """在 bar j 以 p_entry 入场，沿用真实部分平仓与真实平仓时点；SL 用 tier 常数。"""
    sign = 1.0 if r["side"] == "long" else -1.0
    s = r["s"]
    parts, pi = r["parts"], 0
    qty = r["sz0"]
    realized = 0.0
    fees = qty * p_entry * (FEE_SIDE + SLIP)          # 开仓费
    sl = p_entry * (1 - sign * SL_PCT.get(r["tier"], 5.0) / 100.0)
    exit_ts = r["close_ts"]
    for k in range(j, len(s)):
        ts, _o, h, l, c = s[k]
        while pi < len(parts) and parts[pi][0] <= ts:
            _t, q, px = parts[pi]
            q = min(q, qty)
            realized += q * sign * (px - p_entry)
            fees += q * px * (FEE_SIDE + SLIP)
            qty -= q
            pi += 1
        if qty <= 1e-12:
            break
        hit = (l <= sl) if sign > 0 else (h >= sl)
        if hit:
            fill = sl * (1 - sign * SLIP)
            realized += qty * sign * (fill - p_entry)
            fees += qty * fill * (FEE_SIDE + SLIP)
            qty = 0.0
            break
        if ts >= exit_ts:
            realized += qty * sign * (r["close"] - p_entry)
            fees += qty * r["close"] * (FEE_SIDE + SLIP)
            qty = 0.0
            break
    if qty > 1e-12:
        c = s[-1][4]
        realized += qty * sign * (c - p_entry)
        fees += qty * c * (FEE_SIDE + SLIP)
    hold_h = max((exit_ts - s[j][0]) / 3600.0, 0.0)
    fund = r["notional0"] * FUND_8H * (hold_h / 8.0)
    return realized - fees - fund


def med(xs):
    v = sorted(xs)
    return v[len(v) // 2] if v else float("nan")


def main() -> int:
    recs = build(75)
    print(f"样本 n={len(recs)}（近 75 天 mid/long，真实出场路径 + 生产门口径）")
    withsig = [r for r in recs if r["sig"]]
    print(f"入场前 14 天内出现过门信号的笔数={len(withsig)}/{len(recs)}"
          f"（无信号笔不参与 P_last/P_first）")

    # ---------- A. 择时解剖 ----------
    print("\n===== A. hub 择时解剖（相对最近一次信号 bar）=====")
    print(f"{'分组':<18}{'n':>5}{'滞后h中位':>11}{'漂移%中位':>11}{'漂移%均值':>11}"
          f"{'追高占比':>10}")
    for label, sub in (("全部", withsig),
                       ("mid", [r for r in withsig if r["tier"] == "mid"]),
                       ("long", [r for r in withsig if r["tier"] == "long"])):
        if not sub:
            continue
        lags, drifts = [], []
        for r in sub:
            j = r["sig"][-1]
            lags.append((r["s"][r["i"]][0] - r["s"][j][0]) / 3600.0)
            drifts.append((r["entry"] - r["s"][j][4]) / r["s"][j][4] * 100)
        pos_share = sum(1 for x in drifts if x > 0) / len(drifts)
        print(f"{label:<18}{len(sub):>5}{med(lags):>11.1f}{med(drifts):>+11.3f}"
              f"{sum(drifts)/len(drifts):>+11.3f}{pos_share:>10.3f}")
    print("  （漂移>0 = hub 在信号 bar 收盘价**之上**入场，即追高）")

    # ---------- B. 反事实 ----------
    print("\n===== B. 反事实（真实出场路径，总 USD）=====")
    print(f"{'方案':<22}{'n':>5}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'模式率':>8}{'ΔUSD':>10}"
          f"  逐月USD")
    variants = [
        ("实际（hub 入场）", None),
        ("P_fresh（入场bar收盘）", "fresh"),
        ("P_last（最近信号bar）", "last"),
        ("P_first（窗口首次信号）", "first"),
    ]
    for label, sub in (("全量", recs), ("mid", [r for r in recs if r["tier"] == "mid"]),
                       ("long", [r for r in recs if r["tier"] == "long"]),
                       ("入场bar即满足门(fresh)", [r for r in recs if r["fresh_ok"]])):
        if not sub:
            continue
        print(f"\n--- {label} n={len(sub)} ---")
        base = sum(r["base_usd"] for r in sub)
        for name, mode in variants:
            usds = []
            for r in sub:
                if mode is None:
                    usds.append(r["base_usd"])
                elif mode == "fresh":
                    usds.append(sim_shifted(r, r["i"], r["s"][r["i"]][4]))
                elif mode == "last" and r["sig"]:
                    j = r["sig"][-1]
                    usds.append(sim_shifted(r, j, r["s"][j][4]))
                elif mode == "first" and r["sig"]:
                    j = r["sig"][0]
                    usds.append(sim_shifted(r, j, r["s"][j][4]))
                else:
                    usds.append(r["base_usd"])
            pcts = [u / r["notional0"] * 100 for u, r in zip(usds, sub)]
            pat = sum(1 for u, r in zip(usds, sub) if r["db_peak"] >= 0.5 and u < 0)
            bym = defaultdict(float)
            for u, r in zip(usds, sub):
                bym[r["mon"]] += u
            bym_s = " ".join(f"{m[5:]}:{v:+.0f}" for m, v in sorted(bym.items()))
            print(f"{name:<22}{len(sub):>5}{sum(usds):>+10.2f}{sum(pcts)/len(pcts):>+9.3f}"
                  f"{sum(1 for x in pcts if x > 0)/len(pcts):>7.3f}{pat/len(sub):>8.3f}"
                  f"{sum(usds)-base:>+10.2f}  {bym_s}")

    # ---------- C. bootstrap（P_last vs 实际） ----------
    print("\n===== C. bootstrap：P_last − 实际（pct 差，仅窗口内有信号笔）=====")
    for label, sub in (("全量", withsig), ("mid", [r for r in withsig if r["tier"] == "mid"]),
                       ("long", [r for r in withsig if r["tier"] == "long"])):
        if len(sub) < 10:
            continue
        diffs = []
        for r in sub:
            j = r["sig"][-1]
            diffs.append(sim_shifted(r, j, r["s"][j][4]) / r["notional0"] * 100
                         - r["base_usd"] / r["notional0"] * 100)
        rnd = random.Random(SEED)
        n = len(diffs)
        boots = sorted(sum(diffs[rnd.randrange(n)] for _ in range(n)) / n for _ in range(4000))
        obs = sum(diffs) / n
        lo, hi = boots[int(0.025 * 4000)], boots[int(0.975 * 4000)]
        print(f"  {label:<6} n={n:>3} 观测差={obs:+.4f}% CI[{lo:+.4f},{hi:+.4f}] "
              f"{'显著' if lo > 0 or hi < 0 else '不显著(跨0)'}")

    # ---------- D. 分解：入场价效应 vs 路径效应 ----------
    print("\n===== D. 分解（P_last）：入场价效应 vs 路径效应（SL/资金费/费用）=====")
    price_eff, path_eff, totals = [], [], []
    for r in withsig:
        j = r["sig"][-1]
        p_sig = r["s"][j][4]
        # 所有腿同价替换：合计一阶项 = sz0 × (entry − p_sig)（多头口径）
        pe = r["sz0"] * (r["entry"] - p_sig)
        tot = sim_shifted(r, j, p_sig) - r["base_usd"]
        price_eff.append(pe)
        totals.append(tot)
        path_eff.append(tot - pe)
    print(f"  n={len(withsig)} 总Δ=${sum(totals):+.2f}"
          f" = 入场价效应 ${sum(price_eff):+.2f} + 路径效应 ${sum(path_eff):+.2f}")
    print(f"  中位：入场价效应 ${med(price_eff):+.2f} / 路径效应 ${med(path_eff):+.2f}"
          f" / 总Δ ${med(totals):+.2f}")

    # ---------- E. 追高 vs 低吸：入场漂移分档 ----------
    print("\n===== E. 入场漂移分档（相对最近信号 bar 收盘；>0 = 追高）=====")
    print(f"{'漂移档':<18}{'n':>5}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'模式率':>8}{'≤-2%':>7}")
    buckets = [(-99, -3), (-3, -1.5), (-1.5, -0.5), (-0.5, 0.0),
               (0.0, 0.5), (0.5, 1.5), (1.5, 99)]
    for lo, hi in buckets:
        sub = []
        for r in withsig:
            j = r["sig"][-1]
            d = (r["entry"] - r["s"][j][4]) / r["s"][j][4] * 100
            if lo <= d < hi:
                sub.append(r)
        if len(sub) < 5:
            continue
        pcts = [r["base_usd"] / r["notional0"] * 100 for r in sub]
        pat = sum(1 for r in sub if r["db_peak"] >= 0.5 and r["base_usd"] < 0)
        print(f"[{lo:+.1f},{hi:+.1f}){'':<6}{len(sub):>5}{sum(r['base_usd'] for r in sub):>+10.2f}"
              f"{sum(pcts)/len(pcts):>+9.3f}{sum(1 for x in pcts if x>0)/len(pcts):>7.3f}"
              f"{pat/len(sub):>8.3f}{sum(1 for x in pcts if x<=-2):>7}")
    # 追高 vs 低吸 合并 + bootstrap
    for label, fn in (("追高(漂移>0)", lambda d: d > 0),
                      ("低吸(漂移≤0)", lambda d: d <= 0)):
        sub = [r for r in withsig
               if fn((r["entry"] - r["s"][r["sig"][-1]][4]) / r["s"][r["sig"][-1]][4] * 100)]
        if len(sub) < 10:
            continue
        pcts = [r["base_usd"] / r["notional0"] * 100 for r in sub]
        pat = sum(1 for r in sub if r["db_peak"] >= 0.5 and r["base_usd"] < 0)
        print(f"{label:<18}{len(sub):>5}{sum(r['base_usd'] for r in sub):>+10.2f}"
              f"{sum(pcts)/len(pcts):>+9.3f}{sum(1 for x in pcts if x>0)/len(pcts):>7.3f}"
              f"{pat/len(sub):>8.3f}{sum(1 for x in pcts if x<=-2):>7}")
    a = [r for r in withsig if (r["entry"] - r["s"][r["sig"][-1]][4]) / r["s"][r["sig"][-1]][4] > 0]
    b = [r for r in withsig if (r["entry"] - r["s"][r["sig"][-1]][4]) / r["s"][r["sig"][-1]][4] <= 0]
    if len(a) >= 10 and len(b) >= 10:
        da = [r["base_usd"] / r["notional0"] * 100 for r in a]
        db = [r["base_usd"] / r["notional0"] * 100 for r in b]
        rnd = random.Random(SEED)
        na, nb = len(da), len(db)
        boots = sorted(sum(da[rnd.randrange(na)] for _ in range(na)) / na
                       - sum(db[rnd.randrange(nb)] for _ in range(nb)) / nb
                       for _ in range(4000))
        obs = sum(da) / na - sum(db) / nb
        lo, hi = boots[int(0.025 * 4000)], boots[int(0.975 * 4000)]
        print(f"  追高 − 低吸 均值差={obs:+.4f}% CI[{lo:+.4f},{hi:+.4f}] "
              f"{'显著' if lo > 0 or hi < 0 else '不显著(跨0)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
