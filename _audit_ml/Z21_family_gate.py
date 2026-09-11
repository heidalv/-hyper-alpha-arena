# -*- coding: utf-8 -*-
"""Z21：来源家族 × learned 门 的交互 + 「封堵毒性家族」反事实。

Z20 发现来源家族分化明显：
  mid_reversion n=69 -$35.03（模式率 0.130）
  mid_range     n=60 -$30.99（模式率 0.150）
  pro/gen       模式率 0.038/0.073、USD 为正
  auto         模式率 0.333（-$81.57）
本轮检验：**封堵某个家族**是否在「门已生效」的前提下仍有增量价值（避免 §31.1 的门×机制冗余）。
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
sys.path.insert(0, str(ROOT / "_audit_ml"))

from deep_long_freshness import build_bar_features, load_klines, learned_ok_prod  # noqa: E402
from Z10_sl_overlay import pick  # noqa: E402  (ts-aware)
from Z20_family_pwin import family  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
SEED = 20260910


def load(days=75):
    eng = create_engine(URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        poss = [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, timeframe_tier, strategy_id, entry_price, original_size, size,
                   peak_pnl_pct, unrealized_pnl, partial_realized_pnl, partial_fee_paid,
                   opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '{int(days)} days'
            order by opened_at
        """)).fetchall()]
    h1, d1 = load_klines({p["symbol"] for p in poss})
    cache = {}
    for sym in {p["symbol"] for p in poss}:
        s = pick(h1, sym, int(min(p["opened_at"].timestamp() for p in poss if p["symbol"] == sym)))
        ds = pick(d1, sym, int(min(p["opened_at"].timestamp() for p in poss if p["symbol"] == sym)))
        if s and ds and len(s) >= 300 and len(ds) >= 70:
            cache[sym] = (s, build_bar_features(s, ds))
    recs = []
    for p in poss:
        sz0 = float(p["original_size"] or p["size"] or 0)
        entry = float(p["entry_price"] or 0)
        if sz0 <= 0 or entry <= 0 or not p["opened_at"]:
            continue
        ts = int(p["opened_at"].timestamp())
        allow = None
        cc = cache.get(p["symbol"])
        if cc:
            s, (reg, pos, chg) = cc
            i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
            if i is not None and i > 0 and abs(s[i][0] - ts) <= 7200:
                allow = bool(learned_ok_prod(reg[i], pos[i], chg[i]))
        recs.append({
            "id": p["id"], "symbol": p["symbol"], "tier": str(p["timeframe_tier"]),
            "fam": family(str(p["strategy_id"] or "")), "sid": str(p["strategy_id"] or ""),
            "notional0": sz0 * entry,
            "usd": (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                    - float(p["partial_fee_paid"] or 0)),
            "peak": float(p["peak_pnl_pct"] or 0) * 100,
            "allow": allow, "mon": str(p["opened_at"])[:7],
        })
    return recs


def agg(sub):
    if not sub:
        return None
    pcts = [r["usd"] / r["notional0"] * 100 for r in sub]
    pat = [r for r in sub if r["peak"] >= 0.5 and r["usd"] < 0]
    return {"n": len(sub), "usd": sum(r["usd"] for r in sub),
            "mean": sum(pcts) / len(pcts),
            "win": sum(1 for x in pcts if x > 0) / len(pcts),
            "pat_n": len(pat), "pat_rate": len(pat) / len(sub),
            "pat_usd": sum(r["usd"] for r in pat)}


def line(label, a):
    if not a:
        return
    print(f"{label:<26}{a['n']:>6}{a['usd']:>+10.2f}{a['mean']:>+9.3f}{a['win']:>7.3f}"
          f"{a['pat_n']:>7}{a['pat_rate']:>8.3f}{a['pat_usd']:>+10.2f}")


