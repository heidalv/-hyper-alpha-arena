# AI 自动交易链路审计报告

> 审计时间：2026-09-14 ｜ 范围：`D:\001Alpha\Hyper-Alpha-Arena\backend`（只读审计，未修改任何业务文件）
> 方法：代码静态追踪（read/grep 全链路 caller-callee）+ 配置核对（`.env`）+ 运行态证据（PostgreSQL 只读查询）
> 结论前置：**「AI 自动交易」真实存在且正在跑，但闭环带 13 处断点；"AI 学习/认知层"大部分是空转或 write-only。**

---

## ① 闭环链路图（文字）

**现网真实运行的链路（mid/long 车道，每 45s 一个 tick）：**

```
[K线/市场数据/因子证据]
   │
   ▼
① LLM 主脑 dual_call（双云端模型投票 + 仲裁模型，brain.py:1311）
   │   → mlto_thesis 落库：direction / llm_conviction / recommend_open / sl_pct / tp_pct
   ▼
② maybe_open（brain.py:1652）→ execute_midlong_open（midlong_executor.py:322）
   │   → 白名单/权威/熔断/图审/位置闸/regime/冷却/MTF/组合预算（约 20 道规则门）
   ▼
③ TradeProposal → evaluate_and_execute_proposal（proposal_execution.py:55）
   │   → V5Gate + EV 门（校准胜率 p_win，midlong_ev_gate.py:143-148）
   ▼
④ 下单：paper → paper_engine；live → LiveExecutor().place_order（live_trading.py:661）
   ▼
⑤ 平仓回收（paper_trading_engine.py:2000-2130）
   │   ├→ PositionClosed / final_trade_outcome 事件（含 MFE/MAE）
   │   ├→ unified_learning.process_outcome（strategy_trades / signal_trade_feedback）
   │   ├→ episodic_memory.backfill_outcome ──────────────┐
   │   ├→ calibrator 回填盈亏（score→pnl→PAVA 曲线）──────┤
   │   └→ learning_bridge.record_outcome ─┐               │
   ▼                                       ▼               ▼
⑥ 反馈学习（见下）                    OWM 权重表(写)   下次 LLM prompt 检索"相似情景结局"✅
   ✅ 校准器→EV 门（真实影响下一笔开仓）
   ✅ 连亏自适应 genome 直改/暂停/禁用（unified_learning_service.py:1278-1351）
   ✅ 熔断闸 / 情报引擎权重 / 因子 IC / 因子进化 / Prompt 进化 / 因果约束→prompt
   ❌ OWM（MltoSignalWeight）权重学习：写入活跃，但读取端（load_owm_weights）只挂在
      已下线的 MltoOrchestrator.run_tick 上 → **write-only，从不改变任何决策**
```

**名义存在但未接入的链路（设计文档中的"认知层"）：**
- `decision_hub.fuse_signals`（LLM 权重 0.60 的加权融合 + ai_governed 方向闸）→ 无生产调用方
- QAA 智能体组（rule_router / cards / reflection_engine / legacy tick / v3）→ 默认配置全关
- 观察 Agent 群（anomaly/signal_review/timing/event_impact/param_search/execution_qa）→ 只写预测、只被评分，无决策消费方
- learning_core 统一内核（orchestrator / unified_scheduler / mechanism_router / HPO）→ API 手动实验室
- thesis_shadow / committee_shadow / sizing_overlay / trade_review_officer → 无生产调用或写入目标表不存在

---

## ② 每个环节的实际证据（文件:行号）

### 环节 1：LLM 出信号 —— ✅ 真实且活跃

