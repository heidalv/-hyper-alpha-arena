# -*- coding: utf-8 -*-
"""轮次状态速查（账户 14 口径）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
sys.stdout.reconfigure(encoding="utf-8")
from backend.database.connection import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    db.execute(text("set app.is_admin='on'"))
    checks = [
        ("10:19后 mid/long 平仓(14)", """select count(*) from paper_positions
            where account_id=14 and status='closed' and closed_at >= timestamp '2026-09-11 10:19'
              and lower(coalesce(timeframe_tier,'')) in ('mid','long')"""),
        ("17:20后新开 long(14)", """select count(*) from paper_positions
            where account_id=14 and opened_at >= timestamp '2026-09-11 17:20'
              and lower(coalesce(timeframe_tier,''))='long'"""),
        ("抑制事件", "select count(*) from position_exit_events where event_type='exit_channel_broken'"),
        ("开放仓位(14)", "select count(*) from paper_positions where account_id=14 and status='open'"),
    ]
    for label, sql in checks:
        print(label, "=", db.execute(text(sql)).scalar())
finally:
    db.close()
