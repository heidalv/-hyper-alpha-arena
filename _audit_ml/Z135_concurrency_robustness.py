# -*- coding: utf-8 -*-
"""Z135: 同标的并发劣势的**稳健性**检验（n=18 的小样本，均值易被尾部驱动）。

检查：
  1. bootstrap 中位数差 / 胜率差；
  2. 留一法（leave-one-out）：剔除最差 1-3 笔后差是否仍显著；
  3. 换定义（对称并发：任一同标的仓位与其时间重叠即计入两侧）；
  4. 分 tier（mid / long）看是否只在一侧。
"""
from __future__ import annotations

import random
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from sqlalchemy import text  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402

RND = random.Random(20260910)


def boot_diff(a, b, stat=st.mean, n=4000):
    obs = stat(a) - stat(b)
    ds = []
    for _ in range(n):
        sa = [RND.choice(a) for _ in a]
        sb = [RND.choice(b) for _ in b]
        ds.append(stat(sa) - stat(sb))
    ds.sort()
    return obs, ds[int(0.025 * len(ds))], ds[int(0.975 * len(ds))]


db = SessionLocal()
try:
    rows = db.execute(text(
        "select id, symbol, side, timeframe_tier, size, entry_price, close_price, opened_at, "
        "closed_at, partial_realized_pnl from paper_positions "
        "where account_id=14 and timeframe_tier in ('mid','long') and status <> 'open' "
        "and closed_at is not null order by opened_at"
    )).fetchall()
    pos = []
    for r in rows:
        pid, sym, side, tier, size, ep, cp, o, c, part = r
        try:
            ep = float(ep or 0); cp = float(cp or 0); size = float(size or 0)
        except Exception:
            continue
        if ep <= 0 or cp <= 0 or size <= 0 or o is None or c is None:
            continue
        d = 1 if str(side).lower() == "long" else -1
        net = (cp - ep) * d * size + float(part or 0)
        noti = ep * size
        pos.append({"id": int(pid), "sym": str(sym).upper(), "tier": str(tier), "o": o, "c": c,
                    "net": net, "pct": net / noti if noti else 0.0})

    def group(definition: str):
        conc, solo = [], []
        for p in pos:
            others = [q for q in pos if q["sym"] == p["sym"] and q["id"] != p["id"]
                      and q["o"] < p["c"] and p["o"] < q["c"]]
            if definition == "asym":
                others = [q for q in others if q["o"] <= p["o"]]
            (conc if others else solo).append(p)
        return conc, solo

    for dname in ("asym", "sym"):
        conc, solo = group(dname)
        a = [x["pct"] for x in conc]
        b = [x["pct"] for x in solo]
        if not a or not b:
            continue
        print(f"\n=== 定义={dname}（并发 n={len(a)} / 单笔 n={len(b)}）===")
        print(f"  均值差 {st.mean(a)-st.mean(b):+.4%} | 中位差 {st.median(a)-st.median(b):+.4%} | "
              f"胜率 {sum(1 for x in a if x>0)/len(a):.1%} vs {sum(1 for x in b if x>0)/len(b):.1%}")
        for label, fn in (("均值", st.mean), ("中位", st.median)):
            obs, lo, hi = boot_diff(a, b, stat=fn)
            print(f"  bootstrap {label}差 = {obs:+.4%} 95%CI=[{lo:+.4%}, {hi:+.4%}] "
                  f"{'显著' if lo*hi>0 else '不显著'}")
        # 留一法：剔除并发组最差 k 笔
        for k in (1, 2, 3):
            if len(a) <= k:
                break
            a2 = sorted(a)[:len(a)-k]
            obs, lo, hi = boot_diff(a2, b)
            print(f"  剔除最差 {k} 笔后：均值差 {obs:+.4%} CI=[{lo:+.4%}, {hi:+.4%}] "
                  f"{'显著' if lo*hi>0 else '不显著'}")
        # 分 tier
        for t in ("mid", "long"):
            at = [x["pct"] for x in conc if x["tier"] == t]
            bt = [x["pct"] for x in solo if x["tier"] == t]
            if at and bt:
                print(f"  tier={t}: 并发 n={len(at)} 均值 {st.mean(at):+.4%} | "
                      f"单笔 n={len(bt)} 均值 {st.mean(bt):+.4%} 差 {st.mean(at)-st.mean(bt):+.4%}")
    # 最差几笔
    conc, _ = group("asym")
    print("\n并发组明细（按 pct 升序）：")
    for x in sorted(conc, key=lambda z: z["pct"])[:6]:
        print(f"   #{x['id']} {x['sym']:<9} {x['tier']:<5} pct={x['pct']:+.3%} net=${x['net']:+.2f}")
finally:
    db.close()
