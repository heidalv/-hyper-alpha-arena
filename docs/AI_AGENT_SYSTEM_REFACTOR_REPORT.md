# AI 策略 Agent 系统深度审查与升级 — 交付报告

## 1. 决策入口与调用路径
# AI 决策入口与调用路径

## 当前激活入口
- **统一循环 · QAA v3** (`unified_loop_qaa_v3`)
  - 触发: QAA_MODE=qaa 且 QAA_V3_ENABLED=true
  - 调用: `full_auto_trading_service._run_unified_loop → _run_qaa_v3_tick → _run_analyst_system_v3`
  - ⚠️ 旁路风险: QAA 未就绪时回退 _run_trading_cycle（与 ai_first 相同）
- **持仓时限复审** (`hold_timeout_review`)
  - 触发: 持仓到达 tier 复审点，独立于主 tick
  - 调用: `full_auto_trading_service._run_hold_timeout_ai_review_if_needed`

## 标准执行链路（冻结）
```
MarketData → Direction(Master/Dual) → PositionSizingAgent → RiskGate → ExecutionEngine → DecisionRetrospective → PromptFeedback
```

## 全部注册入口
### 统一循环 · ai_first [✅ 默认激活]
- ID: `unified_loop_ai_first`
- 模块: `full_auto_trading_service._run_unified_loop → _run_trading_cycle`
- 触发: FULLAUTO_FLOW_MODE=ai_first（默认），每 90s tick
- 下游:
  - _collect_market_snapshot
  - _run_analyst_system
  - _run_analyst_system_unified
  - analyst_system.run_full_analysis
  - DualAgentCoordinator.coordinate | MasterController.synthesize
  - _execute_master_decisions
  - PositionSizingAgent.build_plan
  - UnifiedRiskGate / master_close_guard
  - paper_trading_engine
- 备注: 生产默认主路径

### 统一循环 · QAA v3 [⏸ 条件激活]
- ID: `unified_loop_qaa_v3`
- 模块: `full_auto_trading_service._run_unified_loop → _run_qaa_v3_tick → _run_analyst_system_v3`
- 触发: QAA_MODE=qaa 且 QAA_V3_ENABLED=true
- 下游:
  - QAAContext TickOrchestrator
  - _run_analyst_system_v3
  - analyst_system.run_full_analysis
  - _execute_master_decisions
  - PositionSizingAgent.build_plan
  - paper_trading_engine
- 旁路风险:
  - QAA 未就绪时回退 _run_trading_cycle（与 ai_first 相同）
- 备注: v3 路径用短生命周期 DB session，LLM 阶段不持锁

### 统一循环 · QAA legacy [⏸ 条件激活]
- ID: `unified_loop_qaa_legacy`
- 模块: `full_auto_trading_service._run_unified_loop → _run_qaa_tick`
- 触发: QAA_MODE=qaa 且 QAA_V3_ENABLED=false
- 下游:
  - event_bus 多 Agent handler
  - _qaa_master_controller（规则化快评）
  - _run_analyst_system（hybrid 兜底 LLM）
  - _execute_master_decisions
- 旁路风险:
  - 快 tick 可能先走规则化 _qaa_master_controller，再 hybrid 调 LLM

### 三 tier 并行（legacy） [⏸ 条件激活]
- ID: `analyst_legacy_tier_parallel`
- 模块: `full_auto_trading_service._run_analyst_system → tier_executor.execute_parallel_tiers`
- 触发: FULLAUTO_AI_UNIFIED_ANALYSIS=false
- 下游:
  - TierParallelExecutor ×3（每 tier 独立全套分析师+LLM）
  - multi_timeframe_orchestrator 协调
  - _execute_master_decisions
- 旁路风险:
  - 每 tier 独立 LLM，决策可能冲突
  - historically 硬编码 leverage=15（已改为动态）
- 备注: 降级时仍走 _run_analyst_system_unified

### 分析师异常回退 [⏸ 条件激活]
- ID: `analyst_fallback_legacy_execute`
- 模块: `full_auto_trading_service._run_analyst_system_unified except → _execute_ai_decisions`
- 触发: FULLAUTO_ANALYST_FALLBACK=legacy 且分析师抛异常
- 下游:
  - call_ai_for_decision（逐策略 LLM）
  - PositionSizingAgent.build_plan
  - paper_trading_engine
