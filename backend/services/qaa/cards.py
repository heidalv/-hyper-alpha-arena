"""Agent Card 注册表 — 各 Agent 的能力卡片实例。

对应设计文档: docs/V4_MULTI_AGENT_ARCHITECTURE.md Section 3.3
"""

from .models import (
    AgentCard,
    Capability,
    CircuitBreakerConfig,
    GuardDef,
    LLMLevel,
    ToolDef,
)

# ──────────────────────────────────────────────────────────────
#  8 张 Agent Card — 对应 V4 架构的 8 个 Agent
# ──────────────────────────────────────────────────────────────

GENETIC_OPTIMIZER_CARD = AgentCard(
    agent_id="genetic_optimizer",
    display_name="遗传优化器",
    description="遗传算法优化策略参数 (离线, 不在主循环)",
    llm_level=LLMLevel.NONE,
    capabilities=[
        Capability(name="optimize", description="执行遗传算法优化", cost_estimate_ms=60000),
    ],
    max_timeout_sec=120,
    timeout_strategy="skip",
    fallback_value=None,
    circuit_breaker=CircuitBreakerConfig(failure_threshold=5, recovery_timeout_sec=600),
    tags=["offline", "optimization"],
)

MARKET_DATA_CARD = AgentCard(
    agent_id="market_data",
    display_name="市场数据 Agent",
    description="获取价格/K线/订单流数据快照",
    llm_level=LLMLevel.NONE,
    capabilities=[
        Capability(name="get_snapshot", description="获取统一数据快照", cost_estimate_ms=5000,
                   is_destructive=False),
    ],
    tools=[
        ToolDef(name="unified_data_pool", description="统一数据池", read_only=True),
    ],
    max_timeout_sec=10,
    timeout_strategy="cached",
    fallback_value=None,  # 由调度器根据上下文提供缓存值
    circuit_breaker=CircuitBreakerConfig(failure_threshold=5, recovery_timeout_sec=60),
    tags=["data", "readonly"],
)

FACTOR_ENGINE_CARD = AgentCard(
    agent_id="factor_engine",
    display_name="因子引擎 Agent",
    description="V3 因子计算 + 体制分类 + 异常检测 + 新旧系统桥接",
    llm_level=LLMLevel.QUICK,
    capabilities=[
        Capability(name="compute_full", description="完整因子计算 (含异常检测)", cost_estimate_ms=20000),
        Capability(name="compute_basic", description="基础因子计算", cost_estimate_ms=10000),
        Capability(name="compute_signals", description="因子信号生成+体制识别+权重", cost_estimate_ms=8000),
        Capability(name="compute_unified", description="新旧系统合并因子计算", cost_estimate_ms=25000),
    ],
    max_timeout_sec=30,
    timeout_strategy="cached",
    fallback_value=None,
    circuit_breaker=CircuitBreakerConfig(failure_threshold=3, recovery_timeout_sec=300),
    tags=["computation", "factors"],
)

INTEL_SIGNAL_CARD = AgentCard(
    agent_id="intel_signal",
    display_name="情报信号 Agent",
    description="8 源加权汇流信号 (funding/OI/鲸鱼/新闻/恐贪)",
    llm_level=LLMLevel.QUICK,
    capabilities=[
        Capability(name="get_signals", description="获取多源汇流交易信号", cost_estimate_ms=5000),
    ],
    max_timeout_sec=10,
    timeout_strategy="skip",
    fallback_value=None,
    circuit_breaker=CircuitBreakerConfig(failure_threshold=5, recovery_timeout_sec=600),
    tags=["signal", "intelligence"],
)

RISK_CONTROL_CARD = AgentCard(
    agent_id="risk_control",
    display_name="风控 Agent",
    description="确定性风控检查: 5 条硬规则 + 方向翻转检测",
    llm_level=LLMLevel.NONE,
    capabilities=[
        Capability(name="check", description="执行全部风控检查", cost_estimate_ms=100,
                   is_destructive=False),
    ],
    guards=[
        GuardDef(name="consistency_gate", description="30min 方向翻转检测", priority=0),
        GuardDef(name="risk_gate", description="5 条硬风控红线", priority=1),
        GuardDef(name="entry_confidence_gate", description="开仓置信度门槛", priority=2),
    ],
    max_timeout_sec=5,
    timeout_strategy="hold",
    fallback_value={"allowed": False, "reason": "risk_control_timeout"},
    circuit_breaker=CircuitBreakerConfig(failure_threshold=2, recovery_timeout_sec=30),
    tags=["risk", "safety"],
)

