"""学习中心统一配置 (L5)

把原先散落在各处 os.getenv / settings 的学习相关开关集中到一处，
便于 .env.example 维护、dashboard 暴露、后端 enabled 属性读取。

各 LearningBackend 的 enabled 属性应读这里，而非就地 os.getenv。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict, fields
from typing import Any, Dict


def _flag(env_key: str, default: str = "false") -> bool:
    return os.getenv(env_key, default).lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class LearningConfig:
    """学习中心统一配置（只读快照）。

    所有字段在首次实例化时从 env 读取。运行时改动需通过 dashboard 的
    feature-flags 端点刷新（或重启）。默认值与原各处 os.getenv 默认值一致。
    """

    # ── 总开关 ──
    loop_enabled: bool = None              # LEARNING_LOOP_ENABLED (默认 true)
    drl_retrain_auto: bool = None          # DRL_RETRAIN_AUTO (默认 false)

    # ── 协调器 / 仓位 ──
    enable_coordinator: bool = None        # ENABLE_COORDINATOR (默认 true)
    enable_kelly_position: bool = None     # ENABLE_KELLY_POSITION (默认 true)

    # ── 进化 ──
    nsga2_enabled: bool = None             # NSGA2_ENABLED (默认 true)

    # ── 后端开关（对应 LearningBackend.enabled）──
    factor_strategy_joint: bool = None     # AI_FACTOR_STRATEGY_JOINT_ENABLED (默认 false)
    concept_drift_detection: bool = None   # AI_CONCEPT_DRIFT_DETECTION_ENABLED (默认 false)
    causal_discovery: bool = None          # AI_CAUSAL_DISCOVERY_ENABLED (默认 false)

    def __post_init__(self):
        # None → 读 env 默认值（frozen dataclass 用 object.__setattr__ 绕过只读）
        defaults = {
            "loop_enabled": ("LEARNING_LOOP_ENABLED", "true"),
            "drl_retrain_auto": ("DRL_RETRAIN_AUTO", "false"),
            "enable_coordinator": ("ENABLE_COORDINATOR", "true"),
            "enable_kelly_position": ("ENABLE_KELLY_POSITION", "true"),
            "nsga2_enabled": ("NSGA2_ENABLED", "true"),
            "factor_strategy_joint": ("AI_FACTOR_STRATEGY_JOINT_ENABLED", "false"),
            "concept_drift_detection": ("AI_CONCEPT_DRIFT_DETECTION_ENABLED", "false"),
            "causal_discovery": ("AI_CAUSAL_DISCOVERY_ENABLED", "false"),
        }
        for fname, (env_key, dflt) in defaults.items():
            if getattr(self, fname) is None:
                object.__setattr__(self, fname, _flag(env_key, dflt))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def field_names(cls):
        return [f.name for f in fields(cls)]


# 全局快照（import 时读取一次）
learning_config = LearningConfig()


def get_learning_config() -> LearningConfig:
    """获取学习中心配置快照。"""
    return learning_config


def is_enabled(flag_name: str) -> bool:
    """便捷查询某个开关是否启用。

    Args:
        flag_name: LearningConfig 字段名（如 'causal_discovery'）
    """
    return bool(getattr(learning_config, flag_name, False))
