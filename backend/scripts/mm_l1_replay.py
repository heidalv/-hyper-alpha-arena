# -*- coding: utf-8 -*-
"""L1 被动做市 · 快照级回放（第十一轮）。

## 模型（保守）

数据：`market_orderbook_snapshots`（15s 快照，best_bid/best_ask/depth）+ `market_trades_aggregated`
（同 15s 桶的 taker 成交量/成交价）。

每个快照 t：
  1. 在 `best_bid(t)` 挂买单、`best_ask(t)` 挂卖单（各 `Q` 名义）；
  2. 用 **t 桶的成交流** 判定成交：
     - 买单成交 = `min(taker_sell_notional, Q)`，成交价 = best_bid(t)；
     - 卖单成交 = `min(taker_buy_notional, Q)`，成交价 = best_ask(t)；
  3. 用 **下一快照的中间价** 给未平仓库存 mark-to-market；
  4. 费率：Asterdex maker = **0**（官方文档 + 系统费率表一致）。

输出：每币的毛价差收益、库存 markout、净边际（bp / 每单位成交名义）。
这是**上界估计**（假设所有 taker 流都打到我的价位），用于判断"值不值得做"。
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
OUT = ROOT / "data" / "mm_l1_replay.json"
SYMS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "UNI", "BNB"]
QUOTE_NOTIONAL = 1000.0  # 每侧挂单名义（USD）


def main() -> int:
    eng = create_engine(MARKET_URL)
    ob: dict[str, list[tuple]] = defaultdict(list)
    tr: dict[str, dict] = defaultdict(dict)
    with eng.connect() as c:
        c.execute(text("set statement_timeout='900000'"))
        for s in SYMS:
            for ts, bb, ba, bd5, ad5 in c.execute(text("""
                select timestamp, best_bid, best_ask, bid_depth_5, ask_depth_5
                from market_orderbook_snapshots
                where exchange='asterdex' and symbol=:s order by timestamp
            """), {"s": s}).fetchall():
                try:
                    bb, ba = float(bb), float(ba)
                    if bb <= 0 or ba <= bb:
                        continue
                    ob[s].append((int(ts), bb, ba, float(bd5 or 0), float(ad5 or 0)))
                except (TypeError, ValueError):
                    continue
            for ts, tb, tsell, tbn, tsn in c.execute(text("""
                select timestamp, taker_buy_volume, taker_sell_volume,
                       taker_buy_notional, taker_sell_notional
                from market_trades_aggregated
                where exchange='asterdex' and symbol=:s order by timestamp
            """), {"s": s}).fetchall():
                try:
                    tr[s][int(ts)] = (float(tbn or 0), float(tsn or 0))
                except (TypeError, ValueError):
                    continue

    print(f"每侧挂单 {QUOTE_NOTIONAL:.0f} USD | maker 费率 0 | 快照 15s")
    print(f"{'币':<7}{'快照':>7}{'成交额$':>12}{'成交腿数':>9}{'毛价差bp':>10}"
          f"{'库存markout bp':>16}{'净边际bp':>11}{'换手/天':>9}")
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "quote_notional_usd": QUOTE_NOTIONAL, "maker_fee": 0.0, "rows": []}

    for s in SYMS:
        v = ob.get(s) or []
        if len(v) < 500:
            continue
        v.sort()
        tmap = tr.get(s) or {}
        gross_pnl = 0.0
        inventory_pnl = 0.0
        filled_notional = 0.0
        legs = 0
        inv = 0.0          # 库存（base 数量，正=多）
        for i in range(len(v) - 1):
            ts, bb, ba, _bd, _ad = v[i]
            ts1, bb1, ba1, _bd1, _ad1 = v[i + 1]
            mid = (bb + ba) / 2.0
            mid1 = (bb1 + ba1) / 2.0
            if mid <= 0 or mid1 <= 0:
                continue
            # 本桶 taker 流 → 成交（先用成交价更新库存）
            tbn, tsn = tmap.get(ts, (0.0, 0.0))
            buy_fill = min(tsn, QUOTE_NOTIONAL)   # 卖单被打中 → 我方买入
            sell_fill = min(tbn, QUOTE_NOTIONAL)  # 买单被打中 → 我方卖出
            if buy_fill > 0:
                inv += buy_fill / bb
                filled_notional += buy_fill
                legs += 1
                # 捕获价差：以中间价计的公允价值 - 成交价
                gross_pnl += buy_fill * (mid - bb) / mid
            if sell_fill > 0:
                inv -= sell_fill / ba
                filled_notional += sell_fill
                legs += 1
                gross_pnl += sell_fill * (ba - mid) / mid
            # 库存按下一快照中间价 mark-to-market（只对真实库存）
            inventory_pnl += inv * (mid1 - mid)

        if filled_notional <= 0:
            continue
        net = gross_pnl + inventory_pnl
        per_notional_bp = net / filled_notional * 10000.0
        gross_bp = gross_pnl / filled_notional * 10000.0
        inv_bp = inventory_pnl / filled_notional * 10000.0
        # 日均换手（成交名义 / 天数）
        span_days = max((v[-1][0] - v[0][0]) / 1000.0 / 86400.0, 1e-9)
        turnover = filled_notional / span_days
        rec = {"symbol": s, "n_snap": len(v), "filled_notional": round(filled_notional, 2),
               "legs": legs, "gross_bp": round(gross_bp, 4), "inventory_bp": round(inv_bp, 4),
               "net_bp": round(per_notional_bp, 4), "turnover_usd_per_day": round(turnover, 2)}
        report["rows"].append(rec)
        print(f"{s:<7}{len(v):>7}{filled_notional:>12.0f}{legs:>9}{gross_bp:>10.3f}"
              f"{inv_bp:>16.3f}{per_notional_bp:>11.3f}{turnover:>9.0f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")

    pos = [r for r in report["rows"] if r["net_bp"] > 0]
    print(f"\n净边际 > 0 的币: {len(pos)} / {len(report['rows'])}")
    for r in sorted(pos, key=lambda x: -x["net_bp"]):
        print(f"  [OK] {r['symbol']}: 毛{r['gross_bp']}bp + 库存{r['inventory_bp']}bp = "
              f"净{r['net_bp']}bp | 日均成交额 ${r['turnover_usd_per_day']:,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
