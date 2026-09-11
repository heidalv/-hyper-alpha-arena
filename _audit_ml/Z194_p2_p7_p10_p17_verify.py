# -*- coding: utf-8 -*-
"""Z194（P2/P7/P10/P17 运行期核验）：四个改动在**运行进程**里的实际生效值。

注意：本脚本在独立进程里运行，读的是同一份 `.env`/settings/DB，因此能反映真实生效口径；
真正"进程内"的验证以重启后的 backend.log 为准（见 Z195）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env", override=False)

from backend.config import settings  # noqa: E402
from backend.services.risk_management import portfolio_budget as pbm  # noqa: E402
from backend.services.risk_management.portfolio_budget import portfolio_budget as pb  # noqa: E402

print("=== P2 再入场冷却键名 ===")
print("  REENTRY_COOLDOWN_SECONDS =", settings.REENTRY_COOLDOWN_SECONDS,
      "（期望 60；修复前 .env 写旧键被忽略、实际 600）")
print("  .env 旧键 REENTRY_COOLDOWN_SEC 仍在?", "REENTRY_COOLDOWN_SEC=" in (
    ROOT / ".env").read_text(encoding="utf-8-sig"))

print("\n=== P7 EV 闸 mid 车道强制 ===")
print("  MIDLONG_EV_ENFORCE_MID =", settings.MIDLONG_EV_ENFORCE_MID, "（期望 True）")
print("  MIDLONG_EV_GATE_ENABLED =", settings.MIDLONG_EV_GATE_ENABLED)
print("  MIDLONG_EV_ENFORCE_REQUIRES_CALIBRATION =", settings.MIDLONG_EV_ENFORCE_REQUIRES_CALIBRATION)

print("\n=== P10 审计备份保留期 ===")
print("  AUDIT_BACKUP_KEEP_DAYS =", settings.AUDIT_BACKUP_KEEP_DAYS, "（期望 180，0=永不删）")
print("  LOG_RETENTION_DAYS =", settings.LOG_RETENTION_DAYS)

print("\n=== P17 回撤判据自愈 ===")
print("  PB_DD_STALE_HOURS =", pbm._cfg_float("PB_DD_STALE_HOURS", 12.0), "（期望 12，0=关闭自愈）")
from backend.database.connection import SessionLocal  # noqa: E402

db = SessionLocal()
try:
    m = pb._strategy_drawdown_metric("midlong", db, 14)
    print("  当前判据:", {k: m[k] for k in ("ratio", "age_hours", "n_samples", "last_sample_ts")} if m else None)
    d = pb.evaluate_open(symbol="XRP", action="buy", notional_usd=800.0, equity=4717.0,
                         strategy="midlong", mode="paper", db=db, account_id=14, positions=None)
    print("  XRP buy ->", "允许" if d.allowed else "拦截", "|", d.reasons[:2],
          "| stale=", d.metrics.get("dd_sigma_stale"))
finally:
    db.close()
