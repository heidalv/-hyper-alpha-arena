# -*- coding: utf-8 -*-
"""研究②：多腿止损仓（XRP/BNB 11 腿）的加仓时间线——45s 停加仓为何没拦住。只读。"""
import sys
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()

# 找最近的 XRP/BNB 止损腿
for sym in ("XRP", "BNB"):
    cur.execute("""
        SELECT ts, meta_json->>'side', (meta_json->>'fill_px')::float8
        FROM lane_ledger
        WHERE event='fill' AND symbol=%s AND ts >= '2026-09-28T04:56:00+00'::timestamptz
          AND meta_json->>'exit_path' = 'stop_loss_taker'
        ORDER BY ts DESC LIMIT 1
    """, (sym,))
    row = cur.fetchone()
    if not row:
        continue
    ts0, side, fill_px = row
    pos_side = "sell" if side == "buy" else "buy"
    print(f"\n== {sym} 止损 @ {ts0:%H:%M:%S}（平 {side}）——反侧入场时间线 ==")
    cur.execute("""
        SELECT ts, (meta_json->>'qty')::float8, (meta_json->>'fill_px')::float8
        FROM lane_ledger
        WHERE event='fill' AND symbol=%s AND ts < %s AND ts > %s - interval '45 minutes'
          AND meta_json->>'side' = %s
        ORDER BY ts
    """, (sym, ts0, ts0, pos_side))
    legs = cur.fetchall()
    t_first = legs[0][0] if legs else ts0
    print(f"  {'时刻':<10}{'距首腿':>8}{'qty':>12}{'px':>14}")
    for t, q, px in legs:
        d = (t - t_first).total_seconds()
        print(f"  {t:%H:%M:%S}  {d:>6.0f}s  {q:>10.4f}  {px:>12.6f}")
    print(f"  共 {len(legs)} 腿，跨度 {(legs[-1][0]-t_first).total_seconds():.0f}s")
