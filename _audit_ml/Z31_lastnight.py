# -*- coding: utf-8 -*-
"""Z31：「昨晚到现在」交易细节全景（用户报告大亏）。

窗口：2026-09-09 12:00 起（含昨晚 + 今日）。
输出：账户权益、逐笔平仓明细、当前持仓、平仓事件（部分平仓/通道）、逐小时盈亏。
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
SINCE = os.getenv("Z31_SINCE", "2026-09-09 12:00:00+08")


def usd(r):
    return (float(r.get("unrealized_pnl") or 0) + float(r.get("partial_realized_pnl") or 0)
            - float(r.get("partial_fee_paid") or 0))


def main() -> int:
    eng = create_engine(URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        print("=== 当前时间 ===")
        for r in c.execute(text("select now()")).fetchall():
            print("  db now() =", r[0])

        print("\n=== 账户权益（paper_balances）===")
        try:
            for r in c.execute(text("""
                select account_id, total_equity, available_balance, updated_at
                from paper_balances order by account_id
            """)).fetchall():
                print(f"  acct={r[0]:<4} equity={float(r[1] or 0):>10.2f} "
                      f"avail={float(r[2] or 0):>10.2f}  upd={r[3]}")
        except Exception as e:
            print("  paper_balances 读取失败:", str(e)[:100])

        print(f"\n=== 窗口内平仓（closed_at >= {SINCE}）===")
        rows = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, timeframe_tier, trade_nature, side, strategy_id,
                   entry_price, close_price, sl_price, original_size, size, leverage, margin,
                   peak_pnl_pct, trough_pnl_pct, unrealized_pnl, partial_realized_pnl,
                   partial_fee_paid, close_reason, opened_at, closed_at
            from paper_positions
            where status='closed' and closed_at >= :since
            order by closed_at
        """), {"since": SINCE}).fetchall()]
        tot = 0.0
        print(f"{'id':>6}{'sym':<10}{'tier':<6}{'nature':<13}{'USD':>9}{'峰值%':>7}"
              f"{'入场':>11}{'出场':>11}{'持仓h':>7}  通道")
        for r in rows:
            u = usd(r)
            tot += u
            hold = ((r["closed_at"] - r["opened_at"]).total_seconds() / 3600.0
                    if r["closed_at"] and r["opened_at"] else 0)
            print(f"{r['id']:>6}{str(r['symbol']):<10}{str(r['timeframe_tier']):<6}"
                  f"{str(r['trade_nature'] or ''):<13}{u:>+9.2f}"
                  f"{float(r['peak_pnl_pct'] or 0)*100:>7.2f}"
                  f"{float(r['entry_price'] or 0):>11.6g}{float(r['close_price'] or 0):>11.6g}"
                  f"{hold:>7.1f}  {str(r['close_reason'] or '')[:40]}")
        print(f"\n  合计 {len(rows)} 笔，总 USD = {tot:+.2f}")

        print("\n=== 窗口内开仓 ===")
        for r in c.execute(text("""
            select id, symbol, timeframe_tier, trade_nature, entry_price, original_size,
                   leverage, margin, opened_at, status
            from paper_positions where opened_at >= :since order by opened_at
        """), {"since": SINCE}).fetchall():
            print(f"  #{r[0]} {str(r[1]):<9}{str(r[2]):<5}{str(r[3] or ''):<13}"
                  f"entry={float(r[4] or 0):<11.6g} qty={float(r[5] or 0):<10.4g} "
                  f"lev={r[6]} margin={float(r[7] or 0):<8.2f} {str(r[8])[:16]} {r[9]}")

        print("\n=== 当前持仓 ===")
        opens = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, timeframe_tier, trade_nature, entry_price, mark_price, sl_price,
                   original_size, size, leverage, margin, peak_pnl_pct, trough_pnl_pct,
                   unrealized_pnl, partial_realized_pnl, partial_fee_paid, opened_at
            from paper_positions where status='open' order by opened_at
        """)).fetchall()]
        tu = 0.0
        for r in opens:
            u = usd(r)
            tu += u
            print(f"  #{r['id']} {str(r['symbol']):<9}{str(r['timeframe_tier']):<5}"
                  f"{str(r['trade_nature'] or ''):<13} entry={float(r['entry_price'] or 0):<11.6g}"
                  f" mark={float(r['mark_price'] or 0):<11.6g} sl={float(r['sl_price'] or 0):<11.6g}"
                  f" peak={float(r['peak_pnl_pct'] or 0)*100:>6.2f}% "
                  f"trough={float(r['trough_pnl_pct'] or 0)*100:>6.2f}% USD={u:>+8.2f}")
        print(f"\n  未实现合计 = {tu:+.2f}")

        print("\n=== 窗口内平仓事件（部分平仓 / 通道 / 硬线）===")
        for r in c.execute(text("""
            select position_id, symbol, event_type, exit_channel, quantity, price,
                   pnl_pct_at_event, peak_pnl_pct_at_event, close_ratio, metadata_json,
                   created_at
            from position_exit_events
            where created_at >= :since
            order by created_at
        """), {"since": SINCE}).fetchall():
            md = r[9]
            if isinstance(md, str):
                try:
                    md = json.loads(md)
                except Exception:
                    md = {}
            brief = ""
            if isinstance(md, dict):
                brief = str({k: md.get(k) for k in ("reason", "channel", "exit_source")
                             if k in md})[:70]
            print(f"  {str(r[10])[:19]} #{r[0]:<5}{str(r[1]):<9}{str(r[2]):<22}"
                  f"{str(r[3] or '-'):<22} qty={r[4]} px={r[5]} "
                  f"pnl%={r[6]} peak%={r[7]} {brief}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
