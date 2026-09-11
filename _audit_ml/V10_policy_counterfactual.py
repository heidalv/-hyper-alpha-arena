# -*- coding: utf-8 -*-
"""V10 决定性反事实：把「纯 ExitPolicy 出场」（SL6%/追踪3-1.5%/168h）套在
paper 账户 14 的 179 笔 mid/long 入场信号上，按 24h 区间分位分桶，
回答：位置闸「应拒」的那一半，在纯政策出线下到底赚不赚钱？
"""
import sys

import numpy as np
from sqlalchemy import create_engine, text

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.scripts.replay_midlong_policy import (  # noqa: E402
    cost_pct,
    load_klines,
    location_blocked,
    pick,
    regime_at,
    sim_exit,
)

ARENA = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")

with ARENA.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows = [dict(r._mapping) for r in c.execute(text("""
        select symbol, side, entry_price, opened_at, close_reason,
               (unrealized_pnl+partial_realized_pnl) as pnl, margin, leverage,
               timeframe_tier, trade_nature
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
    entry = s[i][1]
    win = s[max(0, i - 23): i + 1]
    hi = max(x[2] for x in win)
    lo = min(x[3] for x in win)
    pos = (entry - lo) / (hi - lo) * 100 if hi > lo else 50.0
    ds = None
    d_series = daily.get(("asterdex", r["symbol"])) or daily.get(("binance", r["symbol"]))
    reg = regime_at(d_series, ts)
    blocked, why = location_blocked(s, i, r["side"], reg)
    gross, hold, kind = sim_exit(s, i, r["side"])
    net = gross - cost_pct(hold)
    notional = abs(float(r["margin"] or 0)) * float(r["leverage"] or 1)
    recs.append({
        "pos": pos, "reg": reg, "blocked": blocked, "why": why,
        "policy_net": net, "kind": kind, "hold": hold,
        "actual_pnl": float(r["pnl"] or 0), "notional": notional,
        "side": r["side"], "reason": r["close_reason"],
    })

print(f"可用样本 {len(recs)} / {len(rows)}")

def stat(name, sub):
    if not sub:
        return
    nets = [x["policy_net"] for x in sub]
    acts = [x["actual_pnl"] for x in sub]
    wr = sum(1 for v in nets if v > 0) / len(nets) * 100
    print(f"  {name:<30} n={len(sub):>3} 政策净均={np.mean(nets):>+7.3f}% "
          f"胜率={wr:>5.1f}% 实际PnL均={np.mean(acts):>+7.2f} 实际合计={sum(acts):>+8.2f}")

print("\n== A) 纯政策出线：按 24h 分位桶 ==")
for b in range(5):
    stat(f"{b*20}-{b*20+20}%", [x for x in recs if b * 20 <= x["pos"] < b * 20 + 20])

print("\n== B) 纯政策出线：位置闸应拒 / 放行 ==")
stat("闸应拒(pos>=60 long)", [x for x in recs if x["blocked"]])
stat("闸放行", [x for x in recs if not x["blocked"]])
print("  拒因分布:", {k: sum(1 for x in recs if x["why"].startswith(k)) for k in ("loc_high", "knife", "chase", "loc_low", "")})

print("\n== C) 纯政策出线：方向 ==")
stat("long", [x for x in recs if x["side"] == "long"])
stat("short", [x for x in recs if x["side"] == "short"])

print("\n== D) 纯政策出线 全局 vs 实际 ==")
stat("全局", recs)
print("  政策出线分布:", {k: sum(1 for x in recs if x["kind"] == k) for k in ("sl", "trail", "timeout")})
print(f"  政策净合计={sum(x['policy_net'] for x in recs):+.1f}%  实际PnL合计={sum(x['actual_pnl'] for x in recs):+.2f}")

print("\n== E) 9/9 后逐笔（政策 vs 实际）==")
for x in [r for r in recs if True][-12:]:
    print(f"  pos={x['pos']:>5.1f}% reg={x['reg']:>7} 政策净={x['policy_net']:>+7.3f}% ({x['kind']},{x['hold']}h) "
          f"实际={x['actual_pnl']:>+7.2f} {str(x['reason'] or '')[:22]}")
