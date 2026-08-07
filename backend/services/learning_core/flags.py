"""统一进化学习内核 — 特性开关（安全护栏）

所有新内核能力**默认关闭 / 影子模式**，通过环境变量或运行时内存开关灰度启用，
保证在跑实盘的系统不被新代码影响（对应方案「最高原则：实盘不中断」）。

读取优先级：运行时内存覆盖 > backend.config.settings > 环境变量 > 默认值。
"""

from __future__ import annotations

import os
from typing import Dict


# 当前为模拟交易环境（无实盘），功能开关默认全部开启，让内核完整运转；
# 两个 *_SHADOW_ONLY 仍默认 True（影子/沙箱保护），这是"反向安全开关"：
#   True = 保护开启（RL 只并行输出不接管下单 / codegen 只进隔离沙箱不自动合入）。
# 即便模拟盘也保留这两道防线，避免未训练好的 RL 乱下单、或 LLM 生成的 .py 未经审查直接合入运行。
# 如需 RL 在模拟盘真正接管执行，可在配置页关闭 RL_SHADOW_ONLY（仍受 Governor 门控）。
_DEFAULTS: Dict[str, bool] = {
    # 统一内核总开关：开启后 orchestrator 编排层生效（仍只是编排现有引擎，不改行为）
    "LEARNING_CORE_ENABLED": True,
    # 血缘账本记录 + WebSocket 实时推送
    "LEARNING_LEDGER_ENABLED": True,
    # 假设晋升后自动进入 GA 进化（P2 补断链）
    "HYPOTHESIS_AUTO_EVOLVE": True,
    # RL 决策 agent 总开关
    "RL_DECISION_ENABLED": True,
    # RL 仅影子模式（True=只并行输出不接管下单；实盘/模拟接管需显式关闭此项 + Governor 审批）
    "RL_SHADOW_ONLY": True,
    # opencode 产品内 codegen（shadow_py）总开关
    "OPENCODE_CODEGEN_ENABLED": True,
    # codegen 仅影子 worktree（True=只在隔离 worktree 生成+验证，不自动合入主干）
    "OPENCODE_CODEGEN_SHADOW_ONLY": True,
}

# 运行时内存覆盖（POST /api/learning/flags 修改，重启失效）
_runtime_overrides: Dict[str, bool] = {}


def _coerce_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return False


def get_flag(key: str) -> bool:
    """读取单个开关。"""
    if key in _runtime_overrides:
        return _runtime_overrides[key]
    # backend.config.settings 优先
    try:
        from backend.config import settings  # type: ignore
        if hasattr(settings, key):
            return _coerce_bool(getattr(settings, key))
    except Exception:
        pass
    # 环境变量
    env_val = os.environ.get(key)
    if env_val is not None:
        return _coerce_bool(env_val)
    return _DEFAULTS.get(key, False)


def set_flag(key: str, value: bool) -> None:
    """运行时设置开关（仅内存，重启后失效）。"""
    if key not in _DEFAULTS:
        raise KeyError(f"未知开关: {key}. 合法: {sorted(_DEFAULTS)}")
    _runtime_overrides[key] = _coerce_bool(value)


def all_flags() -> Dict[str, bool]:
    """返回全部开关的当前有效值。"""
    return {k: get_flag(k) for k in _DEFAULTS}


def flag_keys() -> list[str]:
    return sorted(_DEFAULTS)
