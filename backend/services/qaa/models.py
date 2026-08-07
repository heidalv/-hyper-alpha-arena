"""QAA 协议核心数据模型 —— 双轨收敛（整改#7）。

单一事实来源：`qaa.core.models`（qaa_architecture_package）。本模块优先从 qaa 包
再导出模型，消除 `backend/services/qaa/` 与 `qaa/` 的重复定义与概念漂移。

零风险保障：
  - 若 qaa 包不可导入（路径缺失/环境异常），自动回退到 `models_local.py` 的本地定义，
    行为与收敛前完全一致。
  - 已核验两处模型字段完全一致（包内 AgentCard 为超集，仅多一个可选 `domain` 字段），
    因此再导出对现有构造/校验完全兼容。

对应设计文档: docs/V4_MULTI_AGENT_ARCHITECTURE.md Section 4.2
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

# 确保 qaa 包可导入（与 backend/main.py、config/settings.py 的路径注入一致）
_QAA_PKG_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "qaa_architecture_package")
)
if os.path.isdir(_QAA_PKG_DIR) and _QAA_PKG_DIR not in sys.path:
    sys.path.insert(0, _QAA_PKG_DIR)

# 数据模型来源标记（"qaa.core.models" | "local"），便于诊断与前端展示
MODELS_SOURCE = "local"

try:
    from qaa.core.models import (  # noqa: F401
        AgentCall,
        AgentCard,
        AgentSignal,
        AgentTask,
        AgentTaskState,
        AuditEntry,
        Capability,
        CircuitBreakerConfig,
        GuardDef,
        LLMLevel,
        QAARequest,
        QAAResponse,
        QAAStatus,
        ToolDef,
    )

    # 包独有的通用信号（可选，存在则一并再导出）
    try:
        from qaa.core.models import GenericSignal  # noqa: F401
    except Exception:  # noqa: BLE001
        pass

    MODELS_SOURCE = "qaa.core.models"
    logger.debug("[QAA#7] 模型来源: qaa.core.models（双轨收敛生效）")
except Exception as exc:  # noqa: BLE001 —— 任意导入异常都回退本地，绝不影响 backend 启动
    logger.warning("[QAA#7] 无法从 qaa 包导入模型，回退本地定义: %s", exc)
    from backend.services.qaa.models_local import (  # noqa: F401
        AgentCall,
        AgentCard,
        AgentSignal,
        AgentTask,
        AgentTaskState,
        AuditEntry,
        Capability,
        CircuitBreakerConfig,
        GuardDef,
        LLMLevel,
        QAARequest,
        QAAResponse,
        QAAStatus,
        ToolDef,
    )
    MODELS_SOURCE = "local"


__all__ = [
    "LLMLevel",
    "QAAStatus",
    "AgentTaskState",
    "CircuitBreakerConfig",
    "Capability",
    "ToolDef",
    "GuardDef",
    "AgentCard",
    "QAARequest",
    "QAAResponse",
    "AgentCall",
    "AgentTask",
    "AuditEntry",
    "AgentSignal",
    "MODELS_SOURCE",
]
