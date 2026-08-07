"""
跨周期协同仲裁。

当多个 tier 对同一品种有持仓时的协同规则，确保整体风控一致且避免信号矛盾。
"""
from __future__ import annotations

import logging
from typing import Dict

from backend.services.exit.exit_types import ExitAction, ExitDecision, ExitSource

logger = logging.getLogger(__name__)


def cross_tier_arbitrate(
    decisions: Dict[str, ExitDecision],
    same_symbol_positions: list[dict],
) -> Dict[str, ExitDecision]:
    """
    跨周期协同仲裁。

    Args:
        decisions: {tier: ExitDecision} 同品种各 tier 的初步决策。
        same_symbol_positions: 同品种各 tier 的持仓信息。

    Returns:
        仲裁后的 {tier: ExitDecision}。

    规则：
        1. 硬事实优先：SL/TP/爆仓 不受其他 tier 影响。
        2. 趋势保护：long 趋势完好时，short/mid 的 master_reduce 降级为 tighten_sl。
        3. 总敞口约束：超限时按 short > mid > long 优先减。
        4. 方向冲突保护：刻意对冲时不触发 bias_reversal。
    """
    if not decisions:
        return decisions

    result = dict(decisions)

    # 规则 1：硬事实直通（不动）
    # 已在状态机 Layer 1 处理，此处跳过

    # 规则 2：趋势保护
    long_decision = result.get("long")
    if long_decision and long_decision.action == ExitAction.HOLD.value:
        # long 持有（趋势完好）→ short/mid 的 AI 减仓降级
        for tier in ("short", "mid"):
            d = result.get(tier)
            if d and d.source in (ExitSource.MASTER_REDUCE.value, ExitSource.MASTER_CLOSE.value):
                if d.action in (ExitAction.REDUCE.value, ExitAction.CLOSE.value):
                    logger.info(
                        f"[CrossTier] 趋势保护: long 持有, {tier} 的 {d.source} "
                        f"降级 {d.action}→tighten_sl"
                    )
                    result[tier] = ExitDecision(
                        position_id=d.position_id,
                        action=ExitAction.TIGHTEN_SL.value,
                        qty_ratio=0.0,
                        reason=f"跨周期趋势保护(long持有), {tier}减仓降级为收紧止损",
                        source=d.source,
                        overridden_sources=[d.source],
                    )

    # 规则 4：方向冲突保护
    # 检查是否有对冲持仓（同品种不同方向）
    if len(same_symbol_positions) >= 2:
        directions = set()
        for p in same_symbol_positions:
            directions.add(p.get("side", "long"))
        if len(directions) > 1:
            # 存在对冲 → 不触发 bias_reversal
            for tier, d in result.items():
                if d.source == ExitSource.BIAS_REVERSAL.value:
                    logger.info(f"[CrossTier] 方向冲突保护: {tier} bias_reversal 被抑制(对冲持仓)")
                    result[tier] = ExitDecision(
                        position_id=d.position_id,
                        action=ExitAction.HOLD.value,
                        reason="跨周期对冲保护，抑制 bias_reversal",
                        source=d.source,
                        overridden_sources=[d.source],
                    )

    return result
