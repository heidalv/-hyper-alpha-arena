"""回填数据库中 timeframe_tier 为空的策略记录。

从 genome.trade_nature 推断正确的 tier 值并更新到数据库。
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.database.connection import SessionLocal
from backend.database.models import AIStrategy

# trade_nature → timeframe_tier 映射
NATURE_TO_TIER = {
    "scalp": "short",
    "intraday": "short",
    "swing": "mid",
    "position": "long",
    "trend_follow": "long",
}


def backfill_tiers():
    db = SessionLocal()
    try:
        # 查询所有 timeframe_tier 为空的策略
        strats = db.query(AIStrategy).filter(
            (AIStrategy.timeframe_tier == None) | (AIStrategy.timeframe_tier == "")
        ).all()

        if not strats:
            print("所有策略的 timeframe_tier 已有值，无需回填。")
            return

        print(f"找到 {len(strats)} 个 timeframe_tier 为空的策略")

        updated = 0
        for strat in strats:
            genome = strat.genome or {}
            nature = genome.get("trade_nature", "") if isinstance(genome, dict) else ""
            tier = NATURE_TO_TIER.get(nature, "mid")

            old_tier = strat.timeframe_tier
            strat.timeframe_tier = tier
            updated += 1
            print(f"  {strat.strategy_id[:12]}... | {strat.primary_symbol} | "
                  f"nature={nature} | tier: {old_tier!r} → {tier}")

        db.commit()
        print(f"\n回填完成: 更新了 {updated} 个策略的 timeframe_tier")
    except Exception as e:
        print(f"回填失败: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    backfill_tiers()
