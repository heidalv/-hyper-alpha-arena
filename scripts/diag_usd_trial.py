# -*- coding: utf-8 -*-
"""USD 真口径 + #2 试跑现况（预判 01:00 判决）+ 止损触发-成交价差。只读。"""
import sys
import json
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()

print("== 真实 USD 逐 2h（Σ net_bp×notional/1e4）==")
cur.execute("""
    SELECT date_trunc('hour', ts) + (EXTRACT(MINUTE FROM ts)::int / 120) * interval '2 hour' AS bucket,
           COUNT(*), ROUND(SUM(net_bp * notional / 1e4)::numeric, 2),
           ROUND(AVG(notional)::numeric, 0)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '10 hours'
    GROUP BY 1 ORDER BY 1
""")
tot = 0.0
for b, n, s, avg_not in cur.fetchall():
    tot += float(s)
    print(f"  {b:%H:%M}  n={n:>4}  Σusd={s:>+8}  mean_notional={avg_not}")
print(f"  10h 累计 ≈ {tot:+.2f} USD")

print("\n== #2 试跑（13:00 起 7 新币 vs 基线 3 币）==")
cur.execute("""
    SELECT CASE WHEN symbol IN ('BNB','ETH','BTC') THEN 'baseline' ELSE 'trial' END AS grp,
           COUNT(*), SUM(net_bp)::int, ROUND(AVG(net_bp)::numeric,2)
    FROM lane_ledger
    WHERE event='fill' AND ts >= '2026-09-27 05:00:00+00'
    GROUP BY 1
""")
for r in cur.fetchall():
    print(f"  {r[0]:<10} n={r[1]:>5}  Σnet={r[2]:>+8}bp  mean={r[3]:>+7}bp")

print("\n== 止损腿：触发时刻 mid 到成交价差（meta 里的字段）==")
cur.execute("""
    SELECT symbol, meta_json->>'side', (meta_json->>'fill_px')::float8,
           (meta_json->>'mid_px')::float8, ROUND(net_bp::numeric,1)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '4 hours'
      AND meta_json->>'exit_path' = 'stop_loss_taker'
    ORDER BY ts DESC LIMIT 6
""")
for sym, sd, fp, mp, nb in cur.fetchall():
    gap = (fp - mp) / mp * 1e4 * (1 if sd == "sell" else -1)
    print(f"  {sym:<6} {sd:<5} fill={fp} mid={mp} 偏离={gap:+.1f}bp net={nb}")

print("\n== 当前持仓年龄（未平腿近 30min 的 ts 龄）==")
cur.execute("""
    SELECT COUNT(*), MAX(EXTRACT(EPOCH FROM (now()-ts)))::int
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '30 minutes'
""")
print("  ", cur.fetchone())
