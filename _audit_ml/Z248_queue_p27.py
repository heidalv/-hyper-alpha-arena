# -*- coding: utf-8 -*-
"""[§84] 决策队列追加 P27（已执行 A）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
lines = P.read_text(encoding="utf-8").splitlines()
if any("| **P27** |" in l for l in lines):
    print("已存在 P27，跳过")
    raise SystemExit(0)
ROW = ("| **P27** | ~~熔断证据自锁（用过期证据抑制）~~ ✅ **已执行（选 A）2026-09-11**（§84.6）："
       "`last_ts` + `BREAKER_EVIDENCE_STALE_DAYS=7`（`0`=关闭），过期 ⇒ **只记录不抑制** + WARNING | "
       "现场实测 `mid…trend_broken`(13.8 天)/`mid…midlong`(7.7 天) 已不再抑制；"
       "`short…scalp_review_time_decay`(6.0 天) 照常抑制；变异 M13/M14 变红 | #69 |")
idx = next((i for i, l in enumerate(lines) if l.startswith("| **P26** |")), None)
assert idx is not None, "未找到 P26 行"
lines.insert(idx + 1, ROW)
P.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("已追加 P27 行")
