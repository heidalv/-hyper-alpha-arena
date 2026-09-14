# -*- coding: utf-8 -*-
"""[F91 巡检] 做市双账对账 CLI（运行态持仓 vs 账本重建持仓）。

逻辑在 `backend/services/market_maker/reconcile.py`（API `/lanes/{id}/reconcile`
与前端健康指标共用同一实现，避免两处口径漂移）。

用法：
    python scripts/mm_reconcile_positions.py                # 只报告（有分叉 exit 2）
    python scripts/mm_reconcile_positions.py --fix          # 写净额 0 的对账调整行
    python scripts/mm_reconcile_positions.py --lane mm_asterdex --since 2026-09-14T11:50:00+08:00
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.reconcile import (  # noqa: E402
    apply_adjustments,
    compare_lane_books,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", default="mm_asterdex")
    ap.add_argument("--since", default=None,
                    help="统计时代起点（默认取车道 meta.stats_since）")
    ap.add_argument("--fix", action="store_true", help="写对账调整行（净额 0）")
    args = ap.parse_args()

    res = compare_lane_books(lane_id=args.lane, since=args.since)
    print(f"车道 {res['lane_id']}  交易所 {res['venue']}  时代起点 {res['since']}")
    print(f"{'币种':<6}{'运行态':>16}{'账本重建':>16}{'差异(USD)':>12}  状态")
    print("-" * 62)
    for r in res["rows"]:
        print(f"{r['symbol']:<6}{r['runtime_qty']:>16.8f}{r['ledger_qty']:>16.8f}"
              f"{r['diff_usd']:>12.2f}  {'✅' if r['ok'] else '❌ 分叉'}")
    if res.get("rt_only"):
        print(f"\n⚠️ 运行态里存在**不在宇宙**的币种（F90 孤儿持仓）: {', '.join(res['rt_only'])}")

    if res["ok"]:
        print("\n✅ 双账一致（运行态 == 账本重建），无需调整")
        return 0

    print(f"\n❌ {len(res['mismatches'])} 个币种分叉")
    if not args.fix:
        print("（dry-run：加 --fix 写对账调整行；调整行净额恒为 0，不制造盈亏）")
        return 2
    n = apply_adjustments(lane_id=args.lane, mismatches=res["mismatches"],
                          ts=res.get("state_ts"))
    for m in res["mismatches"]:
        side = "sell" if m["diff_qty"] > 0 else "buy"
        print(f"  ✅ {m['symbol']}: {side} {abs(m['diff_qty']):.8f} @ {m['mark_px']:.8f}"
              f"（净额 0，仅校正库存）")
    print(f"\n已写入 {n}/{len(res['mismatches'])} 条对账调整行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
