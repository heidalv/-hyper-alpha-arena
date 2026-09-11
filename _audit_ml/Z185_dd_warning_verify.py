# -*- coding: utf-8 -*-
"""Z185：直接调用组合预算闸，验证 §72 新增的"回撤熔断拒单"限流 WARNING 真的会发。

（不依赖市场时机：直接喂 worst symbol（XRP/BTC/ASTER）与非 worst symbol（SOL）各一次。）
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stdout)

from backend.database.connection import SessionLocal  # noqa: E402
from backend.services.risk_management.portfolio_budget import portfolio_budget as pb  # noqa: E402

db = SessionLocal()
try:
    for sym in ("XRP", "SOL"):
        d = pb.evaluate_open(
            symbol=sym, action="buy", notional_usd=800.0, equity=4717.0,
            strategy="midlong", mode="paper", db=db, account_id=14, positions=None,
        )
        verdict = "允许" if d.allowed else "拦截"
        print(f"[Z185] {sym}: {verdict} | reasons={d.reasons[:2]} | "
              f"dd_sigma={d.metrics.get('drawdown_sigma')} | "
              f"worst={(d.metrics.get('drawdown_worst') or [])[:3]}")
finally:
    db.close()
