"""ShadowDecisionService — RL 影子决策服务（方案需求 3：影子先行）

在**不接管下单**的前提下，让 RL 策略与现有交易管线并行输出决策，并把每次决策记为
rl_decide 阶段血缘，用于与真实管线对比、积累 paper 验证证据。

安全门控（三重）：
  1. RL_DECISION_ENABLED=False       → 完全关闭，返回 disabled；
  2. RL_SHADOW_ONLY=True（默认）       → 仅影子，live_allowed=False，绝不执行；
  3. 即便关闭 shadow_only，实盘接管仍需 Governor 审批 + paper 达标（本阶段不实现下单）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .. import flags
from ..envelope import EvolutionEnvelope, STAGE_RL_DECIDE, STATUS_PENDING
from ..ledger import ledger
from .policy import policy
from .env import ACTION_NAMES

logger = logging.getLogger(__name__)


class ShadowDecisionService:
    """RL 影子决策（单例 shadow_service）。"""

    def enabled(self) -> bool:
        return flags.get_flag("RL_DECISION_ENABLED")

    def live_allowed(self) -> bool:
        """是否允许接管实盘：需显式关闭 shadow_only + Governor 批准（本阶段恒返回按 flag 计算，不执行下单）。"""
        if not flags.get_flag("RL_DECISION_ENABLED"):
            return False
        if flags.get_flag("RL_SHADOW_ONLY"):
            return False
        return self._governor_ok()

    def _governor_ok(self) -> bool:
        """预留：向 RuntimeGovernor 查询实盘接管审批状态。"""
        try:
            from backend.services.runtime_governor import runtime_governor  # noqa: F401
            # 实盘接管审批点：当前保守返回 False，须人工在 Governor 明确放行后再放开。
            return False
        except Exception:
            return False

    def decide(
        self,
        symbol: str,
        timeframe: str = "1h",
        *,
        position: float = 0.0,
        record: bool = True,
    ) -> Dict[str, Any]:
        """产出一次影子决策（不执行）。"""
        if not self.enabled():
            return {"enabled": False, "reason": "RL_DECISION_ENABLED=False"}

        state = self._build_state(symbol, timeframe, position)
        if state is None:
            return {"enabled": True, "error": "insufficient factor data", "symbol": symbol}

        policy.load()
        detail = policy.act_detail(state)
        result = {
            "enabled": True,
            "executed": False,           # 影子阶段绝不执行
            "live_allowed": self.live_allowed(),
            "symbol": symbol,
            "timeframe": timeframe,
            **detail,
        }

        if record:
            try:
                env = EvolutionEnvelope.root(
                    stage=STAGE_RL_DECIDE,
                    source="rl_shadow",
                    symbol=symbol,
                    payload={
                        "action": detail["action"],
                        "action_name": detail["action_name"],
                        "executed": False,
                        "live_allowed": result["live_allowed"],
                    },
                    metrics={"confidence": detail["confidence"]},
                    status=STATUS_PENDING,
                )
                ledger.record(env)
                result["lineage_id"] = env.lineage_id
            except Exception as exc:
                logger.debug("[ShadowDecisionService] 记录血缘失败: %s", exc)

        return result

    def _build_state(self, symbol: str, timeframe: str, position: float) -> Optional[Dict[str, Any]]:
        try:
            from backend.services.factor_engine.factor_service import factor_service
            fv_map = factor_service.compute(symbol, timeframe)
            if not fv_map:
                return None
            state = {k: float(getattr(v, "normalized", 0.0)) for k, v in fv_map.items()}
            state["__position__"] = float(position)
            return state
        except Exception as exc:
            logger.debug("[ShadowDecisionService] build_state 失败: %s", exc)
            return None

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled(),
            "shadow_only": flags.get_flag("RL_SHADOW_ONLY"),
            "live_allowed": self.live_allowed(),
            "policy": policy.stats(),
            "actions": ACTION_NAMES,
        }


# 单例
shadow_service = ShadowDecisionService()
