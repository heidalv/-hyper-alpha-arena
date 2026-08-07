"""统一进化学习内核（Athena / Evolution Nexus）

把"策略假设引擎 / Hermes 进化 / 智能学习中心"重构合并为单一内核：
  - envelope.py     统一交互协议 EvolutionEnvelope（全链路可追溯）
  - ledger.py       血缘账本 LearningLedger（独立 SQLite + 实时广播）
  - flags.py        特性开关（安全护栏，默认影子/关闭）
  - adapters/       薄封装现有引擎（假设 / Hermes / 在线学习），合门面不改内核
  - orchestrator.py LearningOrchestrator 唯一编排入口

设计遵循"实盘不中断"：所有能力默认关闭 / 影子模式，通过特性开关灰度启用。
"""

from .envelope import (
    EvolutionEnvelope,
    new_lineage_id,
    STAGE_HYPOTHESIS,
    STAGE_VALIDATE,
    STAGE_EVOLVE,
    STAGE_LEARN,
    STAGE_RL_DECIDE,
    STAGE_DEPLOY,
    STAGE_OBSERVE,
    STAGE_FEEDBACK,
    STATUS_PENDING,
    STATUS_PASSED,
    STATUS_REJECTED,
    STATUS_DEPLOYED,
    STATUS_ROLLED_BACK,
)
from .ledger import ledger, LearningLedger
from .orchestrator import orchestrator, LearningOrchestrator
from . import flags

__all__ = [
    "EvolutionEnvelope",
    "new_lineage_id",
    "ledger",
    "LearningLedger",
    "orchestrator",
    "LearningOrchestrator",
    "flags",
    "STAGE_HYPOTHESIS",
    "STAGE_VALIDATE",
    "STAGE_EVOLVE",
    "STAGE_LEARN",
    "STAGE_RL_DECIDE",
    "STAGE_DEPLOY",
    "STAGE_OBSERVE",
    "STAGE_FEEDBACK",
    "STATUS_PENDING",
    "STATUS_PASSED",
    "STATUS_REJECTED",
    "STATUS_DEPLOYED",
    "STATUS_ROLLED_BACK",
]
