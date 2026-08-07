"""Direction / Risk / Master 共用的提示词上下文（反馈闭环 + 历史教训）。"""

from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


def build_agent_context_block(
    *,
    db=None,
    account_id: Optional[int] = None,
    strategy_ids: Optional[List[str]] = None,
    role: str = "direction",
) -> str:
    """为 DirectionAgent / TradeRiskAgent 注入与 Master 一致的反馈约束。"""
    parts: List[str] = []

    # V5 决策核心：费用感知 + 盈亏结构纪律（与 Master 同一来源，消除双轨提示词不一致）
    try:
        from backend.services.decision_core import build_v5_prompt_block

        v5_block = build_v5_prompt_block(db=db, account_id=account_id)
        if v5_block:
            parts.append(v5_block)
    except Exception as err:
        logger.debug("[AgentContext] V5 context skip: %s", err)

    try:
        from backend.services.decision_feedback_service import decision_feedback_service

        feedback = decision_feedback_service.get_prompt_injection(
            db=db,
            account_id=account_id,
            strategy_ids=strategy_ids,
        )
        if feedback:
            parts.append(feedback)
    except Exception as err:
        logger.debug("[AgentContext] feedback skip: %s", err)

    if role == "direction":
        # 2026-06-18: 门槛文案与执行层 paper 放宽同步
        _is_paper_dir = True
        try:
            from backend.services.lock_strength_service import get_lock_strength_service as _glss
            _is_paper_dir = _glss().get_profile("paper").disable_loss_locks
        except Exception:
            pass
        _dir_scalp = 50 if _is_paper_dir else 70
        _dir_min_rr = 1.3 if _is_paper_dir else 1.8
        parts.append(
            "## 方向层纪律\n"
            "- 你只判断方向与 trade_nature，不填 leverage/position_pct。\n"
            f"- short/scalp/intraday 需更高置信度（scalp ≥{_dir_scalp}%）；证据不足时 hold。\n"
            f"- 开仓建议必须能支撑 TP:SL ≥ {_dir_min_rr}:1 的出场结构，否则给 hold。\n"
            "- 有 loss_analysis 教训的 symbol 参考（不禁止）同 nature 同向开仓。"
        )
    elif role == "risk":
        parts.append(
            "## 风控层纪律\n"
            "- 只能拒绝开仓、降杠杆(leverage_cap)、缩仓(size_multiplier≤1)。\n"
            "- 禁止放大仓位；有 SL 的持仓禁止 close，优先 hold/adjust_sl。\n"
            "- reduce 需多证据；健康分低 alone 不强制减仓。"
        )

    return "\n\n".join(p for p in parts if p)