def main() -> int:
    recs = load(75)
    print(f"样本 n={len(recs)}  基线=${sum(r['usd'] for r in recs):+.2f}  "
          f"门判定覆盖={sum(1 for r in recs if r['allow'] is not None)}")

    print("\n===== A. 家族 × 门（USD / 模式率）=====")
    print(f"{'分组':<26}{'n':>6}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'模式n':>7}"
          f"{'模式率':>8}{'模式USD':>10}")
    fams = sorted({r["fam"] for r in recs})
    for fam in fams:
        sub = [r for r in recs if r["fam"] == fam]
        if len(sub) < 5:
            continue
        line(f"{fam} · 全部", agg(sub))
        for flag, tag in ((True, "门放行"), (False, "门拦截")):
            s2 = [r for r in sub if r["allow"] is flag]
            if s2:
                line(f"    └ {tag}", agg(s2))

    print("\n===== B. 反事实：封堵某家族（保留门）=====")
    print(f"{'方案':<26}{'保留n':>6}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'模式n':>7}"
          f"{'模式率':>8}{'模式USD':>10}  逐月USD")
    base = sum(r["usd"] for r in recs)
    cands = ["mid_reversion", "mid_range", "mid_reversion+mid_range", "auto", "long_swing"]
    for name in cands:
        fams_block = set(name.split("+"))
        keep = [r for r in recs if r["fam"] not in fams_block]
        a = agg(keep)
        bym = defaultdict(float)
        for r in keep:
            bym[r["mon"]] += r["usd"]
        bym_s = " ".join(f"{m[5:]}:{v:+.0f}" for m, v in sorted(bym.items()))
        print(f"{name:<26}{a['n']:>6}{a['usd']:>+10.2f}{a['mean']:>+9.3f}{a['win']:>7.3f}"
              f"{a['pat_n']:>7}{a['pat_rate']:>8.3f}{a['pat_usd']:>+10.2f}  {bym_s}"
              f"   (Δ{a['usd']-base:+.2f})")
        blocked = [r for r in recs if r["fam"] in fams_block]
        d = [r["usd"] / r["notional0"] * 100 for r in blocked]
        if len(d) >= 5:
            rnd = random.Random(SEED)
            n = len(d)
            boots = sorted(sum(d[rnd.randrange(n)] for _ in range(n)) / n for _ in range(4000))
            lo, hi = boots[int(0.025 * 4000)], boots[int(0.975 * 4000)]
            print(f"    被封堵 {n} 笔均值={sum(d)/n:+.4f}% CI[{lo:+.4f},{hi:+.4f}] "
                  f"{'显著为负（封堵有效）' if hi < 0 else '不显著(跨0)'}")

    print("\n===== C. 仅在「门放行流」上封堵（生产口径）=====")
    allowed = [r for r in recs if r["allow"] is True]
    print(f"  门放行流 n={len(allowed)} 基线=${sum(r['usd'] for r in allowed):+.2f}")
    print(f"{'方案':<26}{'保留n':>6}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'模式n':>7}"
          f"{'模式率':>8}{'模式USD':>10}")
    for name in cands:
        fams_block = set(name.split("+"))
        keep = [r for r in allowed if r["fam"] not in fams_block]
        a = agg(keep)
        print(f"{name:<26}{a['n']:>6}{a['usd']:>+10.2f}{a['mean']:>+9.3f}{a['win']:>7.3f}"
              f"{a['pat_n']:>7}{a['pat_rate']:>8.3f}{a['pat_usd']:>+10.2f}"
              f"   (Δ{a['usd']-sum(r['usd'] for r in allowed):+.2f})")

    print("\n===== D. walk-forward（前 2/3 选「封堵哪个家族」→ 后 1/3 验证）=====")
    cut = int(len(recs) * 2 / 3)
    tr, te = recs[:cut], recs[cut:]
    best = None
    for fam in fams:
        keep = [r for r in tr if r["fam"] != fam]
        u = sum(r["usd"] for r in keep)
        if best is None or u > best[1]:
            best = (fam, u)
    fam, u_tr = best
    keep_te = [r for r in te if r["fam"] != fam]
    blocked_te = [r for r in te if r["fam"] == fam]
    print(f"  训练最优=封堵 {fam}（训练 ${u_tr:+.2f} vs 实际 "
          f"${sum(r['usd'] for r in tr):+.2f}）")
    print(f"  验证 实际 ${sum(r['usd'] for r in te):+.2f} → 封堵后 {len(keep_te)} 笔 "
          f"${sum(r['usd'] for r in keep_te):+.2f}（剔除 {len(blocked_te)} 笔）")
    d = [r["usd"] / r["notional0"] * 100 for r in blocked_te]
    if len(d) >= 5:
        rnd = random.Random(SEED)
        n = len(d)
        boots = sorted(sum(d[rnd.randrange(n)] for _ in range(n)) / n for _ in range(4000))
        lo, hi = boots[int(0.025 * 4000)], boots[int(0.975 * 4000)]
        print(f"  被剔除 {n} 笔均值={sum(d)/n:+.4f}% CI[{lo:+.4f},{hi:+.4f}] "
              f"{'显著为负' if hi < 0 else '不显著(跨0)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
