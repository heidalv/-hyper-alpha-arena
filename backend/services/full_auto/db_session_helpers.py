"""DB session 辅助 — 安全 commit / 失败事务恢复（从 monolith 迁出）。"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


def safe_commit(db, label: str = "", session=None, retries: int = 4):
    """安全 commit — SQLite 写锁 + PG 死锁退避重试。"""
    from backend.database.connection import sqlite_write_commit, DATABASE_URL as _DB_URL

    # PG 路径：先尝试带死锁重试的直接 commit
    _is_pg = _DB_URL and (_DB_URL.startswith("postgresql") or _DB_URL.startswith("postgres"))
    if _is_pg:
        last_err = None
        for attempt in range(max(1, retries)):
            try:
                db.commit()
                return True
            except Exception as e:
                last_err = e
                err_s = str(e).lower()
                is_deadlock = "deadlock" in err_s or "could not serialize" in err_s
                is_connection = "connection" in err_s and ("closed" in err_s or "lost" in err_s)
                # 2026-07-20：PendingRollbackError（"can't reconnect until invalid transaction
                # is rolled back"）需要先 rollback 再重试 commit，否则整个 midlong tick 失败。
                is_pending_rollback = "invalid transaction" in err_s or "rolled back" in err_s
                if (is_deadlock or is_connection or is_pending_rollback) and attempt < retries - 1:
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    wait = 0.1 * (2 ** attempt)
                    logger.warning(
                        f"[DB] safe_commit {label} 死锁/连接丢失/PendingRollback, retry {attempt+1}/{retries} wait={wait:.2f}s"
                    )
                    time.sleep(wait)
                    continue
                # 非死锁或最后一次：rollback 后抛出
                try:
                    db.rollback()
                except Exception:
                    pass
                raise
        # 全部重试耗尽
        try:
            db.rollback()
        except Exception:
            pass
        logger.error(f"[DB] safe_commit {label} 全部{retries}次失败: {last_err}")
        return False

    # SQLite 路径：走 WriteQueue 串行化
    return sqlite_write_commit(db, label=label, retries=retries)


def recover_db_session(db, label: str = "") -> None:
    """检测并恢复失败事务，避免 place_order 连锁报错。"""
    try:
        from sqlalchemy import text

        db.execute(text("SELECT 1"))
    except Exception:
        try:
            db.rollback()
            if label:
                logger.debug(f"[FullAuto] DB session 已 rollback 恢复 ({label})")
        except Exception:
            pass


def deferred_signal_key(account_id: int, sym: str, action: str, tier: str = "mid") -> str:
    t = (tier or "mid").strip().lower()
    if t not in ("short", "mid", "long"):
        t = "mid"
    return f"{account_id}:{(sym or '').upper()}:{action}:{t}"
