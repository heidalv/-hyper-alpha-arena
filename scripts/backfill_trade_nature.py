"""
回填历史仓位的 trade_nature 字段
运行方式: python scripts/backfill_trade_nature.py
"""
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database.connection import SessionLocal
from backend.database.models import PaperPosition, AIStrategy

VALID_NATURES = {"scalp", "intraday", "swing", "position", "trend_follow"}
TIER_TO_NATURE = {"short": "intraday", "mid": "swing", "long": "position"}


def backfill():
    db = SessionLocal()
    try:
        # 查询所有 trade_nature 为 NULL 或空字符串的仓位
        positions = db.query(PaperPosition).filter(
            (PaperPosition.trade_nature.is_(None)) | (PaperPosition.trade_nature == "")
        ).all()

        print(f"找到 {len(positions)} 个 trade_nature=NULL/空 的仓位")

        from_genome = 0
        from_tier = 0

        for pos in positions:
            # 尝试从关联策略的 genome 读取
            if pos.strategy_id:
                strategy = db.query(AIStrategy).filter(
                    AIStrategy.strategy_id == pos.strategy_id
                ).first()
                if strategy and strategy.genome:
                    genome = strategy.genome if isinstance(strategy.genome, dict) else {}
                    nature = (genome.get("trade_nature") or "").strip().lower()
                    if nature in VALID_NATURES:
                        pos.trade_nature = nature
                        from_genome += 1
                        print(f"  [G] pos#{pos.id} {pos.symbol}: genome -> {nature}")
                        continue

            # 从 timeframe_tier 反推
            tier = (pos.timeframe_tier or "mid").strip().lower()
            fallback = TIER_TO_NATURE.get(tier, "swing")
            pos.trade_nature = fallback
            from_tier += 1
            print(f"  [T] pos#{pos.id} {pos.symbol}: tier={tier} -> {fallback}")

        db.commit()
        print(f"\n回填完成!")
        print(f"  从genome恢复: {from_genome}")
        print(f"  从tier推断:   {from_tier}")
        print(f"  总计:         {from_genome + from_tier}")
    finally:
        db.close()


if __name__ == "__main__":
    backfill()
