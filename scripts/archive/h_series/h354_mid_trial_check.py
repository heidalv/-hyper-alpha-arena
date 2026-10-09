# -*- coding: utf-8 -*-
"""h354 试跑中段监控：时代起至今的分币种账本快照 + Welch 预演（非判定，仅观察）。"""
import math
import sys

sys.path.insert(0, ".")
from scripts.h354_p2_deploy import read_env_dsn  # noqa: E402

import psycopg  # noqa: E402


def _welch(a, b):
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return None
    m1, m2 = sum(a) / n1, sum(b) / n2
    v1 = sum((x - m1) ** 2 for x in a) / (n1 - 1)
    v2 = sum((x - m2) ** 2 for x in b) / (n2 - 1)
    se = math.sqrt(v1 / n1 + v2 / n2)
    if se <= 0:
        return None
    t = (m1 - m2) / se
    df = (v1 / n1 + v2 / n2) ** 2 / ((v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1))

    def _pdf(x):
        return math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) \
            / math.sqrt(math.pi * df) * (1 + x * x / df) ** (-(df + 1) / 2)

    step, s, x = 0.05, 0.0, abs(t)
    while x < abs(t) + 30.0:
        s += step * (_pdf(x) + _pdf(x + step)) / 2
        x += step
    return {"t": t, "p": min(1.0, 2 * s), "delta": m1 - m2,
            "mean_trial": m1, "mean_base": m2, "n_trial": n1, "n_base": n2}


with psycopg.connect(read_env_dsn()) as c:
    with c.cursor() as cur:
        cur.execute("SELECT meta_json->'h354_p2_trial'->>'started_at' "
                    "FROM lane_registry WHERE lane_id='mm_asterdex'")
        since = cur.fetchone()[0]
        print("trial since:", since)
        cur.execute("""
            SELECT symbol, count(*), sum(net_bp)::float8, sum(notional)::float8
            FROM lane_ledger WHERE lane_id='mm_asterdex' AND ts > %s::timestamptz
            GROUP BY symbol ORDER BY symbol
        """, (since,))
        tot_legs = 0
        tot_bp = 0.0
        for r in cur.fetchall():
            print(f"  {r[0]}: {r[1]} legs, net {r[2]:+.1f} bp, notional {r[3]:.0f}")
            tot_legs += r[1]
            tot_bp += float(r[2] or 0.0)
        hours = 12.0  # 预演假设 12h 窗（实际判决以当时为准）
        print(f"TOTAL: {tot_legs} legs, net {tot_bp:+.1f} bp, "
              f"per-leg {tot_bp / max(tot_legs, 1):+.3f} bp")
        # Welch 预演（试跑 vs 基线 12h）
        cur.execute("SELECT net_bp FROM lane_ledger WHERE lane_id='mm_asterdex' "
                    "AND ts > %s::timestamptz", (since,))
        trial = [float(r[0] or 0.0) for r in cur.fetchall()]
        cur.execute("SELECT %s::timestamptz - interval '12 hours'", (since,))
        base_cut = cur.fetchone()[0]
        cur.execute("SELECT net_bp FROM lane_ledger WHERE lane_id='mm_asterdex' "
                    "AND ts > %s::timestamptz AND ts <= %s::timestamptz",
                    (base_cut, since))
        base = [float(r[0] or 0.0) for r in cur.fetchall()]
        w = _welch(trial, base)
        if w:
            t_h = len(trial) / hours
            b_h = len(base) / 12.0
            print(f"\nWelch 预演：Δ={w['delta']:+.3f}bp t={w['t']:+.2f} p={w['p']:.3f} "
                  f"(trial {len(trial)} legs / base {len(base)})")
            print(f"频率：trial {t_h:.1f}/h vs base {b_h:.1f}/h "
                  f"（地板 0.8×base = {0.8 * b_h:.1f}/h）")
            if t_h < 0.8 * b_h:
                print("⇒ 若此刻判决：频率塌陷 ⇒ ROLLBACK")
            elif w["p"] <= 0.10 and w["delta"] > 0:
                print("⇒ 若此刻判决：PASS")
            elif w["p"] <= 0.10 and w["delta"] < 0:
                print("⇒ 若此刻判决：ROLLBACK")
            else:
                print("⇒ 若此刻判决：INCONCLUSIVE（延长 12h）")
        else:
            print("样本不足，无法预演")
