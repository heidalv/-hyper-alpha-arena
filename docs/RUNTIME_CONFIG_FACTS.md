# 运行时配置事实清单（RUNTIME_CONFIG FACTS）

> 单一事实来源：本表登记「关键配置开关的声明意图与期望值」。
> 校验脚本：`scripts/check_config_drift.py`（有漂移时退出码 1）。
> 变更纪律：改 `.env` 必须同步本表，同一 commit 提交；提交前跑校验脚本确认 0 漂移。
> 首次生成：2026-08-13，期望值以当时 `.env` 实况为准；备注中 `⚠ 待确认` 表示
> 实况与历史 README/文档声明不一致，需维护者确认「以哪边为准」后消除该标记。

| 配置键 | 声明意图 | 期望值 | 备注 |
| --- | --- | --- | --- |
| MARKET_DATA_DC_ONLY | 行情唯一来源=数据中心落库，禁直连交易所 | true | README §数据中心唯一数据源 |
| MARKET_DATA_VERIFIER_ENABLED | 行情数据校验器 | true | 安全关键 flag |
| V5_DECISION_CORE_ENABLED | V5 决策核心总闸；Live 下为 false 启动即抛错 | true | README §V5 |
| V5_DAILY_TRADE_CAP_ENABLED | 日交易笔数硬约束 | true | 安全关键 flag |
| V5_MAX_DAILY_TRADES_LIVE | 实盘日开仓上限 | 12 | README §V5 频率治理 |
| V5_MAX_DAILY_TRADES_PAPER | 模拟盘日开仓上限（刻意高配额攒样本） | 60 | 同上 |
| V5_MIN_RISK_REWARD | 中长线一体盈亏比硬约束 | 1.8 | 同上 |
| V5_SCALP_MIN_RR | 短线 Live 盈亏比下限 | 1.4 | 同上 |
| V5_TREND_MIN_RR | 中长线 Live 盈亏比下限 | 1.8 | 同上 |
| ENABLE_KELLY_POSITION | Kelly 仓位上限夹紧 | true | ARCHITECTURE §5 |
| ENABLE_PORTFOLIO_RISK | 组合风险聚合（PortfolioRiskAggregator） | true | 2026-08-13 用户确认实况即意图（架构文档 v3 旧描述待同步） |
| ENABLE_COORDINATOR | SystemCoordinator 自动触发进化/重训仲裁 | true | ARCHITECTURE §5 |
| ENABLE_DRL_INTEGRATION | DRL 进入主循环（已下线，恒 false） | false | README §学习进化系统 |
| DRL_SHADOW_MODE | DRL 影子预测记录（无执行权） | true | 2026-08-13 用户确认：有意保持开启（README 旧文案「已关闭」已修正） |
| DRL_RETRAIN_AUTO | DRL 自动重训 | true | 2026-08-13 用户确认：保持实况 true |
| PROMPT_EVOLUTION_ENABLED | Prompt 自动进化 | true | 2026-08-13 用户确认：有意开启（README 旧文案「默认 false」已修正） |
| PROMPT_TRAINING_AB_ENABLED | Prompt 训练 A/B | false | |
| MIDLONG_EXEC_AUTHORITY | 中长线执行权归属（trend/mlto） | mlto | 2026-08-13 用户确认：实况 mlto 即意图（README 旧文案「默认 trend」已修正） |
| MIDLONG_MLTO_CONTROLS_EXEC | MLTO 控制中长线执行 | true | 与上一条一致 |
| MIDLONG_POSITION_MGMT_ENABLED | Phase 5 持仓发展分析（模式 B） | true | README §Phase 5 |
| MIDLONG_DIRECTION_CONSISTENCY_ENABLED | 中长线方向一致性门 | false | |
| MULTI_VENUE_FUNDING_COLLECTOR_ENABLED | 多场所资金费采集（Binance/Bybit/OKX/Gate/Asterdex） | true | 2026-08-13 用户确认：实况 true 即意图（README 旧文案「默认 false」已修正） |
| CYCLE_PROB_GATE_ENABLED | 周期方向概率门禁（校准达标才硬拦截） | false | README §周期方向概率引擎 |
| ARBITRAGE_ENABLED | V3 统计套利总开关 | false | README §套利开关语义 |
| RISK_ENGINE_ENABLED | 风险引擎主开关 | true | 安全关键 flag |
| LIVE_SCALP_VETO_FAIL_OPEN | Live 短线否决层 fail-open（禁止） | false | 三周期整改：Live 下必须 fail-closed |
| LIVE_ORCHESTRATOR_HARD_GATE | Live 编排器硬门禁 | true | |
| LIVE_DIRECTION_COHERENCE_MODE | Live 方向一致性模式 | enforce | |
| LEGACY_RISK_HARD_ROLLBACK | 旧风控硬回滚 | false | |
| CONSECUTIVE_LOSS_PROTECTION_ENABLED | 连续亏损保护 | false | |
| SCALP_DAILY_OPEN_CAP | 短线日开仓配额 | 150 | README §V5 频率治理 |
| TREND_DAILY_OPEN_CAP | 中长线日开仓配额 | 15 | 同上 |
| SCALP_EV_GATE_ENABLED | 短线 EV 门禁 | false | |
| SCALP_EV_FAIL_CLOSED_LIVE | 短线 EV Live fail-closed | true | |
| SCALP_MTF_RESONANCE_ENABLED | 短线 MTF 共振 | false | |
| DATA_CENTER_MODE | 数据中心运行模式 | standalone | README §独立数据中心模块 |
| KLINE_DEPTH_BACKFILL_ENABLED | K 线深度回填 | true | README §数据补齐 |
| KLINE_QUALITY_REPAIR_ENABLED | K 线质量修复 | true | |
| QAA_V3_ENABLED | QAA V3 调度框架 | true | |
| QAA_SCHEDULER_ENABLED | QAA 调度器 | true | |
| QAA_FULLAUTO_SCHEDULE_ENABLED | QAA 全自动调度 | true | |
| QAA_REBATE_SCHEDULE_ENABLED | QAA 套利调度 | true | |
| LLM_ANALYSIS_FORCE_STREAM | LLM 分析强制流式 | true | |
| OPENCODE_ENABLED | OpenCode 侧车 | true | |
| ONCHAIN_DATA_ENABLED | 链上数据采集 | false | |
| HERMES_L2_AB_ENABLED | Hermes L2 A/B | false | |
| PAIR_BINDING_LANE_ENABLED | 交易对绑定车道 | false | |
| AI_AB_FRAMEWORK_ENABLED | AI A/B 框架 | false | |
| TIER_DAILY_LOSS_BUDGET_PCT_SHORT | P0-E 短线周期独立日亏预算（权益%，0=禁用） | 2.0 | 只冻本周期新开仓，绝不跨周期 |
| TIER_DAILY_LOSS_BUDGET_PCT_MID | P0-E 中线周期独立日亏预算（权益%，0=禁用） | 2.0 | 只冻本周期新开仓，绝不跨周期 |
| TIER_DAILY_LOSS_BUDGET_PCT_LONG | P0-E 长线周期独立日亏预算（权益%，0=禁用） | 3.0 | 只冻本周期新开仓，绝不跨周期 |
