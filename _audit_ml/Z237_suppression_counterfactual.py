# -*- coding: utf-8 -*-
"""[§83 度量 2026-09-11 / 目标①] **反事实代理**：被抑制通道"再持有"会更好还是更差？

问题：P19-B 抑制 `mid|trend_broken`、`mid|midlong` 等通道的离场 ⇒ 等价于"**再持有一段时间**"。
     历史数据里这些离场是"砍在继续下跌之前"（抑制=省钱）还是"砍在反弹之前"（抑制=更亏）？

为什么用**代理**而不是完整回放：本仓库没有本地 OHLC 表（只有 `kline_research_log` 476 行，
行情在 data-center 外部服务里），无法逐 tick 回放。因此用**同一标的的下一笔平仓价**作为
"之后的价格"代理（`P1`），并给出两个变体：
  * `exit_proxy`：用下一笔平仓的**成交价**（≈ 那一刻的市场价）
  * `entry_proxy`：用下一笔平仓的**开仓价**（≈ 更早一点的市场价）
两者方向一致时结论可信；不一致则只报区间、不下结论。

反事实净额（仅价格项，不含费用）：`(P1 − P0) × size × dir`，`dir=+1` 多头。
与"实际净额"比较：`delta = 反事实 − 实际`（**>0 = 当时该继续持有**）。
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
from backend.services.exit.channel_breaker_gate import channel_of, is_protected  # noqa: E402
from sqlalchemy import text  # noqa: E402

NET = ("(case when lower(side) in ('long','buy') then (close_price-entry_price)*size "
       "else -(close_price-entry_price)*size end) "
       "+ coalesce(partial_realized_pnl,0) - coalesce(partial_fee_paid,0)")
HORIZON_H = 48.0


def counterfactual_delta(*, size: float, side: str, p0: float, p1: float,
                         actual_net: float) -> float:
    """纯函数：`持有代理 − 实际净额`（**>0 = 当时继续持有更好 ⇒ 抑制该通道更亏**）。

    符号约定（这里错一个符号就会把结论反过来，故单列并用契约测试锁住）：
      * 价格项 = `(p1 − p0) × size × dir`，`dir = +1`（long/buy）/ `−1`（short/sell）；
      * `p0` = 本次离场价，`p1` = 之后某个时点的价格代理；
      * 只比**价格项**与**实际净额**（含费用/部分平仓），因此 `delta` 是"延后离场"的
        近似收益，不含延后期间的费用与资金费 —— 报告里必须写明这一口径限制。
    """
    d = 1.0 if str(side or "").lower() in ("long", "buy") else -1.0
    cf = (float(p1) - float(p0)) * float(size) * d
    return cf - float(actual_net)


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    db = SessionLocal()
    try:
        db.execute(text("set app.is_admin='on'"))
        rows = db.execute(text(f"""
            select id, upper(symbol), lower(coalesce(side,'')), size, close_price, entry_price,
                   coalesce(close_reason,'?'), closed_at, {NET} as net,
                   lower(coalesce(timeframe_tier,'?'))
            from paper_positions
            where status='closed' and closed_at >= now() - interval '{days} day'
              and lower(coalesce(timeframe_tier,'')) in ('mid','long')
            order by closed_at
        """)).fetchall()
        all_closes = db.execute(text(f"""
            select upper(symbol), to_char(closed_at,'YYYY-MM-DD HH24:MI:SS'), close_price, entry_price
            from paper_positions
            where status='closed' and closed_at >= now() - interval '{days + 5} day'
            order by closed_at
        """)).fetchall()
    finally:
        db.close()

    by_sym = defaultdict(list)
    for sym, ts, cp, ep in all_closes:
        by_sym[sym].append((ts, float(cp or 0), float(ep or 0)))

    agg = defaultdict(lambda: {"n": 0, "actual": 0.0, "cf_exit": 0.0, "cf_entry": 0.0,
                               "matched": 0, "conflict": 0})
    for pid, sym, side, size, cp, ep, reason, closed_at, net, tier in rows:
        ch = channel_of(reason)
        key = f"{tier}|{ch}"
        rec = agg[key]
        rec["n"] += 1
        rec["actual"] += float(net or 0)
        if is_protected(ch):
            continue
        t0 = closed_at.strftime("%Y-%m-%d %H:%M:%S")
        nxt = next(((t, c, e) for (t, c, e) in by_sym.get(sym, []) if t > t0), None)
        if not nxt:
            continue
        t1, p1c, p1e = nxt
        from datetime import datetime
        dt = (datetime.strptime(t1, "%Y-%m-%d %H:%M:%S") - closed_at).total_seconds() / 3600.0
        if dt > HORIZON_H or p1c <= 0:
            continue
        d = 1.0 if side in ("long", "buy") else -1.0
        cf_exit = (p1c - float(cp)) * float(size) * d
        cf_entry = (p1e - float(cp)) * float(size) * d if p1e > 0 else 0.0
        rec["matched"] += 1
        rec["cf_exit"] += cf_exit
        rec["cf_entry"] += cf_entry
        if (cf_exit > 0) != (cf_entry > 0):
            rec["conflict"] += 1

    print("=" * 100)
    print(f"反事实代理（近 {days} 天 mid/long，非保护通道；持有上限 {HORIZON_H:.0f}h）")
    print("=" * 100)
    print(f"{'通道':26s} {'笔数':>4} {'匹配':>4} {'实际净额':>10} {'持有代理(平仓价)':>16} "
          f"{'持有代理(开仓价)':>16} {'结论':>10}")
    tot_actual = tot_cf = 0.0
    for key, r in sorted(agg.items(), key=lambda kv: kv[1]["actual"]):
        if r["n"] == 0:
            continue
        if is_protected(key.partition("|")[2]) or r["matched"] == 0:
            continue
        delta_e = r["cf_exit"] - r["actual"]
        delta_n = r["cf_entry"] - r["actual"]
        same_dir = (delta_e > 0) == (delta_n > 0)
        verdict = ("抑制更省" if (delta_e < 0 and delta_n < 0) else
                   "抑制更亏" if (delta_e > 0 and delta_n > 0) else "方向不一致")
        if not same_dir:
            verdict = "方向不一致"
        tot_actual += r["actual"]
        tot_cf += r["cf_exit"]
        print(f"{key:26s} {r['n']:>4} {r['matched']:>4} {r['actual']:>10.2f} "
              f"{r['cf_exit']:>16.2f} {r['cf_entry']:>16.2f} {verdict:>10s}")
    print()
    print(f"合计（可匹配样本）：实际 {tot_actual:.2f} vs 持有代理 {tot_cf:.2f} "
          f"⇒ 差 {tot_cf - tot_actual:+.2f}（>0 表示'当时若继续持有更好'）")

    # ── 稳健性：不同持有上限 + 中位数（防被个别大额样本带偏）──
    print("\n【稳健性】主要被抑制通道在不同持有上限下的代理差（delta = 持有代理 − 实际）")
    import statistics
    main_keys = ("mid|trend_broken", "mid|midlong")
    print(f"  {'通道':22s} {'上限':>5} {'匹配':>5} {'delta合计':>11} {'delta中位':>10} {'正负比':>8}")
    for key in main_keys:
        tier, _, ch = key.partition("|")
        sel = [r for r in rows
               if channel_of(r[6]) == ch and r[9] == tier]
        for horizon in (4.0, 12.0, 48.0):
            deltas = []
            for pid, sym, side, size, cp, ep, reason, closed_at, net, _tier in sel:
                t0 = closed_at.strftime("%Y-%m-%d %H:%M:%S")
                nxt = next(((t, c) for (t, c, e) in by_sym.get(sym, []) if t > t0), None)
                if not nxt:
                    continue
                from datetime import datetime
                dt = (datetime.strptime(nxt[0], "%Y-%m-%d %H:%M:%S") - closed_at).total_seconds() / 3600.0
                if dt > horizon or nxt[1] <= 0:
                    continue
                d = 1.0 if side in ("long", "buy") else -1.0
                cf = (nxt[1] - float(cp)) * float(size) * d
                deltas.append(cf - float(net or 0))
            if not deltas:
                continue
            pos = sum(1 for x in deltas if x > 0)
            print(f"  {key:22s} {horizon:>4.0f}h {len(deltas):>5} {sum(deltas):>11.2f} "
                  f"{statistics.median(deltas):>10.2f} {pos}/{len(deltas)-pos:>5}")
    print("  （正负比 = delta>0 的笔数 / delta<=0 的笔数；【正值占多 ⇒ 当时继续持有更好 ⇒ 抑制更亏】）")
    print("⚠️ 口径限制：这是**代理**（用同标的下一笔平仓价/开仓价近似'之后的价格'），"
          "不是逐 tick 回放；两者方向不一致的通道不下结论。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
