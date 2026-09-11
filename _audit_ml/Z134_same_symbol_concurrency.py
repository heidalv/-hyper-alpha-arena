# -*- coding: utf-8 -*-
"""Z134: §24 #21 同标的并发持仓 —— 用**完整历史**重测（轮转修复后样本量大幅提升）。

§33.7 曾测得"并发笔均值 -0.518% vs 单笔 +0.103%，反事实 +$32.13"，但 bootstrap 不显著、
83% 来自单笔。本轮用全量已平仓 mid/long 仓位重跑，检验是否达到显著。
"""
from __future__ import annotations

import random
import statistics as st
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from sqlalchemy import text  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402

db = SessionLocal()
try:
    rows = db.execute(text(
        "select id, symbol, side, timeframe_tier, trade_nature, size, entry_price, close_price, "
        "opened_at, closed_at, partial_realized_pnl, status "
        "from paper_positions where account_id=14 and timeframe_tier in ('mid','long') "
        "and status <> 'open' and closed_at is not null order by opened_at"
    )).fetchall()
    print(f"已平仓 mid/long 仓位 {len(rows)} 笔")

    pos = []
    for r in rows:
        (pid, sym, side, tier, nat, size, ep, cp, o, c, part, status) = r
        try:
            ep = float(ep or 0); cp = float(cp or 0); size = float(size or 0)
        except Exception:
            continue
        if ep <= 0 or cp <= 0 or size <= 0 or o is None or c is None:
            continue
        d = 1 if str(side).lower() == "long" else -1
        gross = (cp - ep) * d * size
        net = gross + float(part or 0)
        notional = ep * size
        pos.append({"id": int(pid), "sym": str(sym).upper(), "tier": str(tier), "o": o, "c": c,
                    "net": net, "notional": notional, "pct": net / notional if notional else 0.0})

    print(f"可用样本 {len(pos)} 笔；净口径总盈亏 ${sum(p['net'] for p in pos):+.2f}")

    # 并发判定：同标的、时间区间重叠（有别的仓位同时在手）
    concurrent, solo = [], []
    for p in pos:
        others = [q for q in pos if q["sym"] == p["sym"] and q["id"] != p["id"]
                  and q["o"] < p["c"] and p["o"] < q["c"] and q["o"] <= p["o"]]
        # 仅统计「同标的、在 p 之前或同时开、且与 p 重叠」的仓位
        if others:
            concurrent.append(p)
        else:
            solo.append(p)

    def stats(xs, label):
        if not xs:
            print(f"  {label}: n=0")
            return
        pcts = [x["pct"] for x in xs]
        nets = [x["net"] for x in xs]
        print(f"  {label}: n={len(xs)} 均值={st.mean(pcts):+.4%} 中位={st.median(pcts):+.4%} "
              f"合计=${sum(nets):+.2f} 胜率={sum(1 for v in pcts if v>0)/len(pcts):.1%}")

    print("\n=== 并发 vs 单笔（同标的）===")
    stats(concurrent, "同标的并发")
    stats(solo, "同标的单笔")

    # bootstrap 均值差
    if concurrent and solo:
        a = [x["pct"] for x in concurrent]
        b = [x["pct"] for x in solo]
        obs = st.mean(a) - st.mean(b)
        rnd = random.Random(20260910)
        diffs = []
        for _ in range(4000):
            sa = [rnd.choice(a) for _ in a]
            sb = [rnd.choice(b) for _ in b]
            diffs.append(st.mean(sa) - st.mean(sb))
        diffs.sort()
        lo, hi = diffs[int(0.025 * len(diffs))], diffs[int(0.975 * len(diffs))]
        print(f"\n  bootstrap 均值差 = {obs:+.4%}  95%CI=[{lo:+.4%}, {hi:+.4%}]  "
              f"{'显著' if lo*hi>0 else '不显著（跨 0）'}")

    print("\n按标的看并发占比:")
    cnt = Counter(p["sym"] for p in concurrent)
    tot = Counter(p["sym"] for p in pos)
    for s, n in tot.most_common(10):
        print(f"   {s:<9} 并发 {cnt.get(s,0):>2}/{n:<3}  合计 ${sum(p['net'] for p in pos if p['sym']==s):+8.2f}")
finally:
    db.close()
