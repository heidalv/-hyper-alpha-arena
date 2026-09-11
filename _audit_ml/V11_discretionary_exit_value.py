# -*- coding: utf-8 -*-
"""V11 自由裁量出场的价值分 regime 检验：long 单在 up / chop / down 下，
「纯政策出场」与「实际出场」孰优？→ 决定是否该按 regime 抑制裁量出场。
"""
import sys

import numpy as np
from sqlalchemy import create_engine, text

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.scripts.replay_midlong_policy import (  # noqa: E402
    cost_pct, load_klines, pick, regime_at, sim_exit,
)

ARENA = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")

with ARENA.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows = [dict(r._mapping) for r in c.execute(text("""
        select symbol, side, entry_price, opened_at, close_reason,
               (unrealized_pnl+partial_realized_pnl) as pnl, margin, leverage,
               timeframe_tier, trade_nature, peak_pnl_pct, trough_pnl_pct
        from paper_positions
        where account_id=14 and timeframe_tier in ('mid','long')
          and opened_at >= '2026-08-01'
    """)).fetchall()]

data, daily = load_klines({r["symbol"] for r in rows})

recs = []
for r in rows:
    s = pick(data, r["symbol"])
    if not s:
        continue
    ts = int(r["opened_at"].timestamp())
    i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
    if i is None or i < 24 or i + 2 >= len(s):
        continue
    d_series = daily.get(("asterdex", r["symbol"])) or daily.get(("binance", r["symbol"]))
    reg = regime_at(d_series, ts)
    gross, hold, kind = sim_exit(s, i, r["side"])
    recs.append({
        "side": r["side"], "reg": reg, "policy_net": gross - cost_pct(hold),
        "policy_kind": kind, "policy_hold": hold,
        "actual": float(r["pnl"] or 0), "reason": str(r["close_reason"] or ""),
        "tier": r["timeframe_tier"], "sym": r["symbol"],
    })

longs = [x for x in recs if x["side"] == "long"]
print(f"样本 {len(recs)}（long {len(longs)}）")

def line(name, sub):
    if not sub:
        return
    pn = [x["policy_net"] for x in sub]
    ac = [x["actual"] for x in sub]
    delta = [x["policy_net"] * 1.0 - x["actual"] / 100.0 for x in sub] if False else None
    print(f"  {name:<26} n={len(sub):>3} 政策净均={np.mean(pn):>+7.3f}% "
          f"胜率={sum(1 for v in pn if v>0)/len(pn)*100:>5.1f}% | 实际均={np.mean(ac):>+7.2f} "
          f"合计={sum(ac):>+8.2f}")

print("\n== A) long × 日线 regime：政策 vs 实际 ==")
for g in ("up", "chop", "down"):
    line(f"long/{g}", [x for x in longs if x["reg"] == g])

print("\n== B) long × 实际出场原因：政策反事实 ==")
groups = {}
for x in longs:
    r = x["reason"]
    key = ("thesis_invalidation" if r.startswith("thesis_invalidation") else
           "thesis_should_close" if r.startswith("thesis_should_close") else
           "trend_broken" if r.startswith("trend_broken") else
           "bias_reversal" if "bias_reversal" in r else
           "swing_invalidation" if "swing_invalidation" in r else
           "no_progress" if "no_progress" in r else
           "breakeven_tp" if r.startswith("breakeven") else
           "trail/tp" if r in ("tp", "trailing", "staged_tp", "max_hold_timeout") else
           r[:18] or "?")
    groups.setdefault(key, []).append(x)
for k in sorted(groups, key=lambda k: -len(groups[k])):
    sub = groups[k]
    pn = [x["policy_net"] for x in sub]
    ac = [x["actual"] for x in sub]
    verdict = "裁量出场更好" if np.mean(ac) > np.mean(pn) * 0 else ""
    print(f"  {k:<24} n={len(sub):>3} 实际均={np.mean(ac):>+7.2f} 合计={sum(ac):>+8.2f} | "
          f"政策净均={np.mean(pn):>+7.3f}% 胜率={sum(1 for v in pn if v>0)/len(pn)*100:>5.1f}% "
          f"政策均≈${np.mean(pn)/100*150:>+6.2f}(按$150名义)")

print("\n== C) 综合：long 全部 ==")
line("long 全部", longs)
print(f"  政策净合计（价格口径%）= {sum(x['policy_net'] for x in longs):+.1f}%")
print(f"  实际 PnL 合计 = {sum(x['actual'] for x in longs):+.2f} USD")

print("\n== D) 9/9 后的 long 逐笔 ==")
recent = [x for x in longs if True][-8:]
for x in recent:
    print(f"  {x['sym']:>8} reg={x['reg']:>5} 政策={x['policy_net']:>+7.3f}%({x['policy_kind']},{x['policy_hold']}h) "
          f"实际={x['actual']:>+7.2f} {x['reason'][:26]}")
