# -*- coding: utf-8 -*-
"""Z188：保留期类 env 键在 settings 里是否声明（决定 `.env` 能否生效）。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
src = (ROOT / "backend/config/settings.py").read_text(encoding="utf-8-sig", errors="replace")
KEYS = [
    "LOG_RETENTION_DAYS", "REPORT_RETENTION_DAYS", "AUDIT_JSONL_MAX_BYTES",
    "AI_DECISION_LOG_RETENTION_DAYS", "AUDIT_BACKUP_KEEP_DAYS",
    "BACKEND_CONSOLE_LOG_MAX_BYTES", "MIDLONG_MAX_SAME_SYMBOL_POSITIONS",
    "MIDLONG_EV_ENFORCE_MID", "PB_DD_STALE_HOURS", "MIDLONG_CHART_ADVICE_TTL_MIN",
]
print(f"{'键':38s} settings 声明?  声明形态")
for k in KEYS:
    m = re.search(rf"^{k}\b[^\n]*", src, re.M)
    ok = bool(m)
    print(f"  {k:36s} {'✅' if ok else '❌ 未声明（.env 改了也不生效）':16s} {m.group(0)[:70] if m else ''}")
