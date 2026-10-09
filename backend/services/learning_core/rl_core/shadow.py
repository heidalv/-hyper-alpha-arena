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
            self._last_block_reason = "RL_DECISION_ENABLED=false（RL 决策总开关关闭）"
            return False
        if flags.get_flag("RL_SHADOW_ONLY"):
            # [2026-10-04] 逐道门都留原因，否则面板只能显示一个恒 False 的布尔值
            self._last_block_reason = "shadow_only=true（需 RL_SHADOW_ONLY=false 才进入放行校验）"
            return False
        return self._governor_ok()

    def _governor_ok(self) -> bool:
        """实盘接管的**人工放行开关** + paper 达标校验。

        [2026-10-04 用户指令「补齐」· 面板⑨"RL 仅影子（shadow_only，未上实盘）"]
        原实现是 `return False` **硬编码**：注释写着"须人工在 Governor 明确放行后再放开"，
        但**没有任何放行入口** ⇒ 这道门永远是关的，"补齐"无从谈起。
        现改为真实门控（两道都满足才放行）：
          1. `RL_GOVERNOR_APPROVED=true` —— 人工放行（默认 false，与旧行为等价）；
          2. paper 样本达标：`RL_MIN_LIVE_SAMPLES`（默认 200）条**真实**回放样本
             （`source='live'`），不足则仍然拒绝并给出明确原因。
        即：默认行为不变（仍不放行），但把"永远不可能"变成"条件满足即可放行"。
        """
        import os as _os

        if str(_os.getenv("RL_GOVERNOR_APPROVED", "false")).strip().lower() not in (
            "1", "true", "yes", "on",
        ):
            self._last_block_reason = "governor_not_approved（RL_GOVERNOR_APPROVED=false）"
            return False
        try:
            need = int(_os.getenv("RL_MIN_LIVE_SAMPLES", "200") or 200)
            from .replay_buffer import replay_buffer

            stats = replay_buffer.stats() or {}
            live = int((stats.get("by_source") or {}).get("live", 0) or 0)
            if live < need:
                self._last_block_reason = f"paper_samples_insufficient（live={live} < {need}）"
                return False
            self._last_block_reason = ""
            return True
        except Exception as exc:  # 校验失败一律不放行（保守）
            self._last_block_reason = f"sample_check_failed（{type(exc).__name__}）"
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
        ok = self.live_allowed()
        return {
            "enabled": self.enabled(),
            "shadow_only": flags.get_flag("RL_SHADOW_ONLY"),
            "live_allowed": ok,
            # [2026-10-04] 把"为什么不能接管"讲清楚（此前只有一个恒 False 的布尔值）
            "live_block_reason": getattr(self, "_last_block_reason", "") or ("" if ok else "unknown"),
            "policy": policy.stats(),
            "actions": ACTION_NAMES,
        }


# 单例
shadow_service = ShadowDecisionService()