| 证据 | 位置 |
|---|---|
| midlong 独立循环注册到 APScheduler（45s，`TIER_MID_AI_TICK_SEC=45`） | `full_auto/orchestrator.py:211-231`；启动入口 `startup.py:175 restore_running_sessions()` |
| 循环入口 → 委派 LLM 主脑批次 | `full_auto/loops/midlong_loop.py:20,383` → `full_auto/mlto_cycle.py:216-225(mid), 303-312(long)` |
| 主脑：`run_midlong_brain` → `refresh_thesis` → **真实 LLM 调用** `gw.dual_call(...)` | `mlto/brain.py:1779, 1255, 1311` |
| 双模型投票 + 仲裁（模型网关；deepseek 已于 2026-09-05 退役为应急后备，主票 glm_opencode/minimax，另有本地 ollama qwen3） | `analysis/model_gateway.py:10, 707, 719` |
| LLM 输出直接决定方向/置信度/是否开仓/止损止盈 | `mlto/brain.py:1385, 1437, 1443-1444, 1448` |
| 论题落库 `mlto_thesis`（thesis_store） | `mlto/brain.py:1467`；`mlto/thesis_store.py:97-119` |
| 决策归因落库（decision_source='brain'，含 dir_src=llm_brain） | `mlto/brain.py:1391-1414`；`mlto/hub_decision_log.py:133` |
| 开关默认全开：`MIDLONG_BRAIN_MODE=llm` → `midlong_brain_enabled()`；`MIDLONG_AI_MANDATORY=true` | `config/settings.py:2466-2468, 233-239`；`.env` |
| 无论题禁开：`MIDLONG_NO_THESIS_NO_OPEN=true`（不开仓 = LLM 无票） | `.env`；`config/settings.py:2471-2475` |
| 运行态证据：近 7 天 `ai_decision_logs` 中 decision_source='brain' **1,149 条**；`dir_src=llm_brain` **1,149 条**；全表 47,893 条、最新 2026-09-14 00:01 | PostgreSQL `alpha_analytics.ai_decision_logs` 只读查询 |
| 最新一条主脑决策样本：tier=mid, direction=long, dir_src=llm_brain, recommend_open=true, consensus_score=0.325 | 同上（decision_snapshot JSON） |

### 环节 2：LLM 输出 → 下单执行 —— ✅ 链路存在（paper + live 双通道）

| 证据 | 位置 |
|---|---|
| `maybe_open`：LLM 论题 recommend_open=true → 转 action | `mlto/brain.py:1652-1684, 1851-1859` |
| 唯一中长线开仓 Writer（authority=mlto 门禁 + 熔断 + 图审 + 位置闸 + regime + 共识缩仓） | `full_auto/midlong_executor.py:322-628` |
| 独立路径执行（白名单/周线/MTF/冷却/结构止损/TP clamp/组合预算） | `full_auto/midlong_helpers.py:227-998` |
| 构造 TradeProposal → 评估执行 | `full_auto/midlong_helpers.py:932-975` |
| 提案执行：V5Gate + EV 门 + 预算 + tranche + 决策价一致性 | `full_auto/proposal_execution.py:55-384` |
| 评估入口（按 tier 路由） | `decision_core/execute_proposal.py:33-102` |
| **EV 门使用校准胜率**（feedback 闭环的关键消费点） | `decision_core/pipeline.py:327-328` → `decision_core/midlong_ev_gate.py:143-148` |
| paper 下单 / live 下单分叉 | `full_auto/proposal_execution.py:200-279` |
| **live 真交易所下单**：`execute_live_trade` → `LiveExecutor().place_order(db, _ctx)` | `full_auto/live_trading.py:548, 661` |
| paper 落仓（master 路径） | `full_auto_trading_service.py:4041 paper_engine.place_order` |
| scalp 车道 LLM 触点（当前休眠，见断点 D）：本地 LLM 确认层（qwen3，fail-open） | `full_auto/loops/scalp_loop.py:1887-1922`；`scalp/scalp_llm_confirm.py:1-58` |

### 环节 3：结果回收（trade outcome）→ 写入哪里 —— ✅ 多路写入

