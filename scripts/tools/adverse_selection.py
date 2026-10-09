"""Adverse selection: why does each leg lose ~8bp?

A passive maker at the touch should EARN ~half-spread when filled. We are
losing 8bp. Standard suspect: adverse selection -- we get filled only when
the market is about to move against us (toxic flow).

For every ENTRY leg, using the real book:
   capture_bp = (mid_at_fill - fill_px)/fill_px*1e4   for buys   (positive = good)
              = (fill_px - mid_at_fill)/fill_px*1e4   for sells
   drift_30s  = mid(t+30s) - mid(t) in bp, signed by our side
                negative = price moved AGAINST our position   (toxic)

If capture_bp > 0 but drift is strongly negative, we are being picked off:
we win the spread but lose more to the move.
"""
from __future__ import annotations

import io
import math
import statistics as st
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
CORE = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MKT = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
LANE = "mm_asterdex"
ERA = "2026-10-05 15:57:56+08"
CUT = "2026-10-06 02:57:27+08"     # small-size era starts here


def _t(vals):
    n = len(vals)
    if n < 2:
        return 0.0, 0.0, 0.0
    mu, sd = st.mean(vals), st.pstdev(vals)
    se = sd / math.sqrt(n)
    return mu, se, (mu / se if se else 0.0)


def main() -> int:
    with psycopg.connect(CORE, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"""
            SELECT ts, symbol, coalesce(meta_json->>'side',''),
                   coalesce(meta_json->>'mid_px','0')::float8,
                   coalesce(meta_json->>'fill_px','0')::float8,
                   notional, coalesce(net_bp,0), coalesce(spread_bp,0),
                   coalesce(price_bp,0)
            FROM lane_ledger
            WHERE lane_id='{LANE}' AND event='fill'
              AND ts >= '{CUT}'::timestamptz
              AND coalesce(meta_json->>'flatten','false')='false'
            ORDER BY ts
        """)
        entries = cur.fetchall()

    print("=" * 96)
    print(f"进场腿逆选择测量（小规模时代，n={len(entries)}）")
    print("=" * 96)
    if len(entries) < 5:
        print("  样本太少")
        return 0

    rows = []
    with psycopg.connect(MKT, autocommit=True) as m, m.cursor() as mc:
        for ts, sym, side, mid, fpx, notl, net, sp, px in entries:
            if mid <= 0 or fpx <= 0 or side not in ("buy", "sell"):
                continue
            s = str(sym).upper()
            if not s.endswith("USDT"):
                s += "USDT"
            t0 = int(ts.timestamp() * 1000)
            # ── 关键：用**独立**的盘口基准做 t0 中价，而不是账本里的 mid_px ──
            # 风险：若账本 mid_px 是在"导致成交的那次移动**之前**"抓的，
            # 那么测出的 drift 会把"引起成交的移动"算进去 ⇒ **虚假的逆选择**。
            # 因此这里另取成交时刻附近的真实盘口中价 m0_book。
            mc.execute("""
                SELECT ((bid_px+ask_px)/2.0) FROM asterdex_book_ticker
                WHERE symbol=%s AND event_ts_ms BETWEEN %s AND %s
                ORDER BY abs(event_ts_ms - %s) ASC LIMIT 1
            """, (s, t0 - 3000, t0 + 3000, t0))
            rb = mc.fetchone()
            m0_book = float(rb[0]) if rb and rb[0] is not None else 0.0
            mc.execute("""
                SELECT ((bid_px+ask_px)/2.0) FROM asterdex_book_ticker
                WHERE symbol=%s AND event_ts_ms BETWEEN %s AND %s
                ORDER BY event_ts_ms ASC LIMIT 1
            """, (s, t0 + 25000, t0 + 35000))
            r = mc.fetchone()
            if not r or r[0] is None or float(r[0]) <= 0:
                continue
            m30 = float(r[0])
            # capture: did we buy below mid / sell above mid?
            if side == "buy":
                cap = (mid - fpx) / fpx * 1e4
                dr = (m30 - mid) / mid * 1e4
                dr_book = ((m30 - m0_book) / m0_book * 1e4) if m0_book > 0 else 0.0
            else:
                cap = (fpx - mid) / fpx * 1e4
                dr = (mid - m30) / mid * 1e4
                dr_book = ((m0_book - m30) / m0_book * 1e4) if m0_book > 0 else 0.0
            rows.append((str(sym), side, cap, dr, float(net), float(sp),
                         float(px), dr_book))

    print(f"  成功测量 n={len(rows)}")
    if len(rows) < 5:
        return 0

    caps = [r[2] for r in rows]
    drs = [r[3] for r in rows]
    nets = [r[4] for r in rows]
    drs_book = [r[7] for r in rows if r[7] != 0.0]
    for label, vals in (("成交时捕获 capture_bp", caps),
                        ("之后 30s 漂移 drift_bp(账本 mid 基准)", drs),
                        ("之后 30s 漂移 drift_bp(**独立盘口基准**)", drs_book),
                        ("实际净额 net_bp", nets)):
        mu, se, t = _t(vals)
        print(f"  {label:<40} 均值 {mu:+8.2f}  SE {se:5.2f}  t {t:+6.2f}")

    print()
    print("=" * 96)
    print("分组：捕获好 vs 捕获差")
    print("=" * 96)
    good = [r for r in rows if r[2] >= 0]
    bad = [r for r in rows if r[2] < 0]
    print(f"  {'组':<26}{'n':>5}{'capture':>10}{'drift30':>10}{'net_bp':>10}")
    for name, g in (("捕获 >= 0（买在中间价下）", good),
                    ("捕获 < 0 （买在中间价上）", bad)):
        if len(g) < 3:
            print(f"  {name:<26}{len(g):>5}   (太少)")
            continue
        print(f"  {name:<26}{len(g):>5}{st.mean([x[2] for x in g]):>10.2f}"
              f"{st.mean([x[3] for x in g]):>10.2f}"
              f"{st.mean([x[4] for x in g]):>10.2f}")

    print()
    print("=" * 96)
    print("判定")
    print("=" * 96)
    mcap, _, tcap = _t(caps)
    mdr, _, tdr = _t(drs)
    print(f"  捕获均值 {mcap:+.2f}bp (t={tcap:+.2f})")
    print(f"  30s 漂移均值 {mdr:+.2f}bp (t={tdr:+.2f})")
    print()
    if mcap > 0 and mdr < 0:
        print("  ⇒ **典型逆选择**：我们确实拿到了价差（capture>0），")
        print("     但成交后行情朝不利方向走（drift<0），且幅度更大 ⇒ 被「挑单」。")
        print("     ⇒ 修法是**降低毒流量**（少挂会被打穿的档），不是改价差。")
    elif mcap < 0:
        print("  ⇒ **连价差都没拿到**（capture<0）：我们的成交价劣于当时中价。")
        print("     ⇒ 这更像**报价/取整问题**，而不是被挑单。")
    else:
        print("  ⇒ 捕获与漂移同号，需看具体分布。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
