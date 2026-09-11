# -*- coding: utf-8 -*-
"""Z211：软退出免疫（`is_close_reason_blocked_for_midlong`）覆盖了哪些通道？

用于修正 §77.4 的口径：若 `master_running_close` 在统一出口**本来就已被软退出免疫拦住**，
则 P19-B 的**增量覆盖**只体现在"免疫名单之外"的通道（如 thesis_*、midlong 等）。
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

from backend.config import settings as S  # noqa: E402
from backend.services.risk_band_resolver import is_close_reason_blocked_for_midlong as blocked  # noqa: E402

print("RISK_USE_MID_TIER_IMMUNE  =", getattr(S, "RISK_USE_MID_TIER_IMMUNE", "<无>"))
print("RISK_USE_LONG_TIER_IMMUNE =", getattr(S, "RISK_USE_LONG_TIER_IMMUNE", "<无>"))
print("MID_TIER_PROTECTED_FROM   =", sorted(getattr(S, "MID_TIER_PROTECTED_FROM", []) or []))
print("LONG_TIER_PROTECTED_FROM  =", sorted(getattr(S, "LONG_TIER_PROTECTED_FROM", []) or []))
print()
CH = ("master_running_close", "trend_broken", "thesis_should_close", "thesis_invalidation",
      "midlong", "profit_drawdown_full", "trend_review_close", "exit_policy",
      "master_running_reduce", "scalp_review_time_decay", "ai_reverse")
print(f"{'通道':26s} {'mid 免疫':>9s} {'long 免疫':>10s}")
for ch in CH:
    m = blocked(ch, "mid")
    lg = blocked(ch, "long")
    print(f"{ch:26s} {str(m):>9s} {str(lg):>10s}")
