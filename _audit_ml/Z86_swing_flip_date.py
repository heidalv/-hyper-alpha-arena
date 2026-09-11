# -*- coding: utf-8 -*-
"""Z86: swing 校准器何时跨过 min_samples=60 → EV 闸何时从影子转为硬拦（含 45 天窗口）。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from sqlalchemy import text  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402

swing_min = 60
trend_min = 25
lookback = 45

db = SessionLocal()
try:
    rows = db.execute(text(
        "select created_at, signal_value, trade_pnl from signal_trade_feedback "
        "where signal_type='swing_agent_score' order by created_at"
    )).fetchall()
    print(f"swing_agent_score 总样本 {len(rows)}（其中含 pnl {sum(1 for r in rows if r[2] is not None)}）")

    # 逐日推进：计算「以该日为今」时的窗口内样本数（近似：窗口=该日往前 45 天）
    by_day = {}
    for ts, val, pnl in rows:
        if pnl is None:
            continue
        d = ts.date()
        by_day[d] = by_day.get(d, 0) + 1
    days = sorted(by_day)
    print("\n窗口内有效样本数随时间（跨阈值即 EV 闸由影子转硬拦）:")
    cum = {}
    prev_state = False
    for d in days:
        lo = d - timedelta(days=lookback)
        n = sum(v for k, v in by_day.items() if lo <= k <= d)
        state = n >= swing_min
        if state != prev_state:
            print(f"  *** {d}: 窗口内样本 {n} ≥ {swing_min} → 校准器转 calibrated，EV 闸开始硬拦")
            prev_state = state
        cum[d] = n
    tail = sorted(cum.items())[-12:]
    for d, n in tail:
        print(f"   {d}: 窗口内样本 {n}  {'(calibrated)' if n >= swing_min else '(cold_linear→影子)'}")

    print("\n=== 今日 swing 样本（含 pnl）===")
    today = datetime.now(timezone.utc).date()
    lo = today - timedelta(days=lookback)
    n_today = sum(v for k, v in by_day.items() if lo <= k <= today)
    print(f"  以 {today} 为今：窗口({lo}~{today})内样本 = {n_today}")
finally:
    db.close()
