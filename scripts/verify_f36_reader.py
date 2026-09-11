# -*- coding: utf-8 -*-
"""F36 读侧修复验证：新逻辑每 tier 独立配额 vs 旧逻辑全局 300 条。"""
import sys

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena/backend")

from sqlalchemy import create_engine, text

from backend.database.connection import SessionLocal  # noqa: E402

# 用项目自己的会话工厂调新实现（走生产库）
from backend.api.full_auto_routes import _tier_activity_impl  # noqa: E402

db = SessionLocal()
try:
    res = _tier_activity_impl("fa_7e12e7a1b6", 60, db)
    print("新逻辑 tier 计数:",
          {k: len(v) for k, v in res.items()})
    print("  long 前 5 条:")
    for it in res["long"][:5]:
        print("   ", it.get("time"), it.get("symbol"), it.get("action"),
              "repeat=", it.get("repeat", 1), "source=", it.get("source"))
finally:
    db.close()

# 旧逻辑复现：全局最新 300 条中 tier=long 有多少（证明饥饿）
ANA = create_engine(
    "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_analytics",
    isolation_level="AUTOCOMMIT",
)
adb = ANA.connect()
old_long = adb.execute(text(
    "SELECT COUNT(*) FROM (SELECT tier FROM decision_snapshots "
    "ORDER BY id DESC LIMIT 300) t WHERE tier='long'"
)).scalar()
old_short = adb.execute(text(
    "SELECT COUNT(*) FROM (SELECT tier FROM decision_snapshots "
    "ORDER BY id DESC LIMIT 300) t WHERE tier='short'"
)).scalar()
print(f"旧逻辑(全局300) → short={old_short} long={old_long}")
print("DONE")
