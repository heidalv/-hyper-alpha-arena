# -*- coding: utf-8 -*-
"""Z26：门特征空间扫描——有没有「可放行但被拦掉」的正期望象限？（§24 #25）

现行门（生产口径）：
  up   : chg24 ∈ [3,6)
  chop : pos24 ≥ 60 且 chg24 ≥ 2
  down : 全拦
本脚本把 75 天 283 笔真实成交按 (regime × pos24 分箱 × chg24 分箱) 铺开，
找 n≥10 且期望为正的格子——这些就是「保质量提流量」的候选。
随后做 walk-forward（前 2/3 选格子 → 后 1/3 验证）与 bootstrap。
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

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
SEED = 20260910

POS_BINS = [0, 20, 40, 60, 80, 100.1]
CHG_BINS = [-99, -5, -2, 0, 2, 3, 6, 10, 99]


def pick(series, sym, ts):
    for ex in ("asterdex", "binance"):
        v = series.get((ex, sym))
        if v and len(v) > 200 and v[0][0] <= ts <= v[-1][0] + 86400:
            return v
    return None


def build(days=75):
    eng = create_engine(URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        poss = [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, timeframe_tier, entry_price, original_size, size, peak_pnl_pct,
                   unrealized_pnl, partial_realized_pnl, partial_fee_paid, opened_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '{int(days)} days'
            order by opened_at
        """)).fetchall()]
    h1, d1 = load_klines({p["symbol"] for p in poss})
    cache = {}
    recs = []
    for p in poss:
        sym = p["symbol"]
        ts = int(p["opened_at"].timestamp())
        if sym not in cache:
            s = pick(h1, sym, ts)
            ds = pick(d1, sym, ts)
            cache[sym] = (s, build_bar_features(s, ds)) if (
                s and ds and len(s) >= 300 and len(ds) >= 70) else None
        cc = cache.get(sym)
        if not cc:
            continue
        s, (reg, pos, chg) = cc
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i == 0 or abs(s[i][0] - ts) > 7200:
            continue
        sz0 = float(p["original_size"] or p["size"] or 0)
        entry = float(p["entry_price"] or 0)
        if sz0 <= 0 or entry <= 0:
            continue
        recs.append({
            "id": p["id"], "symbol": sym, "tier": str(p["timeframe_tier"]),
            "notional0": sz0 * entry,
            "usd": (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                    - float(p["partial_fee_paid"] or 0)),
            "peak": float(p["peak_pnl_pct"] or 0) * 100,
            "reg": reg[i], "pos": pos[i], "chg": chg[i],
            "allow": bool(learned_ok_prod(reg[i], pos[i], chg[i])),
            "mon": str(p["opened_at"])[:7],
        })
    return recs


def cell(r):
    pb = None
    for k in range(len(POS_BINS) - 1):
        if POS_BINS[k] <= r["pos"] < POS_BINS[k + 1]:
            pb = k
            break
    cb = None
    for k in range(len(CHG_BINS) - 1):
        if CHG_BINS[k] <= r["chg"] < CHG_BINS[k + 1]:
            cb = k
            break
    return (r["reg"], pb, cb)


def agg(sub):
    if not sub:
        return None
    pcts = [r["usd"] / r["notional0"] * 100 for r in sub]
    pat = sum(1 for r in sub if r["peak"] >= 0.5 and r["usd"] < 0)
    return {"n": len(sub), "usd": sum(r["usd"] for r in sub),
            "mean": sum(pcts) / len(pcts),
            "win": sum(1 for x in pcts if x > 0) / len(pcts),
            "pat_rate": pat / len(sub)}


