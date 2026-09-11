# -*- coding: utf-8 -*-
"""L1 被动做市可行性评估（第十一轮）。

## 为什么做这个

Asterdex 官方费率（docs.asterdex.com/program-and-rewards/vip-program.md）：
**所有 VIP 档 maker 费率 = 0**；系统 `fee_schedule_service` 也确认 asterdex maker=0.0%。
MM 计划另有返佣（MM1 -0.25bp / MM2 -0.35bp / MM3 -0.5bp，门槛 14 天 $150M 或 maker vol ≥0.25%）。

因此被动做市的毛收益 = 捕获价差，成本 = **逆向选择**（挂单成交后价格继续朝不利方向走）。

## 本脚本测什么

用 `market_orderbook_snapshots`（快照级 best_bid/best_ask/spread）+
`market_trades_aggregated`（taker 买卖量与成交价）做 **markout 分析**：

- 对每个快照，用**下一快照**的中间价变化，衡量"如果在 best_bid 挂单被卖单打中"后的
  价格漂移（同理 best_ask 被买单打中）；
- 净做市边际 ≈ 捕获价差/2 - 逆向选择（markout）；
- 只统计有足够快照密度的币。

用法：.venv\\Scripts\\python.exe backend/scripts/mm_l1_feasibility.py
输出：控制台 + `data/mm_l1_feasibility.json`
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

MARKET_URL = os.getenv("MARKET_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
OUT = ROOT / "data" / "mm_l1_feasibility.json"
SYMS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "UNI", "BNB"]


def main() -> int:
    eng = create_engine(MARKET_URL)
    rows = []
    with eng.connect() as c:
        c.execute(text("set statement_timeout='900000'"))
        for s in SYMS:
            r = c.execute(text("""
                select symbol, timestamp, best_bid, best_ask
                from market_orderbook_snapshots
                where exchange='asterdex' and symbol=:s
                order by timestamp
            """), {"s": s}).fetchall()
            rows.extend(r)

    by: dict[str, list[tuple]] = defaultdict(list)
    for s, ts, bb, ba in rows:
        try:
            bb, ba = float(bb), float(ba)
            if bb <= 0 or ba <= 0 or ba <= bb:
                continue
            by[s].append((int(ts), bb, ba))
        except (TypeError, ValueError):
            continue

    print(f"{'币':<8}{'n':>7}{'快照间隔s':>10}{'价差bp中位':>11}"
          f"{'买腿markout bp':>15}{'卖腿markout bp':>15}{'净边际bp':>10}")
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "rows": []}
    for s in SYMS:
        v = by.get(s) or []
        if len(v) < 500:
            continue
        v.sort()
        gaps = [(v[i + 1][0] - v[i][0]) / 1000.0 for i in range(len(v) - 1)]
        med_gap = st.median(gaps) if gaps else 0.0
        spreads, mo_bid, mo_ask = [], [], []
        for i in range(len(v) - 1):
            ts0, bb0, ba0 = v[i]
            ts1, bb1, ba1 = v[i + 1]
            if bb0 <= 0:
                continue
            mid0 = (bb0 + ba0) / 2.0
            mid1 = (bb1 + ba1) / 2.0
            if mid0 <= 0:
                continue
            sp_bp = (ba0 - bb0) / mid0 * 10000.0
            spreads.append(sp_bp)
            # 在 best_bid 挂买单：若成交，随后价格漂移 = (mid1-mid0)/mid0
            # 对做市方而言，买腿的"损失"是价格下跌，故 markout 记 -(mid1-mid0)
            mo_bid.append(-(mid1 - mid0) / mid0 * 10000.0)
            # 在 best_ask 挂卖单：卖腿的损失是价格上涨
            mo_ask.append((mid1 - mid0) / mid0 * 10000.0)
        if not spreads:
            continue
        sp_med = st.median(spreads)
        mb = st.mean(mo_bid)
        ma = st.mean(mo_ask)
        # 一次往返：赚半价差×2（=价差），承担两条腿的逆向选择
        gross = sp_med
        adverse = mb + ma
        net = gross - adverse
        rec = {"symbol": s, "n": len(v), "median_gap_s": round(med_gap, 3),
               "spread_bp_median": round(sp_med, 3),
               "markout_bid_bp": round(mb, 4), "markout_ask_bp": round(ma, 4),
               "gross_bp": round(gross, 3), "adverse_bp": round(adverse, 4),
               "net_bp": round(net, 4)}
        report["rows"].append(rec)
        print(f"{s:<8}{len(v):>7}{med_gap:>10.2f}{sp_med:>11.3f}{mb:>15.4f}{ma:>15.4f}{net:>10.4f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")

    pos = [r for r in report["rows"] if r["net_bp"] > 0]
    print(f"\n净边际 > 0 的币: {len(pos)} / {len(report['rows'])}")
    for r in sorted(pos, key=lambda x: -x["net_bp"]):
        print(f"  ✅ {r['symbol']}: 价差{r['spread_bp_median']}bp - 逆向选择{r['adverse_bp']}bp "
              f"= 净{r['net_bp']}bp（快照间隔{r['median_gap_s']}s）")
    if not pos:
        print("[WARN] 全部为负 —— 在快照粒度上，捕获的价差不足以覆盖逆向选择。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
