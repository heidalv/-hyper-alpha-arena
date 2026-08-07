"""
量化框架升级 — 激进 rollout 默认配置（2026-07-09）。

用户选择「激进快开」：所有已落地能力默认开启，除非用户在 .env / 系统环境里
**显式**设置了对应变量（setdefault 不覆盖已有配置）。

注意：本模块只做 os.environ 注入，不 import 任何业务代码，避免循环依赖。
在 settings.py / main.py 最早期 import 一次即可全局生效。
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# env 未设置时写入的激进默认值（已接线、可安全降级的项全开）
_AGGRESSIVE_DEFAULTS: dict[str, str] = {
    # ── 因子 & ML ──
    "FACTOR_WEIGHTING_MODE": "hybrid",          # #4 regime+learned 并行
    "LEARNED_WEIGHTING_ENABLED": "true",        # #4 学习层重训开关
    "ML_PIPELINE_ENABLED": "true",              # #10 持续重训管线主路径
    "ML_LIVE_RETRAIN_HOURS": "12",              # #10 重训节奏
    # ── RAG / 记忆 ──
    "QAA_EMBEDDING_BACKEND": "neural",          # #16 神经嵌入（失败自动降级 hash）
    "QAA_KNOWLEDGE_BACKEND": "chroma",        # #16 ChromaDB（失败自动降级 jsonl）
    "QAA_RERANKER_ENABLED": "true",           # #16b 重排序
    "QAA_RERANKER_BACKEND": "lexical",        # 零依赖词法精排（无 sentence-transformers 也能跑）
    # ── 交易执行链 ──
    "RISK_ENGINE_ENABLED": "true",            # #5 引擎硬风控
    "ADVERSARIAL_DEBATE_ENABLED": "true",     # #11 对抗辩论
    "LLM_SEMANTIC_CACHE_ENABLED": "true",     # #13 语义缓存
    # ── 进化 / 学习 ──
    "MAP_ELITES_ENABLED": "true",             # #19 多样性冠军库
    "MAP_ELITES_MODE": "single",
    "QAA_OPTIMIZER": "cmaes",                 # #20 CMA-ES 精调
    "PBO_AUDIT_ENABLED": "true",              # #21 PBO 审计
    "EWC_ENABLED": "true",                    # #17 防遗忘
    "DDGDA_ENABLED": "true",                  # #18 主动分布预测（简化版）
    "DDGDA_MODE": "simplified",
    "DSPY_COMPILE_ENABLED": "true",           # #15 认知层编译
    "PROMPT_EVOLUTION_ENABLED": "true",       # 复活 prompt 进化（走 DSPy metric 驱动，非盲目突变）
    # ── 架构 ──
    "EVENT_SOURCING_ENABLED": "true",         # #9 事件溯源写路径双写
    "EVENT_SOURCING_PHASE2_RECONCILE": "true",  # #9 Phase 2 C7 持续对拍
    "EVENT_SOURCING_PHASE2_READ": "false",    # Phase 2 显式投影读（Phase3 开启时自动等效）
    "EVENT_SOURCING_PHASE3": "true",          # #9 Phase 3 投影默认读 + 启动 DB 引导
    "EVENT_SOURCING_WRITE_RETIRE_DB": "false",  # #9 Phase4：Paper .env 可显式 true；默认关保安全
    # ── G4 / 晋升门 ──
    "RESOURCE_GUARD_ENABLED": "true",         # G4 热路径资源隔离
    "PROMOTION_GATE_ENABLED": "true",         # shadow→canary→full 统计晋升门
    "DERIBIT_OPTIONS_ENABLED": "true",        # #12 免费期权源
    # ── 防幻觉 ──
    "MARKET_DATA_VERIFIER_ENABLED": "true",     # #11 LLM 数值校验
}

_applied = False


def apply_aggressive_rollout(*, force: bool = False) -> list[str]:
    """注入激进默认值。返回本次新注入的 key 列表（已有 env 的不动）。"""
    global _applied
    if _applied and not force:
        return []
    newly_set: list[str] = []
    for key, val in _AGGRESSIVE_DEFAULTS.items():
        if key not in os.environ:
            os.environ[key] = val
            newly_set.append(key)
    _applied = True
    if newly_set:
        logger.info(
            "[FrameworkRollout] 激进模式已注入 %d 项默认开关: %s",
            len(newly_set), ", ".join(newly_set),
        )
    return newly_set


def is_rollout_active() -> bool:
    return _applied
