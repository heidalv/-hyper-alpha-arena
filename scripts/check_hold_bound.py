# -*- coding: utf-8 -*-
"""硬约束体检：近 2h 持仓周期时长分布（>300s 即违反 30s–5min 约束）。只读。"""
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
cur.execute("""
    SELECT ts, symbol, meta_json->>'side', (meta_json->>'qty')::float8,
           COALESCE(NULLIF(meta_json->>'exit_path',''),'')
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '3 hours'
    ORDER BY ts
""")
rows = cur.fetchall()
by_sym = {}
for ts, sym, side, qty, ep in rows:
    by_sym.setdefault(sym, []).append((ts, side, qty or 0.0, ep))

durs = []
open_long = []
for sym, fills in by_sym.items():
    inv = 0.0
    start = None
    for ts, side, qty, ep in fills:
        d = qty if side == "buy" else -qty
        prev = inv
        inv += d
        if abs(prev) < 1e-12 and abs(inv) > 1e-12:
            start = ts
        if start is not None and abs(inv) < 1e-12:
            durs.append(((ts - start).total_seconds(), sym, ep))
            start = None
    if start is not None:
        open_long.append((sym, inv, (rows[-1][0] - start).total_seconds()))

durs.sort()
if durs:
    n = len(durs)
    print(f"近 3h 已平周期 {n} 个；时长分位：")
    for q, lab in ((0.5, "中位"), (0.9, "p90"), (1.0, "最大")):
        d = durs[min(n - 1, int(n * q))][0]
        print(f"  {lab:<5} {d:>7.0f}s")
    over = [d for d in durs if d[0] > 300]
    print(f"  超过 300s 的周期：{len(over)}/{n} = {len(over)/n*100:.1f}%"
          + (f"（最长 {over[-1][0]:.0f}s {over[-1][1]} {over[-1][2]}）" if over else ""))
print(f"\n当前未平仓：{len(open_long)} 个")
for sym, inv, age in open_long:
    print(f"  {sym:<6} qty={inv:>12.4f} 已持 {age:>6.0f}s"
          + ("  ⚠ 超 300s" if age > 300 else ""))
