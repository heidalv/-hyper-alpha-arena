# -*- coding: utf-8 -*-
"""[F290 2026-09-16] 标的可得性普查：我们**已经在采**的币里，谁的价差够宽？

**为什么先做这个（而不是先建 1s 采集链路）**：F289 起，三条剩余路线里只有"换标的/
场所"能用**现有数据**直接验证——数据库里已有数据中心采的全量盘口快照。若存在中位
价差 ≫ ETH 的合规标的，那么"$30 腿量 + 30s 节奏"下的 maker 端收益会成倍放大
（maker 赚的就是价差的一部分），值得下一步做模型 A/B；若全都和 ETH 一个量级，
则这条路线也可先排除，只剩节奏/数据或本金两条。

判据（每币）：
  · 中位相对价差（bp）= median((ask−bid)/mid×1e4)，窗口内快照数 ≥ MIN_N；
  · $30 腿的**步长合规性**（用 core.SYMBOL_STEP；表外币种标注"需查 exchangeInfo"）；
  · 快照覆盖时长/条数（数据中心是否真在采）。
"""
from __future__ import annotations

import io
import os
import sys
from datetime import datetime, timedelta, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from backend.core.tenant import system_identity  # noqa: E402
from backend.database.connection import MarketSessionLocal  # noqa: E402
from backend.services.market_maker.core import SYMBOL_STEP, leg_qty_compliant  # noqa: E402

MIN_N = 200
LEG = 30.0
HOURS = float(os.getenv("F290_HOURS", "12"))


def main() -> int:
    since = int((datetime.now(timezone.utc) - timedelta(hours=HOURS)).timestamp() * 1000)
    with system_identity():
        with MarketSessionLocal() as db:
            rows = db.execute(text(
                "SELECT symbol, COUNT(*) n,"
                " percentile_cont(0.5) WITHIN GROUP (ORDER BY (best_ask-best_bid)/"
                "   ((best_ask+best_bid)/2)*1e4) AS med_bp,"
                " percentile_cont(0.9) WITHIN GROUP (ORDER BY (best_ask-best_bid)/"
                "   ((best_ask+best_bid)/2)*1e4) AS p90_bp,"
                " AVG((best_ask+best_bid)/2) AS mid"
                " FROM market_orderbook_snapshots"
                " WHERE exchange='asterdex' AND timestamp >= :t"
                "   AND best_bid > 0 AND best_ask > best_bid"
                " GROUP BY symbol HAVING COUNT(*) >= :n"
                " ORDER BY med_bp DESC"), {"t": since, "n": MIN_N}).mappings().all()
    print(f"[F290] 窗口=近 {HOURS:.0f} 小时 | 交易所=asterdex | 快照数≥{MIN_N} 的币 "
          f"{len(rows)} 个（$30 腿量）")
    print(f"{'symbol':<10}{'n':>7}{'med_bp':>8}{'p90_bp':>8}{'mid':>12}  合规性")
    for r in rows[:18]:
        sym = str(r["symbol"])
        ok, qty, why = leg_qty_compliant(sym, LEG, float(r["mid"] or 0.0))
        tag = ("合规 ✓" if ok else f"✗ {why}") if sym.upper() in SYMBOL_STEP else "表外(需查)"
        print(f"{sym:<10}{int(r['n']):>7}{float(r['med_bp']):>8.2f}"
              f"{float(r['p90_bp']):>8.2f}{float(r['mid'] or 0):>12,.4f}  {tag}")
    if rows:
        eth = [r for r in rows if str(r["symbol"]).upper() == "ETH"]
        base = float(eth[0]["med_bp"]) if eth else 0.0
        wider = [r for r in rows if float(r["med_bp"]) >= max(3 * base, 15.0)
                 and str(r["symbol"]).upper() in SYMBOL_STEP]
        print(f"\n  ETH 中位价差={base:.2f}bp；中位价差 ≥max(3×ETH, 15bp) 且**表内合规**的标的: "
              f"{[str(r['symbol']) for r in wider] or '无'}")
        print("  ⇒ 有 ⇒ 下一步对这些币做模型 A/B（同参数、同窗口）；无 ⇒ 该路线先用证据排除。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
