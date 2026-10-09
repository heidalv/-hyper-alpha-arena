# -*- coding: utf-8 -*-
"""[h861] 止损集中在哪(修复后 3 小时窗口):按币 / 按方向 / 按入场时的波动。"""
import importlib.util
import io
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg  # noqa: E402

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute(
        "SELECT symbol, count(*), AVG(net_bp), SUM(net_bp*notional)/10000.0"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND COALESCE(meta_json->>'exit_path','')='taker_stop'"
        " AND ts > now() - interval '3 hours' GROUP BY 1"
        " HAVING count(*) >= 3 ORDER BY 4 LIMIT 12")
    print("== 止损腿 按币(近 3h,n≥3)==")
    for r in cur.fetchall():
        print(f"  {str(r[0]):<9} n={r[1]:>3} 每腿 {float(r[2] or 0):+7.1f}bp "
              f"合计 {float(r[3] or 0):+8.2f}U")
    cur.execute(
        "SELECT symbol, count(*), SUM(net_bp*notional)/10000.0"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND COALESCE(meta_json->>'exit_path','')='flow_exit_maker'"
        " AND ts > now() - interval '3 hours' GROUP BY 1"
        " HAVING count(*) >= 3 ORDER BY 3 DESC LIMIT 8")
    print("== 挂单离场腿 按币(top)==")
    for r in cur.fetchall():
        print(f"  {str(r[0]):<9} n={r[1]:>3} 合计 {float(r[2] or 0):+8.2f}U")

# 往返日志:按币算"止损率"
rows = [json.loads(x) for x in (ROOT / "data" / "flow_roundtrip_log.jsonl")
        .read_text(encoding="utf-8").splitlines() if x.strip()]
rec = [r for r in rows if float(r.get("ts") or 0) > time.time() - 3 * 3600
       and r.get("y_bp") is not None]
by = {}
for r in rec:
    d = by.setdefault(str(r.get("symbol")), [0, 0, []])
    d[0] += 1
    if float(r.get("fee_bp") or 0) > 0:
        d[1] += 1
    d[2].append(float(r["y_bp"]))
print("== 近 3h 按币:往返数 / 止损率 / 组合期望 ==")
for sym, (n, nstop, ys) in sorted(by.items(), key=lambda x: -x[1][0])[:14]:
    if n < 4:
        continue
    print(f"  {sym:<9} n={n:>3} 止损率 {nstop/n*100:>3.0f}% "
          f"期望 {np.mean(ys):+7.2f}bp 合计 {sum(ys):+7.0f}bp")
