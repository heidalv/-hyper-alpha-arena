"""
trade_nature_resolver — trade_nature 统一解析器

解决 trade_nature 三个来源可能冲突的问题：
1. 编排器 recommended_nature（基于多周期分析，最可靠）
2. AI 决策中的 trade_nature（AI 综合判断）
3. genome.trade_nature（遗传优化结果，可能过时）
4. timeframe_tier 推断（最低优先级默认值）

设计文档: SYSTEM_UPGRADE_DESIGN_V3.md 第6.2节
"""

import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# 优先级顺序（从高到低）:
# 1. 编排器 recommended_nature（基于多周期分析，最可靠）
# 2. AI 决策中的 trade_nature（AI 综合判断）
# 3. genome.trade_nature（遗传优化结果，可能过时）
# 4. timeframe_tier 推断（最低优先级默认值）

TIER_TO_NATURE_MAP = {
    'short': 'scalp',
    'mid': 'intraday',
    'long': 'swing',
}

VALID_NATURES = {'scalp', 'intraday', 'swing', 'position', 'trend_follow'}


def resolve_trade_nature(
    orchestrator_nature: Optional[str] = None,
    ai_nature: Optional[str] = None,
    genome_nature: Optional[str] = None,
    timeframe_tier: Optional[str] = None,
    context: Optional[Dict] = None,
) -> str:
    """
    统一解析 trade_nature

    优先级：
    1. orchestrator_nature (多周期分析结果)
    2. ai_nature (AI 综合判断)
    3. genome_nature (遗传优化)
    4. timeframe_tier 推断 (默认)

    Args:
        orchestrator_nature: OrchestratorDecision.recommended_nature
        ai_nature: AI 决策中的 trade_nature
        genome_nature: 基因组中的 trade_nature
        timeframe_tier: 编排器的 tier (short/mid/long)
        context: 额外上下文（预留扩展）

    Returns:
        scalp | intraday | swing | position | trend_follow
    """
    # 按优先级选择
    for candidate in [
        orchestrator_nature,
        ai_nature,
        genome_nature,
        TIER_TO_NATURE_MAP.get(timeframe_tier),
    ]:
        if candidate and candidate in VALID_NATURES:
            return candidate

    # 全部缺失，默认 intraday
    return 'intraday'
