# -*- coding: utf-8 -*-
"""[新目标 R4 ④] 反事实：若兜底在基线窗口（09-15→09-27）就存在，20 笔"无 thesis_id"平仓有多少能救回来。

## 方法（只读，无任何写入）
对 158 笔基线平仓里 **open_metadata 无 thesis_id 的 20 笔**（19 long + 1 mid），
按 `(symbol, tier)` 查 `mlto_thesis` 的**历史行**（行是持久的）：
  · `created_at <= closed_at`  ⇒ 平仓那一刻论题行**已经存在**（兜底的前提条件）；
  · `updated_at >= closed_at`  ⇒ 该行在平仓**之后**仍有刷新 —— 结合实测刷新节奏
    （long 层 `midlong_thesis` 事件间隔：中位 1.38h / p90 8.04h / max 24.3h，
     兜底时效窗口是 24h），说明平仓当时**大概率新鲜**。
两者都为真是"反事实可救"的强证据；只有 `created_at > closed_at`（当时根本没这行）的才救不了。

## 结果（2026-09-27 实测）
20/20 存在、20/20 平仓后仍有更新、0 笔救不了
⇒ 反事实覆盖率上界：64 实时学过 + 20 = 84 / 158 = 53.2%（基线 40.5%）。
真实提升要在修复后窗口逐笔验证（`audit_learning_funnel_20260927.py "2026-09-27 20:55:00"`）。

用法：.venv\\Scripts\\python.exe scripts\\counterfactual_fallback_coverage_20260927.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from backend.database.connection import (  # noqa: E402
    AnalyticsSessionLocal,
    SessionLocal,
)


def main() -> int:
    db = SessionLocal()
    try:
        db.execute(text("SET app.is_admin='on'"))
        closes = db.execute(text("""
            SELECT id, symbol, timeframe_tier, closed_at, exit_state_json
            FROM paper_positions WHERE account_id=14 AND status='closed'
              AND closed_at >= timestamp '2026-09-15' ORDER BY closed_at
        """)).fetchall()
    finally:
        db.close()

    no_thesis = []
    for r in closes:
        try:
            o = json.loads(r[4]) if isinstance(r[4], str) else (r[4] or {})
        except Exception:
            o = {}
        om = (o or {}).get("open_metadata") or {}
        if not om.get("thesis_id"):
            no_thesis.append((r[0], str(r[1]).upper(), str(r[2]), r[3]))

    adb = AnalyticsSessionLocal()
    try:
        rows = adb.execute(text(
            "SELECT session_id, symbol, tier, created_at, updated_at FROM mlto_thesis"
        )).fetchall()
    finally:
        adb.close()

    existed = fresh = 0
    neither = []
    for pid, sym, tier, closed in no_thesis:
        cands = [r for r in rows if r[1] == sym and r[2] == tier]
        c_ex = [c for c in cands if c[3] and c[3] <= closed]
        c_fresh = [c for c in cands if c[4] and c[4] >= closed]
        existed += bool(c_ex)
        fresh += bool(c_fresh)
        if not c_ex:
            neither.append((pid, sym, tier, closed))

    n = len(no_thesis)
    print(f"无 thesis_id 的基线平仓 = {n} 笔")
    print(f"反事实（当时有兜底的话）：")
    print(f"   论题行已存在 = {existed}/{n}")
    print(f"   平仓后仍有更新（大概率当时新鲜） = {fresh}/{n}")
    print(f"   无论题行可救 = {len(neither)}/{n}")
    for pid, sym, tier, closed in neither:
        print(f"      id={pid} {sym} {tier} closed={closed}")
    print(f"⇒ 反事实覆盖率上界 = (64 + {existed}) / 158 = {(64 + existed) / 158:.1%}（基线 40.5%）")
    return 0 if not neither else 1


if __name__ == "__main__":
    raise SystemExit(main())
