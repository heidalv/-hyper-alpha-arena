"""RL 决策内核 rl_core

  - replay_buffer.py  经验回放缓冲区（P3 起：回测/交易 → 转移样本）
  - env.py            TradingDecisionEnv 交易决策环境（P4）
  - policy.py         RL 策略/训练器（P4）
  - shadow.py         影子决策服务（P4，与现管线并行，不接管下单）

安全：RL 相关能力默认关闭 / 影子模式，通过 flags 门控。
"""

from .replay_buffer import replay_buffer, ReplayBuffer
from .env import TradingDecisionEnv, ACTION_NAMES, N_ACTIONS
from .policy import policy, LinearQPolicy
from .shadow import shadow_service, ShadowDecisionService

__all__ = [
    "replay_buffer",
    "ReplayBuffer",
    "TradingDecisionEnv",
    "ACTION_NAMES",
    "N_ACTIONS",
    "policy",
    "LinearQPolicy",
    "shadow_service",
    "ShadowDecisionService",
]
