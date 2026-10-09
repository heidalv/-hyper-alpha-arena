# -*- coding: utf-8 -*-
"""[轮160 清理] 归档 short tier（已永久关闭车道）的历史遗留 active 策略。

依据（全部实测，见 reports/_probe198/199）：
  · 车道状态：`SCALP_OPEN_DISABLED=true`（2026-09-05 起，lane_registry 注明"永久关闭"）
    + `SCALP_MASTER_HARD_BLOCK=true` + scalp 循环不注册；`_轮63` 已把短线并入日内车道。
  · 近 7 天 tier=short / nature=scalp 的持仓 = **0 笔**（全 87 笔都是 mid/long）。
  · 扫描侧只有 mid/long（`midlong_loop` 的 fixed/ai mid+long；`TIER_MID/LONG_ENABLED`），
    **没有任何消费者读 short tier 策略**。
  · 但 10 条 short active 策略仍被非交易组件反复处理：
    `strategy_lifecycle`（每 tick「策略自适应」）+ `strategy_learning_service`（扫描）
    ⇒ 今天（09-21）光 tpl_short 相关日志就有 2719 行。
  · 引用检查：0 持仓、0 订单、仅 1 个会话的 active_strategy_ids 列着它们。

用法：
    python scripts\\_fix160_archive_short_lane.py            # 干跑
    python scripts\\_fix160_archive_short_lane.py --apply    # 落库
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text  # noqa: E402

from backend.database.connection import SessionLocal, engine  # noqa: E402
from backend.database.models import AIStrategy, FullAutoSession  # noqa: E402

REASON = ("轮160 清理：short tier 属**已永久关闭**的短线车道（SCALP_OPEN_DISABLED=true，"
          "2026-09-05 起；轮63 已并入日内车道）。近 7 天 0 笔 short/scalp 持仓，扫描侧只有 "
          "mid/long，无任何消费者；但这些 active 策略仍被 strategy_lifecycle/learning 每 tick "
          "处理（09-21 日志 2719 行）。归档以停止无效扫描。")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = (db.query(AIStrategy)
                .filter(AIStrategy.timeframe_tier == "short",
                        AIStrategy.status.in_(["active", "paused", "frozen"]))
                .order_by(AIStrategy.strategy_id).all())
        sids = [r.strategy_id for r in rows]
        print("=" * 100)
        print(f"待归档 short tier（active/paused/frozen）= {len(rows)}")
        with engine.connect() as c:
            for r in rows:
                npos = c.execute(text(
                    "select count(*) from paper_positions where strategy_id = :s"),
                    {"s": r.strategy_id}).scalar()
                npos_open = c.execute(text(
                    "select count(*) from paper_positions where strategy_id = :s and status='open'"),
                    {"s": r.strategy_id}).scalar()
                nord = c.execute(text(
                    "select count(*) from paper_orders where strategy_id = :s"),
                    {"s": r.strategy_id}).scalar()
                print(f"   {r.strategy_id:<26} {str(r.primary_symbol):<8} status={str(r.status):<8} "
                      f"持仓={npos}（open={npos_open}） 订单={nord} last_trade={r.last_trade_at}")
        if sids:
            with engine.connect() as c:
                n_open = c.execute(text(
                    "select count(*) from paper_positions where strategy_id = any(:s) and status='open'"),
                    {"s": sids}).scalar()
            print(f"\n   ⚠️ open 持仓合计 = {n_open}")
            if n_open:
                print("   ✗ 有在途持仓，拒绝归档（先平仓/换绑）")
                return 2

        sess_hits = []
        for s in db.query(FullAutoSession).all():
            ids = list(getattr(s, "active_strategy_ids", None) or [])
            hit = [x for x in ids if x in set(sids)]
            if hit:
                sess_hits.append((s, hit))
                print(f"\n   会话 {s.session_id} 的 active_strategy_ids 含 {len(hit)} 条：{hit[:4]}…")

        if not args.apply:
            print("\n[干跑] 未改动任何数据。加 --apply 落库。")
            return 0

        now = datetime.now(timezone.utc)
        for r in rows:
            r.status = "archived"
            r.archived_at = now
            r.archive_reason = REASON[:500]
        for s, hit in sess_hits:
            s.active_strategy_ids = [x for x in list(s.active_strategy_ids or []) if x not in set(sids)]
        db.commit()
        left = (db.query(AIStrategy)
                .filter(AIStrategy.timeframe_tier == "short",
                        AIStrategy.status.in_(["active", "paused", "frozen"])).count())
        print(f"\n[已落库] archived {len(rows)} 行；清理 {len(sess_hits)} 个会话。"
              f"剩余 short active/paused/frozen = {left}")
        return 0 if left == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
