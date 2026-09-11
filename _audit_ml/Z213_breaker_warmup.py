# -*- coding: utf-8 -*-
"""Z213：熔断器持久化状态 `_breaker` 的**预热程度**（为什么只有 5 个通道可评估）。

`rebuild_breaker_shadow()` 要求 `len(recent) >= min(MIN_N, ROLLING_WINDOW)=15`；
若多数通道的 `recent` 很短，则熔断器**永远不会**覆盖 Z210 算出的那 15 条通道。
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

from backend.services import source_attribution as sa  # noqa: E402

path = Path(sa._STATE_PATH)
print("状态文件:", path, "| 存在:", path.exists())
if not path.exists():
    raise SystemExit(0)
d = json.loads(path.read_text(encoding="utf-8"))
br = d.get("breaker", {}) or {}
print(f"breaker 条目: {len(br)}")

lens = Counter()
no_recent = 0
for k, v in br.items():
    rec = v.get("recent") if isinstance(v, dict) else None
    if not isinstance(rec, list):
        no_recent += 1
        lens["无 recent 字段"] += 1
        continue
    n = len(rec)
    lens["≥15（可评估）" if n >= 15 else f"{n}"] += 1
print("recent 长度分布:", dict(lens))
print(f"完全没有 recent 字段的通道: {no_recent}")

print("\n=== 关键通道的 recent 长度（mid/long 出血通道）===")
for key in ("mid|trend_broken", "mid|master_running_close", "mid|midlong",
            "long|thesis_invalidation", "mid|thesis_should_close",
            "short|scalp_review_time_decay", "short|sl"):
    v = br.get(key)
    rec = (v or {}).get("recent") if isinstance(v, dict) else None
    wr = (sum(1 for x in rec if x) / len(rec)) if isinstance(rec, list) and rec else None
    print(f"  {key:32s} recent={len(rec) if isinstance(rec, list) else '—':>3} "
          f"胜率={f'{wr:.0%}' if wr is not None else '—':>5}")
print("\n⇒ 结论：熔断器只对『样本已预热』的通道生效；DB 历史里合格但窗口未预热的通道仍不会被拦。")
