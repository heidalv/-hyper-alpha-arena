# -*- coding: utf-8 -*-
"""双模型深度分析层（v3 方向 2，p0-model-gateway，2026-09-03）。

  model_gateway   ModelGateway：三条传输——MiniMax 直连（Anthropic 兼容）/ GLM 经 OpenCode sidecar /
                  DeepSeek 直连（第三票仲裁）；统一输入（system + context pack）与严格 JSON 输出；
                  盲评 → 结构化比对 → 分歧仲裁 → consensus_score
  quota_guard     QuotaGuard：每模型/每任务类的日预算、5 小时滚动窗、周配额、上下文/输出 token 上限、
                  GLM 峰时（工作日 14:00–18:00）规避；超预算自动降级为单模型或跳过并告警
  context_pack    ContextPack：五层（市场 / 资金面 / 持仓 / 绩效 / 配置）+ 数据截止时间 + hash
  ledgers         四张账本表：analysis_runs / signal_ledger / agent_predictions / experiments
                  + llm_quota_usage；到期自动评分（命中率 / Brier / 超额收益）
  schemas         各任务族的输出 JSON schema（轻量校验，不引第三方依赖）
  scheduling      非峰时调度助手 + Phase0/1 定时任务注册
  tasks           日度简报 / 周度复盘 / 择时 / 事件评估执行器与入账（signal_ledger / experiments）

设计约束：
  - 合规：GLM Coding Plan 只能经 OpenCode/Claude Code 使用 → GLM 传输固定走 sidecar，不做后端直连；
  - 不造数：任何传输失败 → run 记 status=error，consensus 不成立，绝不用占位结论；
  - 可重复：每次 run 落 context_hash + data_cutoff_ms，可用同一 context pack 回放；
  - 单向依赖：只依赖 database.connection / opencode_bridge / llm_config_service / events / ledger，
    不反向依赖策略层。
"""
