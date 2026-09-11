# -*- coding: utf-8 -*-
"""Z199：同向再入场冷却的**实际生效值**（基座 vs 分层覆盖）。

结论用于两件事：
  1. 说明 P2（`.env` 键名修正 → 60s）到底影响了谁；
  2. 记录"tier 覆盖会盖掉基座"这一层，避免再次把基座值当成全链路生效值。
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

from backend.config.settings import REENTRY_COOLDOWN_SECONDS, TIER_PROTECTION_PARAMS  # noqa: E402
from backend.services.reentry_cooldown import _get_cooldown_sec  # noqa: E402

print(f"基座 REENTRY_COOLDOWN_SECONDS = {REENTRY_COOLDOWN_SECONDS}s （P2 修正后受 .env 控制）")
print(f"{'tier':8s} {'TIER_PROTECTION_PARAMS.cooldown_sec':>36s} {'_get_cooldown_sec()':>20s}")
for t in ("short", "mid", "long"):
    cfg = TIER_PROTECTION_PARAMS.get(t, {}) or {}
    cds = cfg.get("cooldown_sec")
    eff = _get_cooldown_sec(t)
    print(f"{t:8s} {(str(cds) + 's = ' + str(int(cds) // 60) + 'min') if cds is not None else '?':>36s} {str(eff):>20s}")
print("\nenv 覆盖键（若设置则优先）:")
for k in ("TIER_SHORT_COOLDOWN_SEC", "TIER_MID_COOLDOWN_SEC", "TIER_LONG_COOLDOWN_SEC"):
    print(f"  {k} = {os.getenv(k, '(未设 → 用代码默认)')}")
print("\n⇒ 口径：`REENTRY_COOLDOWN_SECONDS` 只在**没有 tier 覆盖**时生效；")
print("   mid/long 实际用 TIER_PROTECTION_PARAMS[tier].cooldown_sec（long 目前 240min）。")
