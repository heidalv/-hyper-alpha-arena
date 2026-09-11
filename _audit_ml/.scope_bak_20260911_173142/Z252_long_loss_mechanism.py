# -*- coding: utf-8 -*-
"""[§86-b 核查 2026-09-11 / 目标①] long 层"大亏"的机制：**止损到底有没有在兜**？

上一段（`Z251`）发现：long 层 34 笔里亏 14 笔、平均单笔 **−$19.22（mid 的 6.3 倍）**，
而胜率反而更高（58.8%）⇒ 问题在**亏损单的止血**，不在选方向。本脚本继续往下钻：

  Q7 亏损单**是按哪个通道离场的**？（若很少走 `sl` ⇒ 止损形同虚设）
  Q8 入场时的 **SL 距离**（`|entry−sl|/entry`）与**风险预算**对比：
     `margin×leverage` 决定名义，`PC_RISK_PER_TRADE_PCT_*` 给风险预算 ⇒ 实际亏损是否超预算？
  Q9 逐层对照（mid vs long）同样的三项，找出差异到底在"仓位/止损/滑点"哪一环。
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(ROOT / ".env"), override=False)

from backend.database.connection import SessionLocal  # noqa: E402
from backend.services.exit.channel_breaker_gate import channel_of  # noqa: E402
from sqlalchemy import text  # noqa: E402

NET = ("(case when lower(side) in ('long','buy') then (close_price-entry_price)*size "
       "else -(close_price-entry_price)*size end) "
       "+ coalesce(partial_realized_pnl,0) - coalesce(partial_fee_paid,0)")


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    db = SessionLocal()
    try:
        db.execute(text("set app.is_admin='on'"))
        rows = db.execute(text(f"""
            select upper(symbol), lower(coalesce(timeframe_tier,'?')), lower(coalesce(side,'')),
                   coalesce(close_reason,'?'), {NET} as net,
                   entry_price, close_price, coalesce(size,0),
                   coalesce(margin,0), coalesce(leverage,0), sl_price, tp_price,
                   coalesce(peak_pnl_pct,0)
            from paper_positions
            where status='closed' and closed_at >= now() - interval '{days} day'
              and lower(coalesce(timeframe_tier,'')) in ('mid','long')
              and entry_price is not null and entry_price > 0
        """)).fetchall()
    finally:
        db.close()

    items = []
    for r in rows:
        entry = float(r[5] or 0)
        sl = float(r[10]) if r[10] is not None else None
        side = r[2]
        sl_dist = None
        if sl and entry > 0:
            sl_dist = abs(entry - sl) / entry * 100.0
        notional = float(r[7] or 0) * entry
        items.append({"sym": r[0], "tier": r[1], "side": side, "channel": channel_of(r[3]),
                      "net": float(r[4] or 0), "entry": entry, "close": float(r[6] or 0),
                      "size": float(r[7] or 0), "margin": float(r[8] or 0),
                      "lev": float(r[9] or 0), "sl_dist": sl_dist,
                      "notional": notional, "peak": float(r[12] or 0)})

    print("=" * 100)
    print(f"long 层大亏机制核查（近 {days} 天 mid/long，n={len(items)}）")
    print("=" * 100)

    print("\nQ7. 亏损单的**离场通道**分布（若 `sl` 极少 ⇒ 止损没在兜）")
    for tier in ("mid", "long"):
        ls = [x for x in items if x["tier"] == tier and x["net"] < 0]
        if not ls:
            continue
        ch = defaultdict(lambda: [0, 0.0])
        for x in ls:
            ch[x["channel"]][0] += 1
            ch[x["channel"]][1] += x["net"]
        sl_n = ch.get("sl", [0, 0.0])[0]
        print(f"  {tier:5s} 亏损 {len(ls):>3} 笔｜走 `sl` 的 {sl_n:>2} 笔"
              f"（{100.0*sl_n/len(ls):>4.1f}%）｜其余按金额：")
        for c, (n, v) in sorted(ch.items(), key=lambda kv: kv[1][1])[:6]:
            if c == "sl":
                continue
            print(f"        {c:24s} {n:>2} 笔 {v:>9.2f}")

    print("\nQ8. 仓位 / 止损距离 / 实际亏损（逐层）")
    print(f"  {'层':5s} {'笔数':>4} {'名义均值$':>10} {'保证金均值$':>11} {'杠杆均值':>8} "
          f"{'SL距离均值':>10} {'SL距离中位':>10} {'单笔风险$(=名义×SL)':>18}")
    for tier in ("mid", "long"):
        xs = [x for x in items if x["tier"] == tier]
        if not xs:
            continue
        nots = [x["notional"] for x in xs]
        marg = [x["margin"] for x in xs if x["margin"]]
        levs = [x["lev"] for x in xs if x["lev"]]
        dists = [x["sl_dist"] for x in xs if x["sl_dist"] is not None]
        risk = [x["notional"] * (x["sl_dist"] or 0) / 100.0 for x in xs if x["sl_dist"]]
        srt = sorted(dists)
        print(f"  {tier:5s} {len(xs):>4} {sum(nots)/len(nots):>10.2f} "
              f"{(sum(marg)/len(marg) if marg else 0):>11.2f} "
              f"{(sum(levs)/len(levs) if levs else 0):>8.2f} "
              f"{(sum(dists)/len(dists) if dists else 0):>9.2f}% "
              f"{(srt[len(srt)//2] if srt else 0):>9.2f}% "
              f"{(sum(risk)/len(risk) if risk else 0):>18.2f}")

    print("\nQ9. 亏损单的**实际亏损 vs 单笔风险预算**（预算 = 名义 × SL距离）")
    for tier in ("mid", "long"):
        ls = [x for x in items if x["tier"] == tier and x["net"] < 0 and x["sl_dist"]]
        if not ls:
            continue
        ratios = []
        for x in ls:
            budget = max(1e-9, x["notional"] * x["sl_dist"] / 100.0)
            ratios.append(abs(x["net"]) / budget)
        ratios.sort()
        over = sum(1 for r in ratios if r > 1.0)
        print(f"  {tier:5s} 亏损 {len(ls):>3} 笔｜实际亏损/预算：中位 {ratios[len(ratios)//2]:>5.2f}×"
              f"｜均值 {sum(ratios)/len(ratios):>5.2f}×｜超预算(>1×) {over:>2} 笔"
              f"（{100.0*over/len(ratios):>4.1f}%）")
    print("\n⚠️ 口径：预算用**入场时的 SL 距离**估（未计滑点/资金费/部分平仓）；"
          "\n   `sl_price` 覆盖 3446 行（存量仓可能为空）⇒ 仅统计有 SL 的样本。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
