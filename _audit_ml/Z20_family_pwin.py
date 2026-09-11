# -*- coding: utf-8 -*-
"""Z20：入场来源家族归因 + 元模型 pwin 检验。

来源 = `paper_positions.strategy_id`（tpl_mid_reversion_*/tpl_mid_range_*/tpl_long_*/gen_*/auto_*）。
入场时元模型信息 = `trade_facts.factor_exposures->'fusion'->'tags'` 里的
`pwin`（预测胜率）/`size_mult`/`factor_score`/`reason`。

问题：
A. 「先盈利后大亏」是否集中在某个来源家族？
B. 入场时就能看到的 `pwin` 是否能区分模式笔？按 pwin 过滤是否稳健（walk-forward+bootstrap）？
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
SEED = 20260910


def family(sid: str) -> str:
    s = (sid or "").lower()
    if s.startswith("tpl_mid_reversion"):
        return "mid_reversion"
    if s.startswith("tpl_mid_range"):
        return "mid_range"
    if s.startswith("tpl_mid_swing"):
        return "mid_swing"
    if s.startswith("tpl_mid_bull"):
        return "mid_bull"
    if s.startswith("tpl_long_swing"):
        return "long_swing"
    if s.startswith("tpl_long_mean_reversion"):
        return "long_reversion"
    if s.startswith("tpl_long"):
        return "long_other"
    if s.startswith("tpl_pro"):
        return "pro"
    if s.startswith("gen_"):
        return "gen"
    if s.startswith("auto_"):
        return "auto"
    if s.startswith("trend_e1"):
        return "trend_e1"
    if s.startswith("scalp"):
        return "scalp"
    return "other"


def load(days=75):
    eng = create_engine(URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        poss = [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, timeframe_tier, strategy_id, trade_nature, entry_price,
                   original_size, size, peak_pnl_pct, unrealized_pnl, partial_realized_pnl,
                   partial_fee_paid, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '{int(days)} days'
            order by opened_at
        """)).fetchall()]
        facts = {}
        for r in c.execute(text("""
            select position_id, factor_exposures from trade_facts
            where tier in ('mid','long') and factor_exposures is not null
        """)).fetchall():
            fe = r[1]
            if isinstance(fe, str):
                try:
                    fe = json.loads(fe)
                except Exception:
                    continue
            if not isinstance(fe, dict):
                continue
            tags = ((fe.get("fusion") or {}).get("tags") or {})
            if tags:
                facts[str(r[0])] = tags
    recs = []
    for p in poss:
        sz0 = float(p["original_size"] or p["size"] or 0)
        entry = float(p["entry_price"] or 0)
        if sz0 <= 0 or entry <= 0:
            continue
        tags = facts.get(str(p["id"])) or {}
        recs.append({
            "id": p["id"], "symbol": p["symbol"], "tier": str(p["timeframe_tier"]),
            "sid": str(p["strategy_id"] or ""), "fam": family(str(p["strategy_id"] or "")),
            "nature": str(p["trade_nature"] or ""),
            "notional0": sz0 * entry,
            "usd": (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                    - float(p["partial_fee_paid"] or 0)),
            "peak": float(p["peak_pnl_pct"] or 0) * 100,
            "pwin": tags.get("pwin"), "size_mult": tags.get("size_mult"),
            "factor_score": tags.get("factor_score"), "fusion_reason": tags.get("reason"),
            "mon": str(p["opened_at"])[:7],
        })
    return recs


