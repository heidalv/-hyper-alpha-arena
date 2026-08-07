# backend/services/tp_sl_authority.py
"""单一 TP/SL 权威(根因:7 套系统 + 5 张表 + 双映射)。

合并为 1 张表 + 1 个映射。所有路径只读此模块。
"""
from __future__ import annotations

# 唯一 tier→nature 映射(消除 paper_trading_engine:75 与 position_memory_manager:381 分歧)
# short→scalp, long→trend_follow(取 position_memory_manager 的语义,更贴合实际持仓周期)
TIER_TO_NATURE: dict[str, str] = {
    "short": "scalp",
    "mid": "swing",
    "long": "trend_follow",
}

# 唯一 nature→(tp_pct, sl_pct) 权威表
# [2026-07-30 crypto-native] scalp TP 2.5%→2%（与 paper_tp_sl.py DEFAULT 对齐，
# RR=1.67 适合 crypto 5m scalp，避免 breakeven_tp 微利刷手续费）
NATURE_TP_SL: dict[str, tuple[float, float]] = {
    "scalp":       (0.020, 0.012),   # tp 2%, sl 1.2% (RR=1.67)
    "swing":       (0.060, 0.025),   # tp 6%,  sl 2.5%
    "trend_follow": (0.120, 0.040),  # tp 12%, sl 4%
}

def resolve_tp_sl_pct(tier: str | None) -> tuple[float, float]:
    """返回 (tp_pct, sl_pct)。tier→nature→(tp,sl) 单一路径。"""
    if tier is None:
        return NATURE_TP_SL["scalp"]
    _nature = TIER_TO_NATURE.get(tier, "scalp")
    return NATURE_TP_SL.get(_nature, NATURE_TP_SL["scalp"])
