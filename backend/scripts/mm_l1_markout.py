# -*- coding: utf-8 -*-
"""L1 被动做市 · 逐笔 markout 评估（第十一轮，修正版）。

## 为什么改用 markout

快照级回放里"库存 PnL"会被**方向性漂移**主导（样本期内币价涨跌），
不是做市能力。正确的度量是**逐笔 markout**：

  对每一笔被动成交，记录成交价与成交后 k 个快照的中间价之差，
  方向调整后取均值 → 这就是"成交后价格朝不利方向走了多少"（逆向选择成本）。

  做市净边际 = 捕获的半价差 - markout。

数据：`market_orderbook_snapshots`（15s）+ `market_trades_aggregated`（同桶 taker 流）。
成交判定：桶内 taker 卖出额 > 0 → 我方买单成交（价 = best_bid）；
        桶内 taker 买入额 > 0 → 我方卖单成交（价 = best_ask）。
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
OUT = ROOT / "data" / "mm_l1_markout.json"
SYMS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "UNI", "BNB"]
HORIZONS = (1, 4, 20, 80, 240, 960)  # 快照数（15s → 15s / 1min / 5min / 20min / 1h / 4h）
QUOTE_NOTIONAL = 1000.0


def main() -> int:
    eng = create_engine(MARKET_URL)
    ob: dict[str, list[tuple]] = defaultdict(list)
    tr: dict[str, dict] = defaultdict(dict)
    with eng.connect() as c:
        c.execute(text("set statement_timeout='900000'"))
        for s in SYMS:
            for ts, bb, ba in c.execute(text("""
                select timestamp, best_bid, best_ask from market_orderbook_snapshots
                where exchange='asterdex' and symbol=:s order by timestamp
            """), {"s": s}).fetchall():
                try:
                    bb, ba = float(bb), float(ba)
                    if bb > 0 and ba > bb:
                        ob[s].append((int(ts), bb, ba))
                except (TypeError, ValueError):
                    continue
            for ts, tbn, tsn in c.execute(text("""
                select timestamp, taker_buy_notional, taker_sell_notional
                from market_trades_aggregated
                where exchange='asterdex' and symbol=:s order by timestamp
            """), {"s": s}).fetchall():
                try:
                    tr[s][int(ts)] = (float(tbn or 0), float(tsn or 0))
                except (TypeError, ValueError):
                    continue

    print(f"每侧挂单 {QUOTE_NOTIONAL:.0f} USD | maker 费率 0 | 快照 15s | markout 单位 bp")
    hdr = f"{'币':<7}{'成交腿':>7}{'半价差bp':>10}"
    for h in HORIZONS:
        label = f"mo{h * 15}s" if h * 15 < 3600 else f"mo{h * 15 // 3600}h"
        hdr += f"{label:>12}"
    hdr += f"{'净(15s)':>10}{'净(4h)':>10}"
    print(hdr)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "quote_notional_usd": QUOTE_NOTIONAL, "maker_fee": 0.0, "rows": []}

    for s in SYMS:
        v = ob.get(s) or []
        if len(v) < 500:
            continue
        v.sort()
        tmap = tr.get(s) or {}
        mids = [(bb + ba) / 2.0 for _ts, bb, ba in v]
        half_spreads = [(ba - bb) / 2.0 / ((bb + ba) / 2.0) * 10000.0 for _ts, bb, ba in v]
        mo = {h: [] for h in HORIZONS}
        fills = 0
        for i in range(len(v)):
            ts, bb, ba = v[i]
            tbn, tsn = tmap.get(ts, (0.0, 0.0))
            mid = mids[i]
            if mid <= 0:
                continue
            # 买腿（被 taker 卖单打中）
            if min(tsn, QUOTE_NOTIONAL) > 0:
                fills += 1
                for h in HORIZONS:
                    j = i + h
                    if j < len(mids) and mids[j] > 0:
                        # 买入后价格下跌 = 损失
                        mo[h].append(-(mids[j] - mid) / mid * 10000.0)
            # 卖腿（被 taker 买单打中）
            if min(tbn, QUOTE_NOTIONAL) > 0:
                fills += 1
                for h in HORIZONS:
                    j = i + h
                    if j < len(mids) and mids[j] > 0:
                        mo[h].append((mids[j] - mid) / mid * 10000.0)
        if fills == 0:
            continue
        hs = st.median(half_spreads)
        mv = {h: (st.mean(mo[h]) if mo[h] else 0.0) for h in HORIZONS}
        net1 = hs - mv[1]
        net4h = hs - mv[960]
        rec = {"symbol": s, "n_snap": len(v), "fills": fills,
               "half_spread_bp_median": round(hs, 4),
               **{f"markout_{h}_bp": round(mv[h], 4) for h in HORIZONS},
               "net_bp_15s": round(net1, 4), "net_bp_4h": round(net4h, 4),
               "n_markout_1": len(mo[1])}
        report["rows"].append(rec)
        line = f"{s:<7}{fills:>7}{hs:>10.3f}"
        for h in HORIZONS:
            line += f"{mv[h]:>12.4f}"
        line += f"{net1:>10.4f}{net4h:>10.4f}"
        print(line)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")

    pos = [r for r in report["rows"] if r["net_bp_15s"] > 0]
    print(f"\n15s markout 下净边际 > 0 的币: {len(pos)} / {len(report['rows'])}")
    for r in sorted(pos, key=lambda x: -x["net_bp_15s"]):
        print(f"  [OK] {r['symbol']}: 半价差 {r['half_spread_bp_median']}bp - "
              f"mo15s {r['markout_1_bp']}bp = {r['net_bp_15s']}bp | "
              f"但 mo4h {r['markout_960_bp']}bp → 净4h {r['net_bp_4h']}bp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
