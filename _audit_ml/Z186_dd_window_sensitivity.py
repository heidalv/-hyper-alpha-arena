# -*- coding: utf-8 -*-
"""Z186：回撤判据对**窗口长度**的敏感度（给 P17 的选项 C 提供数字）。

同一套口径，只改 `PB_DD_LOOKBACK_DAYS` 的有效窗口，看 midlong 的 dd/σ 如何变化：
窗口越短，越接近"近期状态"；越长越稳但越难自愈。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

import datetime as _dt  # noqa: E402

from backend.database.models import PaperPosition  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402
from backend.services.risk_management import portfolio_budget as pbm  # noqa: E402

print(f"{'窗口(天)':>8} {'笔数':>6} {'σ($)':>8} {'峰值($)':>10} {'末值($)':>10} {'回撤($)':>10} {'dd/σ':>8} {'>10σ?':>7}")
db = SessionLocal()
try:
    for days in (3, 7, 14, 21, 30, 45):
        cutoff = _dt.datetime.now() - _dt.timedelta(days=days)
        rows = (
            db.query(PaperPosition)
            .filter(PaperPosition.account_id == 14,
                    PaperPosition.status == "closed",
                    PaperPosition.closed_at >= cutoff)
            .order_by(PaperPosition.closed_at.asc())
            .all()
        )
        pnls = []
        for r in rows:
            if not pbm._is_strategy_pos(
                {"trade_nature": r.trade_nature, "timeframe_tier": r.timeframe_tier}, "midlong"
            ):
                continue
            d = 1 if str(r.side or "").lower() in ("long", "buy") else -1
            if r.close_price is not None and r.entry_price:
                pnl = (float(r.close_price) - float(r.entry_price)) * float(r.size or 0) * d
            else:
                pnl = float(r.unrealized_pnl or 0)
            pnl += float(r.partial_realized_pnl or 0)
            if np.isfinite(pnl):
                pnls.append(pnl)
        if len(pnls) < 3:
            print(f"{days:>8} {len(pnls):>6}  （样本不足）")
            continue
        arr = np.asarray(pnls, dtype=float)
        sigma = float(arr.std())
        curve = np.cumsum(arr)
        peak = float(np.maximum.accumulate(curve)[-1])
        dd = float(peak - curve[-1])
        ratio = dd / sigma if sigma > 0 else float("nan")
        print(f"{days:>8} {len(pnls):>6} {sigma:>8.2f} {peak:>10.2f} {curve[-1]:>10.2f} "
              f"{dd:>10.2f} {ratio:>8.2f} {'是' if ratio > 10 else '否':>7}")
finally:
    db.close()