| 写入内容 | 位置 |
|---|---|
| 平仓主路径：已实现盈亏落 `paper_positions` + 风控事件 | `paper_trading_engine.py:2000-2024` |
| 来源归因（出场通道熔断滚动窗） | `paper_trading_engine.py:2027-2042`（`source_attribution.attribution.record_close`） |
| 事件溯源：PositionClosed + final_trade_outcome（含 MFE/MAE/funding，供因子进化归因） | `paper_trading_engine.py:2044-2072` |
| **情景记忆结局回填**（下次 LLM prompt 的"相似行情结局"） | `paper_trading_engine.py:2074-2106`（`episodic_memory.backfill_outcome`） |
| 统一学习入口 `UnifiedLearningService.process_outcome` | `paper_trading_engine.py:4239-4327`；`unified_learning_service.py:197` |
| 因子分反馈行回填盈亏（校准器数据源） | `paper_trading_engine.py:4392-4397`（`signal_feedback_tracker.update_trade_pnl`） |
| **MLTO 反馈：OWM 权重 + postmortem 事件** | `unified_learning_service.py:252-257` → `mlto/learning_bridge.py:75-106, 142-184` |
| live 平仓 outcome（**缺 thesis_id/session_id 元数据**） | `trading_commands.py:2175-2203` |

### 环节 4：反馈学习 —— 谁读、是否改变下一次决策（真实闭环 11 条 + 断环 1 条）

**✅ 真实改变下一次决策的机制（有读者、有参数/权重/门限突变）：**

| # | 机制 | 写（file:line） | 读/生效（file:line） |
|---|---|---|---|
| 1 | **情景记忆 → 主脑 prompt**：平仓结局回填 → 下次 dual_call 检索相似情景+巩固教训注入 feed | `paper_trading_engine.py:2096`；`episodic_memory.py:320-389`（每日 05:30 `evolution_scheduler.py:1597-1602`） | `mlto/brain.py:1015-1016, 1063` |
| 2 | **置信度校准器 → EV 门**：开仓记分 → 平仓回填盈亏 → PAVA 曲线 → EV 门 p_win 硬闸 | `proposal_execution.py:288-312`；`confidence_calibrator.py:142, 167` | `midlong_ev_gate.py:143-148`；`pipeline.py:327-328`（`MIDLONG_EV_GATE_ENABLED=true`） |
| 3 | 连亏自适应：**直改策略 genome**（仓位/杠杆/最小间隔）、暂停、永久禁用 | `unified_learning_service.py:1278-1351` | 策略执行读 genome（master 路径） |
| 4 | 熔断闸：short/midlong 周期熔断由每笔 outcome 记账 | `short_tier_entry_gate.py:95-129`；`midlong_circuit_gate.py:612` | 拦截点 `short_tier_entry_gate.py:145-207`；`midlong_circuit_gate.py:495`（被 `midlong_executor.py:444-462` 调用） |
| 5 | 情报引擎权重（每日 05:10 从 signal_trade_feedback 重算） | `evolution_scheduler.py:463-475` → `signal_feedback_tracker.py:325-372` | `intelligence_signal_engine.py:206-213`（被 ai_decision_service/master_execution/paper_engine 使用） |
| 6 | 因子 IC → `data/factor_runtime_weights.json` | `evolution_scheduler.py:484-490`；`factor_ic_evaluator.py:304` | `factor_engine/midlong_regime_weights.py:25-37` 等 |
| 7 | 因子进化/晋升/漂移重置（凌晨 cron） | `main.py:1426-1459`；`factor_evolution_loop.py:1918, 2254-2311, 3468` | `factor_active_set`（base_factors.py:223/299/400 读取） |
| 8 | **Prompt 进化**：每 ~15-20 笔复盘 → 换 `master_prompt_template_id` | `strategy_learning_service.py:1012, 1030-1032`；`unified_learning_service.py:1507-1566` | 下次 LLM 调用即用新模板 |
| 9 | 因果约束 → MasterController prompt 注入 | `causal_feedback.py:31-80`（每小时 `health_check_cycle.py:1262-1271`） | `trading_analysts.py:2353-2366` |
| 10 | 信念环：亏损复盘 → 抬升 `trend_follow.min_score`（runtime_tuning）+ 信念文本入 TrendAgent prompt | `midlong_belief_loop.py:256-269, 412`；调用链 `learning_loop.py:176-179` | `decision_core/unified_gate.py:354-356`；`trend_agent.py:278-279` |
| 11 | 决策反馈（lessons/约束）→ 分析师 prompt + RuntimeGovernor 门限 | `decision_feedback_service.py:32-34, 450-533`（写于 `paper_trading_engine.py:4020-4021`） | `trading_analysts.py:2340-2345`；`trend_agent.py:250-251` |

