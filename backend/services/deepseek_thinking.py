"""DeepSeek V4 思考模式分层策略。

官方（OpenAI 兼容）：
  - 思考默认开启，effort 默认 high
  - thinking: {"type": "enabled"|"disabled"}
  - reasoning_effort: low|high|max（及部分端点的 xhigh）

本项目分层（可被环境变量覆盖）：
  - short   : 短线/高频 → 关闭思考（快、省）
  - deep    : 中长线/主控决策 → high（默认）
  - max     : 架构演进/深度复盘等重任务 → max

环境变量：
  DEEPSEEK_THINKING_MODE=auto|enabled|disabled
      auto=按 caller 分层；enabled/disabled=全局强制
  DEEPSEEK_REASONING_EFFORT=auto|low|high|max
      auto=按分层；其它值=全局强制 effort（仍受 MODE 影响）
  DEEPSEEK_THINKING_MAX_TOKENS_FLOOR=16000
      effort=max 时抬高 max_completion_tokens 下限，降低思维链截断风险
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# caller 子串 → 档位（先匹配 max，再 short，其余 deep）
_MAX_MARKERS = (
    "hermes",
    "architecture",
    "strategy_evolver",
    "backtest_evolution",
    "ai_review_champion",
    "codegen",
    "opencode",
    "strategic_analyst",
    "audit",
)
_SHORT_MARKERS = (
    "scalp",
    "flash_veto",
    "coin_select",
    "auto_coin",
    "news_intelligence",
    "whale_tracker",
    "gate_param",
    "gate_optimizer",
    "routine",
    "tick",
    "factor_discovery",  # 扫描类，偏快
)


def is_deepseek_v4_model(model: str) -> bool:
    m = (model or "").lower()
    if "deepseek" not in m:
        return False
    # v4 全系 + 仍在兼容期的 chat/reasoner 别名
    return any(
        x in m
        for x in (
            "deepseek-v4",
            "deepseek-chat",
            "deepseek-reasoner",
            "deepseek-flash",
            "deepseek-pro",
        )
    )


def classify_thinking_tier(caller: Optional[str]) -> str:
    """返回 short | deep | max。"""
    c = (caller or "").lower()
    for m in _MAX_MARKERS:
        if m in c:
            return "max"
    for m in _SHORT_MARKERS:
        if m in c:
            return "short"
    return "deep"


def resolve_thinking_policy(
    model: str,
    caller: Optional[str] = None,
) -> Dict[str, Any]:
    """解析应对当前请求注入的思考参数。

    返回:
      {
        "apply": bool,                 # 是否为 DeepSeek V4 且应注入
        "thinking_enabled": bool,
        "reasoning_effort": Optional[str],  # low/high/max；关闭思考时为 None
        "tier": str,                   # short|deep|max|forced
        "bump_max_tokens": bool,       # effort=max 时建议抬高 token 下限
      }
    """
    if not is_deepseek_v4_model(model):
        return {
            "apply": False,
            "thinking_enabled": False,
            "reasoning_effort": None,
            "tier": "n/a",
            "bump_max_tokens": False,
        }

    mode = (os.getenv("DEEPSEEK_THINKING_MODE", "auto") or "auto").strip().lower()
    effort_override = (os.getenv("DEEPSEEK_REASONING_EFFORT", "auto") or "auto").strip().lower()
    tier = classify_thinking_tier(caller)

    if mode in ("disabled", "off", "0", "false", "no"):
        return {
            "apply": True,
            "thinking_enabled": False,
            "reasoning_effort": None,
            "tier": "forced_off",
            "bump_max_tokens": False,
        }

    if mode in ("enabled", "on", "1", "true", "yes"):
        effort = effort_override if effort_override in ("low", "high", "max", "xhigh") else "high"
        return {
            "apply": True,
            "thinking_enabled": True,
            "reasoning_effort": effort,
            "tier": "forced_on",
            "bump_max_tokens": effort in ("max", "xhigh"),
        }

    # auto：按 caller 分层
    if tier == "short":
        return {
            "apply": True,
            "thinking_enabled": False,
            "reasoning_effort": None,
            "tier": "short",
            "bump_max_tokens": False,
        }

    if effort_override in ("low", "high", "max", "xhigh"):
        effort = effort_override
    elif tier == "max":
        effort = "max"
    else:
        effort = "high"

    return {
        "apply": True,
        "thinking_enabled": True,
        "reasoning_effort": effort,
        "tier": tier,
        "bump_max_tokens": effort in ("max", "xhigh"),
    }


def max_tokens_floor_for_max_effort() -> int:
    try:
        return max(4000, int(os.getenv("DEEPSEEK_THINKING_MAX_TOKENS_FLOOR", "16000")))
    except Exception:
        return 16000


def apply_deepseek_thinking_to_payload(
    payload: Dict[str, Any],
    *,
    model: Optional[str] = None,
    caller: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """就地注入 thinking / reasoning_effort；返回 (payload, policy)。"""
    model_name = model or str(payload.get("model") or "")
    policy = resolve_thinking_policy(model_name, caller)
    if not policy.get("apply"):
        return payload, policy

    if policy.get("thinking_enabled"):
        payload["thinking"] = {"type": "enabled"}
        effort = policy.get("reasoning_effort")
        if effort:
            payload["reasoning_effort"] = effort
        if policy.get("bump_max_tokens"):
            floor = max_tokens_floor_for_max_effort()
            # 兼容 max_completion_tokens / max_tokens
            for key in ("max_completion_tokens", "max_tokens"):
                if key in payload:
                    try:
                        payload[key] = max(int(payload[key] or 0), floor)
                    except Exception:
                        payload[key] = floor
                    break
            else:
                payload["max_completion_tokens"] = floor
    else:
        payload["thinking"] = {"type": "disabled"}
        payload.pop("reasoning_effort", None)
        # 关闭思考后 temperature 生效；若调用方未设则给稳健默认
        if "temperature" not in payload:
            payload["temperature"] = 0.3

    logger.debug(
        "[DeepSeekThinking] model=%s caller=%s tier=%s enabled=%s effort=%s",
        model_name,
        caller,
        policy.get("tier"),
        policy.get("thinking_enabled"),
        policy.get("reasoning_effort"),
    )
    return payload, policy
