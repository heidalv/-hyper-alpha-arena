# -*- coding: utf-8 -*-
"""[新目标 R4 ④] 生产只读验证：长线车道开仓元数据 → 兜底解析，且**全程零写入**。

验证什么：
  1. 对账户 14 当前所有**无 thesis_id 的持仓/近期平仓**，用真实 `exit_state_json.open_metadata`
     跑 `_resolve_fallback_thesis`，打印解析结果（thesis / via / 归因 session / tier）；
  2. 断言解析过程**不产生任何写入**：对比调用前后 `mlto_thesis_events` 行数
     （`find_latest`/`get` 只读；只有模块内 DTO 缓存会变，那是进程内存，不是库）。

用法：.venv\\Scripts\\python.exe scripts\\verify_thesis_fallback_prod_20260927.py
退出码 0 = 全部解析成功且零写入；非 0 = 有解析失败或发现写入。
"""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from backend.database.connection import (  # noqa: E402
    AnalyticsSessionLocal,
    SessionLocal,
)
import backend.services.unified_learning_service as U  # noqa: E402


def event_count() -> int:
    adb = AnalyticsSessionLocal()
    try:
        return int(adb.execute(text("SELECT count(*) FROM mlto_thesis_events")).scalar() or 0)
    finally:
        adb.close()


def main() -> int:
    db = SessionLocal()
    try:
        db.execute(text("SET app.is_admin='on'"))
        rows = db.execute(text("""
            SELECT id, symbol, timeframe_tier, status, exit_state_json, opened_at
            FROM paper_positions WHERE account_id=14
              AND (status='open' OR (status='closed' AND closed_at >= timestamp '2026-09-24'))
            ORDER BY status, id
        """)).fetchall()
    finally:
        db.close()

    before = event_count()
    resolved = 0
    missing = 0
    for pid, sym, tier, st, es, opened in rows:
        try:
            obj = json.loads(es) if isinstance(es, str) else (es or {})
        except Exception:
            obj = {}
        obj = obj if isinstance(obj, dict) else {}
        om = obj.get("open_metadata") or {}
        if om.get("thesis_id"):
            continue  # 本来就有 → 不走兜底
        outcome = SimpleNamespace(symbol=sym, tier=str(tier))
        tid, via, sid, t = U._resolve_fallback_thesis(om, outcome)
        if tid:
            resolved += 1
        else:
            missing += 1
        print(f"  id={pid} {sym:<9} tier={str(tier):<5} {st:<6} opened={str(opened)[:16]} "
              f"→ thesis={tid[:8] or 'None':<10} via={via or '-':<14} session={sid or '-':<16} tier_used={t}")

    after = event_count()
    print(f"\n解析成功 {resolved} / 失败 {missing}；事件表行数 {before} → {after}"
          f"（{'零写入 ✓' if before == after else '⚠️ 出现写入！'}）")
    if missing:
        print("[FAIL] 存在解析失败的仓位 —— 它们平仓时仍会 skip=no_thesis")
        return 1
    if before != after:
        print("[FAIL] 解析过程出现库写入（违反只读契约）")
        return 2
    print("[OK] 全部可解析且零写入：这些仓位未来平仓时将进入 MLTO 论题学习。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
