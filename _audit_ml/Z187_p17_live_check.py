# -*- coding: utf-8 -*-
"""Z187（P17-C① 运行期核验）：回撤判据的样本年龄与当前裁决。

判定：`age_hours`（距最后一次已平仓 midlong 样本）与 `PB_DD_STALE_HOURS`（默认 12h）比较：
  * age < 12h  ⇒ 维持拒单（现状）
  * age ≥ 12h  ⇒ 只告警不拒单（自愈闸生效，打破"禁止开仓→无新样本"的死锁）
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

print("PB_DD_STALE_HOURS(env) =", os.getenv("PB_DD_STALE_HOURS", "(未设 → 12)"))
db = SessionLocal()
try:
    m = pb._strategy_drawdown_metric("midlong", db, 14)
    print("回撤判据输入:", m)
    for sym in ("XRP", "SOL"):
        d = pb.evaluate_open(
            symbol=sym, action="buy", notional_usd=800.0, equity=4717.0,
            strategy="midlong", mode="paper", db=db, account_id=14, positions=None,
        )
        print(f"[Z187] {sym}: {'允许' if d.allowed else '拦截'} | {d.reasons[:2]} | "
              f"stale={d.metrics.get('dd_sigma_stale')}")
finally:
    db.close()
