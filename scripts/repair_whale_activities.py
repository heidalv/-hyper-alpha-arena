# -*- coding: utf-8 -*-
"""
一次性数据修复（2026-09 审计 P0-1 落地）：
1) 回填 whale_activities.timestamp（NULL → created_at），让链上鲸鱼行对
   事件时间轴 / 因子数据集可见；
2) 删除 CVD 假大单（activity_type='large_order' AND blockchain='exchange'，
   由已下线的 _infer_from_market_flow 产出，金额为 CVD×1000 的虚报值，
   实测存在 27.5 亿美元级假单）。

幂等：可重复运行；运行前后打印行数。
"""
import sys

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
from sqlalchemy import create_engine, text

MARKET_URL = "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market"
eng = create_engine(MARKET_URL, pool_pre_ping=True, isolation_level="AUTOCOMMIT")


def scalar(db, sql, label):
    v = db.execute(text(sql)).scalar()
    print(f"  {label}: {v}")
    return v


with eng.connect() as db:
    print("=== 修复前 ===")
    total = scalar(db, "SELECT COUNT(*) FROM whale_activities", "whale_activities 总行数")
    null_ts = scalar(
        db, "SELECT COUNT(*) FROM whale_activities WHERE timestamp IS NULL",
        "timestamp IS NULL 行数",
    )
    fake = scalar(
        db,
        "SELECT COUNT(*) FROM whale_activities "
        "WHERE activity_type='large_order' AND blockchain='exchange'",
        "CVD 假大单行数",
    )
    print("  large_order 按 blockchain 分布:")
    for r in db.execute(text(
        "SELECT blockchain, COUNT(*) FROM whale_activities "
        "WHERE activity_type='large_order' GROUP BY blockchain"
    )):
        print("   ", r)

    # 1) 回填 timestamp
    if null_ts:
        n = db.execute(text(
            "UPDATE whale_activities SET timestamp = created_at WHERE timestamp IS NULL"
        ))
        print(f"\n[1] timestamp 回填完成，影响行数: {n.rowcount}")
    else:
        print("\n[1] 无 NULL timestamp，跳过回填")

    # 2) 删除 CVD 假大单
    if fake:
        n = db.execute(text(
            "DELETE FROM whale_activities "
            "WHERE activity_type='large_order' AND blockchain='exchange'"
        ))
        print(f"[2] CVD 假大单删除完成，影响行数: {n.rowcount}")
    else:
        print("[2] 无 CVD 假大单，跳过删除")

    print("\n=== 修复后 ===")
    scalar(db, "SELECT COUNT(*) FROM whale_activities", "whale_activities 总行数")
    scalar(db, "SELECT COUNT(*) FROM whale_activities WHERE timestamp IS NULL",
           "timestamp IS NULL 行数")
    scalar(
        db,
        "SELECT COUNT(*) FROM whale_activities WHERE activity_type='large_order' "
        "AND blockchain='exchange'",
        "CVD 假大单行数",
    )

print("\nDONE")