**❌ 断环（最大）：OWM 在线权重学习 = write-only**

- 写：每笔带 thesis 的平仓 → `_bump_owm` 更新 `mlto_signal_weights.weight`（±5% 基础权重、invalidation −10%）：`learning_bridge.py:142-184`；信念环也写同一表：`midlong_belief_loop.py:208-253`
- 读：`load_owm_weights`（`learning_bridge.py:187-199`）的**唯一调用点是 `mlto/orchestrator.py:43-44`**，而 `MltoOrchestrator.run_tick` 自 2026-09-05 下线、零生产调用方（自证注释：`mlto_cycle.py:992-1014`、`brain.py:1386-1387`、`hub_decision_log.py:71`、`scripts/audit_llm_direction_edge.py:68-70`）
- 连带死路径：`decision_hub.fuse_signals`（OWM 乘子应用点 213-214）无任何生产调用方；`quant_layer._feedback_loop`（165-168）同死
- **运行态证据：`mlto_signal_weights` 表中确有真实行（如 llm/mid 2胜7负 weight=0.945、llm/long 2胜4负 weight=0.61、llm/trend_follow 7胜0负 weight=1.105）——数据在被认真写入，但没有任何代码再读它们影响决策。**

---

## ③ 断点清单（含具体 file:line 证据）

### A. LLM→订单链路内的断点

1. **`decision_hub.fuse_signals` 加权融合整体死路径**（`mlto/decision_hub.py:183`）：ai_governed 0.60 权重、NIBBLE/BUILD 分档、`_derive_direction` 的「LLM 定方向需框架同意」闸（`decision_hub.py:396-419, 422-473`）都只在已下线的 `MltoOrchestrator.run_tick` 里生效。**现网主脑（brain.py:1385）直接采用 LLM 输出的 direction，不经过框架同意闸**——`.env` 里 `MLTO_LLM_DIRECTION_REQUIRE_FW_AGREE=true` 对现网决策无约束力。`hub_mode=ai_governed` 只是写入日志的标签（`master_execution.py:41`、`hub_decision_log.py:197-203`，DB 中 22,819 条），不是真实权重。
2. **OWM 权重学习 write-only**（见环节 4 断环）。
3. **live 平仓缺 thesis 元数据**：`trading_commands.py:2192-2201` 的 TradeOutcome.metadata 无 thesis_id/session_id/memory_event_ids → live 交易永不触发 MLTO OWM、`trade_review_officer.py:66` 也拒绝 live → live 交易的"AI 反馈学习"是空的。
4. **`brain_theses` / `brain_agent_calibration` 表在 alpha_analytics 库不存在**（迁移 add_brain_m0_tables 未执行；DB 实测 UndefinedTable）→ committee_shadow（`committee_shadow.py:115`）与校准表写入静默失败；「LLM 方向命中率滚动校准」至今无载体（代码注释 `hub_decision_log.py:77` 自证"此前 0 行"）。

### B. 定时任务/调度断点

