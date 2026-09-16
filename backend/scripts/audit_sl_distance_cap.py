# -*- coding: utf-8 -*-
"""[调研轮15b 2026-09-16] 止损距离（SL distance）与按层上限的**只读**体检。

背景：线上 mid 仓实测硬止损距离 4.50~4.74%、long 仓 6.50%，而赢家持仓期最大逆行
（MAE）均值仅约 −1.2% ⇒ 止损"远到不会响"，亏损只能走到 4.5% 才被砍。三层封顶
（提案层 clamp / 价格层 / PosMgr）都改完仍不生效，因为 `paper_trading_engine` 每
个保护 tick 会调用 `_enforce_min_sl()` 按 nature **硬下限**把 SL 拉回
swing 4.5% / position 5.5% / trend_follow 6.5% —— 下限赢。

本脚本回答两个问题：
  1. 当前每个未平仓位的 SL 距离、tier/nature、以及"是否已经比上限更远"；
  2. **若把上限应用到存量仓，会不会立刻触发止损**（mark 是否已经越过上限价位）。

完全只读：不写库、不下单、不改配置。用法：
  python backend/scripts/audit_sl_distance_cap.py                # 默认 account 14
  python backend/scripts/audit_sl_distance_cap.py --account 14 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:  # Windows 控制台默认 GBK，避免汇总行编码崩溃
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

#: 与 `paper_trading_engine._MIN_SL_DISTANCE_BY_NATURE` 对齐（硬下限）
NATURE_FLOOR = {
    "scalp": 0.010, "intraday": 0.035, "swing": 0.045,
    "position": 0.055, "trend_follow": 0.065,
}
#: 与 `paper_trading_engine.NATURE_TO_TIER` / 车道语义对齐
NATURE_TO_TIER = {
    "swing": "mid", "intraday": "short", "scalp": "short",
    "trend_follow": "long", "position": "long",
}


def _tier_of(pos) -> str:
    nat = (getattr(pos, "trade_nature", None) or "").strip().lower()
    t = (getattr(pos, "timeframe_tier", None) or "").strip().lower()
    if t in ("mid", "long", "short"):
        return t
    return NATURE_TO_TIER.get(nat, t or "?")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=14)
    ap.add_argument("--cap-mid", type=float, default=0.02)
    ap.add_argument("--cap-long", type=float, default=0.03)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    from backend.database.connection import SessionLocal
    from backend.database.models import PaperPosition

    caps = {"mid": args.cap_mid, "long": args.cap_long}
    rows = []
    db = SessionLocal()
    try:
        q = db.query(PaperPosition).filter(
            PaperPosition.status == "open",
            PaperPosition.account_id == args.account,
        ).order_by(PaperPosition.id)
        for p in q.all():
            entry = float(p.entry_price or 0)
            sl = float(p.sl_price or 0)
            mark = float(getattr(p, "mark_price", 0) or 0)
            side = (p.side or "").lower()
            if entry <= 0:
                continue
            dist = (abs(entry - sl) / entry) if sl > 0 else 0.0
            tier = _tier_of(p)
            cap = caps.get(tier, 0.0)
            nat = (getattr(p, "trade_nature", None) or "").strip().lower()
            floor = NATURE_FLOOR.get(nat, 0.0)
            # mark 相对 entry 的价格变动（未杠杆，正=有利）
            move = ((mark - entry) if side in ("long", "buy") else (entry - mark)) / entry
            capped_sl = (entry * (1 - cap)) if side in ("long", "buy") else (entry * (1 + cap))
            # 上限价位是否已被 mark 越过（应用上限 = 立刻止损）
            breach = False
            if cap > 0 and mark > 0:
                breach = (mark <= capped_sl) if side in ("long", "buy") else (mark >= capped_sl)
            rows.append({
                "id": p.id, "symbol": p.symbol, "side": side, "tier": tier,
                "nature": nat or "-", "leverage": float(p.leverage or 0),
                "entry": round(entry, 6), "sl": round(sl, 6),
                "sl_dist_pct": round(dist * 100, 3),
                "nature_floor_pct": round(floor * 100, 3),
                "cap_pct": round(cap * 100, 3),
                "mark": round(mark, 6), "move_pct": round(move * 100, 3),
                "unrealized_pnl": round(float(p.unrealized_pnl or 0), 2),
                "capped_sl": round(capped_sl, 6),
                "immediate_breach": breach,
                "opened_at": str(getattr(p, "opened_at", "") or ""),
            })
    finally:
        db.close()

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print(f"未平仓 {len(rows)} 笔 (account={args.account}) "
          f"cap: mid={args.cap_mid:.1%} long={args.cap_long:.1%}")
    print(f"{'id':>5} {'sym':<9}{'side':<5}{'tier':<5}{'nature':<13}"
          f"{'SL距%':>7}{'下限%':>7}{'上限%':>7}{'浮动%':>8}{'浮盈$':>9}  立即触发?")
    for r in rows:
        print(f"{r['id']:>5} {r['symbol']:<9}{r['side']:<5}{r['tier']:<5}{r['nature']:<13}"
              f"{r['sl_dist_pct']:>7.2f}{r['nature_floor_pct']:>7.2f}{r['cap_pct']:>7.2f}"
              f"{r['move_pct']:>8.2f}{r['unrealized_pnl']:>9.2f}  "
              f"{'⚠️ 是' if r['immediate_breach'] else '否'}")
    over = [r for r in rows if r["cap_pct"] > 0 and r["sl_dist_pct"] > r["cap_pct"]]
    breach = [r for r in over if r["immediate_breach"]]
    conflict = [r for r in rows
                if r["cap_pct"] > 0 and r["nature_floor_pct"] > r["cap_pct"]]
    print(f"\n超过上限的持仓: {len(over)}/{len(rows)}"
          f"；其中若立即套用上限会被打掉: **{len(breach)}**"
          f"；上/下限冲突(nature 下限 > 层上限): {len(conflict)}")
    for r in over:
        print(f"  · {r['symbol']}[{r['tier']}] SL {r['sl_dist_pct']:.2f}% → 上限 "
              f"{r['cap_pct']:.2f}%（浮动 {r['move_pct']:+.2f}%，浮盈 "
              f"{r['unrealized_pnl']:+.2f}）{' ← 立即触发' if r['immediate_breach'] else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