MT_ORCHESTRATOR_CARD = AgentCard(
    agent_id="mt_orchestrator",
    display_name="多周期编排 Agent",
    description="长/中/短三周期独立分析 + 情报注入 + 三层协调",
    llm_level=LLMLevel.QUICK,
    capabilities=[
        Capability(name="evaluate", description="评估单个交易对", cost_estimate_ms=5000),
        Capability(name="evaluate_portfolio", description="批量评估交易对", cost_estimate_ms=30000),
    ],
    max_timeout_sec=30,
    timeout_strategy="skip",
    fallback_value=None,
    circuit_breaker=CircuitBreakerConfig(failure_threshold=5, recovery_timeout_sec=300),
    dependencies=["market_data", "intel_signal"],
    tags=["orchestration", "multi-timeframe"],
)

MASTER_CONTROLLER_CARD = AgentCard(
    agent_id="master_controller",
    display_name="总控决策 Agent",
    description="综合六路分析师 + DebateLayer 辩论 + LLM 最终决策",
    llm_level=LLMLevel.DEEP,
    capabilities=[
        Capability(name="synthesize", description="综合所有信号做最终交易决策",
                   cost_estimate_ms=60000, is_llm_required=True, is_destructive=True),
    ],
    tools=[
        ToolDef(name="position_analyst", description="持仓健康评估", read_only=True),
        ToolDef(name="market_analyst", description="行情趋势/波动率", read_only=True),
        ToolDef(name="intel_analyst", description="新闻/鲸鱼/衍生品", read_only=True),
        ToolDef(name="risk_analyst", description="账户风险/保证金", read_only=True),
        ToolDef(name="strategy_analyst", description="策略有效性/胜率", read_only=True),
        ToolDef(name="kline_analyst", description="K 线形态识别 (LLM 增强)", read_only=True),
    ],
    guards=[
        GuardDef(name="consistency_gate", description="30min 方向翻转检测", priority=0),
        GuardDef(name="risk_gate", description="5 条硬风控红线", priority=1),
        GuardDef(name="entry_confidence_gate", description="开仓置信度门槛", priority=2),
    ],
    max_timeout_sec=60,
    timeout_strategy="hold",
    fallback_value={"action": "hold", "reason": "master_controller_timeout"},
    circuit_breaker=CircuitBreakerConfig(failure_threshold=3, recovery_timeout_sec=120),
    dependencies=["market_data", "factor_engine", "mt_orchestrator", "intel_signal"],
    tags=["decision", "llm", "core"],
)

TRADE_EXECUTION_CARD = AgentCard(
    agent_id="trade_execution",
    display_name="交易执行 Agent",
    description="下单/平仓/减仓执行 (带幂等性)",
    llm_level=LLMLevel.NONE,
    capabilities=[
        Capability(name="place_order", description="下单执行", cost_estimate_ms=3000,
                   is_destructive=True, is_idempotent=True),
        Capability(name="close_position", description="平仓执行", cost_estimate_ms=3000,
                   is_destructive=True, is_idempotent=True),
    ],
    max_timeout_sec=10,
    timeout_strategy="retry",
    fallback_value=None,
    max_retries=1,
    circuit_breaker=CircuitBreakerConfig(failure_threshold=3, recovery_timeout_sec=120),
    tags=["execution", "order"],
)

SIGNAL_BUS_CARD = AgentCard(
    agent_id="signal_bus",
    display_name="统一信号总线",
    description="聚合因子/情报/确认/融合四源信号，输出统一方向与置信度",
    llm_level=LLMLevel.NONE,
    capabilities=[
        Capability(name="get_unified_signal", description="获取统一融合信号", cost_estimate_ms=5000),
        Capability(name="get_signal_detail", description="获取各信号源详细分解", cost_estimate_ms=8000),
    ],
    max_timeout_sec=10,
    timeout_strategy="cached",
    fallback_value=None,
    circuit_breaker=CircuitBreakerConfig(failure_threshold=5, recovery_timeout_sec=60),
    dependencies=["factor_engine", "intel_signal"],
    tags=["signal", "unified", "core"],
)

# ──────────────────────────────────────────────────────────────
#  Card 注册表 — agent_id → AgentCard
# ──────────────────────────────────────────────────────────────

ALL_CARDS: dict[str, AgentCard] = {
    card.agent_id: card
    for card in [
        GENETIC_OPTIMIZER_CARD,
        MARKET_DATA_CARD,
        FACTOR_ENGINE_CARD,
        INTEL_SIGNAL_CARD,
        RISK_CONTROL_CARD,
        MT_ORCHESTRATOR_CARD,
        MASTER_CONTROLLER_CARD,
        TRADE_EXECUTION_CARD,
        SIGNAL_BUS_CARD,
    ]
}
