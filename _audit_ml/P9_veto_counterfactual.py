# -*- coding: utf-8 -*-
"""P9 K线趋势否决反事实：若历史 MR 入场时应用 veto，能拦掉多少亏损。
费用口径：8bp 往返 × 名义（真实 taker）。
"""
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

ARENA = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")

klines = {}
with MARKET.connect() as c:
    c.execute(text("set statement_timeout='600000'"))
    for s, in c.execute(text("select distinct symbol from crypto_klines where period='5m' and exchange='asterdex'")):
        rows = c.execute(text("""select timestamp, close_price from crypto_klines
                                 where period='5m' and exchange='asterdex' and symbol=:s order by timestamp"""),
                         {"s": s}).fetchall()
        if len(rows) > 350:
            klines[s] = (np.array([int(r[0]) for r in rows]), np.array([float(r[1]) for r in rows]))

with ARENA.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows = [dict(r._mapping) for r in c.execute(text("""
        select symbol, side, entry_price, close_price, opened_at, closed_at,
               unrealized_pnl, partial_realized_pnl, close_reason, leverage, margin
        from paper_positions where strategy_id like 'scalp_mr_%' and status='closed'
        order by opened_at
    """)).fetchall()]

def veto(sym, ts):
    d = klines.get(sym)
    if d is None:
        return None  # 无数据 → 无法判定（fail-open）
    t, c = d
    i = int(np.searchsorted(t, ts, side="right") - 1)
    if i < 289 or i >= len(c) - 1:
        return None
    chg24 = c[i] / c[i - 289] - 1
    chg1 = c[i] / c[i - 13] - 1
    if abs(chg24) >= 0.12 or abs(chg1) >= 0.05:
        return "extreme"
    if abs(chg24) >= 0.04 and (chg1 == 0 or chg1 * chg24 > 0):
        return "trend"
    return False

recs = []
for r in rows:
    ts = int(r["opened_at"].timestamp())
    v = veto(r["symbol"], ts)
    if v is None:
        continue
    gross = float(r["unrealized_pnl"] or 0) + float(r["partial_realized_pnl"] or 0)
    notional = abs(float(r["margin"] or 0)) * float(r["leverage"] or 1)
    net = gross - notional * 0.0008
    recs.append({"side": r["side"], "gross": gross, "net": net,
                 "reason": r["close_reason"], "veto": v, "opened_at": r["opened_at"]})

n = len(recs)
blocked = [r for r in recs if r["veto"]]
passed = [r for r in recs if not r["veto"]]

def agg(grp, name):
    g = sum(r["gross"] for r in grp)
    net = sum(r["net"] for r in grp)
    sl = sum(1 for r in grp if r["reason"] == "sl")
    print(f"{name}: n={len(grp):>4} gross={g:+8.2f} 费后净={net:+8.2f} 单笔净={net/max(len(grp),1):+.3f} sl率={sl/len(grp)*100:4.1f}%")

print(f"可判定样本 {n} 笔")
agg(blocked, "否决拦截(trend/extreme)")
agg(passed, "放行(真震荡)")
for v in ("trend", "extreme"):
    agg([r for r in recs if r["veto"] == v], f"  其中 {v}")

# 时间外：8/24 前后
for lab, lo, hi in (("8/24 前", None, pd.Timestamp("2026-08-24")), ("8/24 后", pd.Timestamp("2026-08-24"), None)):
    sub_b = [r for r in blocked if (lo is None or r["opened_at"] >= lo) and (hi is None or r["opened_at"] < hi)]
    sub_p = [r for r in passed if (lo is None or r["opened_at"] >= lo) and (hi is None or r["opened_at"] < hi)]
    print(f"\n{lab}: 拦截 {len(sub_b)} 笔 费后净 {sum(r['net'] for r in sub_b):+.2f} | "
          f"放行 {len(sub_p)} 笔 费后净 {sum(r['net'] for r in sub_p):+.2f}")

# 否决后剩余样本是否仍有任何正口袋（方向×安静特征太复杂，这里只按方向）
print("\n放行样本按方向：")
for side in ("long", "short"):
    ss = [r for r in passed if r["side"] == side]
    if ss:
        print(f"  {side}: n={len(ss)} gross={sum(r['gross'] for r in ss):+.2f} "
              f"费后净={sum(r['net'] for r in ss):+.2f} 单笔={sum(r['net'] for r in ss)/len(ss):+.3f}")
