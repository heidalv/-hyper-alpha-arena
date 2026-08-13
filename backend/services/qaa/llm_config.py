"""
QAA LLM 双模型配置 — quick / deep 均默认 DeepSeek-V4 Flash

设计文档: docs/V4_MULTI_AGENT_ARCHITECTURE.md §3.8.3

90% tick 只用 quick 模型 (因子分类、信号生成、轻量判断),
MasterController 最终决策等走 deep 档位，但默认模型同为 v4-flash。
"""

from __future__ import annotations

import os
from typing import Any, Dict

# ══════════════════════════════════════════════════
#  双模型配置
# ══════════════════════════════════════════════════

LLM_CONFIG: Dict[str, Dict[str, Any]] = {
    "quick": {
        # 快速推理: 分类、打分、简单判断 (<5s)
        "model": os.getenv("QAA_QUICK_MODEL", "deepseek-v4-flash"),
        "temperature": float(os.getenv("QAA_QUICK_TEMPERATURE", "0")),
        "max_tokens": int(os.getenv("QAA_QUICK_MAX_TOKENS", "1024")),
        "timeout": int(os.getenv("QAA_QUICK_TIMEOUT", "10")),
        "cost_per_1k_tokens": float(os.getenv("QAA_QUICK_COST", "0.001")),
    },
    "deep": {
        # 深度任务档位：统一使用 V4 Flash（不再默认 Pro/reasoner）
        "model": os.getenv("QAA_DEEP_MODEL", "deepseek-v4-flash"),
        "temperature": float(os.getenv("QAA_DEEP_TEMPERATURE", "0")),
        "max_tokens": int(os.getenv("QAA_DEEP_MAX_TOKENS", "4096")),
        "timeout": int(os.getenv("QAA_DEEP_TIMEOUT", "90")),
        "cost_per_1k_tokens": float(os.getenv("QAA_DEEP_COST", "0.001")),
    },
}


def get_llm_config(level: str) -> Dict[str, Any]:
    """获取指定级别的 LLM 配置

    Args:
        level: "quick" 或 "deep"

    Returns:
        LLM 配置字典
    """
    cfg = dict(LLM_CONFIG.get(level, LLM_CONFIG["quick"]))
    try:
        from backend.services.llm_config_service import get_llm_config as get_default_config

        default_cfg = get_default_config(tier="deep" if level == "deep" else "quick")
        if default_cfg and default_cfg.model:
            cfg["model"] = default_cfg.model
            cfg["provider"] = default_cfg.provider
            cfg["base_url"] = default_cfg.base_url
    except Exception:
        pass
    return cfg
