"""分析全自动 Paper 近 N 日成交与退出原因。"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from dotenv import load_dotenv

load_dotenv(".env")

from backend.database.connection import SessionLocal
from backend.database.models import PaperOrder, PaperPosition, PositionExitEvent


def main(days: int = 7) -> None:
    db = SessionLocal()
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    try:
        closes = (
            db.query(PaperOrder)
            .filter(
                PaperOrder.status == "filled",
                PaperOrder.pnl.isnot(None),
                PaperOrder.filled_at >= cutoff,
            )
            .order_by(PaperOrder.filled_at.desc())
            .all()
        )
        print(f"=== 平仓订单 last {days}d: {len(closes)} ===")

        by_reason: dict = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0, "pnls": []})
        by_nature: dict = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
        by_tier: dict = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
        win_sum = loss_sum = 0.0
        win_n = loss_n = 0

        for o in closes:
            pnl = float(o.pnl or 0)
            reason = (o.close_reason or "unknown").strip()
            by_reason[reason]["n"] += 1
            by_reason[reason]["pnl"] += pnl
            by_reason[reason]["pnls"].append(pnl)
            if pnl >= 0:
                by_reason[reason]["wins"] += 1
                win_sum += pnl
                win_n += 1
            else:
                loss_sum += pnl
                loss_n += 1
            nat = o.trade_nature or "?"
            by_nature[nat]["n"] += 1
            by_nature[nat]["pnl"] += pnl
            if pnl >= 0:
                by_nature[nat]["wins"] += 1

        # join tier from positions via symbol+time heuristic - use closed positions
        closed_pos = (
            db.query(PaperPosition)
            .filter(
                PaperPosition.status.in_(["closed", "liquidated"]),
                PaperPosition.closed_at >= cutoff,
            )
            .all()
        )
        for p in closed_pos:
            pr = float(p.partial_realized_pnl or 0)
            # full close pnl often in orders; use position margin-based estimate
            if p.close_price and p.entry_price and p.size:
                if p.side in ("long", "buy"):
                    gross = (float(p.close_price) - float(p.entry_price)) * float(p.size)
                else:
                    gross = (float(p.entry_price) - float(p.close_price)) * float(p.size)
                est = gross + pr
            else:
                est = pr
            tier = p.timeframe_tier or "?"
            by_tier[tier]["n"] += 1
            by_tier[tier]["pnl"] += est
            if est >= 0:
                by_tier[tier]["wins"] += 1

        print(f"orders: wins={win_n} losses={loss_n} win_sum={win_sum:.2f} loss_sum={loss_sum:.2f}")
        if win_n:
            print(f"  avg_win={win_sum/win_n:.2f}")
        if loss_n:
            print(f"  avg_loss={loss_sum/loss_n:.2f}")
        if win_n and loss_n:
            print(f"  profit_factor={abs(win_sum/loss_sum):.2f}" if loss_sum else "  profit_factor=inf")

        print("\n--- by close_reason (orders) ---")
        for k, v in sorted(by_reason.items(), key=lambda x: -x[1]["n"]):
            wr = v["wins"] / max(v["n"], 1)
            avg = v["pnl"] / max(v["n"], 1)
            wins_pnls = [x for x in v["pnls"] if x >= 0]
            loss_pnls = [x for x in v["pnls"] if x < 0]
            aw = sum(wins_pnls) / len(wins_pnls) if wins_pnls else 0
            al = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0
            print(
                f"  {k:28} n={v['n']:3} wr={wr:5.1%} "
                f"total={v['pnl']:9.2f} avg={avg:8.2f} avg_win={aw:7.2f} avg_loss={al:7.2f}"
            )

        print("\n--- by trade_nature ---")
        for k, v in sorted(by_nature.items(), key=lambda x: -x[1]["n"]):
            print(
                f"  {k:16} n={v['n']:3} wr={v['wins']/max(v['n'],1):5.1%} pnl={v['pnl']:9.2f}"
            )

        print("\n--- by tier (positions est.) ---")
        for k, v in sorted(by_tier.items(), key=lambda x: -x[1]["n"]):
            print(
                f"  {k:8} n={v['n']:3} wr={v['wins']/max(v['n'],1):5.1%} pnl={v['pnl']:9.2f}"
            )

        print("\n--- last 20 close orders ---")
        for o in closes[:20]:
            print(
                f"  {o.filled_at} {o.symbol:10} {o.side:5} "
                f"pnl={float(o.pnl or 0):8.2f} reason={o.close_reason} nature={o.trade_nature}"
            )

        ev_count = db.query(PositionExitEvent).filter(PositionExitEvent.created_at >= cutoff).count()
        print(f"\nexit_events={ev_count}")

        # partial vs full
        partial = [o for o in closes if o.close_reason and "partial" in o.close_reason]
        full = [o for o in closes if o not in partial]
        p_pnl = sum(float(o.pnl or 0) for o in partial)
        f_pnl = sum(float(o.pnl or 0) for o in full)
        print(f"partial_closes={len(partial)} pnl={p_pnl:.2f} | full_closes={len(full)} pnl={f_pnl:.2f}")

    finally:
        db.close()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
