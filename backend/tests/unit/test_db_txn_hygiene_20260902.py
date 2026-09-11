"""[2026-09-02] DB 事务卫生：release_idle_txn 与 LeakGuard 事务发起方追踪。

背景：LeakGuard 三天每小时强杀 10-20 个 idle-in-transaction 事务，持有者是
scalp/midlong 循环里"读完就去跑 LLM/行情"的同步 session。本文件锁住两件事：
1. release_idle_txn：只读事务被结束；有未提交写入时绝不动；结束后已加载对象仍可用。
2. _record_txn_origin：事务 begin 时按 PG 后端 pid 记录发起调用链，txn_origin_for_pid 可查。
全部只读 + rollback，不落库。
"""
import pytest
from sqlalchemy import text

from backend.database import connection as C

pytestmark = pytest.mark.unit


def _pg_only():
    if not str(C.DATABASE_URL).lower().startswith(("postgresql", "postgres")):
        pytest.skip("需要 PostgreSQL（pg_stat_activity / backend_pid）")


class TestReleaseIdleTxn:
    def test_readonly_txn_is_released_and_objects_stay_usable(self):
        _pg_only()
        from backend.database.models import Account
        db = C.SessionLocal()
        try:
            db.execute(text("SET LOCAL app.is_admin='on'"))
            acct = db.query(Account).order_by(Account.id).first()
            assert db.in_transaction(), "SELECT 后应处于 autobegin 事务中"
            assert C.release_idle_txn(db, where="test") is True
            assert not db.in_transaction(), "只读事务应被结束"
            if acct is not None:
                # expire_on_commit=False：不触发懒加载重开事务
                _ = (acct.id, acct.name)
                assert not db.in_transaction()
        finally:
            db.rollback()
            db.close()

    def test_pending_write_is_never_committed(self):
        _pg_only()
        from backend.database.models import Account
        db = C.SessionLocal()
        try:
            db.execute(text("SET LOCAL app.is_admin='on'"))
            acct = db.query(Account).order_by(Account.id).first()
            if acct is None:
                pytest.skip("库内无账户")
            acct_id = int(acct.id)  # rollback 会让实例过期，先把 id 拿出来
            original = acct.name
            acct.name = (original or "") + "_hygiene_probe"
            assert acct in db.dirty
            assert C.release_idle_txn(db, where="test") is False, "有脏写时不得替业务提交"
            assert db.in_transaction()
        finally:
            db.rollback()
            db.close()
        # 复核确实没落库
        db2 = C.SessionLocal()
        try:
            db2.execute(text("SET LOCAL app.is_admin='on'"))
            row = db2.query(Account).filter(Account.id == acct_id).first()
            assert row is not None and not str(row.name or "").endswith("_hygiene_probe")
        finally:
            db2.rollback()
            db2.close()

    def test_no_txn_is_noop(self):
        db = C.SessionLocal()
        try:
            assert C.release_idle_txn(db) is False
            assert C.release_idle_txn(None) is False
        finally:
            db.close()


class TestTxnOriginTrace:
    def test_origin_recorded_by_backend_pid(self):
        _pg_only()
        if not C._TXN_TRACE_ENABLED:
            pytest.skip("DB_LEAK_GUARD_TRACE 已关闭")
        db = C.SessionLocal()
        try:
            pid = db.execute(text("select pg_backend_pid()")).scalar()
            origin = C.txn_origin_for_pid(int(pid))
            assert origin, "begin 钩子应已按 pid 记录发起调用链"
            # 调用链应指回本测试文件（仓库内帧）而不是 SQLAlchemy 内部
            assert "test_db_txn_hygiene_20260902.py" in origin
            assert "sqlalchemy" not in origin.lower()
        finally:
            db.rollback()
            db.close()

    def test_unknown_pid_returns_none(self):
        assert C.txn_origin_for_pid(-1) is None
