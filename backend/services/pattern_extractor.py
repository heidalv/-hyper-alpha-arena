"""
Pattern Extractor — 从高胜率策略中提取成功模式模板 (F2-1)

当策略 WR>50% 且 trades>20 时，自动提取其参数组合和市场状态，
写入全局成功模板库供新策略学习。
"""

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# 提取阈值
MIN_WIN_RATE = 0.50
MIN_TOTAL_TRADES = 20
MIN_SAMPLE_PATTERNS = 5


class PatternExtractor:
    """从高胜率策略中提取可复用的成功模式模板"""

    def extract_successful_pattern(
        self, db: Session, strategy_id: str
    ) -> Optional[Dict[str, Any]]:
        """当策略 WR>50% 且 trades>20 时，提取其参数+市场状态作为模板"""
        from backend.database.models import StrategyMemory

        memory = db.query(StrategyMemory).filter(
            StrategyMemory.strategy_id == strategy_id,
            StrategyMemory.win_rate >= MIN_WIN_RATE,
            StrategyMemory.total_trades >= MIN_TOTAL_TRADES,
        ).first()

        if not memory:
            logger.debug(f"[PatternExtractor] 策略 {strategy_id} 未达标，跳过提取")
            return None

        patterns = memory.successful_patterns or []
        if len(patterns) < MIN_SAMPLE_PATTERNS:
            logger.debug(
                f"[PatternExtractor] 策略 {strategy_id} 成功样本不足 "
                f"({len(patterns)}<{MIN_SAMPLE_PATTERNS})"
            )
            return None

        # 统计最成功的市场状态
        regime_counts = Counter(
            p.get("regime") for p in patterns if p.get("regime")
        )
        best_regime = (
            regime_counts.most_common(1)[0][0] if regime_counts else "unknown"
        )

        avg_pnl = sum(p.get("pnl", 0) for p in patterns) / len(patterns)

        # 统计最佳 trade_nature
        nature_counts = Counter(
            p.get("trade_nature") for p in patterns if p.get("trade_nature")
        )
        best_nature = (
            nature_counts.most_common(1)[0][0] if nature_counts else "swing"
        )

        # 统计最佳 symbol
        symbol_counts = Counter(
            p.get("symbol") for p in patterns if p.get("symbol")
        )
        best_symbol = (
            symbol_counts.most_common(1)[0][0] if symbol_counts else ""
        )

        template = {
            "strategy_id": strategy_id,
            "best_regime": best_regime,
            "best_nature": best_nature,
            "best_symbol": best_symbol,
            "avg_pnl_per_trade": round(avg_pnl, 4),
            "win_rate": memory.win_rate,
            "total_trades": memory.total_trades,
            "sharpe": memory.sharpe_ratio,
            "regime_distribution": dict(regime_counts),
            "nature_distribution": dict(nature_counts),
            "symbol_distribution": dict(symbol_counts),
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "sample_patterns": patterns[-10:],
        }

        self._save_pattern_template(db, template)
        logger.info(
            f"[PatternExtractor] 策略 {strategy_id} 成功模式已提取: "
            f"best_regime={best_regime}, wr={memory.win_rate:.1%}, "
            f"trades={memory.total_trades}"
        )
        return template

    def _save_pattern_template(
        self, db: Session, template: Dict[str, Any]
    ) -> None:
        """将成功模板持久化到 strategy_memories 的 key_lessons 字段"""
        from backend.database.models import StrategyMemory

        memory = db.query(StrategyMemory).filter(
            StrategyMemory.strategy_id == template["strategy_id"],
        ).first()
        if not memory:
            return

        # 将模板追加到 key_lessons（保持历史记录）
        lessons = memory.key_lessons or []
        # 移除过期的同类型模板，保留最近 5 条
        existing_templates = [
            l for l in lessons if l.get("type") == "success_pattern_template"
        ]
        if len(existing_templates) >= 5:
            # 移除最旧的
            oldest = min(
                existing_templates, key=lambda x: x.get("extracted_at", "")
            )
            lessons.remove(oldest)

        template["type"] = "success_pattern_template"
        lessons.append(template)
        memory.key_lessons = lessons

        db.flush()
        logger.debug(
            f"[PatternExtractor] 模板已保存到 strategy_memories.key_lessons "
            f"({len(lessons)} 条)"
        )

    def extract_all_eligible(
        self, db: Session, account_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """批量提取所有达标策略的成功模式"""
        from backend.database.models import StrategyMemory

        query = db.query(StrategyMemory).filter(
            StrategyMemory.win_rate >= MIN_WIN_RATE,
            StrategyMemory.total_trades >= MIN_TOTAL_TRADES,
        )
        if account_id:
            # [2026-08-11 修复] 原占位过滤写成了 id == account_id（strategy_memories
            # 没有 account_id 字段）。改为按 ai_strategies.account_id 关联过滤。
            from backend.database.models import AIStrategy
            query = query.join(
                AIStrategy,
                AIStrategy.strategy_id == StrategyMemory.strategy_id,
            ).filter(AIStrategy.account_id == account_id)

        results = []
        for memory in query.all():
            template = self.extract_successful_pattern(db, memory.strategy_id)
            if template:
                results.append(template)

        logger.info(
            f"[PatternExtractor] 批量提取完成: {len(results)} 个成功模板"
        )
        return results


# 模块级单例
pattern_extractor = PatternExtractor()
