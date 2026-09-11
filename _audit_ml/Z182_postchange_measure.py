# -*- coding: utf-8 -*-
"""Z182：P11/P12/P13/P7 上线后的**效果量化**（对照 §68.5 基线）。

窗口：切换点 ≈ 2026-09-10 14:31（本地），此处用 lookback 分档看漏斗与拦截结构变化。
输出：
  1. 漏斗（skip/opened/open_attempt + 阶段分布 + top 原因）——与 §68.5 基线对比；
  2. `midlong_portfolio_block:net_exposure` 频次（P12 应显著下降）；
  3. `chart_gate_veto` 频次与是否出现"立场建议陈旧"放行；
  4. 新增闸 `same_symbol_concurrency` 是否出现（P13）；
  5. EV 闸影子行里的 `calib=swing`（P7 接线是否生效）与"该拦/可放行"占比。
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

from backend.services.mlto.midlong_direction_audit import (  # noqa: E402
    audit_paths,
    _iter_rows,
    summarize_decision_funnel,
)

print("=== 1. 漏斗（旋转感知）===")
for h in (2, 12, 48):
    s = summarize_decision_funnel(lookback_hours=h)
    print(f"  {h:3d}h: skip={s.get('skips')} opened={s.get('opened')} "
          f"open_attempt={s.get('open_attempts')} by_stage={(s.get('by_stage_skip') or {})}")
    for row in (s.get("top_skip_reasons") or [])[:6]:
        print(f"        {row['count']:6d}  {row['reason']}")

print("\n=== 2. 关键拦截原因的时间分布（按小时，仅近 8h）===")
since = time.time() - 8 * 3600
per_hour: dict[str, Counter] = {}
interesting = ("midlong_portfolio_block", "chart_gate_veto", "same_symbol_concurrency",
               "location_gate_veto", "midlong_mtf_block")
advice_stale = 0
for r in _iter_rows(audit_paths()):
    ep = float(r.get("epoch") or 0)
    if ep < since:
        continue
    reason = str(r.get("reason") or "")
    hour = time.strftime("%H:%M", time.localtime(ep))[:2] + ":00"
    for key in interesting:
        if key in reason:
            per_hour.setdefault(hour, Counter())[key] += 1
    if "立场建议陈旧" in reason or "立场建议陈旧" in str(r.get("detail") or ""):
        advice_stale += 1
hours = sorted(per_hour)
if hours:
    print("  小时   " + "  ".join(f"{k:>22s}" for k in interesting))
    for hh in hours:
        print(f"  {hh}   " + "  ".join(f"{per_hour[hh].get(k, 0):22d}" for k in interesting))
else:
    print("  （近 8h 无相关行）")
print(f"  图审'立场建议陈旧'→放行 行数（近 8h）: {advice_stale}")

print("\n=== 3. 组合闸拦截明细（近 8h，看 after_pct 是否已回到 <100% 量级）===")
cnt = Counter()
samples: list[str] = []
for r in _iter_rows(audit_paths()):
    ep = float(r.get("epoch") or 0)
    if ep < since:
        continue
    reason = str(r.get("reason") or "")
    if "midlong_portfolio_block" not in reason:
        continue
    cnt[reason[:70]] += 1
    if len(samples) < 5:
        samples.append(reason[:180])
for k, v in cnt.most_common(8):
    print(f"  {v:6d}  {k}")
for s in samples:
    print("   例:", s)

print("\n=== 4. EV 闸（P7 接线后）日志统计 ===")
log = ROOT / "logs" / "backend.log"
hits = []
if log.exists():
    with log.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if "MidLongEvGate" in line:
                hits.append(line.strip())
recent = hits[-12:]
print(f"  日志中 MidLongEvGate 行数: {len(hits)}；最近 12 条:")
for line in recent:
    print("   ", line[:190])
swing_seen = sum(1 for line in hits if "calib=swing" in line)
print(f"  含 calib=swing 的行数: {swing_seen}")
