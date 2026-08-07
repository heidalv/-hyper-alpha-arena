# CHANGELOG_RISK_TUNING — v3 整改（2026-04）

> 汇总本轮风控与 AI 学习系统整改的**所有行为变化**。配合
> `.qoder/plans/alpha_arena_系统重构设计_5bcf7d68.plan.md` 阅读。

---

## P0 · 阻断类安全网

| # | 事项 | 文件 | 影响 |
| --- | --- | --- | --- |
| 1 | **Rebound Gate**：减仓后禁止 pyramid / DCA | `services/full_auto_trading_service.py`（4 处入口） + `services/reentry_cooldown.py` | 防"reduce→rebuild"死亡螺旋；冷却由 `NATURE_RULES.reduce_cooldown_hours` 决定 |
| 2 | `_partial_close` / `_execute_defensive_verdicts` 部分减仓后同步 `record_partial_close` | FA L≈6533 | rebound gate 能覆盖防守减仓 |
| 3 | **删除 `exposure_gate_block`** 段，改由 `deterministic_risk_gate` Rule2 统一判定 | FA L4624 | 行为更严格且一致：新单 margin 预估加入判定 |
| 4 | `strategy_hypothesis_engine` `StrategyTemplate(genome=...)` → `strategy_config=...` | `services/strategy_hypothesis_engine.py` | 修复 promote 失败 |
| 5 | `AIStrategy.timeframe_tier` 必填 + DB `server_default='mid'`；`_CYCLE_SLOT_MAP` 反向分配 | `database/models.py`, `api/ai_strategy_routes.py` | 抑制"100% mid-skew" |

## P1 · 学习闭环实锤

| # | 事项 | 文件 | 影响 |
| --- | --- | --- | --- |
| 1 | `main.py` 挂载 `evolution_routes`、启动注入 `system_coordinator` 到 TDI | `backend/main.py` | Coordinator 真正通电 |
| 2 | `ENABLE_COORDINATOR=true`、`ENABLE_KELLY_POSITION=true` 默认开 | `config/settings.py` | Kelly 上限+协调器默认启用 |
| 3 | `evolution_scheduler._coordinator_check` 消费 `trigger_evolution / trigger_drl_retrain` | `services/evolution_scheduler.py` | 学习→进化反馈落地 |
| 4 | `rl_routes.py` 新增 `/kelly/portfolio`, `/drl/performance`, `/coordinator/status`, `/coordinator/optimize` | `api/rl_routes.py` | 前端 AI Learning Center 真实可用 |
| 5 | `POST /api/rl/train` 改**异步 + task_id 查询** | `api/rl_routes.py` | 训练不再阻塞 API |
| 6 | FA 两处决策出口接 `_apply_tdi_position_advice`（TDI/Kelly/DRL/组合风险） | FA L3521 / L4600 | 仓位真正受 Kelly 夹紧 |
| 7 | 主循环开仓前跑 `PortfolioRiskAggregator.aggregate`（shadow） | FA | 组合相关性风险可观测 |
| 8 | WebSocket `subscribe_drl/kelly/evolution` 真实 server→client 广播 | `services/ws_broadcast.py`, `api/ws.py` | UI 实时刷新 |
| 9 | `parent_strategy_id` + `lineage_generation` 落库；evolution 传 `seed_genome` | `database/models.py`, `api/ai_strategy_routes.py`, `services/evolution_scheduler.py` | 血统可追溯、紧急进化收敛更快 |
| 10 | `TIER_DIVERSITY_QUOTA` + `/api/ai-strategies/stats/tier-distribution` | `api/ai_strategy_routes.py` | tier 分布可视化监控 |

## P2 · 协调器补全与观测

| # | 事项 | 文件 | 影响 |
| --- | --- | --- | --- |
| 1 | `SystemCoordinator.arbitrate_conflicts` / `update_kelly_from_outcomes` | `services/rl/system_coordinator.py` | 冲突仲裁 + Kelly 后验更新 |
| 2 | `_safe_modify_genome` 增加 PostgreSQL `with_for_update` 行级锁 | `services/unified_learning_service.py` | 并发写入 genome 冲突修复 |
| 3 | `StrategyEvolver` 走 `StrategyParamsRegistry.apply_genome` 统一写入口 | `services/strategy_evolver.py`, `services/strategy_params_registry.py` | 统一 sync / leverage guard / rollback |
| 4 | AI Learning Center 子组件拆成独立文件 | `frontend/.../ai-learning/*.tsx` | `SystemCoordinationBanner / MultiSymbolKellyTable / DRLPerformanceChart / CorrelationMatrixView` |
| 5 | 防守横幅增退出信息（当前/进入/退出阈值 + 进度条） | `frontend/.../atas-v2/FullAutoPanel.tsx` | "还差多少回撤才退出防守"一目了然 |
| 6 | 新增 `TierDistributionCard` + `BlockReportTop3` + `FeatureFlagsPanel` | `frontend/.../ai-learning/*.tsx` | Overview/Feedback tab 可诊断"为何不开单"与配额偏斜 |
| 7 | **BlockReportAggregator**（进程内）+ `/api/system/block-report-top` | `services/block_report_aggregator.py`, `api/system_control_routes.py` | 阻断事件 Top-N 汇聚 |
| 8 | `/api/system/feature-flags` GET/POST | `api/system_control_routes.py` | 不重启进程切换 flag |

## P3 · 后续治理（延期，不在本轮）

- `full_auto_trading_service.py` 物理拆分 5 个子模块；当前用区段注释与函数边界标注模块职责。
- 统一 `RiskPipeline` 协议合并 DRG / rebound / fee / defensive 四层过滤器。

---

## 运行时行为摘要

- **Kelly 作为上限**（`KELLY_AS_UPPER_BOUND=true`）：LLM/Evolution 给的仓位不能超过 Kelly 建议值；低于则按原值。
- **DRL 默认 shadow**：`ENABLE_DRL_INTEGRATION=false` 或 `DRL_SHADOW_MODE=true` 时，DRL 只记录 `expected_action` 与真实 action 差异。
- **Coordinator 冷却**：`trigger_evolution / trigger_drl_retrain` 带冷却防抖，避免震荡。
- **BlockReport**：仅内存统计，进程重启清零；非合规日志，仅运营观测。
