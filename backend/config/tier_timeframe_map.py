"""
三周期（short/mid/long）→ K线周期 的单一权威映射表。

背景（2026-07-06 整改）：审查报告发现 `trend_classifier.py`、`strategy_coordinator.py`、
`multi_timeframe_orchestrator.py`、`signal_pre_screener.py` 四个模块各自维护了一套不完全
相同的"三周期对应哪些K线周期"定义，导致同一个"周期共振"结论在不同模块间互相矛盾。
本模块是整改后的唯一权威来源，其余模块一律从这里 import，不再各自硬编码。

设计取舍：
- 每个 tier 只保留 1 个 `primary`（用于该 tier 的主判断，例如置信度评分的主输入）
  + 2 个 `confirm`（用于交叉确认/背离检测），避免像旧 signal_pre_screener 那样一个 tier
  绑 3 个周期导致职责不清。
- long tier 的 primary 定为 `4h` 而非 `1d`：`trend_follow` 策略在本项目里以小时级/4小时级
  为主要开仓判断窗口（`1d`/`1w` 更多用于"大势背景确认"而非"入场时机"判断），这与
  `strategy_coordinator._calc_dynamic_risk_params` 等下游消费方的既有语义更接近，
  选它作为统一标准可以让下游改动量最小。
"""
from typing import Dict, List, TypedDict


class TierTimeframes(TypedDict):
    primary: str
    confirm: List[str]


TIER_TIMEFRAME_MAP: Dict[str, TierTimeframes] = {
    "short": {"primary": "15m", "confirm": ["5m", "1m"]},
    "mid":   {"primary": "1h",  "confirm": ["4h", "15m"]},
    "long":  {"primary": "4h",  "confirm": ["1d", "1w"]},
}

# 兼容 trade_nature 命名（scalp/intraday/swing/trend_follow/position）到 tier 的映射，
# 供仍以 nature 命名调用的旧调用点直接查表，不必各自维护 if/elif 分支。
NATURE_TO_TIER: Dict[str, str] = {
    "scalp": "short",
    "intraday": "short",
    "swing": "mid",
    "trend_follow": "long",
    "position": "long",
}


def get_timeframes_for_tier(tier: str) -> TierTimeframes:
    """按 tier 名（short/mid/long）取该 tier 的主周期与确认周期。

    tier 名不存在时抛出 KeyError（不做静默兜底猜测——这正是本轮整改要根治的
    "缺失时用主导周期反推" 类问题的反面教材，配置查找应该 fail-fast）。
    """
    return TIER_TIMEFRAME_MAP[tier]


def get_timeframes_for_nature(nature: str) -> TierTimeframes:
    """按 trade_nature（scalp/swing/trend_follow 等）取对应 tier 的周期定义。"""
    tier = NATURE_TO_TIER.get(nature)
    if tier is None:
        raise KeyError(f"未知 trade_nature: {nature!r}，无法映射到 tier→timeframe")
    return TIER_TIMEFRAME_MAP[tier]


def all_timeframes_for_tier(tier: str) -> List[str]:
    """返回该 tier 的 [primary, *confirm] 完整周期列表（保序，primary 在前）。"""
    tf = TIER_TIMEFRAME_MAP[tier]
    return [tf["primary"], *tf["confirm"]]
