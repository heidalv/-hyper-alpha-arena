"""QAA 协议核心数据模型（本地回退定义 —— 整改#7 双轨收敛）。

本文件为 models.py 的 fallback：当 qaa 包（qaa_architecture_package）不可导入时，
models.py 回退到此处的本地定义，保证 backend 行为与收敛前完全一致（零风险）。

⚠️ 单一事实来源为 `qaa.core.models`；此处仅作降级兜底，请勿在此新增字段。
所有 Pydantic 模型定义, 零依赖 (仅 pydantic + 标准库)。
对应设计文档: docs/V4_MULTI_AGENT_ARCHITECTURE.md Section 4.2
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, Field, ConfigDict


# ────────────────────────── 枚举类型 ──────────────────────────


class LLMLevel(str, Enum):
    """LLM 调用级别 (借鉴 TradingAgents quick_think / deep_think)"""
    NONE = "none"       # 纯规则, 无 LLM (如 RiskControlAgent)
    QUICK = "quick"     # 快速推理, 低延迟 (<5s): DeepSeek-V3, Gemini Flash
    DEEP = "deep"       # 深度推理, 高质量 (<60s): Claude Sonnet, GPT-4o


class QAAStatus(str, Enum):
    """QAA 响应状态"""
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CIRCUIT_OPEN = "circuit_open"
    CANCELED = "canceled"


class AgentTaskState(str, Enum):
    """Agent 任务状态 (借鉴 A2A Task Lifecycle)"""
    CREATED = "created"
    SUBMITTED = "submitted"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELED = "canceled"


# ────────────────────────── 配置模型 ──────────────────────────


class CircuitBreakerConfig(BaseModel):
    """熔断器配置 (借鉴 Circuit Breaker Pattern)"""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    failure_threshold: int = 3            # 连续 N 次失败触发熔断
    recovery_timeout_sec: float = 300     # 熔断恢复时间 (秒)
    half_open_max_calls: int = 1          # 半开状态试探次数
    fallback_agent: Optional[str] = None  # 熔断后的替代 Agent


class Capability(BaseModel):
    """Agent 可调用能力 (借鉴 A2A Skill + MCP Tool)"""
    name: str                              # 能力唯一名称
    description: str                       # 人类可读描述
    input_schema: dict = Field(default_factory=dict)   # JSON Schema
    output_schema: dict = Field(default_factory=dict)  # JSON Schema
    cost_estimate_ms: float = 0            # 预估耗时 (毫秒)
    is_llm_required: bool = False          # 是否需要 LLM 调用
    is_destructive: bool = False           # 是否有副作用 (借鉴 MCP annotations)
    is_idempotent: bool = True             # 是否幂等 (重试安全)


class ToolDef(BaseModel):
    """Agent 内部可用的计算工具 (借鉴 MCP Tool)"""
    name: str
    description: str
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)
    cost_estimate_ms: float = 0
    read_only: bool = True                 # 借鉴 MCP readOnlyHint


class GuardDef(BaseModel):
    """门控规则定义"""
    name: str
    description: str
    priority: int = 0                      # 执行优先级 (0 最先)
    is_blocking: bool = True               # True=拦截, False=仅记录


# ────────────────────────── Agent Card ──────────────────────────


class AgentCard(BaseModel):
    """Agent 注册卡片 (借鉴 A2A AgentCard)"""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    agent_id: str                          # 唯一标识
    display_name: str
    version: str = "1.0.0"
    description: str = ""
    capabilities: list[Capability] = Field(default_factory=list)
    tools: list[ToolDef] = Field(default_factory=list)
    guards: list[GuardDef] = Field(default_factory=list)

    # LLM 级别 (双模型策略)
    llm_level: LLMLevel = LLMLevel.NONE

    # 超时熔断策略
    max_timeout_sec: float = 30
    timeout_strategy: Literal["skip", "cached", "hold", "retry"] = "skip"
    fallback_value: Any = None
    max_retries: int = 0
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)

    # 元数据
    dependencies: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


# ────────────────────────── 消息模型 ──────────────────────────


class QAARequest(BaseModel):
    """QAA 请求消息"""
    msg_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    from_agent: str
    to_agent: str                          # 或 "broadcast"
    action: str                            # 要调用的能力名称
    payload: dict[str, Any] = Field(default_factory=dict)
    timeout_ms: float = 30000             # 默认 30s
    priority: int = 0                      # 0=必须, 1=重要, 2=可选
    session_id: Optional[str] = None


class QAAResponse(BaseModel):
    """QAA 响应消息"""
    msg_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    correlation_id: str                    # 对应 request.msg_id
    status: QAAStatus = QAAStatus.SUCCESS
    data: Any = None
    error: Optional[str] = None
    elapsed_ms: float = 0
    fallback_used: bool = False
    agent_version: str = ""


class AgentCall(BaseModel):
    """Agent 调用计划 (由 RuleRouter 生成)"""
    agent_id: str
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timeout_ms: float = 30000
    priority: int = 0


# ────────────────────────── 任务模型 ──────────────────────────


class AgentTask(BaseModel):
    """Agent 任务实例 (借鉴 A2A Task)"""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    agent_id: str
    session_id: Optional[str] = None
    state: AgentTaskState = AgentTaskState.CREATED
    request: Optional[QAARequest] = None
    response: Optional[QAAResponse] = None
    history: list[dict] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


# ────────────────────────── 审计模型 ──────────────────────────


class AuditEntry(BaseModel):
    """审计记录 — 每步决策不可篡改"""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tick_id: str = ""
    agent_id: str = ""
    action: str = ""
    input_snapshot: dict = Field(default_factory=dict)
    output_data: Any = None
    status: QAAStatus = QAAStatus.SUCCESS
    elapsed_ms: float = 0
    llm_prompt_hash: Optional[str] = None
    llm_response_hash: Optional[str] = None
    fallback_used: bool = False
    circuit_breaker_state: str = "closed"
    metadata: dict[str, Any] = Field(default_factory=dict)


# ────────────────────────── 信号模型 ──────────────────────────


class AgentSignal(BaseModel):
    """统一信号格式 — 所有 Agent 必须输出此格式 (借鉴 ai-hedge-fund)"""
    agent_id: str
    symbol: str
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""
    timeframe: Optional[str] = None        # "1h", "4h", "1d"
    source_data_hash: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