- 旁路风险:
  - 绕过 MasterController 与五路分析师综合

### 持仓时限复审 [✅ 默认激活]
- ID: `hold_timeout_review`
- 模块: `full_auto_trading_service._run_hold_timeout_ai_review_if_needed`
- 触发: 持仓到达 tier 复审点，独立于主 tick
- 下游:
  - analyst_system.run_full_analysis
  - _execute_master_decisions
- 备注: 仅管理已有仓，不开新方向研究

### DualAgent 灰度 [⏸ 条件激活]
- ID: `dual_agent_shadow`
- 模块: `dual_agent_coordinator.coordinate`
- 触发: DUAL_AGENT_MODE=shadow|advisory|primary
- 下游:
  - DirectionAgent.decide
  - TradeRiskAgent.review
  - MasterController.synthesize（shadow/advisory 时并行）
- 备注: 默认 off，primary 时由 Direction+Risk 接管方向/退出


## 2. MasterController 提示词审计
# MasterController 提示词审计

## 逻辑块拆解
### 角色定义 (`role_cto`)
- 职责域: **direction**
- 摘要: CTO 综合五路分析师，审核策略库模板信号

### Tier 专属上下文 (`tier_context`)
- 职责域: **entry**
- 摘要: 空 tier 积极评估进场；满足 2/3 门槛即可 buy/sell
- 关联冲突: C01, C02

### 开仓硬性前提 (`entry_hard_gate`)
- 职责域: **entry**
- 摘要: 置信度≥entry_gate、辩论占优、波动可交易、风控<75
- 关联冲突: C01, C03

### 置信度校准指南 (`confidence_calibration`)
- 职责域: **direction**
- 摘要: K线基础分+加减分；≥50% 可开仓
- 关联冲突: C01, C03

### 模板信号采信门槛 (`template_signal_gate`)
- 职责域: **entry**
- 摘要: <30% 无效；30-45% 仅供参考；≥45% 可采信

### reduce/close 铁律 (`close_iron_rules`)
- 职责域: **exit**
- 摘要: 有 SL 禁止 close；浮亏 hold；AI 主动平仓胜率极低
- 关联冲突: C04, C05

### 动态 TP/SL 管理 (`tp_sl_management`)
- 职责域: **position_mgmt**
- 摘要: 锁利推进、追踪止盈、partial_close 配合 reduce
- 关联冲突: C04

### 持仓时限复审 (`hold_timeout`)
- 职责域: **exit**
- 摘要: 到点必须决策 extend/close/reduce，禁止无限续命
- 关联冲突: C05

### 杠杆与仓位 (`leverage_sizing`)
- 职责域: **sizing**
- 摘要: LLM 输出 leverage 5-20、position_pct 0.04-0.35
- 关联冲突: C06

### 动态交易性质表 (`trade_nature_table`)
- 职责域: **direction**
- 摘要: scalp/intraday/swing/position/trend_follow 选型
- 关联冲突: C02, C07

### P2-3 历史教训 (`recent_lessons`)
- 职责域: **memory**
- 摘要: 从 strategy_memories.key_lessons 注入，loss_analysis 需三思

### 防守模式约束 (`defensive_mode`)
- 职责域: **risk**
- 摘要: 禁止开新仓，优先 hold/adjust_sl

## 冲突规则清单
### 🔴 C01 [high]
- 涉及块: tier_context, entry_hard_gate, confidence_calibration
- 规则 A: tier 铁律：空 tier 满足 2/3 门槛即积极 buy/sell；预筛选通过需更强理由才能拒绝
- 规则 B: 开仓硬性前提：置信度<entry_gate 禁止 buy/sell；辩论 1:1 互锁→hold
- **建议统一**: 统一为证据评分制：每项证据 +N 分，总分≥阈值才开仓；预筛选通过 +10 但不单独放行

### 🔴 C02 [high]
- 涉及块: tier_context, trade_nature_table
- 规则 A: short tier 默认 intraday/scalp，目标捕捉短线机会
- 规则 B: 真实数据：short/intraday/scalp 累计亏损，long/trend_follow 贡献主要盈利
- **建议统一**: short/scalp/intraday 默认门槛 +8%；连续同向短线开仓冷却 30min；tier 预算降至 8%

