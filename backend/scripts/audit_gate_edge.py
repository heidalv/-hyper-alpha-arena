# -*- coding: utf-8 -*-
"""learned 门的样本外验证器（第二十二轮）。

对齐 §1 行业基线（walk-forward + 报告 N + 置信区间），回答两个问题：
  1. **跨月稳定性**：门的「放行集 vs 拦截集」差异在每个月是否同向？
  2. **统计显著性**：用 bootstrap 给出均值差 / 大亏率差 / 模式率差的 95% CI。

判定口径（与 §30.1 一致）：总 USD = unrealized + partial_realized − partial_fee；
模式 = 峰值 ≥0.5% 且总 USD <0；大亏 = 总 USD/名义 ≤ -2%。

用法：
  .venv\\Scripts\\python.exe backend/scripts/audit_gate_edge.py --start 2026-07-01 [--tier mid,long] [--boot 4000]
输出：`data/gate_edge_audit.json` + 控制台。

注意：门条件（up+chg∈[3,6) / chop pos≥60&chg≥2）来自 8/10–9/9 样本，
因此 7 月与 9 月（部分）是样本外；本脚本不做重新标定，只做**诚实检验**。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from deep_long_freshness import build_bar_features, learned_ok_prod, load_klines, pick  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
OUT = ROOT / "data" / "gate_edge_audit.json"


def bootstrap_diff(a, b, key, n_boot=4000, seed=20260910):
    """返回 (obs, lo, hi, significant)；a/b 为记录列表，key 取数值字段。"""
    if not a or not b:
        return None
    rnd = random.Random(seed)
    na, nb = len(a), len(b)
    diffs = []
    for _ in range(n_boot):
        ma = sum(a[rnd.randrange(na)][key] for _ in range(na)) / na
        mb = sum(b[rnd.randrange(nb)][key] for _ in range(nb)) / nb
        diffs.append(ma - mb)
    diffs.sort()
    obs = (sum(r[key] for r in a) / na) - (sum(r[key] for r in b) / nb)
    lo, hi = diffs[int(0.025 * n_boot)], diffs[int(0.975 * n_boot)]
    return {"obs": round(obs, 4), "lo": round(lo, 4), "hi": round(hi, 4),
            "significant": bool(lo > 0 or hi < 0)}


def aggregate(rows):
    """按 (月份, tier, 门判定) 聚合出审计指标（纯函数）。"""
    out = defaultdict(lambda: {"n": 0, "usd": 0.0, "pct_sum": 0.0, "win": 0,
                               "big": 0, "pattern": 0, "pattern_usd": 0.0})
    for r in rows:
        k = (r["mon"], r["tier"], "allow" if r["allow"] else "block")
        b = out[k]
        b["n"] += 1
        b["usd"] += r["usd"]
        b["pct_sum"] += r["pct"]
        b["win"] += 1 if r["usd"] > 0 else 0
        b["big"] += 1 if r["pct"] <= -2 else 0
        if r["pattern"]:
            b["pattern"] += 1
            b["pattern_usd"] += r["usd"]
    res = {}
    for k, b in out.items():
        res["|".join(k)] = {
            "n": b["n"], "usd": round(b["usd"], 2),
            "mean_pct": round(b["pct_sum"] / b["n"], 4) if b["n"] else None,
            "win_rate": round(b["win"] / b["n"], 3) if b["n"] else None,
            "big_loss_n": b["big"],
            "pattern_n": b["pattern"], "pattern_usd": round(b["pattern_usd"], 2),
            "pattern_rate": round(b["pattern"] / b["n"], 4) if b["n"] else None,
        }
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-07-01")
    ap.add_argument("--tier", default="mid,long")
    ap.add_argument("--boot", type=int, default=4000)
    args = ap.parse_args()
    tiers = [t.strip() for t in args.tier.split(",") if t.strip()]

    # [§92 修复 2026-09-11 / 缺陷 #75] 账户口径：业绩一律按活跃 PAPER 账户（默认 14）
    from backend.config.audit_scope import account_clause, describe_scope
    ACCT = account_clause()
    print(describe_scope())

    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        poss = [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, timeframe_tier, entry_price, original_size, size,
                   peak_pnl_pct, unrealized_pnl, partial_realized_pnl, partial_fee_paid,
                   opened_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'{ACCT}
              and opened_at >= '{args.start}'
            order by opened_at
        """)).fetchall()]

    h1, d1 = load_klines({p["symbol"] for p in poss})
    feats = {}
    for sym in {p["symbol"] for p in poss}:
        s = pick(h1, sym)
        ds = pick(d1, sym)
        if s is None or ds is None or len(s) < 300 or len(ds) < 70:
            continue
        feats[sym] = (s, build_bar_features(s, ds))

    rows = []
    for p in poss:
        sym = p["symbol"]
        if sym not in feats:
            continue
        s, (reg_arr, pos_arr, chg_arr) = feats[sym]
        entry = float(p["entry_price"] or 0)
        sz0 = float(p["original_size"] or p["size"] or 0)
        if entry <= 0 or sz0 <= 0:
            continue
        ts = int(p["opened_at"].timestamp())
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i == 0 or abs(s[i][0] - ts) > 7200:
            continue
        usd = (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
               - float(p["partial_fee_paid"] or 0))
        pct = usd / (entry * sz0) * 100
        peak = float(p["peak_pnl_pct"] or 0) * 100
        rows.append({
            "mon": str(p["opened_at"])[:7], "tier": str(p["timeframe_tier"]),
            "symbol": sym, "usd": usd, "pct": pct,
            "big": 1.0 if pct <= -2 else 0.0,
            "pattern": (peak >= 0.5 and usd < 0),
            "allow": learned_ok_prod(reg_arr[i], pos_arr[i], chg_arr[i]),
        })

    rep = {"generated_at": datetime.now(timezone.utc).isoformat(),
           "start": args.start, "n": len(rows),
           "cells": aggregate(rows), "bootstrap": {}}

    print(f"样本 n={len(rows)}（{args.start} 起）")
    for tier in tiers:
        sub = [r for r in rows if r["tier"] == tier]
        if not sub:
            continue
        print(f"\n===== tier={tier} =====")
        for scope, grp in [("全期", sub)] + [(m, [r for r in sub if r["mon"] == m])
                                             for m in sorted({r["mon"] for r in sub})]:
            a = [r for r in grp if r["allow"]]
            b = [r for r in grp if not r["allow"]]
            if not a or not b:
                print(f"  {scope:<9} 样本不足 (放行{len(a)}/拦截{len(b)})")
                continue
            d_mean = bootstrap_diff(a, b, "pct", args.boot)
            d_big = bootstrap_diff(a, b, "big", args.boot)
            d_pat = bootstrap_diff(a, b, "pattern", args.boot)
            rep["bootstrap"][f"{tier}|{scope}"] = {
                "n_allow": len(a), "n_block": len(b),
                "mean_pct": d_mean, "big_rate": d_big, "pattern_rate": d_pat,
                "usd_allow": round(sum(r["usd"] for r in a), 2),
                "usd_block": round(sum(r["usd"] for r in b), 2),
            }
            print(f"  {scope:<9} 放行n={len(a):>3}(USD {sum(r['usd'] for r in a):>+8.2f}) "
                  f"拦截n={len(b):>3}(USD {sum(r['usd'] for r in b):>+8.2f})")
            for label, d in (("均值差%", d_mean), ("大亏率差", d_big), ("模式率差", d_pat)):
                flag = "显著" if d["significant"] else "不显著"
                print(f"      {label:<8} {d['obs']:>+7.3f} CI[{d['lo']:>+7.3f},{d['hi']:>+7.3f}] {flag}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
