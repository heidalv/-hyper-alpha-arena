"""一次性/可重复:创建或提升指定邮箱为 admin + vip,并设置密码。

用法(在仓库根目录):
  set ADMIN_EMAIL=xxx@outlook.com
  set ADMIN_PASSWORD=***
  set ADMIN_USERNAME=heida   (可选)
  .venv\\Scripts\\python.exe -m backend.scripts.ensure_admin_user

不会把密码写入仓库文件;只改数据库。
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    email = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
    password = os.getenv("ADMIN_PASSWORD") or ""
    username = (os.getenv("ADMIN_USERNAME") or "").strip()
    demote_default = (os.getenv("DEMOTE_DEFAULT_ADMIN") or "1").strip() not in (
        "0",
        "false",
        "False",
    )

    if not email or "@" not in email:
        print("ERROR: set ADMIN_EMAIL=user@example.com", file=sys.stderr)
        return 2
    if not password or len(password) < 8:
        print("ERROR: set ADMIN_PASSWORD (min 8 chars)", file=sys.stderr)
        return 2
    if not username:
        username = email.split("@", 1)[0].replace(".", "_")[:50]

    # 保证可从仓库根导入 backend.*
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if root not in sys.path:
        sys.path.insert(0, root)

    from backend.database.connection import SessionLocal
    from backend.database.models import User
    from backend.repositories.user_repo import (
        _hash_password,
        create_user,
        get_user_by_email,
        get_user_by_username,
    )

    db = SessionLocal()
    try:
        user = get_user_by_email(db, email)
        if user is None:
            # 用户名冲突则加后缀
            base = username
            n = 0
            while get_user_by_username(db, username):
                n += 1
                username = f"{base}{n}"[:50]
            user = create_user(db, username, email, password)
            print(f"created user id={user.id} username={user.username}")
        else:
            user.password_hash = _hash_password(password)
            if user.email != email:
                user.email = email
            print(f"updated password for id={user.id} username={user.username}")

        user.role = "admin"
        user.tier = "vip"
        user.is_active = "true"
        db.commit()
        db.refresh(user)

        if demote_default:
            default = db.query(User).filter(User.username == "default").first()
            if default and default.id != user.id and (default.role or "") == "admin":
                default.role = "user"
                db.commit()
                print(f"demoted default user id={default.id} role=user")

        print(
            "OK",
            {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "role": user.role,
                "tier": user.tier,
            },
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
