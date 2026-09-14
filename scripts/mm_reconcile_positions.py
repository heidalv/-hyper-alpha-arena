# -*- coding: utf-8 -*-
"""[F91 巡检] 做市双账对账：运行态持仓 vs 账本重建持仓。

为什么需要：`lane_ledger` 是前端持仓/归因的**唯一事实源**，而实盘决策用的是
`lane_runtime_state` 里的运行态。两者一旦分叉，后果是静默的：
  - 前端显示一个实盘并不存在的持仓（实测 BTC 幽灵仓 $5.10）；
  - 净敞口/方向上限的统计口径与真实风险不一致；
  - 已实现盈亏与期末库存对不上。
2026-09-14 的分叉根因是 F91 之前的「减仓腿按 dollar 腿量平仓」导致每次往返
留下 |Δqty| 残差（均值回归下两方向同号 ⇒ 单向漂移）；F91 已修，本脚本用于
持续巡检 + 一次性清理历史残差。

用法：
    python scripts/mm_reconcile_positions.py                 # 只报告（有分叉 exit 2）
    python scripts/mm_reconcile_positions.py --fix           # 写对账调整行（净额 0）
    python scripts/mm_reconcile_positions.py --lane mm_asterdex --since 2026-09-14T11:50:00+08:00

`--fix` 写入的调整行：`side` = 平掉残差的方向、`fill_px = mid_px = 当前中价`、
`fee_rate = 0` ⇒ 六维净额恒为 0（**不制造盈亏**），meta 标记 `source=reconcile`。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from backend.core.tenant import system_identity  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402
from backend.services import lane_ledger, lane_registry  # noqa: E402

TOL_QTY_REL = 1e-6      # 相对容差：0.0001% 视为浮点噪声


def _lane_meta(lane_id: str) -> dict:
    for ln in lane_registry.list_lanes():
        if ln.get("lane_id") == lane_id:
            return ln.get("meta") or {}
    return {}


def _runtime_positions(lane_id: str) -> dict:
    """运行态持仓（实盘决策的真实账）。"""
    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(text(
                "SELECT symbol, state_json, updated_ts FROM lane_runtime_state"
                " WHERE lane_id=:l ORDER BY symbol"
            ), {"l": lane_id}).mappings().all()
    out = {}
    for r in rows:
        st = r["state_json"] or {}
        if isinstance(st, str):
            st = json.loads(st)
        out[str(r["symbol"])] = {"qty": float(st.get("qty") or 0.0),
                                 "avg_px": float(st.get("avg_px") or 0.0),
                                 "updated_ts": r["updated_ts"]}
    return out


def _marks(symbols, venue: str) -> dict:
    """最新中价（`market_orderbook_snapshots` 在**行情库**，需 MarketSessionLocal）。"""
    from backend.database.connection import MarketSessionLocal

    out = {}
    with system_identity():
        with MarketSessionLocal() as db:
            for s in symbols:
                r = db.execute(text(
                    "SELECT best_bid, best_ask FROM market_orderbook_snapshots"
                    " WHERE exchange=:e AND symbol=:s AND best_bid>0 AND best_ask>best_bid"
                    " ORDER BY timestamp DESC LIMIT 1"
                ), {"e": venue, "s": s}).mappings().first()
                if r:
                    out[s] = (float(r["best_bid"]) + float(r["best_ask"])) / 2.0
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", default="mm_asterdex")
    ap.add_argument("--since", default=None,
                    help="统计时代起点（默认取车道 meta.stats_since）")
    ap.add_argument("--fix", action="store_true", help="写对账调整行（净额 0）")
    args = ap.parse_args()

    meta = _lane_meta(args.lane)
    venue = str(meta.get("venue") or "asterdex")
    since = args.since if args.since is not None else meta.get("stats_since")

    rt = _runtime_positions(args.lane)
    symbols = sorted(set(rt) | set(meta.get("symbols") or []))
    marks = _marks(symbols, venue)
    led_rows = lane_ledger.open_positions(lane_id=args.lane, days=30.0, marks=marks,
                                         since=since)
    led = {r["symbol"]: r for r in led_rows}

    print(f"车道 {args.lane}  交易所 {venue}  时代起点 {since}")
    print(f"{'币种':<6}{'运行态':>14}{'账本重建':>14}{'差异(USD)':>12}  状态")
    print("-" * 62)
    mismatches = []
    for s in symbols:
        q_rt = float((rt.get(s) or {}).get("qty") or 0.0)
        q_led = float((led.get(s) or {}).get("qty") or 0.0)
        mark = float(marks.get(s) or 0.0)
        diff = q_led - q_rt
        scale = max(abs(q_rt), abs(q_led))
        bad = scale > 0 and abs(diff) / scale > TOL_QTY_REL
        print(f"{s:<6}{q_rt:>14.8f}{q_led:>14.8f}{diff * mark:>12.2f}"
              f"  {'❌ 分叉' if bad else '✅'}")
        if bad:
            mismatches.append({"symbol": s, "diff": diff, "mark": mark,
                               "q_rt": q_rt, "q_led": q_led})

    if not mismatches:
        print("\n✅ 双账一致（运行态 == 账本重建），无需调整")
        return 0

    print(f"\n❌ {len(mismatches)} 个币种分叉")
    if not args.fix:
        print("（dry-run：加 --fix 写对账调整行；调整行净额恒为 0，不制造盈亏）")
        return 2

    n = 0
    for m in mismatches:
        side = "sell" if m["diff"] > 0 else "buy"
        if m["mark"] <= 0:
            print(f"  ⚠️ {m['symbol']} 无行情价，跳过（需人工确认）")
            continue
        ok = lane_ledger.record_fill(
            lane_id=args.lane, symbol=m["symbol"], side=side, qty=abs(m["diff"]),
            fill_px=m["mark"], mid_px=m["mark"], fee_rate=0.0,
            meta={"source": "reconcile", "reason": "runtime_vs_ledger_divergence",
                  "runtime_qty": m["q_rt"], "ledger_qty": m["q_led"],
                  "tool": "scripts/mm_reconcile_positions.py"},
        )
        n += 1 if ok else 0
        print(f"  ✅ {m['symbol']}: {side} {abs(m['diff']):.8f} @ {m['mark']:.8f}"
              f"（净额 0，仅校正库存）")
    print(f"\n已写入 {n}/{len(mismatches)} 条对账调整行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
