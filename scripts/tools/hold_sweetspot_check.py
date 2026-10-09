"""Does the hold-time sweet spot survive WITHIN the current size regime?

R051 found a sweet spot (30-45s, +23.79bp) using all 596 roundtrips.
But R065 proved that pooling across size regimes creates fake patterns.
If the sweet spot is an artifact of the big-leg era, it must NOT be used to
justify changing exit timing.

This restricts the analysis to the CURRENT regime (leg notional < $1k) and
re-tests. Only if the pattern survives is it usable evidence.
"""
from __future__ import annotations

import io
import json
import math
import statistics as st
import sys
from collections import defaultdict

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\001Alpha\Hyper-Alpha-Arena"
DSN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
LOG = ROOT + r"\data\flow_roundtrip_log.jsonl"
ERA = "2026-10-05 15:57:56+08"
BUCKETS = [(0, 15, "<15s"), (15, 30, "15-30s"), (30, 45, "30-45s"),
           (45, 60, "45-60s"), (60, 120, "60-120s"), (120, 10 ** 9, ">2min")]


def _tl(vals):
    n = len(vals)
    if n < 2:
        return 0.0, 0.0, 0.0
    mu, sd = st.mean(vals), st.pstdev(vals)
    se = sd / math.sqrt(n)
    return mu, se, (mu / se if se else 0.0)


def main() -> int:
    # roundtrips from the log
    rts = []
    for ln in open(LOG, encoding="utf-8", errors="replace"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        y, h, ts = d.get("y_bp"), d.get("hold_sec"), d.get("ts")
        if y is None or h is None or ts is None:
            continue
        try:
            rts.append((float(ts), float(h), float(y)))
        except (TypeError, ValueError):
            continue

    print("=" * 92)
    print("持仓时长甜蜜区：全样本 vs 当前规模档")
    print("=" * 92)
    print(f"  往返样本总数 = {len(rts)}")

    # Use the ledger to know which roundtrips belong to the small-leg era.
    # The roundtrip log has no notional, so approximate: legs <$1k dominate
    # after the 5% cap took effect. We instead test on the roundtrips whose
    # ts falls in the small-size window (>= 04:00 on 10-06) AND report all.
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"""
            SELECT to_char(ts AT TIME ZONE 'Asia/Shanghai','MM-DD HH24:MI:SS')
            FROM lane_ledger
            WHERE lane_id='mm_asterdex' AND event='fill' AND notional >= 1000
              AND ts >= '{ERA}'::timestamptz
            ORDER BY ts
        """)
        big_ts = [r[0] for r in cur.fetchall()]

    def show(label, rows):
        print()
        print(f"  {label}   n={len(rows)}")
        if len(rows) < 10:
            print("     样本太少")
            return
        print(f"     {'bucket':<10}{'n':>5}{'mean y_bp':>12}{'SE':>8}"
              f"{'t':>8}{'win%':>7}")
        for lo, hi, name in BUCKETS:
            g = [r[2] for r in rows if lo <= r[1] < hi]
            if len(g) < 5:
                continue
            mu, se, t = _tl(g)
            win = sum(1 for x in g if x > 0) / len(g) * 100
            print(f"     {name:<10}{len(g):>5}{mu:>12.2f}{se:>8.2f}"
                  f"{t:>8.2f}{win:>7.0f}")
        mu, se, t = _tl([r[2] for r in rows])
        print(f"     {'ALL':<10}{len(rows):>5}{mu:>12.2f}{se:>8.2f}{t:>8.2f}")

    show("全部往返（含大额腿时代）", rts)

    # ── 关键检验：只取小规模时代（大额腿结束后）的往返 ──
    if big_ts:
        print()
        print(f"  大额腿时段最后一条 = {big_ts[-1]}（此后为小规模时代）")

    CUT = None
    for ts, h, y in rts:
        pass
    # 用账本求出"最后一条大额腿"的 epoch，然后只保留其后的往返
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"""
            SELECT extract(epoch FROM max(ts)) FROM lane_ledger
            WHERE lane_id='mm_asterdex' AND event='fill' AND notional >= 1000
              AND ts >= '{ERA}'::timestamptz
        """)
        row = cur.fetchone()
        CUT = float(row[0]) if row and row[0] else None
    if CUT:
        small = [r for r in rts if r[0] >= CUT]
        show(f"**仅小规模时代**（ts >= 最后大额腿）", small)
        big = [r for r in rts if r[0] < CUT]
        show("大额腿时代", big)

    print()
    print("=" * 92)
    print("结论指引")
    print("=" * 92)
    print("  若小规模时代的甜蜜区消失 ⇒ R051 的甜蜜区是跨时代池化的假象，")
    print("      **不能**用它来支持改动出场时机。")
    print("  若依然存在且 |t| 较大 ⇒ 可以作为证据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
