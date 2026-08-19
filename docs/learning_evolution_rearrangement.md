# 智能学习·进化系统 现状审计与系统性重排方案

版本: v1.0 · 2026-08-19 · 只读审计，先设计后执行

## 第一部分 现状审计（代码级证据）

### 1.1 运行中的主循环（backend/services/full_auto/loops/）

| 循环 | 触发 | 现状 |
| --- | --- | --- |
| coordinator_loop | 每 tick | trading_cycle（按 tier）+ maintenance_cycle + **learning_integration（每 tick，coordinator_loop.py:143）** + **MLTO learning tick（每 tick，:147）** |
| scalp_loop | 独立 10s | 短线因子路由 + 执行 |
| midlong_loop | 独立 | 中线因子 + long_trend_v2 每日管理 |
| arbitrage_loop | 独立 | 套利 tick |
| maintenance_loop | 维护 tick | health check + **learning_integration(is_maintenance=True)（maintenance_loop.py:25）** |
| learning_loop | 随上触发 | P0 概念漂移 / P1 已删 / P2 ML激活+晋升门+因子发现+[跨周期挖掘**本轮已删**] / P3 A/B+跨市场迁移 |

### 1.2 进化调度器（evolution_scheduler.register_evolution_tasks）

12+ 个 interval/cron 任务：factor_evolution_scalp_*_daily_evo（04:10/04:20 cron）、evolution_cycle_auto、evolution_daily_wisdom、evolution_cycle_decay、signal_weight_daily_update、scalp_signal_settle(300s)、scalp_meta_train_daily、cycle_experience_distill、hypothesis_scan_6h、cross_exchange_scan_5m、rag_weekly_reindex、midlong_registry_scan(22h)。

### 1.3 OpenCode 依赖面（用户以为已删，实际未删）

| 证据 | 状态 |
| --- | --- |
| .env:236 `OPENCODE_ENABLED=true` + OPENCODE_CLI_PATH | **仍开启** |
| opencode_bridge.py（1800+ 行，LLM CLI 代理） | **仍在** |
| 8 个消费方 | alpha_assistant、causal_discovery、counterfactual_sandbox、cross_market_transfer、hermes_*（4 个引擎）、factor_discovery、opencode_proposal_applier、learning_loop_service |
| 症状 | 跨周期挖掘单次 LLM 深度思考 180s+ → API 假挂（调用本轮已删，桥还在） |

### 1.4 MLTO 遗留（旧 LLM 未清尸）

| 证据 | 状态 |
| --- | --- |
| full_auto/mlto_cycle.py | **文件仍在** |
| coordinator_loop.py:147 `self._run_mlto_learning_tick(session_id)` | **每 tick 仍在调用** |
| .env `MIDLONG_MID_VIA_MLTO=false` | 已禁用但 tick 空转 |

### 1.5 五套「学习」服务并存（职能重叠）

learning_loop(P0-P3) / learning_loop_service / unified_learning_service / strategy_learning_service / learning_core(+qaa_bridge、hermes 四引擎)——晋升、学习、进化、蒸馏、智慧提取互相重叠，没有单一职责边界。

### 1.6 遗留周期文件（未归档）

full_auto/ 下：analyst_system_cycle.py、analyst_system_v3_cycle.py、qaa_legacy_cycle.py、mlto_cycle.py、monolith_replay.py。

### 1.7 项目的「新现实」（重排的目标基线）

1. **核心进化 = 因子管线**：GP/MCTS/LLM 提案/冷池 → 门禁（中性化+DSR/PBO+held-out）→ active → ICIR 组合。已落地并产出 2 个 active。
2. **长线 = 规则化** long_trend_v2（非 LLM）；中线/短线 = 因子路由 + scalp_router。
3. **旧 LLM 学习（thesis/retrospective/OpenCode/MLTO）已不是决策源**——但尸体还在主循环里每 tick 空转。

---

## 第二部分 脱节问题清单