### 🟠 C03 [medium]
- 涉及块: confidence_calibration, entry_hard_gate
- 规则 A: 校准指南：调整后≥50% 即可 buy/sell
- 规则 B: 硬性前提：本 tier 置信度≥entry_gate（通常 50-58%）
- **建议统一**: 删除「50% 即可开仓」表述；统一引用 entry_gate 且 scalp 额外 +8%

### 🔴 C04 [high]
- 涉及块: close_iron_rules, tp_sl_management
- 规则 A: 铁律 0：有 SL 禁止 close；浮亏一律 hold
- 规则 B: TP/SL 管理：量能萎缩/RSI 超买时可 reduce + partial_close
- **建议统一**: reduce 需双周期反转确认；有 SL 时仅允许 adjust_sl，禁止 close/reduce

### 🟠 C05 [medium]
- 涉及块: close_iron_rules, hold_timeout
- 规则 A: 有 SL 的仓位永不 close
- 规则 B: 持仓超时必须 extend 或 close/reduce，已复审≥2 轮优先 close
- **建议统一**: 超时复审：趋势同向→extend+adjust_sl；结构反转→reduce；仅无 SL 且深亏才 close

### 🔴 C06 [high]
- 涉及块: leverage_sizing, tier_context
- 规则 A: LLM 自由填写 leverage/position_pct，作为最终成交依据
- 规则 B: 架构目标：PositionSizingAgent 为唯一 sizing 源，tier 预算有硬上限
- **建议统一**: LLM 仅输出 sizing_hint（可选）；执行层强制经 PositionSizingAgent 重算并 respect_sizing_plan

### 🟠 C07 [medium]
- 涉及块: trade_nature_table, tier_context
- 规则 A: 动态选择 trade_nature，不束缚于固定短中长线
- 规则 B: tier 约束：short 只能 scalp/intraday
- **建议统一**: tier 为硬约束上限；nature 在 tier 允许范围内按证据选择

## 推荐拆分方案
| 新 Prompt 层 | 职责 | 禁止事项 |
|---|---|---|
| DirectionPrompt | 方向、trade_nature、置信度 | 不输出金额/杠杆 |
| PositionMgmtPrompt | hold/pyramid/dca/adjust_tp/sl | 不决定新开仓方向 |
| RiskReviewPrompt | 审核/拒绝/缩仓/降杠杆 | 不放大仓位 |
| SizingHintPrompt | 可选风险偏好提示 | 不直接填 position_pct |
| SummaryPrompt | overall_assessment + risk_level | 不覆盖前述决策 |

## 3. 交易盈亏归因
# 交易盈亏归因报告

- 生成时间: 2026-06-10T13:53:04.777386+00:00
- 已平仓: 17 笔
- 总盈亏: -4439.79 USDT
- 胜率: 58.8%

## 关键洞察
- 最大亏损来源 close_reason=max_hold_timeout: 6 笔累计 -3906 USDT
- 最赚钱退出 close_reason=breakeven_tp: 胜率 100%，累计 +345
- tier=mid 累计亏损 -3648（7 笔，胜率 86%）
- tier=long 累计亏损 -496（2 笔，胜率 50%）
- tier=short 累计亏损 -295（8 笔，胜率 38%）
- nature=swing 累计亏损 -3648（7 笔，胜率 86%）
- nature=trend_follow 累计亏损 -496（2 笔，胜率 50%）
- nature=scalp 累计亏损 -295（8 笔，胜率 38%）
- symbol=VIRTUAL 拖累最大: -9388，建议单独风控
- 建议：提高 short/intraday/scalp 开仓门槛 +8%，限制连续同向短线开仓
- 止损出场累计 -501：检查 SL 距离是否过紧或入场时机

## 按平仓原因
| 维度 | 笔数 | 胜率 | 累计盈亏 | 均笔 |
|---|---:|---:|---:|---:|
| max_hold_timeout | 6 | 67% | -3905.81 | -650.97 |
| sl | 2 | 50% | -500.91 | -250.46 |
| ai_reverse | 3 | 0% | -363.05 | -121.02 |
| profit_drawdown_full | 1 | 0% | -14.77 | -14.77 |
| breakeven_tp | 5 | 100% | +344.75 | +68.95 |

