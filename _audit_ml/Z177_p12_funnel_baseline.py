# -*- coding: utf-8 -*-
"""Z177（P12 口径切换后的观测基线）：漏斗口径 + 组合闸拦截原因分布。

用于下一轮对比「切换前 vs 切换后」的 `midlong_portfolio_block`（尤其 `net_exposure`）频次。
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

from backend.services.mlto.midlong_direction_audit import summarize_decision_funnel  # noqa: E402

for hours, label in ((6, "近 6 小时（P12 切换前后交界）"), (48, "近 48 小时（含切换前）")):
    s = summarize_decision_funnel(lookback_hours=hours)
    print(f"\n=== {label} ===")
    print("  keys:", sorted(s.keys()))
    for k in ("files", "paths", "rows", "top", "total_rows", "decisions", "outcomes"):
        if k in s:
            v = s[k]
            print(f"  {k}: {v if not isinstance(v, (dict, list)) else ''}")
    for k, v in s.items():
        if isinstance(v, dict) and v and all(isinstance(x, int) for x in v.values()):
            print(f"  [{k}]")
            for kk, vv in sorted(v.items(), key=lambda kv: -kv[1])[:12]:
                print(f"    {vv:6d}  {kk}")
        elif isinstance(v, list) and v and isinstance(v[0], (list, tuple)):
            print(f"  [{k}]")
            for row in v[:12]:
                print("   ", row)

print("\n=== 组合闸拦截原因（按 reason 归并）===")
try:
    from sqlalchemy import text as _t

    from backend.database.connection import SessionLocal

    db = SessionLocal()
    db.execute(_t("set app.is_admin='on'"))
    rows = db.execute(_t(
        "select column_name from information_schema.columns "
        "where table_name='position_exit_events' order by ordinal_position"
    )).fetchall()
    db.rollback()
    print("  position_exit_events 列:", [r[0] for r in rows])
except Exception as exc:  # noqa: BLE001
    print("  DB 查询失败:", type(exc).__name__, str(exc)[:140])
