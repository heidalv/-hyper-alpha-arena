# -*- coding: utf-8 -*-
"""[轮153 清理] 归档长线模板族残余策略（`tpl_long*`），只保留 mid 用途。

背景（用户指令）：
  「669 个 tpl_long* 策略里 9 个 active 仍在池子里，要不要归档清理（只留 mid 用途），
    避免继续被扫到」

  轮151 已在 `strategy_creation.try_create_from_template` 里禁止 long 层用模板族建策略
  （`MIDLONG_LONG_BLOCK_TEMPLATE_SOURCES`），但**历史遗留的 active/paused 行仍在池中**，
  扫描侧（`full_auto_trading_service` / `master_execution` / `midlong_helpers` 的
  `status == "active"` 过滤）仍会把它们捞出来 → 24h 内 `long_template_source_block` 127 次
  的无用拦截。本脚本把残余行落成 `archived`（终态），并把它从会话 active 列表摘掉。

安全性核查（本脚本运行前已逐条验证，见 reports/_轮153*.md）：
  ① 出场/持仓管理不看策略状态：`midlong_position_manager` 全文只把 `strategy_id` 当字符串
     透传写单，**没有任何 AIStrategy 查询**；`mlto_cycle._trend_one` 先管理 long 仓、
     后做入场分析，两者互不依赖。
  ② `archived` 是终态：全库 revive 路径（`paper_session_helpers` 163/210/226、
     `symbol_risk` 348）只复活 `paused/frozen/terminated`，**从不碰 archived**。
  ③ 归档后长线只剩 1 个 active 母本（`auto_cbc7a4d817` BTC long），
     `provision_ai_strategy` 仍能克隆建策略 → 长线不会断粮（脚本会打印实际数量并在 0 时告警）。

用法：
    .venv\\Scripts\\python.exe scripts\\_fix153_archive_tpl_long.py            # 干跑（默认）
    .venv\\Scripts\\python.exe scripts\\_fix153_archive_tpl_long.py --apply    # 落库
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from backend.database.connection import SessionLocal, engine  # noqa: E402
from backend.database.models import AIStrategy, FullAutoSession  # noqa: E402

PREFIX = "tpl_long"
ARCHIVE_REASON = (
    "轮153 清理：长线模板族退役（轮151 已禁止 long 层用模板族建策略；"
    "残留行仍被扫描侧捞起 → long_template_source_block 24h 127 次）。"
    "模板族只保留 mid 用途；长线策略改由 provision_ai_strategy/ai_decisions 按需克隆生成。"
)


def collect(db):
    rows = (
        db.query(AIStrategy)
        .filter(AIStrategy.strategy_id.like(f"{PREFIX}%"),
                AIStrategy.status.in_(["active", "paused"]))
        .order_by(AIStrategy.primary_symbol, AIStrategy.strategy_id)
        .all()
    )
    sids = [r.strategy_id for r in rows]
    refs = {"pos": {}, "ord": {}, "sess": {}}
    if sids:
        with engine.connect() as c:
            for sid, n, syms in c.execute(text(
                "select strategy_id, count(*), string_agg(distinct symbol, ',') "
                "from paper_positions where strategy_id = any(:s) "
                "and status = 'open' group by strategy_id"), {"s": sids}):
                refs["pos"][sid] = (int(n), syms)
            try:
                for sid, n in c.execute(text(
                    "select strategy_id, count(*) from paper_orders "
                    "where strategy_id = any(:s) and created_at > now() - interval '7 days' "
                    "group by strategy_id"), {"s": sids}):
                    refs["ord"][sid] = int(n)
            except Exception as exc:  # noqa: BLE001
                print(f"   [warn] paper_orders 查询失败: {type(exc).__name__}")
    return rows, refs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正落库（默认只干跑）")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows, refs = collect(db)
        print("=" * 96)
        print(f"[轮153] 待归档 tpl_long* (active/paused) = {len(rows)}")
        for r in rows:
            p = refs["pos"].get(r.strategy_id)
            o = refs["ord"].get(r.strategy_id, 0)
            print(f"   {r.strategy_id:<32} {str(r.primary_symbol):<8} tier={r.timeframe_tier:<6} "
                  f"status={str(r.status):<7} acct={r.account_id} "
                  f"持仓={('无' if not p else f'{p[0]} 条({p[1]})')} 近7天订单={o}")

        # 归档后仍存活的长线母本（provision_ai_strategy 依赖）
        donors = (
            db.query(AIStrategy)
            .filter(AIStrategy.status == "active",
                    AIStrategy.timeframe_tier == "long",
                    AIStrategy.primary_symbol.isnot(None),
                    ~AIStrategy.strategy_id.like(f"{PREFIX}%"))
            .all()
        )
        print(f"\n   归档后 long 层 active 母本 = {len(donors)}")
        for d in donors:
            print(f"     {d.strategy_id}  {d.primary_symbol}  auto_mode={d.auto_mode}")
        if not donors:
            print("   ⚠️ 母本为 0：长线按需建策略会退化为『无同层 active 母本可克隆』，"
                  "本脚本拒绝继续（请先补一个 long 母本）。")
            return 2

        # 会话引用
        sess_rows = db.query(FullAutoSession).all()
        sess_hits = []
        for s in sess_rows:
            ids = list(getattr(s, "active_strategy_ids", None) or [])
            hit = [x for x in ids if str(x).startswith(PREFIX)]
            if hit:
                sess_hits.append((s, hit))
                print(f"\n   会话 {s.session_id} active_strategy_ids 含 {len(hit)} 个待归档：{hit[:4]}…")

        if not args.apply:
            print("\n[干跑] 未改动任何数据。加 --apply 落库。")
            return 0

        now = datetime.now(timezone.utc)
        for r in rows:
            r.status = "archived"
            r.archived_at = now
            r.archive_reason = ARCHIVE_REASON[:500]
        for s, hit in sess_hits:
            s.active_strategy_ids = [
                x for x in list(s.active_strategy_ids or [])
                if not str(x).startswith(PREFIX)
            ]
        db.commit()
        print(f"\n[已落库] archived {len(rows)} 行；清理 {len(sess_hits)} 个会话的 active 列表。")

        left = (
            db.query(AIStrategy)
            .filter(AIStrategy.strategy_id.like(f"{PREFIX}%"),
                    AIStrategy.status.in_(["active", "paused"]))
            .count()
        )
        print(f"[校验] 剩余 active/paused 的 tpl_long* = {left}")
        return 0 if left == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
