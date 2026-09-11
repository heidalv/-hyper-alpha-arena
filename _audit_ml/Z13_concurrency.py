# -*- coding: utf-8 -*-
"""Z13：同标的并发持仓（组合层杠杆）——「一个标的同一时间只持一笔」是否更好？

Z12 发现：同币种**并发**持仓 48 笔 -$63.75（均值 -0.518%、胜率 0.250），
而单笔 235 笔 +$99.85（均值 +0.103%、胜率 0.362）——差 -0.62%/笔。
机制假设：并发 = 相关风险翻倍（同标的同方向重复暴露），且往往发生在
系统「很有信心」的趋势标的里（VIRTUAL/SOL/XPL），一旦反转就是双倍回吐。

检验：
A. 并发结构（同层并发 / 跨层 mid+long 并发 / 逐月 / 逐标的）；
B. 反事实：每个并发组只保留**先开**的那笔 → 总 USD / 逐月 / walk-forward；
C. bootstrap：被跳过的「后开笔」pct 均值是否显著为负；
D. 敏感性：只限制「同层」并发 vs 「跨层」也限制。
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

from Z11_exit_delay import channel  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
SEED = 20260910


def build(days=75):
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        poss = [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, side, timeframe_tier, trade_nature, entry_price, size,
                   original_size, peak_pnl_pct, unrealized_pnl, partial_realized_pnl,
                   partial_fee_paid, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '{int(days)} days'
            order by opened_at
        """)).fetchall()]
    recs = []
    for p in poss:
        sz0 = float(p["original_size"] or p["size"] or 0)
        entry = float(p["entry_price"] or 0)
        if sz0 <= 0 or entry <= 0 or not p["opened_at"] or not p["closed_at"]:
            continue
        recs.append({
            "id": p["id"], "symbol": p["symbol"], "tier": str(p["timeframe_tier"]),
            "nature": str(p["trade_nature"] or ""), "notional0": sz0 * entry,
            "base_usd": (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                         - float(p["partial_fee_paid"] or 0)),
            "db_peak": float(p["peak_pnl_pct"] or 0) * 100.0,
            "open_ts": int(p["opened_at"].timestamp()),
            "close_ts": int(p["closed_at"].timestamp()),
            "mon": str(p["opened_at"])[:7],
            "ch": channel(str(p["close_reason"] or "")),
        })
    recs.sort(key=lambda r: r["open_ts"])
    return recs


def overlap_partner(r, recs):
    for o in recs:
        if o is not r and o["symbol"] == r["symbol"] and o["open_ts"] < r["open_ts"] < o["close_ts"]:
            return o
    return None


