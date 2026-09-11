# -*- coding: utf-8 -*-
"""Z212：软退出免疫（`midlong_soft_exit_immune`）到底拦了多少、拦了谁。

发现（§78）：`UnifiedExitExecutor.should_block()` 在 `action == 'close'` 时**无条件**把
`master_running_close` / `master_running` 追加进候选列表，而它们在
`MID_TIER_PROTECTED_FROM` 里 ⇒ 只要 RISK_USE_MID_TIER_IMMUNE=true，
**任何软退出（含 thesis_*、midlong、profit_drawdown_* 等）都会被判为"Master 软退出"并拦下**。

本脚本量化：这些被拦事件的数量/通道/时间分布，以及它们最终是否还是被执行（对比平仓记录）。
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

from sqlalchemy import text as t  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402

db = SessionLocal()
db.execute(t("set app.is_admin='on'"))
try:
    print("=== 1. midlong_soft_exit_immune 事件（近 14 天）===")
    rows = db.execute(t("""
        select date_trunc('day', created_at)::date d, count(*) n
        from position_exit_events where event_type='midlong_soft_exit_immune'
          and created_at > now() - interval '14 days'
        group by 1 order by 1 desc
    """)).fetchall()
    tot = sum(r[1] for r in rows)
    print(f"  合计 {tot} 条；按天:", {str(r[0]): r[1] for r in rows})
    if tot == 0:
        print("  （近 14 天没有该事件 —— 说明该路径要么未被触发，要么事件未落库）")

    print("\n=== 2. 该事件被拦的通道（从 exit_channel 字段，若写入）===")
    rows = db.execute(t("""
        select coalesce(exit_channel,'(空)') ch, tier, count(*) n
        from position_exit_events where event_type='midlong_soft_exit_immune'
        group by 1,2 order by 3 desc limit 15
    """)).fetchall() if True else []
    try:
        cols = [r[0] for r in db.execute(t(
            "select column_name from information_schema.columns where table_name='position_exit_events'"
        )).fetchall()]
        if "exit_channel" in cols and "tier" in cols:
            for r in rows:
                print(f"  {str(r[0])[:40]:42s} tier={r[1]} n={r[2]}")
        else:
            print("  （表结构缺 exit_channel/tier 字段？）")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print("  查询失败:", str(exc)[:120])

    print("\n=== 3. 同期『软退出通道』的实际平仓数（对比：被拦 vs 真执行）===")
    for ch in ("master_running_close", "thesis_should_close", "thesis_invalidation",
               "midlong", "profit_drawdown_full", "trend_broken"):
        n = db.execute(t(f"""
            select count(*) from paper_positions p
            where p.status='closed' and p.closed_at > now() - interval '7 days'
              and coalesce(p.close_reason,'') ilike '{ch}%'
        """)).scalar()
        print(f"  近 7 天 {ch:24s} 实际平仓 {n} 笔")

    print("\n=== 4. 该事件最近 3 条样本（detail 文本）===")
    try:
        rows = db.execute(t("""
            select created_at, coalesce(metadata_json::text,'')::text
            from position_exit_events where event_type='midlong_soft_exit_immune'
            order by created_at desc limit 3
        """)).fetchall()
        for r in rows:
            print(f"  {str(r[0])[:19]} | {r[1][:200]}")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print("  查询失败:", str(exc)[:140])
finally:
    db.rollback()
    db.close()