5. **scalp 短线车道整体休眠**：`SCALP_OPEN_DISABLED=true` → scalp 独立循环不注册（`orchestrator.py:108-110`）；`SCALP_FACTOR_INDEPENDENT_SCHEDULER=false`；`SCALP_MASTER_HARD_BLOCK=true`；`SCALP_SHADOW_MODE=true`（shadow 只记不成交）。本地 LLM 确认层（`scalp_loop.py:1887-1922`，`SCALP_LLM_CONFIRM_ENABLED=true`）代码存在但永不触发。
6. **QAA 智能体组整链默认死**：总开关 `QAA_SCHEDULER_ENABLED` 默认 False（`qaa_scheduler.py:26, 29`）+ `.env QAA_FULLAUTO_SCHEDULE_ENABLED=false`；`QAA_MODE=ai_first`、`QAA_V3_ENABLED=false` → `bootstrap_qaa_v3_context` 静默返回（`full_auto_trading_service.py:397-399`）；`_run_analyst_system_v3` 零调用（5158，自注 5155-5156「QAA v3 路由断裂」）；`qaa/reflection_engine.py:26` 自注已弃用。唯一存活：`qaa_evolution_bridge` 优化循环（`startup.py:857-882`，300s）。
7. **观察 Agent 群 = 空转**：6 个 Agent 定时任务注册并运行（`agents/jobs.py:65-71, 232-317`；经 `ops/v3_jobs_ext.py:70`），但输出 `agent_predictions` 只有写入（`analysis/tasks.py:222`）与评分（`analysis/ledgers.py:958-1095`）两端，**无任何决策代码读取预测或建议**（`apply_advice` 仅 `agents/base.py:289` 内部调用）。`agents/midlong/agent.py` 全库零引用（死骨架）。
8. **learning_core 统一内核未接线**：`learning_core/orchestrator.py:103` 单例从未启动；`scheduler_facade.py:79` unified_scheduler 仅 API 手动触发（`api/learning_core_routes.py:237`）；`mechanism_router.py:16` 自注「route() 无生产调用方」；`evolution/hpo_orchestrator.py:47` HPOOrchestrator 零引用；血缘账本 `ledger.record` 仅 API/手动驱动。continual_learning 仅经 `ml/activation_service.py:117` 间接存活。
9. **已注册但永不执行的函数/任务**：`startup.py:1470 schedule_auto_trading()` 零调用；`orchestrator.py:269-283 dispatch_unified/dispatch_scalp/dispatch_midlong/dispatch_maintenance` 零调用（循环走 monolith shim）；`evolution_scheduler.py:1479-1482` weekly_prompt_review 已不再注册；agent_timing 刻意不注册（`agents/jobs.py:268-269`）。
10. **event_bus.start() 重复接线**：`main.py:1338` 与 `main.py:1346` 两次注册。

### C. 静默失败断点（导入失败/异常被吞）

11. **`ops/v3_jobs.py:96-97` 裸 `except ImportError: pass`**：v3_jobs_ext 导入失败 → 全部扩展任务（risk_engine、watchdog、事件采集、analysis、6 个 Agent、OMS、promotion 等）静默不注册，无任何日志。
12. `full_auto/loops/arbitrage_loop.py:63-64, 128-129`：套利编排器/ExecutionAuthority 导入失败静默 return，套利无声停摆。
13. `services/scheduler.py:174, 242`：scheduler 未运行时 add_interval/add_cron 仅 DEBUG 级丢弃（静默丢任务）；213/262 同理。
14. `learning/backends/block_pattern_learning_backend.py:61-78`：写入 StrategyMemory 的 category/content/source 列不存在（`database/models.py:956-981`），构造异常被吞 → 该后端落库必失败且无痕。
15. `learning_registry_bridge.py:12-14`：`except ImportError` 后重试同一导入，失败被 `unified_learning_service.py:362-364` 吞掉 → **14 个学习后端可整体静默跳过**（当前靠 `learning/__init__.py:33` 导出才不炸）。
16. `main.py:553-584`：strategic_analyst/MLTO/CoinSelect ORM 导入失败仅 warning，create_all 静默缺表；`technical_indicators.py:8-14` pandas_ta 缺失 → TA 全静默失效。
17. `qaa_v3_forced_logs.py`：只写 `executed="false"` 的"超时降级决策"日志，不参与决策（唯一调用链 `analyst_system_v3_cycle.py:25, 267` ← 零调用方 `_run_analyst_system_v3`）。

### D. 已废弃/无消费方的数据与模块

