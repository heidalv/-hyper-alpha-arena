"""一次性恢复模拟盘会话：激活策略、清冷却、放宽门禁验证。"""
from __future__ import annotations

import sys

from backend.database.connection import SessionLocal
from backend.database.models import AIStrategy, FullAutoSession
from backend.services.full_auto_trading_service import full_auto_service


def main(session_id: str = "fa_e55efe8e92") -> int:
    db = SessionLocal()
    try:
        session = (
            db.query(FullAutoSession)
            .filter(FullAutoSession.session_id == session_id)
            .first()
        )
        if not session:
            print(f"会话不存在: {session_id}")
            return 1

        print(f"会话 {session_id} status={session.status} symbols={session.symbols}")
        print(f"auto_coin={session.auto_coin_symbols}")

        if full_auto_service._paper_auto_unlock_session(db, session):
            full_auto_service._safe_commit(db, "recover_paper_trading", session=session)
            print("paper_auto_unlock: 已提交")
        else:
            print("paper_auto_unlock: 无变更或锁仓未关闭")

        active_ids = list(session.active_strategy_ids or [])
        paused = (
            db.query(AIStrategy)
            .filter(
                AIStrategy.strategy_id.in_(active_ids),
                AIStrategy.status != "active",
            )
            .count()
            if active_ids
            else 0
        )
        active = (
            db.query(AIStrategy)
            .filter(
                AIStrategy.strategy_id.in_(active_ids),
                AIStrategy.status == "active",
            )
            .count()
            if active_ids
            else 0
        )
        print(f"active_ids={len(active_ids)} active={active} non_active={paused}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sid = sys.argv[1] if len(sys.argv) > 1 else "fa_e55efe8e92"
    raise SystemExit(main(sid))
