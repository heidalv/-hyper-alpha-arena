# -*- coding: utf-8 -*-
"""Z32：昨晚大亏的行情归因——是 beta（大盘同跌）还是个股/策略问题？

对窗口内每笔仓位：算入场→出场的标的涨跌、同期 BTC/ETH 涨跌，
并给出组合层面的「同向敞口」与逐小时权益路径。
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

ARENA = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET = os.getenv("MARKET_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
T0 = os.getenv("Z32_T0", "2026-09-09 17:00:00+08")
T1 = os.getenv("Z32_T1", "2026-09-10 09:35:00+08")


def px(c, sym, ts, direction="le", ex="asterdex"):
    op = "<=" if direction == "le" else ">="
    q = text(f"""
        select close_price from crypto_klines
        where symbol=:s and period='1h' and exchange=:e and timestamp {op} :t
        order by timestamp {'desc' if direction=='le' else 'asc'} limit 1
    """)
    r = c.execute(q, {"s": sym, "e": ex, "t": int(ts)}).first()
    if not r:
        r = c.execute(q, {"s": sym, "e": "binance", "t": int(ts)}).first()
    return float(r[0]) if r else None


def main() -> int:
    eng = create_engine(ARENA)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, timeframe_tier, trade_nature, entry_price, close_price,
                   original_size, peak_pnl_pct, unrealized_pnl, partial_realized_pnl,
                   partial_fee_paid, close_reason, opened_at, closed_at
            from paper_positions
            where closed_at >= :t0 and closed_at <= :t1
            order by closed_at
        """), {"t0": T0, "t1": T1}).fetchall()]

    eng2 = create_engine(MARKET)
    import time as _t
    from datetime import datetime
    def ts(s):
        return int(datetime.fromisoformat(s).timestamp())
    t0s, t1s = ts("2026-09-09 17:00:00+08:00"), ts("2026-09-10 09:35:00+08:00")

    with eng2.connect() as mc:
        mc.execute(text("set statement_timeout='300000'"))
        print("=== 大盘同期涨跌（9/9 17:00 → 9/10 09:35 CST）===")
        for sym in ("BTC", "ETH", "SOL", "BNB", "XRP", "UNI", "VIRTUAL", "ASTER"):
            a = px(mc, sym, t0s, "le")
            b = px(mc, sym, t1s, "le")
            if a and b:
                print(f"  {sym:<9}{a:>12.6g} → {b:>12.6g}  {(b/a-1)*100:>+7.2f}%")

        print("\n=== 逐笔：标的实际涨跌 vs 本笔盈亏 ===")
        print(f"{'id':>6}{'sym':<10}{'tier':<6}{'入场→出场涨跌%':>15}{'本笔USD':>10}"
              f"{'同期BTC%':>10}{'超额%':>9}  通道")
        tot = 0.0
        for r in rows:
            t_in = ts(str(r["opened_at"]).replace(" ", "T").replace("+08", "+08:00")
                      if "+" not in str(r["opened_at"]) else str(r["opened_at"]))
            t_out = ts(str(r["closed_at"]).replace(" ", "T").replace("+08", "+08:00")
                       if "+" not in str(r["closed_at"]) else str(r["closed_at"]))
            with eng2.connect() as mc:
                pin = px(mc, r["symbol"], t_in, "le")
                pout = px(mc, r["symbol"], t_out, "le")
                btc_in = px(mc, "BTC", t_in, "le")
                btc_out = px(mc, "BTC", t_out, "le")
            move = ((pout / pin - 1) * 100) if pin and pout else float("nan")
            btc = ((btc_out / btc_in - 1) * 100) if btc_in and btc_out else float("nan")
            u = (float(r["unrealized_pnl"] or 0) + float(r["partial_realized_pnl"] or 0)
                 - float(r["partial_fee_paid"] or 0))
            tot += u
            print(f"{r['id']:>6}{str(r['symbol']):<10}{str(r['timeframe_tier']):<6}"
                  f"{move:>15.2f}{u:>+10.2f}{btc:>10.2f}{move-btc:>9.2f}  "
                  f"{str(r['close_reason'] or '')[:26]}")
        print(f"  合计 = {tot:+.2f}")

        print("\n=== 窗口内「同向敞口」时间线（逐个开/平事件）===")
        with eng.connect() as c:
            c.execute(text("set app.is_admin='on'"))
            ev = []
            for r in c.execute(text("""
                select id, symbol, opened_at, closed_at from paper_positions
                where opened_at <= :t1 and (closed_at is null or closed_at >= :t0)
                order by opened_at
            """), {"t0": T0, "t1": T1}).fetchall():
                ev.append((str(r[2]), "OPEN", f"{r[1]}#{r[0]}"))
                if r[3]:
                    ev.append((str(r[3]), "CLOSE", f"{r[1]}#{r[0]}"))
            for t, k, s in sorted(ev):
                print(f"  {t[:19]}  {k:<6}{s}")
        # 峰值之后回吐
        print("\n=== 峰值 → 最终（回吐幅度）===")
        for r in rows:
            u = (float(r["unrealized_pnl"] or 0) + float(r["partial_realized_pnl"] or 0)
                 - float(r["partial_fee_paid"] or 0))
            ntl = float(r["original_size"] or 0) * float(r["entry_price"] or 0)
            peak = float(r["peak_pnl_pct"] or 0) * 100
            fin = (u / ntl * 100) if ntl else 0
            print(f"  #{r['id']} {str(r['symbol']):<9} 峰值={peak:>5.2f}% 最终={fin:>+6.2f}% "
                  f"回吐={peak-fin:>6.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
