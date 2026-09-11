# -*- coding: utf-8 -*-
"""Z7：「先盈利后大亏」的速度学解剖 + 速度条件化浮盈保护。

已被证伪/关闭的杠杆（全部为**与速度无关**的静态规则）：
- X4 固定追踪阶梯（激活/回撤百分比网格）：收紧改善模式子集但整体更差；
- X6 分档止盈、Y21 部分锁：砍右尾；
- Y19 long 层「峰值→保本 / 锁 peak×0.5」5 变体：全部更差；
- Z5 收紧 SL（mid 门放行子集）：-92~-141 vs 实际 -16.84，bootstrap 显著更差；
- Z6 时间止损（死钱 12/24/48h）：long +84 → -66~-78，mid -48 → -195~-234。

共同点：这些规则**不看速度**。而「先盈利后大亏」的直觉机理是**尖峰冲高 → 快速回落**
（上行速度与下行速度不对称）。本轮：
1. 解剖模式笔的峰值到达速度 / 回吐速度 / 回吐时长；
2. 测**速度条件化**保护：仅当「回吐相对峰值又快又深」（回吐 ≥ max(0.3%, k·峰值) 且
   发生在峰值后 ≤ m 小时内）才提前出场，用**纯 overlay** 口径（不触发则保留真实结局，
   因此不会误伤靠真实主动通道/Chandelier 出场的右尾）。
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
FEE_SIDE = 0.0005
SLIP = 0.0005
SEED = 20260910


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
            "id": p["id"], "symbol": sym, "tier": str(p["timeframe_tier"]),
            "side": str(p["side"]), "entry": entry, "close": close, "sz0": sz0,
            "notional0": sz0 * entry,
            "base_usd": (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                         - float(p["partial_fee_paid"] or 0)),
            "db_peak": float(p["peak_pnl_pct"] or 0) * 100.0,  # 价格口径 %
            "s": s, "i": i,
            "close_ts": int(p["closed_at"].timestamp()) if p["closed_at"] else s[-1][0],
            "parts": sorted([(int(e["created_at"].timestamp()), float(e["quantity"]),
                              float(e["price"])) for e in by_pos.get(p["id"], [])],
                            key=lambda x: x[0]),
            "allow": learned_ok_prod(reg_arr[i], pos_arr[i], chg_arr[i]),
            "mon": str(p["opened_at"])[:7], "opened": str(p["opened_at"])[:19],
            "reason": str(p["close_reason"] or "")[:26],
        })
    return recs


def path_stats(r):
    """1h 路径上的峰值速度 / 回吐速度。"""
    sign = 1.0 if r["side"] == "long" else -1.0
    s, i0 = r["s"], r["i"]
    peak, peak_ts, peak_hour = 0.0, None, None
    exit_ts = r["close_ts"]
    for k in range(i0, len(s)):
        ts, _o, h, _l, c = s[k]
        if ts > exit_ts:
            break
        hi = sign * (h - r["entry"]) / r["entry"] * 100
        if hi > peak:
            peak, peak_ts = hi, ts
            peak_hour = (ts - s[i0][0]) / 3600.0
    if peak_ts is None:
        return None
    exit_hour = (min(exit_ts, s[-1][0]) - s[i0][0]) / 3600.0
    gb = peak - sign * (r["close"] - r["entry"]) / r["entry"] * 100
    dn_h = max(exit_hour - (peak_hour or 0.0), 0.25)
    up_h = max(peak_hour or 0.25, 0.25)
    return {"peak": peak, "peak_hour": peak_hour or 0.0, "exit_hour": exit_hour,
            "gb": gb, "up_speed": peak / up_h, "dn_speed": gb / dn_h,
            "asym": (gb / dn_h) / max(peak / up_h, 1e-9)}


def overlay(r, k, m, pmin=0.5, cbf=0.3):
    """纯 overlay：速度条件化回吐保护。触发则在该 1h 收盘出场，否则返回真实结局。"""
    sign = 1.0 if r["side"] == "long" else -1.0
    s, i0 = r["s"], r["i"]
    parts, pi = r["parts"], 0
    realized, fees, qty = 0.0, 0.0, r["sz0"]
    peak, peak_ts = 0.0, None
    for kk in range(i0, len(s)):
        ts, _o, h, _l, c = s[kk]
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
        if hi > peak:
            peak, peak_ts = hi, ts
        cur = sign * (c - r["entry"]) / r["entry"] * 100
        if (peak >= pmin and peak_ts is not None and ts > peak_ts
                and (ts - peak_ts) <= m * 3600
                and (peak - cur) >= max(cbf, k * peak)):
            realized += qty * sign * (c - r["entry"])
            fees += qty * c * (FEE_SIDE + SLIP) * 2
            qty = 0.0
            return realized - fees, True
        if ts >= r["close_ts"]:
            break
    return r["base_usd"], False


def table(label, sub, variants):
    print(f"\n=== {label} n={len(sub)} ===")
    print(f"{'方案':<24}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'≤-2%':>7}{'触发':>6}  逐月USD")
    rows = [("实际（含主动出场）", None, None)] + variants
    for name, k, m in rows:
        if k is None:
            usds = [r["base_usd"] for r in sub]
            trig = 0
        else:
            res = [overlay(r, k, m) for r in sub]
            usds = [x[0] for x in res]
            trig = sum(1 for x in res if x[1])
        if not usds:
            continue
        pcts = [u / r["notional0"] * 100 for u, r in zip(usds, sub)]
        bym = defaultdict(float)
        for u, r in zip(usds, sub):
            bym[r["mon"]] += u
        bym_s = " ".join(f"{mm[5:]}:{v:+.0f}" for mm, v in sorted(bym.items()))
        print(f"{name:<24}{sum(usds):>+10.2f}{sum(pcts)/len(pcts):>+9.3f}"
              f"{sum(1 for x in pcts if x > 0)/len(pcts):>7.3f}"
              f"{sum(1 for x in pcts if x <= -2):>7}{trig:>6}  {bym_s}")


def main() -> int:
    recs = build(75)
    print(f"样本 n={len(recs)}（近 75 天 mid+long，1h 路径可对齐）")

    # ---------- Part A：模式笔速度学解剖 ----------
    pat = []
    for r in recs:
        st = path_stats(r)
        if st is None:
            continue
        if r["db_peak"] >= 0.5 and r["base_usd"] < 0:
            r["st"] = st
            pat.append(r)
    pat.sort(key=lambda r: r["base_usd"])
    print(f"\n===== Part A：模式笔（DB峰值≥0.5% 且 总USD<0）n={len(pat)}，"
          f"合计 ${sum(r['base_usd'] for r in pat):+.2f} =====")
    print(f"{'symbol':<10}{'tier':<6}{'峰值%':>7}{'峰/h':>7}{'出/h':>7}{'回吐%':>8}"
          f"{'上速%/h':>9}{'下速%/h':>9}{'不对称':>8}{'USD':>9}  通道")
    for r in pat[:40]:
        st = r["st"]
        print(f"{r['symbol']:<10}{r['tier']:<6}{st['peak']:>7.2f}{st['peak_hour']:>7.1f}"
              f"{st['exit_hour']:>7.1f}{st['gb']:>8.2f}{st['up_speed']:>9.2f}"
              f"{st['dn_speed']:>9.2f}{st['asym']:>8.2f}{r['base_usd']:>+9.2f}  {r['reason']}")
    if pat:
        up = sorted(r["st"]["up_speed"] for r in pat)
        dn = sorted(r["st"]["dn_speed"] for r in pat)
        print(f"  中位上速={up[len(up)//2]:.2f}%/h 中位下速={dn[len(dn)//2]:.2f}%/h "
              f"→ 中位不对称倍数={dn[len(dn)//2]/max(up[len(up)//2],1e-9):.2f}x")
    # 对照：所有笔（含盈利）的速度分布
    allv = [(r, path_stats(r)) for r in recs]
    allv = [(r, st) for r, st in allv if st]
    win = [st for r, st in allv if r["base_usd"] > 0]
    los = [st for r, st in allv if r["base_usd"] <= 0]
    def med(xs, key):
        v = sorted(x[key] for x in xs)
        return v[len(v)//2] if v else float("nan")
    print(f"  对照 盈利笔 n={len(win)}: 上速中位={med(win,'up_speed'):.2f} 下速中位={med(win,'dn_speed'):.2f} "
          f"峰值中位={med(win,'peak'):.2f}%")
    print(f"  对照 亏损笔 n={len(los)}: 上速中位={med(los,'up_speed'):.2f} 下速中位={med(los,'dn_speed'):.2f} "
          f"峰值中位={med(los,'peak'):.2f}%")

    # ---------- Part B：速度条件化保护网格 ----------
    variants = [(f"k={k} m={m}h", k, m) for k in (0.33, 0.5, 0.75) for m in (2, 4, 8)]
    variants += [("k=0.5 m=∞（纯追踪）", 0.5, 10_000)]
    subsets = [
        ("全量", recs),
        ("mid", [r for r in recs if r["tier"] == "mid"]),
        ("long", [r for r in recs if r["tier"] == "long"]),
        ("mid·门放行", [r for r in recs if r["tier"] == "mid" and r["allow"]]),
    ]
    for label, sub in subsets:
        if sub:
            table(label, sub, variants)

    # ---------- Part C：walk-forward（按时间前 60% 选参，后 40% 验证） ----------
    print("\n===== Part C：walk-forward（前 60% 选参 → 后 40% 验证，分 tier）=====")
    for tier in ("mid", "long", "all"):
        sub = recs if tier == "all" else [r for r in recs if r["tier"] == tier]
        if len(sub) < 30:
            print(f"\n--- tier={tier} n={len(sub)} 样本不足，跳过 ---")
            continue
        cut = int(len(sub) * 0.6)
        tr, te = sub[:cut], sub[cut:]
        best, best_usd = None, None
        for name, k, m in variants:
            u = sum(overlay(r, k, m)[0] for r in tr)
            if best_usd is None or u > best_usd:
                best, best_usd = (name, k, m), u
        tr_base = sum(r["base_usd"] for r in tr)
        te_base = sum(r["base_usd"] for r in te)
        te_var = sum(overlay(r, best[1], best[2])[0] for r in te)
        n_trig = sum(1 for r in te if overlay(r, best[1], best[2])[1])
        print(f"\n--- tier={tier} 训练n={len(tr)} 验证n={len(te)} ---")
        print(f"  训练集最优={best[0]} 训练实际=${tr_base:+.2f} → 变体=${best_usd:+.2f}")
        print(f"  验证集 实际=${te_base:+.2f} → 变体=${te_var:+.2f} "
              f"（差 ${te_var-te_base:+.2f}，触发 {n_trig} 笔）")
        diffs = [overlay(r, best[1], best[2])[0] / r["notional0"] * 100
                 - r["base_usd"] / r["notional0"] * 100 for r in te]
        if diffs:
            rnd = random.Random(SEED)
            n = len(diffs)
            boots = sorted(sum(diffs[rnd.randrange(n)] for _ in range(n)) / n
                           for _ in range(4000))
            obs = sum(diffs) / n
            lo, hi = boots[int(0.025 * 4000)], boots[int(0.975 * 4000)]
            print(f"  bootstrap pct 差 观测={obs:+.4f}% CI[{lo:+.4f},{hi:+.4f}] "
                  f"{'显著' if lo > 0 or hi < 0 else '不显著(跨0)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
