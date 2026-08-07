"""分析昨天至今的交易数据"""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")
from dotenv import load_dotenv

load_dotenv(".env")

from sqlalchemy import text
from backend.database.connection import SessionLocal
from backend.services.trade_performance_analyzer import analyze_closed_trades


def main():
    tz8 = timezone(timedelta(hours=8))
    now = datetime.now(tz8)
    yesterday_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    since_at = yesterday_start.astimezone(timezone.utc)
    since_naive = since_at.replace(tzinfo=None)

    db = SessionLocal()
    try:
        accs = db.execute(
            text("SELECT id, name, account_type, initial_capital, current_cash FROM accounts ORDER BY id")
        ).fetchall()
        print("=== 账户 ===")
        for a in accs:
            print(f"  id={a[0]} {a[1]} ({a[2]}) 本金={a[3]} 现金={a[4]}")

        report = analyze_closed_trades(db=db, since_at=since_at, exclude_rebate=True)
        print()
        print("=== 总体 (排除返佣策略) ===")
        print(f"时间: {yesterday_start.strftime('%Y-%m-%d %H:%M')} ~ {now.strftime('%Y-%m-%d %H:%M')} UTC+8")
        print(f"已平仓: {report.total_closed} 笔")
        print(f"净盈亏: {report.overall_pnl:+.2f} USDT")
        print(f"胜率: {report.overall_win_rate * 100:.1f}%")

        print("\n--- 平仓原因 ---")
        for d in sorted(report.by_close_reason, key=lambda x: -x.count):
            print(
                f"  {d.key:25s} {d.count:3d}笔 "
                f"胜率{d.win_rate * 100:5.1f}% 盈亏{d.total_pnl:+8.2f} 均{d.avg_pnl:+.2f}"
            )

        print("\n--- 周期层级 ---")
        for d in sorted(report.by_tier, key=lambda x: -x.count):
            print(f"  {d.key:10s} {d.count:3d}笔 胜率{d.win_rate * 100:5.1f}% 盈亏{d.total_pnl:+8.2f}")

        print("\n--- 策略类型 ---")
        for d in sorted(report.by_nature, key=lambda x: -x.count):
            print(f"  {d.key:15s} {d.count:3d}笔 胜率{d.win_rate * 100:5.1f}% 盈亏{d.total_pnl:+8.2f}")

        print("\n--- 币种 (按盈亏排序) ---")
        for d in report.by_symbol:
            print(f"  {d.key:10s} {d.count:3d}笔 胜率{d.win_rate * 100:5.1f}% 盈亏{d.total_pnl:+8.2f}")

        print("\n--- 各账户盈亏 ---")
        for aid, aname, atype, *_ in accs:
            r = analyze_closed_trades(db=db, since_at=since_at, account_id=aid, exclude_rebate=True)
            if r.total_closed:
                print(
                    f"  [{aid}] {aname}: {r.total_closed}笔 "
                    f"胜率{r.overall_win_rate * 100:.0f}% 盈亏{r.overall_pnl:+.2f}"
                )

        opens = db.execute(
            text(
                """
                SELECT symbol, side, entry_price, mark_price, unrealized_pnl,
                       partial_realized_pnl, partial_fee_paid, margin,
                       timeframe_tier, trade_nature, opened_at, a.name
                FROM paper_positions p
                JOIN accounts a ON a.id = p.account_id
                WHERE p.status = 'open'
                  AND (p.strategy_id IS NULL OR p.strategy_id NOT LIKE 'rebate_%')
                ORDER BY opened_at DESC
                """
            )
        ).fetchall()
        print(f"\n--- 当前未平仓 ({len(opens)}笔) ---")
        total_unreal = 0.0
        for o in opens[:15]:
            unreal = float(o[4] or 0) + float(o[5] or 0) - float(o[6] or 0)
            total_unreal += unreal
            print(
                f"  {o[11]} {o[0]:8s} {o[1]:5s} entry={o[2]} mark={o[3]} "
                f"unreal={unreal:+.2f} tier={o[8]} {str(o[10])[:19]}"
            )
        if len(opens) > 15:
            print(f"  ... 另有 {len(opens) - 15} 笔")
        print(f"  浮动合计: {total_unreal:+.2f} USDT")

        recent = db.execute(
            text(
                """
                SELECT p.symbol, p.side, p.entry_price, p.close_price, p.close_reason,
                       COALESCE(p.partial_realized_pnl, 0) + COALESCE(p.unrealized_pnl, 0)
                         - COALESCE(p.partial_fee_paid, 0) AS net,
                       p.timeframe_tier, p.trade_nature, p.closed_at, a.name
                FROM paper_positions p
                JOIN accounts a ON a.id = p.account_id
                WHERE p.status = 'closed' AND p.closed_at >= :since
                  AND (p.strategy_id IS NULL OR p.strategy_id NOT LIKE 'rebate_%')
                ORDER BY p.closed_at DESC
                LIMIT 20
                """
            ),
            {"since": since_naive},
        ).fetchall()
        print("\n--- 最近20笔平仓 ---")
        wins = sum(1 for r in recent if r[5] > 0)
        print(f"  近20笔: {wins}胜 {len(recent) - wins}负 净{sum(r[5] for r in recent):+.2f}")
        for r in recent:
            print(
                f"  {str(r[8])[:19]} {r[9]:6s} {r[0]:8s} {r[1]:5s} "
                f"{r[4]:22s} net={r[5]:+.2f} tier={r[6]}"
            )

        daily = db.execute(
            text(
                """
                SELECT DATE(p.closed_at) AS d,
                       COUNT(*) AS cnt,
                       SUM(
                         CASE WHEN COALESCE(p.partial_realized_pnl, 0)
                                + COALESCE(p.unrealized_pnl, 0)
                                - COALESCE(p.partial_fee_paid, 0) > 0
                              THEN 1 ELSE 0 END
                       ) AS wins,
                       SUM(
                         COALESCE(p.partial_realized_pnl, 0)
                         + COALESCE(p.unrealized_pnl, 0)
                         - COALESCE(p.partial_fee_paid, 0)
                       ) AS pnl
                FROM paper_positions p
                WHERE p.status = 'closed' AND p.closed_at >= :since
                  AND (p.strategy_id IS NULL OR p.strategy_id NOT LIKE 'rebate_%')
                GROUP BY DATE(p.closed_at)
                ORDER BY d
                """
            ),
            {"since": since_naive},
        ).fetchall()
        print("\n--- 每日统计 ---")
        for d in daily:
            wr = d[2] / d[1] * 100 if d[1] else 0
            print(f"  {d[0]}: {d[1]}笔 胜率{wr:.0f}% 盈亏{d[3]:+.2f}")

        print("\n--- 系统洞察 ---")
        for ins in report.insights:
            print(f"  * {ins}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