## 按 timeframe_tier
| 维度 | 笔数 | 胜率 | 累计盈亏 | 均笔 |
|---|---:|---:|---:|---:|
| mid | 7 | 86% | -3648.12 | -521.16 |
| long | 2 | 50% | -496.49 | -248.24 |
| short | 8 | 38% | -295.18 | -36.90 |

## 按 trade_nature
| 维度 | 笔数 | 胜率 | 累计盈亏 | 均笔 |
|---|---:|---:|---:|---:|
| swing | 7 | 86% | -3648.12 | -521.16 |
| trend_follow | 2 | 50% | -496.49 | -248.24 |
| scalp | 8 | 38% | -295.18 | -36.90 |

## 按 symbol（全部）
| 维度 | 笔数 | 胜率 | 累计盈亏 | 均笔 |
|---|---:|---:|---:|---:|
| VIRTUAL | 3 | 33% | -9388.41 | -3129.47 |
| BNB | 2 | 0% | -358.77 | -179.39 |
| BTC | 3 | 33% | +10.48 | +3.49 |
| SOL | 1 | 100% | +14.38 | +14.38 |
| XPL | 4 | 75% | +302.73 | +75.68 |
| ASTER | 2 | 100% | +1994.92 | +997.46 |
| ETH | 2 | 100% | +2984.89 | +1492.44 |


## 4. Agent 职责边界
# Agent 职责边界契约

## DIRECTION
- 可决定: 方向, trade_nature, 置信度, 入场理由, 预期持仓时长
- 禁止: 决定 leverage/position_pct, 主动 close 有 SL 的仓位, 放大仓位
- 输出字段: `symbol, action, confidence, reasoning, trade_nature, expected_hold_hours, stop_loss_pct, take_profit_pct, risk_reward_ratio`
- 备注: DirectionAgent / MasterController 方向段

## SIZING
- 可决定: leverage, position_pct, notional_usd, margin_usd, max_loss_usd
- 禁止: 改变方向, 放大超过风险预算, 绕过 tier cap
- 输出字段: `leverage, position_pct, _sizing_notional_usd, _sizing_margin_usd, _sizing_max_loss_usd, _sizing_source, _respect_sizing_plan`
- 备注: PositionSizingAgent 为唯一 sizing 源

## RISK
- 可决定: 拒绝开仓, 降杠杆, 缩仓, 收紧 SL, 限时 extend
- 禁止: 放大仓位, 提高 leverage, 增加 position_pct
- 输出字段: `action(override), size_multiplier, leverage_cap, adjust_sl, adjust_tp, partial_close_pct, extend_hold_hours`
- 备注: TradeRiskAgent / UnifiedRiskGate / master_close_guard

## EXECUTION
- 可决定: 订单价格, 数量, TP/SL 落地, 手续费估算
- 禁止: 重新发明策略方向, 独立修改 sizing 比例
- 输出字段: `order_id, fill_price, filled_size, fee`
- 备注: paper_trading_engine / position_memory_manager（保真模式）

## FEEDBACK
- 可决定: 复盘标签, 教训提炼, 策略门槛调整建议
- 禁止: 直接下单, 覆盖当轮决策
- 输出字段: `was_correct, mistake_analysis, lesson_learned, policy_adjustments`
- 备注: DecisionRetrospective → decision_feedback_service → 下轮 prompt

## 标准流水线
```
Direction → Sizing → Risk → Execution → Feedback
```

## 5. 反馈闭环样例
## 🔄 反馈闭环：复盘约束（DecisionRetrospective + 绩效归因）
> 以下约束来自真实平仓复盘，**必须**在开仓前检查。

### 绩效归因摘要
- 最大亏损来源 close_reason=max_hold_timeout: 6 笔累计 -3906 USDT
- 最赚钱退出 close_reason=breakeven_tp: 胜率 100%，累计 +345
- tier=mid 累计亏损 -3648（7 笔，胜率 86%）
- tier=long 累计亏损 -496（2 笔，胜率 50%）
- tier=short 累计亏损 -295（8 笔，胜率 38%）

### 策略门槛调整（硬约束）
🔴 **disable_natures**: [] → **['scalp']** （建议：提高 short/intraday/scalp 开仓门槛 +8%，限制连续同向短线开仓）

**规则**：若 symbol 有 loss_analysis 且 severity≥high，禁止同 nature 同方向重复开仓。