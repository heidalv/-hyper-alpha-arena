# -*- coding: utf-8 -*-
"""H387 止损腿法医：9 笔 stop_loss_taker 的入场时点/持有时长/方向。

回答：止损腿是不是"15:30 动量窗口追进去的腿"？→ 决定 #16②冷却 与 ③降腿量 的设计参数。
"""
import sys
sys.path.insert(0, ".")
from scripts.h356_universe_trial import read_env_dsn  # noqa: E402
import psycopg  # noqa: E402

with psycopg.connect(read_env_dsn()) as c:
    with c.cursor() as cur:
        cur.execute("""
            WITH stops AS (
              SELECT position_id, symbol, ts AS exit_ts,
                     meta_json->>'side' AS exit_side, net_bp, notional
              FROM lane_ledger
              WHERE lane_id='mm_asterdex'
                AND meta_json->>'exit_path' LIKE 'stop_loss%'
                AND ts > '2026-09-27 16:00:00+08'::timestamptz
                AND ts <= '2026-09-27 17:00:00+08'::timestamptz
            )
            SELECT s.symbol, s.exit_ts, s.exit_side, s.net_bp, s.notional,
                   e.ts AS entry_ts, e.meta_json->>'side' AS entry_side,
                   ROUND(EXTRACT(EPOCH FROM (s.exit_ts - e.ts))::numeric, 0) AS hold_s
            FROM stops s
            LEFT JOIN LATERAL (
              SELECT ts, meta_json FROM lane_ledger
              WHERE lane_id='mm_asterdex' AND position_id = s.position_id
              ORDER BY ts LIMIT 1
            ) e ON true
            ORDER BY s.exit_ts
        """)
        rows = cur.fetchall()
        print(f"{'币':<6} {'平仓时':<8} {'方向':<5} {'bp':>8} {'名义':>7} {'入场时':<8} {'入场方向':<6} {'持仓s':>6}")
        for r in rows:
            print(f"{r[0]:<6} {str(r[1])[11:19]:<8} {r[2]:<5} {float(r[3] or 0):>+8.1f} "
                  f"{float(r[4] or 0):>7.0f} {str(r[5])[11:19] if r[5] else '?':<8} "
                  f"{str(r[6])[:5]:<6} {int(r[7] or 0):>6}s")
