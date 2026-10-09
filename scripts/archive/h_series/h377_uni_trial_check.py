# -*- coding: utf-8 -*-
"""H377 #2 宇宙试跑监控：分币种账本快照 + 部署健康检查（13:00 后使用）。

检查项：
  1. 10 币宇宙全部有成交（0 腿币 = 部署问题，立即人工排查）；
  2. 分币种 net_bp/腿（谁在拖后腿）；
  3. 总腿速 vs 60 腿/h 使命；
  4. Welch 预演（vs 部署前 12h 基线）。
"""
import math
import sys

sys.path.insert(0, ".")
from scripts.h356_universe_trial import read_env_dsn  # noqa: E402
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
    return {"t": t, "p": min(1.0, 2 * s), "delta": m1 - m2}


with psycopg.connect(read_env_dsn()) as c:
    with c.cursor() as cur:
        cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
        meta = cur.fetchone()[0]
        trial = meta.get("h356_trial") or {}
        since = trial.get("started_at")
        if not since:
            print("✗ h356 试跑未部署（h356_trial.started_at 缺失）")
            sys.exit(1)
        print(f"#2 since: {since}  symbols: {meta.get('symbols')}")
        import datetime as dt
        hours = max((dt.datetime.now(dt.timezone.utc)
                     - dt.datetime.fromisoformat(since)).total_seconds() / 3600.0, 0.01)
        cur.execute("""
            SELECT symbol, count(*), sum(net_bp)::float8, sum(notional)::float8
            FROM lane_ledger WHERE lane_id='mm_asterdex' AND ts > %s::timestamptz
            GROUP BY symbol ORDER BY symbol
        """, (since,))
        rows = cur.fetchall()
        tot_legs = 0
        tot_bp = 0.0
        zero = []
        for r in rows:
            print(f"  {r[0] or '?':<10} {r[1]:>5} legs  net {r[2]:+8.1f} bp  "
                  f"notional {r[3]:.0f}")
            tot_legs += r[1]
            tot_bp += float(r[2] or 0.0)
            if r[1] == 0:
                zero.append(r[0])
        uni = set(meta.get("symbols") or [])
        missing = uni - {r[0] for r in rows}
        print(f"TOTAL: {tot_legs} legs / {hours:.1f}h = {tot_legs/hours:.1f} legs/h, "
              f"net {tot_bp:+.1f} bp, per-leg {tot_bp/max(tot_legs,1):+.3f} bp")
        if missing:
            print(f"⚠️ 宇宙内无成交的币：{sorted(missing)}（部署问题？）")
        if zero:
            print(f"⚠️ 0 腿币：{zero}")
        if tot_legs / hours < 60:
            print(f"⚠️ 腿速 {tot_legs/hours:.1f}/h < 60 使命线")
        # Welch 预演 vs 基线
        cur.execute("SELECT net_bp FROM lane_ledger WHERE lane_id='mm_asterdex' "
                    "AND ts > %s::timestamptz", (since,))
        trial_legs = [float(r[0] or 0.0) for r in cur.fetchall()]
        cur.execute("SELECT %s::timestamptz - interval '12 hours'", (since,))
        base_cut = cur.fetchone()[0]
        cur.execute("SELECT net_bp FROM lane_ledger WHERE lane_id='mm_asterdex' "
                    "AND ts > %s::timestamptz AND ts <= %s::timestamptz",
                    (base_cut, since))
        base_legs = [float(r[0] or 0.0) for r in cur.fetchall()]
        w = _welch(trial_legs, base_legs)
        if w:
            print(f"Welch 预演: Δ={w['delta']:+.3f}bp t={w['t']:+.2f} p={w['p']:.3f} "
                  f"(trial {len(trial_legs)} / base {len(base_legs)})")
