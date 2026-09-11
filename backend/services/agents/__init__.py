# -*- coding: utf-8 -*-
"""Agent 群（v3 方向 3，p1-agents-a + p2-agents-b，2026-09-03）。

  base            ObservationAgent 骨架：确定性核心 → 预测落库 → 建议 → 可信度门（不足自动降 observe）
  signal_review   SignalReview：signal_ledger 的命中率 / IC / 半衰期 / regime 分层 → 权重与停用假设
  anomaly         Anomaly：六通道 z-score + CUSUM → stress_score → TradingState 建议（不生效）
  timing          Timing/Regime：L1 计数 + EMA200 + 实现波动 + ADX + 流动性 → 三桶与引擎资本建议
  event_impact    EventImpact：事件冲击显著性跟踪与翻转检测 → 接入 / 下架实验卡
  param_search    ParamSearch：样本外切分 + Bonferroni 校正的参数扫描 → 参数实验卡
  execution_qa    ExecutionQA：滑点 / 拒单率 / 部分成交 / 实际费率体检 → 执行治理实验卡
  jobs            定时任务注册 + 到期评分器注册

统一约定：
  - 每个 Agent 的每条结论都写成**可证伪的预测**进 `agent_predictions`，到期由本包注册的
    评估器用**真实账本/真实价格**打分，形成可信度矩阵（`/api/agents/status`）。
  - 全部 Agent 的 `max_mode = advise`，即任何情况下都不会自己改配置、改 TradingState、改权重；
    advise 模式唯一的副作用是写一张 `proposed` 实验卡，由 `services.experiments` 到期用
    真实账本判定 adopted / rejected / extended。
"""
from backend.services.agents.base import (  # noqa: F401
    MODE_ACT,
    MODE_ADVISE,
    MODE_OBSERVE,
    Advice,
    AgentResult,
    CredibilityGate,
    ObservationAgent,
    Prediction,
    agents_status,
    credibility_of,
    get_agent,
    read_latest,
    register_agent,
    registered_agents,
)
from backend.services.agents.jobs import (  # noqa: F401
    ensure_evaluators,
    ensure_registered,
    register_agent_jobs,
    register_experiment_jobs,
    run_agent,
    run_all_agents,
)

ensure_registered()