18. **`data/prompt_trace.jsonl` 是 write-only 遥测**：写入者 `agent_prompt_service.py:53-54`（经 `trend_agent.py:362, 857`、`swing_agent.py:379`、`qual_layer.py:731`）、`opencode_bridge.py:904-917`、`opencode_proposal_reviewer.py:252-268`；读取函数 `recent_prompt_traces`（`prompt_trace_service.py:59`）**零调用者**。
19. `learning_feedback_layer` 5 个模块中 4 个弃用：`feedback_learner.py:22-27` 自注 DeprecationWarning；get_feedback_learner/trade_reviewer/report_generator 无调用方；仅 auto_optimizer 经 shim 可达（API 驱动）。
20. `thesis_shadow` / `committee_shadow` 只在主脑关闭时运行（`mlto_cycle.py:994-1013`，`midlong_brain_enabled()` 当前恒 true → 休眠）；`sizing_overlay`（U5 仓位官）**零生产调用方**；`trade_review_officer` 只接 paper 且目标表不存在（见 A4）。
21. LearningBus 旧触发层废弃：`learning_bus.py:117-128` dispatch 弃用；`_should_trigger_*/_trigger_*`（199-415）无剩余生产调用方（逻辑已迁 backend 注册表）。
22. `swing_agent` 半废弃：`swing_agent.py:27-34` 自注「analyze 不可再调用」，仅 `is_swing_nature` 路由检测 + 死路径 qual_layer 仍在用。
23. `strategic_analyst` 是活的（4h/1h/24h 定时任务 `startup.py:775-817` → `macro_regime_service.update_from_sources` → 被 `trend_agent.py:260-261` prompt 注入、`agent_fact_guard.py:110-111` 等消费）——不是断点，此处仅澄清其不直接下单。

---

## ④ 运行态证据（PostgreSQL 只读查询，2026-09-14）

| 证据 | 结果 | 含义 |
|---|---|---|
| `alpha_analytics.ai_decision_logs` 行数 / 最新时间 | **47,893 行 / 2026-09-14 00:01:04** | AI 决策日志当天仍在写入 |
| 近 7 天 decision_source 分布 | **brain 1,149 / hub 423 / llm 260** | LLM 主脑是现网决策的主要产出者（hub 为旧编排器+master 标签） |
| decision_snapshot 中 `dir_src=llm_brain` 行数 | **1,149** | LLM 定方向的决策全部带归因 |
| `hub_mode=ai_governed` 标签行数 | 22,819（历史累计） | 标签存在；但见断点 A1——只是标签，权重融合代码是死的 |
| `mlto_thesis` 行数 | 123 | 论题真实落库 |
| `mlto_thesis_events` 中 postmortem 事件 | **65** | 有 65 次"论题绑定仓位平仓"的复盘 → thesis→开仓→平仓 闭环真实发生过 |
| `mlto_signal_weights`（OWM） | ≥10 行，含真实胜负（llm/mid 2胜7负 w=0.945 等） | 反馈数据在写（断点 A2：无人读） |
| `brain_theses` / `brain_agent_calibration` | **表不存在（UndefinedTable）** | 认知层迁移未执行；委员会影子/方向校准无载体 |
| 核心表 RLS | full_auto_sessions / paper_positions / paper_orders 均 `relrowsecurity=True` | 普通用户直连 0 行，无法绕过 system_identity 验证持仓（限制说明） |

---

## ⑤ 对审计问题的逐条回答

### Q1. 是否存在「AI/LLM 出信号→下单执行→结果回收→反馈学习」闭环？
**存在，但只对部分环节闭环。**
- AI 出信号→下单→结果回收：**完整闭环，且在运行**（环节 1-3 全部有生产调用方 + DB 运行态证据）。
- 反馈学习→改变下一次决策：**11 条真实闭环**（环节 4 表 1-11）+ **1 条名义闭环实际断环**（OWM）。
- 设计文档里的"交易大脑认知环"（委员会/影子/元学习/仓位官/校准）大部分未接入现网。

### Q2. 谁在消费 LLM 输出（thesis / prompt_trace.jsonl）？LLM 输出最终会变成订单吗？
- **thesis（mlto_thesis）是真实决策数据**：消费者 = `brain.maybe_open`（→订单，`brain.py:1652`）、`midlong_position_manager`（失效价/持仓管理，`midlong_position_manager.py:242, 290, 1114, 1240`）、`decision_fusion_arbiter`（V2 standdown 否决，当前 `LONG_TREND_V2=0` 半休眠）、`learning_bus` postmortem、`midlong_helpers.py:912`（proposal 绑定 thesis_id）。
- **会变成订单**：`recommend_open=true` + 20 余道门全过 → `paper_engine` 或 `LiveExecutor().place_order`（live_trading.py:661）。
- **prompt_trace.jsonl：无人消费**（断点 D18）——write-only 遥测，不影响任何决策。

