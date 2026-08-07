# backend/services/coin_rank — 共用选币排序内核（平台看板 + 会话 AutoCoin）
"""AI 选币共用 RankEngine。

平台与会话只保留「谁审、谁注入」差异；粗分 / 门控 / 反馈衰减走本包。
开关：COIN_RANK_ENGINE_ENABLED / COIN_RANK_GATES_ENABLED。
"""

from backend.services.coin_rank.engine import (
    RankResult,
    rank_symbols,
    rank_universe,
)
from backend.services.coin_rank.metrics import (
    CycleMetrics,
    get_last_cycle_metrics,
    record_cycle_metrics,
)

__all__ = [
    "RankResult",
    "rank_symbols",
    "rank_universe",
    "CycleMetrics",
    "get_last_cycle_metrics",
    "record_cycle_metrics",
]
