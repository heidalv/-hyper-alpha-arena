# -*- coding: utf-8 -*-
"""方向份额的**前后对照**（只读）：按时间桶统计 short 占比，标出两次修复边界。

边界：
  T1 = 2026-09-18 13:16:44  A(解冻)+E 生效（第二次重启）
  T2 = 2026-09-18 13:46:14  B25 方向修复（提示词硬语义 + 不可执行方向不投票）生效
  T3 = 2026-09-18 14:0x     _short_gate_hint（regime 不可判也注入）生效
"""
from __future__ import annotations

import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402
from backend.database.connection import AnalyticsSessionLocal  # noqa: E402

T2 = "2026-09-18 13:46:14"   # 方向修复生效

db = AnalyticsSessionLocal()
try:
    db.execute(text("SET app.is_admin='on'"))
    rows = db.execute(text("""
        SELECT created_at, symbol, decision_snapshot FROM ai_decision_logs
        WHERE created_at >= now() - interval '3 hours'
          AND decision_snapshot LIKE '%_brain_decision_log%'
        ORDER BY id
    """)).fetchall()
    print(f"近 3h 的 _brain_decision_log 快照 = {len(rows)} 条")

    buckets = defaultdict(Counter)
    for ts, sym, snap in rows:
        try:
            d = json.loads(snap)
        except Exception:  # noqa: BLE001
            continue
        if not d.get("_brain_decision_log"):
            continue
        key = str(ts)[:15]           # yyyy-mm-dd HH:MM
        key = key[:14] + str((int(key[14:15]) // 2) * 2)  # 20 分钟桶
        buckets[key][str(d.get("direction") or "?")] += 1

    print(f"\n{'桶(20min)':18s} {'总计':>5s} {'short':>6s} {'long':>6s} {'neutral':>8s} {'short占比':>9s}  阶段")
    print("-" * 86)
    for k in sorted(buckets):
        c = buckets[k]
        tot = sum(c.values())
        sh = c.get("short", 0)
        stage = "修复后" if k >= T2[:14] + T2[14:15] else "修复前"
        # 简化：以 13:46 为界
        stage = "修复后(B25)" if f"{k[:14]}{k[14:15]}" >= "2026-09-18 13:4" else "修复前"
        print(f"{k:18s} {tot:5d} {sh:6d} {c.get('long',0):6d} {c.get('neutral',0):8d} "
              f"{(sh/tot if tot else 0):8.1%}  {stage}")

    pre = Counter()
    post = Counter()
    for k, c in buckets.items():
        if k >= "2026-09-18 13:4":
            post.update(c)
        else:
            pre.update(c)
    for label, c in (("修复前（13:46 之前）", pre), ("修复后（13:46 起）", post)):
        tot = sum(c.values())
        print(f"\n{label}: 共 {tot} 条  {dict(c)}"
              + (f"  short 占比 = {c.get('short',0)/tot:.1%}" if tot else ""))
    if sum(post.values()) < 20:
        print("\n⚠️ 修复后样本 < 20 条 ⇒ **统计上不足以判定**（不据此宣称成功/失败）")
finally:
    db.close()
