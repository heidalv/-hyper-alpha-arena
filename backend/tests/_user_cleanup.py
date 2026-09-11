"""测试用户清理 —— RLS 环境下的正确删法。

[2026-09-02] 为什么需要这个共享 helper
--------------------------------------
六个测试文件各自写了一份 `_cleanup(username, email)`，做法都是：

    db.query(RefreshToken).filter(RefreshToken.user_id == user.id).delete()
    db.delete(user)
    db.commit()

看起来先删子表再删父表、顺序正确，实际会稳定抛：

    ForeignKeyViolation: 表 "users" 上的删除违反了表 "refresh_tokens" 上的外键

根因是两张表的 RLS 配置不对称：

    refresh_tokens : rowsecurity=True,  force=True   ← 挂 tenant_isolation
    users          : rowsecurity=False, force=False  ← 无策略

策略为 `user_id = app.tenant_id OR user_id IS NULL OR app.is_admin = 'on'`。
清理的目标用户几乎不会正好等于连接当前的 app.tenant_id（实测 GUC 停留在 326，
而要删的是 553），于是子表 DELETE 被策略过滤成**匹配 0 行、静默成功**，父表
users 无 RLS 删除畅通 —— 外键随即报错。这不是顺序写错，是 RLS 让"删子表"变成
了空操作。

正确做法是走策略里的管理员穿透分支：在同一事务内 `SET LOCAL app.is_admin='on'`。
SET LOCAL 的作用域是当前事务，commit 后即失效，所以必须与 DELETE 同事务、且
一次事务内删完。

注：生产代码目前没有任何删除 user 的路径（全库仅测试里有 db.delete(user)），
故这不是线上缺陷。但将来若要实现"注销/删除账号"，必须按同样方式处理，否则会
踩同一个坑。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text


def cleanup_user(username: Optional[str] = None, email: Optional[str] = None,
                 *, user_id: Optional[int] = None,
                 extra_tables: Optional[dict] = None) -> None:
    """删除测试用户及其外键子行，幂等。

    Args:
        username: 按用户名清理（可选）
        email: 按邮箱兜底清理，防 user 行没建但唯一索引残留（可选）
        user_id: 按主键清理（可选）
        extra_tables: 额外要清的表 —— {表名: 关联到 user id 的列名}。
            例如 test_llm_config_byok 需要 {"llm_configurations": "tenant_id"}。
    """
    from backend.database.connection import SessionLocal
    from backend.database.models import User

    db = SessionLocal()
    try:
        # 管理员穿透：让 refresh_tokens 等挂 RLS 的子表 DELETE 真正命中行。
        # 必须与下面的删除同处一个事务（SET LOCAL 随 commit 失效）。
        try:
            db.execute(text("SET LOCAL app.is_admin = 'on'"))
        except Exception:
            pass  # SQLite 等无 GUC 概念的后端

        targets = []
        if user_id is not None:
            targets += db.query(User).filter(User.id == int(user_id)).all()
        if username:
            for u in db.query(User).filter(User.username == username).all():
                if u not in targets:
                    targets.append(u)
        if email:
            for u in db.query(User).filter(User.email == email).all():
                if u not in targets:
                    targets.append(u)

        for u in targets:
            uid = int(u.id)
            for tbl, col in (extra_tables or {}).items():
                try:
                    db.execute(text(f"DELETE FROM {tbl} WHERE {col} = :uid"),
                               {"uid": uid})
                except Exception:
                    pass
            # 已知的 user_id 外键子表。用 raw SQL 而非 ORM bulk delete：
            # 后者受 session 标识映射与 synchronize_session 影响，这里只要
            # 语句在同一事务内执行（才能吃到上面的 SET LOCAL）。
            for tbl in ("refresh_tokens", "user_auth_sessions"):
                try:
                    db.execute(text(f"DELETE FROM {tbl} WHERE user_id = :uid"),
                               {"uid": uid})
                except Exception:
                    pass
            try:
                db.execute(text(
                    "DELETE FROM admin_audit_logs "
                    "WHERE admin_user_id = :uid OR target_user_id = :uid"
                ), {"uid": uid})
            except Exception:
                pass
            db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": uid})

        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()
