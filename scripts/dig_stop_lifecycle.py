# -*- coding: utf-8 -*-
"""深挖①：止损仓生命周期重建——加仓次数/时间线/仓位膨胀。只读。"""
import sys
import json
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()

# 近 12h 止损腿
cur.execute("""
    SELECT ts, symbol, position_id, meta_json
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '12 hours'
      AND meta_json->>'exit_path' = 'stop_loss_taker'
    ORDER BY ts
""")
stops = cur.fetchall()
print(f"止损腿 {len(stops)} 笔；position_id 取值：",
      sorted({str(r[2]) for r in stops})[:6])

# 每个止损仓的完整 fill 链
print("\n== 止损仓生命周期（每仓：加仓笔数 / 首腿→止损历时 / 名义膨胀）==")
for ts0, sym, pid, m in stops:
    if not pid:
        continue
    cur.execute("""
        SELECT ts, meta_json->>'side', (meta_json->>'qty')::float8,
               (meta_json->>'fill_px')::float8, meta_json->>'exit_path'
        FROM lane_ledger
        WHERE event='fill' AND position_id=%s AND symbol=%s
        ORDER BY ts
    """, (pid, sym))
    legs = cur.fetchall()
    if len(legs) < 2:
        continue
    first_ts = legs[0][0]
    dur_min = (ts0 - first_ts).total_seconds() / 60.0
    qty_first = legs[0][2] or 0
    qty_max = max(l[2] or 0 for l in legs)
    notional_max = max((l[2] or 0) * (l[3] or 0) for l in legs)
    sides = {l[1] for l in legs}
    print(f"  {sym:<6} pid={pid[:12]:<12} 腿数={len(legs):>2} "
          f"历时={dur_min:>5.1f}min 首qty={qty_first:<8.3g} 峰qty={qty_max:<8.3g} "
          f"峰值名义={notional_max:>7.0f} 侧={sorted(sides)}")

print("\n== 全部止损仓统计 ==")
cur.execute("""
    WITH stops AS (
        SELECT position_id, symbol, ts
        FROM lane_ledger
        WHERE event='fill' AND ts >= now() - interval '12 hours'
          AND meta_json->>'exit_path' = 'stop_loss_taker'
          AND position_id IS NOT NULL
    )
    SELECT COUNT(*) AS fills,
           ROUND(AVG(cnt)::numeric, 1),
           MAX(cnt),
           ROUND(AVG(dur)::numeric, 1)
    FROM (
        SELECT s.symbol, s.position_id, COUNT(l.id) AS cnt,
               EXTRACT(EPOCH FROM (MAX(s.ts) - MIN(l.ts)))/60 AS dur
        FROM stops s
        JOIN lane_ledger l ON l.position_id = s.position_id AND l.symbol = s.symbol
        GROUP BY s.symbol, s.position_id
    ) x
""")
print("  ", cur.fetchone())