### Q3. 结果反馈写到哪里、被谁读取、是否真的改变下一次决策的参数/权重？
- 写入：`paper_positions`、`strategy_trades`、`signal_trade_feedback`、`mlto_signal_weights`、`position_exit_events`、`episodic memory`、`source_attribution` 等（环节 3 表）。
- 真实改变下一次决策：**是**——校准器→EV 门（`midlong_ev_gate.py:143-148`）、连亏 genome 直改（`unified_learning_service.py:1337-1351`）、熔断、情报权重、因子 IC/进化、Prompt 进化、因果约束、情景记忆→LLM prompt（环节 4 表）。
- **唯一反例：OWM（MltoSignalWeight）权重从不被读取**（断点 A2）。

### Q4. 每条链路的中断点？
见 §③ 断点清单 A-D，共 23 项，全部带 file:line。

### Q5. 「AI 自动交易」是真实参与实盘决策，还是后台空转？
**真实参与决策，但"参与"的是 LLM 主脑这一条链，不是文档宣传的"认知架构"。**
- ✅ 真实：mid/long 车道的**开仓方向、置信度、止损止盈全部由 LLM 主脑（双模型共识+仲裁）直接决定**（`brain.py:1385-1448`），经规则/统计门后落 paper/live 订单；近 7 天 1,149 条 LLM 主脑决策、65 次论题平仓复盘、47,893 条决策日志证明其每日在跑。当前配置下 scalp 车道休眠、中长线是唯一活跃的 AI 决策车道。
- ❌ 空转：QAA 智能体组（配置全关）、观察 Agent 群（只写预测无人读）、learning_core 内核（API 实验室）、learning_feedback_layer（4/5 弃用）、OWM 权重学习（写而无人读）、thesis_shadow/committee_shadow/sizing_overlay/trade_review_officer（休眠/无调用/表不存在）、prompt_trace.jsonl（无人读）。
- ⚠️ 净结论：**"AI 出信号→下单"是活的；"交易结果反哺 AI 权重"半死——最强的闭环是校准器/连亏自适应/情景记忆三条，最名不副实的是 OWM 与 ai_governed 加权融合（代码已死，只剩日志标签）。**

---

## ⑥ 最高优先级断点（若要"收拾垃圾"）

1. **OWM 读端修复或删除**：`mlto/orchestrator.py:43-44` 是 `load_owm_weights` 唯一调用点且整体下线——要么把 OWM 权重接进 `brain.refresh_thesis` 的 feed/共识门槛，要么把 `_bump_owm`/`_apply_owm_nudge` 与 `mlto_signal_weights` 整条链标记废弃，停止每笔平仓写一张没人读的表。
2. **decision_hub 死路径清理**：`fuse_signals`/`_derive_direction`/ai_governed 权重/框架同意闸全在死路径上；若要把「LLM 定方向需框架同意」真正用于现网，必须搬进 `brain.py` 的 consensus/refresh 环节（现网 LLM 方向目前**没有任何框架同意闸**）。
3. **`ops/v3_jobs.py:96-97` 裸 `except ImportError: pass`**：改为可见告警——一个导入失败会静默杀掉全部扩展定时任务。
4. **执行 `add_brain_m0_tables` 迁移**（或删除 committee_shadow/trade_review_officer 的写入目标）：当前 `brain_theses`/`brain_agent_calibration` 表不存在，相关"认知层"写入全部静默失败。
5. **live 平仓补 thesis 元数据**（`trading_commands.py:2192-2201`），否则 live 交易永远游离在 AI 反馈体系之外。
6. **scalp/QAA 死配置统一**：`SCALP_OPEN_DISABLED=true` 与一整套 scalp LLM/因子代码并存、QAA 三大开关全关但 12 个模块仍在仓库里——要么删，要么文档标注"休眠车道"。

---

*报告生成：只读审计 + PostgreSQL 只读查询。未修改项目内任何业务文件。*
