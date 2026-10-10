# -*- coding: utf-8 -*-
"""[2026-10-10 主脑接入高频学习] 验收 1：高频一笔平仓后主脑能看到 mm_asterdex_pp/hft，
中线论题条数不因此增加。

这个脚本做的事：
  1. 从 `lane_ledger` 取 3 笔**真实** PP 平仓腿（只读）；
  2. `pp_brain.run_once(driver=CapturingDriver)` —— 走**真实主脑逻辑**
     （`process_outcome`），但不写 `strategy_trades`（persist_trade=False）；
  3. 打印主脑看到的 strategy_id / tier / 论题键，并对比 `brain_theses` 计数。

用法：.venv\\Scripts\\python.exe backend\\tests\\tools\\accept_hft_inlet_20261010.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import pp_brain as pb  # noqa: E402
from backend.services.unified_learning_service import unified_learning  # noqa: E402


class CapturingDriver:
    """把主脑调用接住：真实 process_outcome，每笔用独立 Session 并打印。"""

    def __init__(self, rows):
        self.rows = rows
        self.seen = []

    def fetch(self, cursor_ts, cursor_id, limit):
        return [r for r in self.rows
                if (float(r["ts"].timestamp()), int(r["id"]))
                > (float(cursor_ts), int(cursor_id))][:limit]

    def process(self, outcome):
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            unified_learning.process_outcome(db, outcome)
            self.seen.append(outcome)
            print(f"  主脑收到: strategy_id={outcome.strategy_id} tier={outcome.tier} "
                  f"nature={outcome.trade_nature} symbol={outcome.symbol} "
                  f"side={outcome.side} pnl={outcome.pnl:+.4f} "
                  f"persist_trade={outcome.persist_trade}")
        finally:
            db.close()


def _thesis_counts(symbols):
    from sqlalchemy import text
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    out = {}
    with system_identity():
        with SessionLocal() as db:
            for sym in symbols:
                for tier in ("hft", "mid", "long"):
                    try:
                        n = db.execute(text(
                            "SELECT count(*) FROM brain_theses"
                            " WHERE symbol = :s AND tier = :t"
                        ), {"s": sym, "t": tier}).scalar()
                    except Exception:
                        db.rollback()
                        n = None
                    out[(sym, tier)] = n
    return out


def main() -> int:
    drv_rows = pb.SqlDriver().fetch(pb.era_start_ts(), 0, 3)
    print(f"取到 PP 平仓腿 {len(drv_rows)} 笔（只读 lane_ledger）")
    symbols = sorted({str(r["symbol"]).upper() for r in drv_rows})
    before = _thesis_counts(symbols)
    print("论题计数（前）:", {f"{k[0]}/{k[1]}": v for k, v in before.items()})

    drv = CapturingDriver(drv_rows)
    cur = {"cursor_ts": pb.era_start_ts(), "cursor_id": 0, "ingested": 0, "failed": 0,
           "skipped": 0, "attempts_id": -1, "attempts": 0}
    res = pb.run_once(driver=drv, root=ROOT, cursor=cur)
    print("run_once:", {k: v for k, v in res.items() if k != "cursor"})

    after = _thesis_counts(symbols)
    print("论题计数（后）:", {f"{k[0]}/{k[1]}": v for k, v in after.items()})
    grew = [k for k in before if (after[k] or 0) > (before[k] or 0)]
    print()
    print("验收 1a: 主脑见到 hft/PP 的笔数 =",
          sum(1 for o in drv.seen if o.tier == "hft"
              and o.strategy_id == "mm_asterdex_pp"))
    print("验收 1b: 论题条数增加项 =", grew or "无（中线/长线记忆未被改写）")
    print("验收 1c: 是否写了 strategy_trades =",
          any(o.persist_trade for o in drv.seen))
    ok = bool(drv.seen) and not grew and not any(o.persist_trade for o in drv.seen)
    print("验收 1 结论:", "通过" if ok else "未通过")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
