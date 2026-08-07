"""把原测试用户(id=1)名下全部租户数据完整继承到管理员 heida(id=326)。

不删除旧用户行，只把 tenant_id / accounts.user_id 归并到 326，避免能力被剥离。
"""
from __future__ import annotations

from backend.core.tenant import set_request_identity
from backend.database.connection import SessionLocal
from sqlalchemy import text

SRC = 1
DST = 326


def main() -> None:
    set_request_identity(DST, "admin")
    db = SessionLocal()
    try:
        # 1) 交易账户归属
        moved_acc = db.execute(
            text("UPDATE accounts SET user_id=:dst WHERE user_id=:src RETURNING id,name"),
            {"dst": DST, "src": SRC},
        ).fetchall()
        print("accounts moved", moved_acc)

        # 2) 所有带 tenant_id 的表：1 -> 326
        tables = db.execute(
            text(
                """
                SELECT DISTINCT c.table_name
                FROM information_schema.columns c
                JOIN information_schema.tables t
                  ON t.table_name=c.table_name AND t.table_schema='public'
                WHERE c.column_name='tenant_id' AND t.table_type='BASE TABLE'
                ORDER BY 1
                """
            )
        ).fetchall()
        for (tn,) in tables:
            try:
                r = db.execute(
                    text(f"UPDATE {tn} SET tenant_id=:dst WHERE tenant_id=:src"),
                    {"dst": DST, "src": SRC},
                )
                if r.rowcount:
                    print(f"  {tn}: {r.rowcount}")
            except Exception as e:
                print(f"  skip {tn}: {e}")

        # 3) LLM：保留原完整 DeepSeek，设为默认；重复默认取消
        db.execute(
            text(
                """
                UPDATE llm_configurations
                SET is_default='false'
                WHERE tenant_id=:dst AND is_default='true' AND id <> 17
                """
            ),
            {"dst": DST},
        )
        db.execute(
            text(
                """
                UPDATE llm_configurations
                SET tenant_id=:dst, is_default='true', is_active='true'
                WHERE id=17
                """
            ),
            {"dst": DST},
        )
        # 4) 会话 tenant 与账户对齐
        db.execute(
            text(
                """
                UPDATE full_auto_sessions s
                SET tenant_id = a.user_id
                FROM accounts a
                WHERE s.account_id = a.id
                """
            )
        )
        db.commit()
        print(
            "llm now",
            db.execute(
                text("SELECT id,tenant_id,name,is_default,usage_scope FROM llm_configurations ORDER BY id")
            ).fetchall(),
        )
        print(
            "accounts",
            db.execute(text("SELECT id,user_id,name FROM accounts")).fetchall(),
        )
        print(
            "sessions",
            db.execute(
                text("SELECT session_id,account_id,tenant_id,status FROM full_auto_sessions")
            ).fetchall(),
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
