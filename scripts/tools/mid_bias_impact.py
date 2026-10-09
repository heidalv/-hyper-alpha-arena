"""Does the stale-mid defect corrupt only the spread/price SPLIT, or the TOTAL?

Discovered: the engine records `mid_px` from a market row (runner.py:692
`mid = float(market_row.get("mid"))`) and that mid is systematically biased
TOWARD our own trade direction (buy +10.17bp, sell -5.52bp, t=+4.03).

If entry mid is inflated by delta, then:
  spread_usd is inflated by  delta*qty          (entry)
  price_usd at exit is deflated by delta*qty    (because avg_mid is higher)
=> the two errors CANCEL in net_bp, leaving only the SPLIT wrong.

This tool checks that cancellation hypothesis by comparing the reported
spread against a spread measured from the REAL book.
"""
from __future__ import annotations

import io
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
CORE = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MKT = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CUT = "2026-10-06 02:57:27+08"


def main() -> int:
    with psycopg.connect(CORE, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"""
            SELECT ts, symbol, coalesce(meta_json->>'side',''),
                   coalesce(meta_json->>'fill_px','0')::float8,
                   coalesce(meta_json->>'mid_px','0')::float8,
                   coalesce(spread_bp,0), coalesce(price_bp,0),
                   coalesce(net_bp,0), coalesce(fee_bp,0), notional
            FROM lane_ledger
            WHERE lane_id='mm_asterdex' AND event='fill'
              AND ts >= '{CUT}'::timestamptz ORDER BY ts
        """)
        rows = cur.fetchall()

    rep_sp = rep_px = rep_net = 0.0
    true_cap = 0.0
    n = 0
    with psycopg.connect(MKT, autocommit=True) as m, m.cursor() as mc:
        for ts, sym, side, fpx, mid, sp, px, net, fee, notl in rows:
            if fpx <= 0 or side not in ("buy", "sell"):
                continue
            s = str(sym).upper()
            if not s.endswith("USDT"):
                s += "USDT"
            t0 = int(ts.timestamp() * 1000)
            mc.execute("""
                SELECT ((bid_px+ask_px)/2.0) FROM asterdex_book_ticker
                WHERE symbol=%s AND event_ts_ms BETWEEN %s AND %s
                ORDER BY abs(event_ts_ms - %s) ASC LIMIT 1
            """, (s, t0 - 3000, t0 + 3000, t0))
            r = mc.fetchone()
            if not r or r[0] is None or float(r[0]) <= 0:
                continue
            mb = float(r[0])
            N = float(notl or 0)
            cap_true = (((mb - fpx) / fpx) if side == "buy"
                        else ((fpx - mb) / fpx))
            rep_sp += N * float(sp) / 1e4
            rep_px += N * float(px) / 1e4
            rep_net += N * float(net) / 1e4
            true_cap += N * cap_true
            n += 1

    print("=" * 92)
    print("报告口径 vs 真实盘口口径")
    print("=" * 92)
    print(f"  样本 n = {n}")
    print(f"  报告 spread    = {rep_sp:+10.2f}")
    print(f"  报告 price     = {rep_px:+10.2f}")
    print(f"  报告 net       = {rep_net:+10.2f}   (spread+price+funding+fee)")
    print(f"  真实捕获(盘口) = {true_cap:+10.2f}")
    print()
    print(f"  报告 spread − 真实捕获 = {rep_sp - true_cap:+.2f}")
    print()
    print("  解读：")
    print("   · 若 |报告 net| 与真实总盈亏接近 ⇒ 拆分失真但**总额可信**")
    print("   · 若差得很多 ⇒ 总额也被污染")
    print("   注意：本工具只能给出**进场侧**的真实捕获；")
    print("         出场侧的价格盈亏需要配对往返才能算，此处不做。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
