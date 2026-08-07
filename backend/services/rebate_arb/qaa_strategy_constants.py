"""QAA rebate 域常量 — S1–S8 action 映射与协调组。"""

from __future__ import annotations

import os
from typing import Dict, Optional

# 套利 QAA：深度推理 Agent 外层防挂死（毫秒）；内层 LLM 走 SSE [DONE] 自然结束
REBATE_QAA_DEEP_TIMEOUT_MS = int(os.getenv("REBATE_QAA_DEEP_TIMEOUT_MS", "600000"))
REBATE_QAA_DEEP_TIMEOUT_SEC = REBATE_QAA_DEEP_TIMEOUT_MS / 1000.0
REBATE_QAA_QUICK_TIMEOUT_MS = int(os.getenv("REBATE_QAA_QUICK_TIMEOUT_MS", "90000"))

STRATEGY_TO_ACTION: Dict[str, Optional[str]] = {
    "S1": "execute_maker_hedge",
    "S2": "execute_vip_sprint",
    "S3": "execute_points_mining",
    "S4": "execute_campaign",
    "S5": "execute_funding_points",
    "S6": "execute_cross_fee",
    "S7": None,
    "S8": "execute_asterdex_rh",
}

ACTION_TO_STRATEGY: Dict[str, str] = {
    v: k for k, v in STRATEGY_TO_ACTION.items() if v
}

COORDINATION_GROUPS: Dict[str, str] = {
    "S1": "hedge_mutex",
    "S2": "volume_program",
    "S3": "maker_roundtrip",
    "S4": "volume_program",
    "S5": "directional_mutex",
    "S6": "hedge_mutex",
    "S7": "monitor_only",
    "S8": "directional_mutex",
}

# 同组内 tick 只选一个
MUTEX_GROUPS: Dict[str, tuple] = {
    "hedge_mutex": ("S1", "S6"),
    "directional_mutex": ("S5", "S8"),
    "volume_program": ("S2", "S4"),
}

AI_DECISION_MODE: Dict[str, str] = {
    "S1": "rule_ev",
    "S2": "optional_deep",
    "S3": "none",
    "S4": "optional_deep",
    "S5": "rule_optional_deep",
    "S6": "rule_ev",
    "S7": "none",
    "S8": "required_deep_quick",
}

QAA_AGENT_CHAINS: Dict[str, list] = {
    "S1": ["rebate_strategy_coordinator", "rebate_risk", "rebate_executor"],
    "S2": ["rebate_strategy_coordinator", "rebate_wash_guard", "rebate_executor"],
    "S3": ["rebate_strategy_coordinator", "rebate_executor", "rebate_monitor"],
    "S4": ["rebate_strategy_coordinator", "rebate_strategy_analyst", "rebate_executor"],
    "S5": ["rebate_strategy_coordinator", "rebate_strategy_analyst", "rebate_executor"],
    "S6": ["rebate_strategy_coordinator", "rebate_risk", "rebate_executor"],
    "S7": ["rebate_monitor"],
    "S8": [
        "rebate_strategy_coordinator",
        "rebate_strategy_analyst",
        "rebate_execution_planner",
        "rebate_executor",
    ],
}

MACRO_FILTER_REQUIRED = {"S8"}


def strategy_to_executor_action(strategy_id: str) -> Optional[str]:
    return STRATEGY_TO_ACTION.get((strategy_id or "").upper())