def med(xs):
    v = sorted(xs)
    return v[len(v) // 2] if v else float("nan")


def main() -> int:
    recs = load(75)
    print(f"样本 n={len(recs)}（近 75 天 mid/long）  基线总USD=${sum(r['usd'] for r in recs):+.2f}")
    print(f"带 pwin 的笔数={sum(1 for r in recs if r['pwin'] is not None)}")

    # ---------- A. 来源家族 ----------
    print("\n===== A. 来源家族（n≥5）=====")
    print(f"{'家族':<18}{'n':>5}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'模式n':>7}"
          f"{'模式USD':>10}{'模式率':>8}{'≤-2%':>7}  逐月USD")
    byfam = defaultdict(list)
    for r in recs:
        byfam[r["fam"]].append(r)
    rows = []
    for fam, sub in byfam.items():
        if len(sub) < 5:
            continue
        usd = sum(r["usd"] for r in sub)
        pat = [r for r in sub if r["peak"] >= 0.5 and r["usd"] < 0]
        pcts = [r["usd"] / r["notional0"] * 100 for r in sub]
        bym = defaultdict(float)
        for r in sub:
            bym[r["mon"]] += r["usd"]
        bym_s = " ".join(f"{m[5:]}:{v:+.0f}" for m, v in sorted(bym.items()))
        rows.append((usd, fam, sub, pat, pcts, bym_s))
    for usd, fam, sub, pat, pcts, bym_s in sorted(rows):
        print(f"{fam:<18}{len(sub):>5}{usd:>+10.2f}{sum(pcts)/len(pcts):>+9.3f}"
              f"{sum(1 for x in pcts if x>0)/len(pcts):>7.3f}{len(pat):>7}"
              f"{sum(r['usd'] for r in pat):>+10.2f}{len(pat)/len(sub):>8.3f}"
              f"{sum(1 for x in pcts if x<=-2):>7}  {bym_s}")
    print(f"  小家族合计 n={sum(len(v) for k,v in byfam.items() if len(v)<5)}")

    # ---------- B. pwin 检验 ----------
    have = [r for r in recs if r["pwin"] is not None]
    print(f"\n===== B. 元模型 pwin 检验（n={len(have)}）=====")
    if have:
        win = [r for r in have if r["usd"] > 0]
        pat = [r for r in have if r["peak"] >= 0.5 and r["usd"] < 0]
        print(f"  全体 pwin 中位={med([float(r['pwin']) for r in have]):.3f}")
        print(f"  盈利笔 n={len(win)} pwin 中位={med([float(r['pwin']) for r in win]):.3f}")
        print(f"  模式笔 n={len(pat)} pwin 中位="
              f"{med([float(r['pwin']) for r in pat]) if pat else float('nan'):.3f}")
        print(f"\n  {'pwin 阈值':<14}{'保留n':>7}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'模式率':>8}")
        for thr in (0.3, 0.4, 0.5, 0.55, 0.6, 0.65):
            keep = [r for r in have if float(r["pwin"]) >= thr]
            if len(keep) < 8:
                continue
            pcts = [r["usd"] / r["notional0"] * 100 for r in keep]
            p2 = sum(1 for r in keep if r["peak"] >= 0.5 and r["usd"] < 0)
            print(f"  ≥{thr:<13}{len(keep):>7}{sum(r['usd'] for r in keep):>+10.2f}"
                  f"{sum(pcts)/len(pcts):>+9.3f}"
                  f"{sum(1 for x in pcts if x>0)/len(pcts):>7.3f}{p2/len(keep):>8.3f}")
        # walk-forward：前 2/3 选阈值 → 后 1/3 验证
        cut = int(len(have) * 2 / 3)
        tr, te = have[:cut], have[cut:]
        best = None
        for thr in (0.3, 0.4, 0.5, 0.55, 0.6, 0.65):
            k = [r for r in tr if float(r["pwin"]) >= thr]
            if len(k) < 8:
                continue
            u = sum(r["usd"] for r in k)
            if best is None or u > best[1]:
                best = (thr, u)
        if best:
            thr = best[0]
            kte = [r for r in te if float(r["pwin"]) >= thr]
            print(f"\n  walk-forward 训练最优 pwin≥{thr}（训练 ${best[1]:+.2f}）")
            print(f"  验证 实际 ${sum(r['usd'] for r in te):+.2f} → 过滤后 {len(kte)} 笔 "
                  f"${sum(r['usd'] for r in kte):+.2f}")
            d = [-r["usd"] / r["notional0"] * 100 for r in te if float(r["pwin"]) < thr]
            if len(d) >= 5:
                rnd = random.Random(SEED)
                n = len(d)
                boots = sorted(sum(d[rnd.randrange(n)] for _ in range(n)) / n
                               for _ in range(4000))
                lo, hi = boots[int(0.025 * 4000)], boots[int(0.975 * 4000)]
                print(f"  被过滤 {n} 笔均值={sum(d)/n:+.4f}% CI[{lo:+.4f},{hi:+.4f}] "
                      f"{'显著为负（过滤有效）' if hi < 0 else '不显著(跨0)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