def main() -> int:
    recs = build(75)
    base = sum(r["base_usd"] for r in recs)
    print(f"样本 n={len(recs)} 基线=${base:+.2f}")

    # ---------- A. 并发结构 ----------
    groups = defaultdict(list)
    for r in recs:
        p = overlap_partner(r, recs)
        if p is None:
            continue
        key = (r["symbol"], min(r["open_ts"], p["open_ts"]))
        groups[key].append(r)
        groups[key].append(p)
    print(f"\n===== A. 并发组 n组={len(groups)} =====")
    print(f"{'symbol':<10}{'组':>4}{'跨层':>6}{'总USD':>10}{'笔数':>6}{'月份':>8}")
    cross = same = 0
    for (sym, _t), sub in sorted(groups.items(), key=lambda x: x[0][1]):
        uniq = {r["id"]: r for r in sub}
        sub = list(uniq.values())
        tiers = {r["tier"] for r in sub}
        if len(tiers) > 1:
            cross += 1
        else:
            same += 1
        print(f"{sym:<10}{len(sub):>4}{('是' if len(tiers)>1 else '否'):>6}"
              f"{sum(r['base_usd'] for r in sub):>+10.2f}{len(sub):>6}{sub[0]['mon']:>8}")
    print(f"  同层并发组={same} 跨层并发组={cross}")

    # ---------- B. 反事实 ----------
    print("\n===== B. 反事实：每个并发组只保留先开的一笔 =====")
    def apply_rule(mode):
        """mode: 'none' | 'same_tier' | 'all'；返回 (keep, skip)"""
        keep, skip = [], []
        open_syms = defaultdict(list)  # sym -> 已保留的未平仓笔
        for r in recs:
            block = False
            for o in open_syms[r["symbol"]]:
                if o["close_ts"] > r["open_ts"]:
                    if mode == "all" or (mode == "same_tier" and o["tier"] == r["tier"]):
                        block = True
                        break
            (skip if block else keep).append(r)
            if not block:
                open_syms[r["symbol"]].append(r)
        return keep, skip

    print(f"{'规则':<20}{'保留n':>7}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'模式率':>8}  逐月USD")
    for mode, label in (("none", "现行（不限制）"), ("same_tier", "仅限同层"),
                        ("all", "同标的一律不并发")):
        keep, skip = apply_rule(mode)
        usds = [r["base_usd"] for r in keep]
        pcts = [u / r["notional0"] * 100 for u, r in zip(usds, keep)]
        bym = defaultdict(float)
        for u, r in zip(usds, keep):
            bym[r["mon"]] += u
        bym_s = " ".join(f"{m[5:]}:{v:+.0f}" for m, v in sorted(bym.items()))
        pat = sum(1 for r in keep if r["db_peak"] >= 0.5 and r["base_usd"] < 0)
        print(f"{label:<20}{len(keep):>7}{sum(usds):>+10.2f}{sum(pcts)/len(pcts):>+9.3f}"
              f"{sum(1 for x in pcts if x>0)/len(pcts):>7.3f}{pat/len(keep):>8.3f}  {bym_s}")

    # ---------- C. bootstrap ----------
    print("\n===== C. bootstrap：被跳过的「后开笔」pct 均值 =====")
    for mode in ("same_tier", "all"):
        _keep, skip = apply_rule(mode)
        d = [r["base_usd"] / r["notional0"] * 100 for r in skip]
        if len(d) < 5:
            print(f"  {mode}: 跳过 {len(d)} 笔，样本不足")
            continue
        rnd = random.Random(SEED)
        n = len(d)
        boots = sorted(sum(d[rnd.randrange(n)] for _ in range(n)) / n for _ in range(4000))
        lo, hi = boots[int(0.025 * 4000)], boots[int(0.975 * 4000)]
        print(f"  {mode}: n={n} 均值={sum(d)/n:+.4f}% CI[{lo:+.4f},{hi:+.4f}] "
              f"{'显著为负（限并发有效）' if hi < 0 else '不显著(跨0)'}")

    # ---------- D. walk-forward ----------
    print("\n===== D. walk-forward（前 60% 选规则 → 后 40% 验证）=====")
    cut = int(len(recs) * 0.6)
    tr, te = recs[:cut], recs[cut:]
    best = None
    for mode, label in (("none", "现行"), ("same_tier", "仅限同层"), ("all", "一律不并发")):
        _keep, _skip = apply_rule(mode)
        keep_ids = {r["id"] for r in _keep}
        u = sum(r["base_usd"] for r in tr if r["id"] in keep_ids)
        if best is None or u > best[1]:
            best = ((mode, label), u)
    mode, label = best[0]
    keep, _skip = apply_rule(mode)
    keep_ids = {r["id"] for r in keep}
    te_base = sum(r["base_usd"] for r in te)
    te_var = sum(r["base_usd"] for r in te if r["id"] in keep_ids)
    print(f"  训练最优={label}（训练 ${best[1]:+.2f}）")
    print(f"  验证集 实际${te_base:+.2f} → 规则后${te_var:+.2f}（差 ${te_var-te_base:+.2f}，"
          f"剔除 {sum(1 for r in te if r['id'] not in keep_ids)} 笔）")

    # ---------- E. 留一法：改善是否由单笔事件驱动 ----------
    print("\n===== E. 留一法（一律不并发）：剔除任一被跳过的笔后，改善还剩多少 =====")
    _keep, skip = apply_rule("all")
    base_usd = sum(r["base_usd"] for r in recs)
    print("  被跳过的 22 笔明细（按 USD 升序）：")
    for r in sorted(skip, key=lambda x: x["base_usd"]):
        print(f"    #{r['id']} {r['symbol']:<9}{r['tier']:<6}{r['mon']} "
              f"${r['base_usd']:>+8.2f}  峰值={r['db_peak']:>5.2f}%  {r['ch']}")
    print(f"  跳过合计=${sum(r['base_usd'] for r in skip):+.2f} → 改善=${-sum(r['base_usd'] for r in skip):+.2f}")
    loo = []
    for r in skip:
        gain = -(sum(x["base_usd"] for x in skip) - r["base_usd"])
        loo.append((gain, r))
    loo.sort(key=lambda x: x[0])
    print(f"  留一法改善区间：最差 ${loo[0][0]:+.2f}（剔除 {loo[0][1]['symbol']} 那笔后）"
          f" ~ 最好 ${loo[-1][0]:+.2f}")
    neg = sum(1 for g, _ in loo if g <= 0)
    print(f"  剔除任一笔后改善转负的次数={neg}/{len(loo)}"
          f" → {'单事件驱动，不稳健' if neg > 0 else '较稳健'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