def main() -> int:
    recs = build(75)
    print(f"样本 n={len(recs)}（75 天；生产门口径）")
    allow = [r for r in recs if r["allow"]]
    print(f"门放行 {len(allow)} 笔（{len(allow)/len(recs):.1%}），"
          f"USD={sum(r['usd'] for r in allow):+.2f}；"
          f"拦截 {len(recs)-len(allow)} 笔 USD={sum(r['usd'] for r in recs if not r['allow']):+.2f}")

    cells = defaultdict(list)
    for r in recs:
        cells[cell(r)].append(r)

    print("\n===== A. 格子表（n≥10；均值% 降序）=====")
    print(f"{'regime':<6}{'pos24':<12}{'chg24':<12}{'n':>5}{'总USD':>10}{'均值%':>9}"
          f"{'胜率':>7}{'模式率':>8}{'放行?':>7}")
    rows = []
    for k, sub in cells.items():
        if len(sub) < 10:
            continue
        a = agg(sub)
        reg, pb, cb = k
        pos_lbl = f"[{POS_BINS[pb]:.0f},{POS_BINS[pb+1]:.0f})"
        chg_lbl = f"[{CHG_BINS[cb]:+.0f},{CHG_BINS[cb+1]:+.0f})"
        rows.append((a["mean"], reg, pos_lbl, chg_lbl, a, sub[0]["allow"]))
    for mean, reg, pos_lbl, chg_lbl, a, al in sorted(rows, reverse=True):
        print(f"{reg or '-':<6}{pos_lbl:<12}{chg_lbl:<12}{a['n']:>5}{a['usd']:>+10.2f}"
              f"{a['mean']:>+9.3f}{a['win']:>7.3f}{a['pat_rate']:>8.3f}"
              f"{'是' if al else '否':>7}")

    print("\n===== B. 「被拦掉但期望为正」的格子（提流量候选）=====")
    cand = [x for x in rows if not x[5] and x[4]["mean"] > 0]
    tot_n = sum(x[4]["n"] for x in cand)
    tot_usd = sum(x[4]["usd"] for x in cand)
    print(f"  候选格子数={len(cand)} 合计 n={tot_n} USD={tot_usd:+.2f} "
          f"（相对全样本 +{tot_usd/max(sum(r['usd'] for r in recs),1e-9):.0%} 的 USD 增量）")
    for mean, reg, pos_lbl, chg_lbl, a, _ in cand:
        print(f"    {reg:<5} pos{pos_lbl:<11} chg{chg_lbl:<11} n={a['n']:>3} "
              f"均值={a['mean']:+.3f}% 模式率={a['pat_rate']:.3f}")

    print("\n===== C. 扩展门（现行 ∪ 候选格子）的整体效果 =====")
    cand_keys = {k for k, sub in cells.items()
                 if len(sub) >= 10 and not sub[0]["allow"] and agg(sub)["mean"] > 0}
    keep = [r for r in recs if r["allow"] or cell(r) in cand_keys]
    for label, sub in (("现行门", allow), ("扩展门", keep), ("全量", recs)):
        a = agg(sub)
        bym = defaultdict(float)
        for r in sub:
            bym[r["mon"]] += r["usd"]
        bym_s = " ".join(f"{m[5:]}:{v:+.0f}" for m, v in sorted(bym.items()))
        print(f"  {label:<8} n={a['n']:>3} USD={a['usd']:>+9.2f} 均值={a['mean']:>+7.3f}% "
              f"胜率={a['win']:.3f} 模式率={a['pat_rate']:.3f}  {bym_s}")

    print("\n===== D. walk-forward（前 2/3 选格子 → 后 1/3 验证）=====")
    cut = int(len(recs) * 2 / 3)
    tr, te = recs[:cut], recs[cut:]
    tr_cells = defaultdict(list)
    for r in tr:
        tr_cells[cell(r)].append(r)
    picked = set()
    for k, sub in tr_cells.items():
        if len(sub) >= 6 and not sub[0]["allow"] and agg(sub)["mean"] > 0:
            picked.add(k)
    keep_te = [r for r in te if r["allow"] or cell(r) in picked]
    a_cur = agg([r for r in te if r["allow"]])
    a_ext = agg(keep_te)
    print(f"  训练选出的格子数={len(picked)}")
    print(f"  验证集：现行门 n={a_cur['n']} USD={a_cur['usd']:+.2f} 均值={a_cur['mean']:+.3f}% "
          f"模式率={a_cur['pat_rate']:.3f}")
    print(f"  验证集：扩展门 n={a_ext['n']} USD={a_ext['usd']:+.2f} 均值={a_ext['mean']:+.3f}% "
          f"模式率={a_ext['pat_rate']:.3f}")
    # 新增笔的 bootstrap
    new = [r for r in te if cell(r) in picked and not r["allow"]]
    if len(new) >= 5:
        d = [r["usd"] / r["notional0"] * 100 for r in new]
        rnd = random.Random(SEED)
        n = len(d)
        boots = sorted(sum(d[rnd.randrange(n)] for _ in range(n)) / n for _ in range(4000))
        lo, hi = boots[int(0.025 * 4000)], boots[int(0.975 * 4000)]
        print(f"  新增 {n} 笔均值={sum(d)/n:+.4f}% CI[{lo:+.4f},{hi:+.4f}] "
              f"{'显著为正（提流量有效）' if lo > 0 else '不显著(跨0)'}")
    else:
        print(f"  验证集新增笔数={len(new)}（样本不足）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
