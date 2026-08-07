"""阶段4 Task 4.1: admin 密码 bootstrap。

背景
----
default 用户(迁移 0006 升为 admin)创建时 password_hash=NULL —— 无法登录。
spec §6.5.2 要求用 ``ADMIN_INIT_PASSWORD`` 环境变量设初始 admin 密码(bcrypt
hash),首次登录强制改密。本任务只做"让 admin 能登录"的最小机制:**强制改密
流程推迟**(见任务说明 Step 4)。

本模块导出 ``ensure_admin_password(db)``,由 main.py 的 startup 调用一次。
行为:
  - 若 default admin 的 password_hash 已存在(非空)→ 什么都不做(已设过)。
  - 若 password_hash 为空 且 ``ADMIN_INIT_PASSWORD`` 环境变量已设 → 用 bcrypt
    哈希它,写入 default admin 的 password_hash。返回状态字符串供日志。
  - 若 password_hash 为空 且环境变量未设 → 不报错,返回 warn 状态,提示运维
    手动设置(否则 admin 无法登录)。

安全注意
--------
  - ``ADMIN_INIT_PASSWORD`` 既接受**明文**也接受**已有的 bcrypt hash**($2b$/...)。
    传明文方便 dev/首次部署;生产推荐直接传 hash,避免明文落在进程环境。
    判断依据:以 "$2" 开头且长度合理 → 视为 hash 直接落库;否则当作明文 hash 之。
  - 本函数只对 default admin(username='default')生效;不碰其它用户。
  - 幂等:password_hash 已存在则直接返回,不覆盖(运维改密后重启不会回退)。
"""
from __future__ import annotations

import os
from typing import Optional

from sqlalchemy.orm import Session

from backend.repositories.user_repo import _hash_password


def _is_bcrypt_hash(value: str) -> bool:
    """启发式判断:bcrypt hash 以 $2 开头且长度在 50-100 之间。"""
    return value.startswith("$2") and 50 <= len(value) <= 100


def ensure_admin_password(db: Session) -> str:
    """确保 default admin 有可用密码。

    返回状态字符串(供调用方记日志):
      - "ok-set":      本次设置了初始密码
      - "ok-exists":   admin 已有密码,未改动
      - "warn-no-env": admin 无密码 且 ADMIN_INIT_PASSWORD 未设
      - "warn-no-user": default admin 用户不存在(迁移未跑?)
    """
    from backend.database.models import User

    admin = db.query(User).filter(User.username == "default").first()
    if admin is None:
        return "warn-no-user"

    # 已有密码 → 幂等,不动。
    if admin.password_hash and admin.password_hash.strip():
        return "ok-exists"

    raw = os.getenv("ADMIN_INIT_PASSWORD")
    if not raw:
        return "warn-no-env"

    # 接受明文或已有 bcrypt hash。
    if _is_bcrypt_hash(raw):
        admin.password_hash = raw
    else:
        admin.password_hash = _hash_password(raw)
    db.commit()
    return "ok-set"