| # | 问题 | 影响 |
| --- | --- | --- |
| G1 | OpenCode 桥 + 8 消费方仍活 | LLM 长阻塞 → API 假挂；用户认知与实际不符 |
| G2 | MLTO tick 每 tick 空转 | 无效开销 + 代码噪音 |
| G3 | 五套学习服务重叠 | 维护成本高、职责不清、难以排障 |
| G4 | 进化调度 12+ 任务与因子管线职能重叠 | old evolution 与 new factor pipeline 双轨并行 |
| G5 | 遗留周期文件未归档 | 死代码风险（import 即坑） |
| G6 | /intelligent-learning 页面展示的与真实系统脱节 | 同「因子系统」旧页面问题 |

---

## 第三部分 系统性重排方案（分阶段，每阶段可回滚）

### R0 OpenCode 全面下线（本轮已动第一刀）
- 已删：learning_loop 的 run_cross_cycle_pattern_mining 线程（API 假挂根因）。
- 待做：`.env OPENCODE_ENABLED=false`；8 个消费方逐一核实是否还有「独立于 OpenCode 的兜底」（opencode_bridge 的 `_enabled()` 守卫已就位，禁用后全部优雅 skip）。
- 风险：alpha_assistant 若真在用，禁用后降级（可在 .env 单独切回）。

### R1 MLTO 清尸
- 删 coordinator_loop.py:147 的 `_run_mlto_learning_tick` 调用；归档 mlto_cycle.py。
- 关联：full_auto_trading_service.py:4109-4112 的 thin shim 一并删。

### R2 学习服务收敛为「一条链」
- 定职责：**进化=因子管线**（evolution/ + factor_engine）+ **晋升=promotion_scan_service** + **复盘=unified_learning_service**。
- 合并/归档：strategy_learning_service 与 learning_loop_service 的晋升/学习重叠部分；hermes 四引擎若无常驻消费方→归档。

### R3 进化调度收敛
- evolution_scheduler 的 12+ 任务按「是否仍在产出」分组：factor_evolution_*（保留，与因子管线对齐）、distill/wisdom/meta_train（评估后合并或归档）、cross_exchange_scan/rag_reindex（独立保留）。
- 目标：调度器任务数 ≤ 8，且每个任务有明确 owner。

### R4 遗留文件归档
- analyst_system_cycle(_v3)、qaa_legacy_cycle、mlto_cycle、monolith_replay → 移至 _archive/（参照 factor_engine/DEPRECATED.md 模式，先 grep 确认 0 import）。

### R5 /intelligent-learning 页面重排（同因子系统页面的做法）
- 学习：盘点该页 API（intelligent_learning_routes）与实际数据源；设计新页面结构（只显示真实活着的链路：因子进化、晋升门、复盘、QAA v3 状态）；删除 OpenCode/MLTO/legacy 展示。
- 先设计后实现。

---

## 第四部分 执行计划

| 阶段 | 内容 | 风险 | 验收 |
| --- | --- | --- | --- |
| M0 | R0 收尾（OPENCODE_ENABLED=false + 消费方核实） | 低（守卫已就位） | 日志无 OpenCode 调用 |
| M1 | R1 MLTO 清尸 | 低 | grep 无 run_mlto_learning_tick 活引用 |
| M2 | R4 遗留文件归档（先 grep 0 import） | 低 | 归档文件 0 引用 |
| M3 | R2 学习服务收敛（设计优先，逐项确认后删） | 中 | 单一职责文档 |
| M4 | R3 调度收敛 | 中 | 任务数 ≤8 + owner 表 |
| M5 | R5 /intelligent-learning 页面重排（学习→设计→实现） | 中 | 页面与真实链路一致 |

**回滚原则**：每阶段独立可回滚；删除类一律先归档（_archive/ 目录）不物理删除；env 开关优先于代码删除。

---

## 第五部分 待你拍板的决策点

1. **alpha_assistant 是否还在用**？若在，R0 只关「跨周期挖掘+计划分析」子集，保留助手对话。
2. **hermes 四引擎 + QAA**：保留还是归档？（它们是否仍是 /intelligent-learning 的展示源？）
3. **执行节奏**：M0~M2 低风险可立即做；M3~M5 建议按「学习→设计→实现」逐个过。
4. **重排文档**：本文档后续并入 docs/ 体系，是否按 v1.0 定稿？