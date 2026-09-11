# -*- coding: utf-8 -*-
"""Z196：收尾状态快照（账户/持仓/漏斗/闸门状态）。"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

from sqlalchemy import text as t  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402
from backend.services.mlto.midlong_direction_audit import (  # noqa: E402
    audit_paths,
    _iter_rows,
    summarize_decision_funnel,
)

db = SessionLocal()
db.execute(t("set app.is_admin='on'"))
try:
    open_n = db.execute(t("select count(*) from paper_positions where status='open'")).scalar()
    eq = db.execute(t("select total_equity from paper_balances where account_id=14")).scalar()
    print("开仓中:", open_n, "| 账户14权益:", eq)
    rows = db.execute(t(
        "select symbol, side, round((size*mark_price)::numeric, 2) as notional, timeframe_tier "
        "from paper_positions where status='open'"
    )).fetchall()
    for r in rows:
        print("   ", r[0], r[1], "$", r[2], "tier=", r[3])
    for hrs in (2, 12):
        f = summarize_decision_funnel(lookback_hours=hrs)
        print(f"漏斗 {hrs}h: skip={f.get('skips')} opened={f.get('opened')} "
              f"top={(f.get('top_skip_reasons') or [])[:3]}")
    # 近 30 分钟是否出现 EV 拦截 / stale 放行
    since = time.time() - 1800
    ev_block = ev_shadow = stale_adv = dd_reject = 0
    for r in _iter_rows(audit_paths()):
        if float(r.get("epoch") or 0) < since:
            continue
        reason = str(r.get("reason") or "")
        if "EVGate" in reason or "ev_gate" in reason:
            ev_block += 1
        if "drawdown" in reason and "σ>" in reason:
            dd_reject += 1
    print(f"近 30 分钟：EVGate 相关审计行 {ev_block}；回撤拒单行 {dd_reject}（P17 释放阈值 age≥12h）")
finally:
    db.rollback()
    db.close()
