"""修复后交易监控 — 对比修复前后 ai_reverse / 开仓频率。"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from sqlalchemy import text

from backend.database.connection import SessionLocal
from backend.services.health_snapshot_service import _fetch_governor_ownership
from backend.services.learning_health_service import build_learning_health
from backend.services.runtime_tuning_store import get_all_tuning

TZ8 = timezone(timedelta(hours=8))
FIX_AT = datetime(2026, 7, 6, 10, 0, 0, tzinfo=TZ8)
ACCOUNT_ID = 14


def _section(title: str) -> None:
    print(f"\n{'=' * 50}")
    print(title)
    print("=" * 50)


def main() -> int:
    parser = argparse.ArgumentParser(description="修复后交易监控")
    parser.add_argument("--account", type=int, default=ACCOUNT_ID)
    args = parser.parse_args()

    now = datetime.now(TZ8)
    since_fix = FIX_AT.replace(tzinfo=None)

    print(f"监控时间: {now.strftime('%Y-%m-%d %H:%M')} (UTC+8)")
    print(f"修复基准: {FIX_AT.strftime('%Y-%m-%d %H:%M')} (UTC+8)")

    tuning = get_all_tuning()
    _section("当前参数")
    for key in ("maturity_global_n1", "scalp_min_confidence", "master_reduce_min_loss_pct"):
        print(f"  {key}: {tuning.get(key)}")
    swing = (tuning.get("by_nature") or {}).get("swing", {})
    print(f"  swing.min_confidence: {swing.get('min_confidence')}")

    _section("系统健康")
    try:
        gov = _fetch_governor_ownership()
        print(f"  governor_ownership: ok ({len(gov.get('ownership', {}))} 项)")
    except Exception as exc:
        print(f"  governor_ownership: FAIL — {exc}")
    lh = build_learning_health()
    print(f"  learning overall: {lh['overall']}")
    for item in lh["items"]:
        if item["name"] in ("runtime_gates", "retrospective", "evolution"):
            print(f"  {item['name']}: {item['status']}")

    db = SessionLocal()
    try:
        def _stats(label: str, start: datetime, end: datetime | None = None) -> None:
            start_naive = start.replace(tzinfo=None) if start.tzinfo else start
            params: dict = {"aid": args.account, "start": start_naive}
            end_clause = ""
            if end is not None:
                end_naive = end.replace(tzinfo=None) if end.tzinfo else end
                params["end"] = end_naive
                end_clause = "AND closed_at < :end"

            rows = db.execute(
                text(
                    f"""
                    SELECT close_reason, COUNT(*) AS cnt,
                           SUM(COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0)
                               - COALESCE(partial_fee_paid,0)) AS pnl
                    FROM paper_positions
                    WHERE status='closed' AND account_id=:aid
                      AND closed_at >= :start {end_clause}
                      AND (strategy_id IS NULL OR strategy_id NOT LIKE 'rebate_%')
                    GROUP BY close_reason
                    ORDER BY cnt DESC
                    """
                ),
                params,
            ).fetchall()
            total = sum(r[1] for r in rows)
            net = sum(float(r[2] or 0) for r in rows)
            print(f"\n  [{label}] {total}笔 净{net:+.2f} USDT")
            for r in rows[:6]:
                print(f"    {r[0]:22s} {r[1]:2d}笔 {float(r[2] or 0):+.2f}")

        _section("交易对比")
        _stats("修复后", FIX_AT, None)
        _stats(
            "修复前3小时",
            FIX_AT - timedelta(hours=3),
            FIX_AT,
        )
        _stats(
            "今早07-10点",
            datetime(2026, 7, 6, 7, 0, 0, tzinfo=TZ8),
            FIX_AT,
        )

        opens = db.execute(
            text(
                """
                SELECT COUNT(*) FROM paper_positions
                WHERE account_id=:aid AND opened_at >= :since
                  AND (strategy_id IS NULL OR strategy_id NOT LIKE 'rebate_%')
                """
            ),
            {"aid": args.account, "since": since_fix},
        ).scalar()
        print(f"\n  修复后新开仓: {opens} 笔")

        ai_post = db.execute(
            text(
                """
                SELECT COUNT(*) FROM paper_positions
                WHERE status='closed' AND account_id=:aid
                  AND closed_at >= :since AND close_reason='ai_reverse'
                """
            ),
            {"aid": args.account, "since": since_fix},
        ).scalar()
        print(f"  修复后 ai_reverse: {ai_post} 笔")

        recent = db.execute(
            text(
                """
                SELECT closed_at, symbol, side, close_reason,
                       COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0)
                         - COALESCE(partial_fee_paid,0) AS net
                FROM paper_positions
                WHERE status='closed' AND account_id=:aid
                  AND (strategy_id IS NULL OR strategy_id NOT LIKE 'rebate_%')
                ORDER BY closed_at DESC LIMIT 8
                """
            ),
            {"aid": args.account},
        ).fetchall()
        _section("最近8笔平仓")
        for r in recent:
            print(f"  {str(r[0])[:19]} {r[1]:8s} {r[2]:5s} {r[3]:22s} net={float(r[4]):+.2f}")

        holding = db.execute(
            text(
                """
                SELECT symbol, side, unrealized_pnl, timeframe_tier, trade_nature, opened_at
                FROM paper_positions
                WHERE status='open' AND account_id=:aid
                  AND (strategy_id IS NULL OR strategy_id NOT LIKE 'rebate_%')
                ORDER BY opened_at DESC
                """
            ),
            {"aid": args.account},
        ).fetchall()
        _section(f"当前持仓 ({len(holding)}笔)")
        for h in holding:
            print(
                f"  {h[0]:8s} {h[1]:5s} unreal={float(h[2] or 0):+.2f} "
                f"{h[3]}/{h[4]} opened={str(h[5])[:19]}"
            )
    finally:
        db.close()

    print("\n提示: 修复后未满1小时时样本可能为0，属正常；请重启后端使代码修改生效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
