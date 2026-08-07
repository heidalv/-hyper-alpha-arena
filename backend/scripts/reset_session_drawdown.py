"""一次性重置 FullAutoSession 的 peak / drawdown 基线。

背景：
    旧版 ``_update_session_stats`` 把 StrategyTrade + PaperPosition 双源累加为
    ``total_pnl``，与 PaperBalance.total_equity 长期偏离；系统用"错误权益"推算
    ``peak_balance`` 和 ``max_drawdown``，在历史上曾把 peak 顶到 139 美元、
    max_drawdown 压到 44%，从而污染 running session 的绩效基线。

    A 方案修复（full_auto_trading_service._update_session_stats）落地之后，
    ``total_pnl`` 改为直接读 PaperBalance 权益差，但 ``peak_balance`` /
    ``max_drawdown`` 是增量累计字段，脏值不会自己消失，需要一次性基线重置。

做什么：
    对所有 status IN ('running', 'paused') 的 FullAutoSession：
      1. 查对应 PaperBalance 的 ``initial_balance`` / ``total_equity``
      2. 新 peak_balance = max(initial_balance, total_equity)
         （若当前权益高于本金，就承认当前就是新的峰值，避免下一 tick 立刻涨回 peak）
      3. max_drawdown 清零、current_drawdown 清零
      4. 若 session 没绑定 PaperBalance（live 模式或 account 未建），跳过不动

使用：
    cd Hyper-Alpha-Arena
    python -m backend.scripts.reset_session_drawdown --dry-run   # 预览
    python -m backend.scripts.reset_session_drawdown --apply     # 实际执行

幂等性：
    本脚本可重复执行。执行后，下一次 ``_update_session_stats`` tick 会立即
    用当前真实权益重算 peak 和 drawdown，基线自此干净。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import List, Dict, Any

_here = os.path.dirname(os.path.abspath(__file__))
_proj_root = os.path.abspath(os.path.join(_here, "..", ".."))
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

from backend.database.connection import SessionLocal  # noqa: E402
from backend.database.models import FullAutoSession, PaperBalance  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("reset_session_drawdown")


def collect_plan(db) -> List[Dict[str, Any]]:
    """汇总待重置的 session 信息，不做任何 UPDATE。"""
    sessions = (
        db.query(FullAutoSession)
        .filter(FullAutoSession.status.in_(["running", "paused"]))
        .order_by(FullAutoSession.id.asc())
        .all()
    )

    plan: List[Dict[str, Any]] = []
    for s in sessions:
        record: Dict[str, Any] = {
            "id": s.id,
            "session_id": s.session_id,
            "status": s.status,
            "trading_mode": s.trading_mode,
            "account_id": s.account_id,
            "old_peak": float(s.peak_balance or 0),
            "old_max_dd": float(s.max_drawdown or 0),
            "old_cur_dd": float(s.current_drawdown or 0),
            "old_total_pnl": float(s.total_pnl or 0),
            "action": "skip",
            "reason": "",
            "new_peak": None,
            "new_max_dd": 0.0,
            "new_cur_dd": 0.0,
            "initial_balance": None,
            "total_equity": None,
        }

        if not s.account_id:
            record["reason"] = "no account_id"
            plan.append(record)
            continue

        bal = (
            db.query(PaperBalance)
            .filter(PaperBalance.account_id == s.account_id)
            .first()
        )
        if not bal:
            record["reason"] = "no PaperBalance"
            plan.append(record)
            continue

        init_bal = float(bal.initial_balance or 0)
        equity = float(bal.total_equity or init_bal)
        new_peak = max(init_bal, equity)

        record["initial_balance"] = init_bal
        record["total_equity"] = equity
        record["new_peak"] = round(new_peak, 4)
        record["action"] = "reset"
        plan.append(record)

    return plan


def apply_plan(db, plan: List[Dict[str, Any]]) -> int:
    """对标 "action=reset" 的 session 写回新基线。"""
    changed = 0
    for item in plan:
        if item["action"] != "reset":
            continue
        sess = db.query(FullAutoSession).filter(FullAutoSession.id == item["id"]).first()
        if not sess:
            continue
        sess.peak_balance = item["new_peak"]
        sess.max_drawdown = 0.0
        sess.current_drawdown = 0.0
        changed += 1
    db.commit()
    return changed


def pretty(item: Dict[str, Any]) -> str:
    if item["action"] == "skip":
        return (
            f"  [SKIP] id={item['id']} session={item['session_id']} "
            f"mode={item['trading_mode']} reason={item['reason']}"
        )
    return (
        f"  [RESET] id={item['id']} session={item['session_id']} mode={item['trading_mode']} "
        f"account={item['account_id']} "
        f"init={item['initial_balance']:.2f} equity={item['total_equity']:.4f} "
        f"total_pnl={item['old_total_pnl']:+.4f} "
        f"peak: {item['old_peak']:.4f} -> {item['new_peak']:.4f}  "
        f"max_dd: {item['old_max_dd']:.4f} -> 0  "
        f"cur_dd: {item['old_cur_dd']:.4f} -> 0"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="重置 running/paused session 的 peak/drawdown 基线")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="只打印计划，不写库")
    group.add_argument("--apply", action="store_true", help="实际执行重置")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        plan = collect_plan(db)
        total = len(plan)
        resets = sum(1 for p in plan if p["action"] == "reset")
        skips = total - resets

        logger.info(f"扫描 running/paused session: 共 {total} 条（待重置 {resets}，跳过 {skips}）")
        for item in plan:
            logger.info(pretty(item))

        if args.dry_run:
            logger.info("[DRY-RUN] 未写库。确认无误后加 --apply 执行。")
            return 0

        changed = apply_plan(db, plan)
        logger.info(f"[APPLY] 已重置 {changed} 条 session 的 peak/drawdown 基线。")
        logger.info("下一次 _update_session_stats tick（<=15s）将基于新基线重新累计。")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
