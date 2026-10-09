"""Verify the reported roundtrip loss against the engine's OWN same-instant mid.

R066 taught: a measurement compared across sources with >1s misalignment
manufactures a fake "systematic bias". So before concluding "we lose 16bp per
roundtrip to adverse drift", measure it with the engine's own `book_mid`
(recorded at BOTH entry and exit, same instant, no alignment error).

Pairs entry/exit legs by position_id, then compares:
    reported : entry.spread_bp + exit.(spread_bp + price_bp + fee_bp)
    clean    : (book_mid_exit - book_mid_entry)/book_mid_entry, signed by side
Only uses legs that have `book_mid` on BOTH ends (new telemetry).
"""
from __future__ import annotations

import io
import math
import statistics as st
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
DSN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
LANE = "mm_asterdex"


def _t(v):
    n = len(v)
    if n < 2:
        return 0.0, 0.0, 0.0
    mu, sd = st.mean(v), st.pstdev(v)
    se = sd / math.sqrt(n)
    return mu, se, (mu / se if se else 0.0)


def main() -> int:
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"""
            SELECT position_id, ts,
                   coalesce(meta_json->>'flatten','false')='true' AS is_exit,
                   coalesce(meta_json->>'side',''),
                   coalesce(meta_json->>'book_mid','0')::float8,
                   coalesce(meta_json->>'mid_px','0')::float8,
                   coalesce(spread_bp,0), coalesce(price_bp,0),
                   coalesce(fee_bp,0), coalesce(net_bp,0), notional
            FROM lane_ledger
            WHERE lane_id='{LANE}' AND event='fill'
              AND position_id IS NOT NULL
              AND ts >= '2026-10-06 08:40:00+08'::timestamptz
            ORDER BY ts
        """)
        rows = cur.fetchall()

    # group by position_id
    pos: dict = {}
    for r in rows:
        pos.setdefault(r[0], []).append(r)

    pairs = []
    for pid, g in pos.items():
        ent = [x for x in g if not x[2]]
        ex = [x for x in g if x[2]]
        if not ent or not ex:
            continue
        e, x = ent[0], ex[-1]
        if e[4] <= 0 or x[4] <= 0:
            continue
        side = str(e[3])
        if side not in ("buy", "sell"):
            continue
        # clean: full roundtrip mid move, signed so that positive = profit
        raw = (x[4] - e[4]) / e[4] * 1e4
        clean = raw if side == "buy" else -raw
        # reported total
        rep = float(e[6]) + float(x[6]) + float(x[7]) + float(x[8])
        pairs.append((e[1], x[1], side, e[10], clean, rep, e[4], x[4]))

    print("=" * 100)
    print("往返级：报告净额 vs 用引擎自有同刻 mid 计算的真实漂移")
    print("=" * 100)
    if len(pairs) < 3:
        print(f"  可用配对 n={len(pairs)}（需要两端都有 book_mid）⇒ 样本不足，暂不判定")
        print()
        print("  ⚠️ book_mid 是本轮(T52)才加的遥测，只有新腿才有；")
        print("     市场当前很静，需要继续累积。")
        # still show the raw leg counts
        with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
            cur.execute(f"""
                SELECT count(*) FILTER (WHERE meta_json ? 'book_mid')
                FROM lane_ledger WHERE lane_id='{LANE}' AND event='fill'
                  AND ts >= '2026-10-06 08:40:00+08'::timestamptz
            """)
            n_bm = (cur.fetchone() or [0])[0]
        print(f"     当前带 book_mid 的腿数 = {n_bm}")
        return 0

    print(f"  配对数 n = {len(pairs)}")
    print()
    print(f"  {'entry':<10}{'exit':<10}{'side':<6}{'notional':>11}"
          f"{'clean_move':>12}{'reported':>11}")
    for e_ts, x_ts, side, notl, clean, rep, bm_e, bm_x in pairs[:20]:
        print(f"  {e_ts.strftime('%H:%M:%S'):<10}{x_ts.strftime('%H:%M:%S'):<10}"
              f"{side:<6}{float(notl or 0):>11,.0f}{clean:>12.2f}{rep:>11.2f}")

    cl = [p[4] for p in pairs]
    rp = [p[5] for p in pairs]
    mc, sec, tc = _t(cl)
    mr, ser, tr = _t(rp)
    print()
    print(f"  真实中价漂移（我的方向为正）: 均值 {mc:+8.2f}bp  t {tc:+6.2f}")
    print(f"  报告净额                    : 均值 {mr:+8.2f}bp  t {tr:+6.2f}")
    print(f"  差（报告 − 真实）           : {mr-mc:+8.2f}bp")
    print()
    if mc < 0 and abs(tc) >= 1.96:
        print("  ⇒ **真实漂移显著为负** ⇒ 损失是真的，不是对齐假象。")
    elif abs(tc) < 1.96:
        print("  ⇒ 真实漂移**不显著** ⇒ 还不能断言损失来自漂移。")
    else:
        print("  ⇒ 真实漂移为正。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
