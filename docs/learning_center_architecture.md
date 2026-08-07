# 智能学习中心架构（L1-L5 统一收敛后）

> 本文档描述 2026-06-22 完成的学习中心统一收敛重构（阶段 L1-L5）。
> 目标：消除"假统一"，建立真正的单一入口 + 注册表式后端架构。

## 一、重构前的问题（诊断结论）

经深度调查确认"智能学习中心"**未真正统一**，存在 4 类结构性问题：

| 问题 | 严重度 |
|------|--------|
| 三个并行入口（process_outcome + LearningBus.dispatch + API），靠注释约定拼接 | 高 |
| V1/V2 双轨制（LEARNING_INTEGRATION_V2），V1 代码长期死跑 | 中 |
| process_outcome 360 行胖方法，6 个后端内联 try/except 拼装 | 高 |
| 因子新旧双表（FactorEngine.FACTORS 21 个 vs FactorRegistry 124 个），key 命名不重合 | 高 |

## 二、重构后架构

```
TradeOutcome（实盘/模拟/回测三源统一结构）
        │
        ▼
UnifiedLearningService.process_outcome(db, outcome)   ← 唯一入口
        │
        ├── 9 步 EMA 核心更新（绩效矩阵/记忆/偏离检测/自适应）
        │
        └── BackendRegistry.handle_all(db, outcome)    ← 注册表统一调度
                │
                ├── CausalDiagnosisBackend      (p=50)  亏损根因诊断
                ├── ReflexionBackend            (p=60)  亏损反思（异步）
                ├── PromotionBackend            (p=100) 达标晋升
                ├── TemplateStatsBackend        (p=110) 模板 live stats 回灌
                ├── QaaBackend                  (p=120) QAA 进化
                ├── FactorJointBackend          (p=130) 因子-策略贝叶斯 [开关]
                ├── DriftDetectionBackend       (p=140) 概念漂移 [开关]
                ├── ReviewBackend               (p=200) 定期复盘（计数触发）
                ├── MinerBackend                (p=210) 模式挖掘（计数触发）
                ├── PatternExtractionBackend    (p=220) 成功模板提取
                └── CausalDiscoveryBackend      (p=230) 因果发现 [开关，异步]
```

### 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| `LearningBackend` | `services/learning/backend_base.py` | 抽象基类（含 AsyncBackend/ThresholdBackend） |
| `BackendRegistry` | `services/learning/backend_registry.py` | 单例注册表，handle_all 统一调度 |
| `BackendLoader` | `services/learning/backend_loader.py` | 显式注册 11 个内置后端 |
| `LearningConfig` | `config/learning_config.py` | 统一配置快照（8 个开关集中） |
| 11 个后端 | `services/learning/backends/*.py` | 各后端插件实现 |

### 因子系统并轨

| 层 | 变更 |
|----|------|
| API | `intelligent_learning_routes` + `factor_sync_routes` 改用 `FactorRegistry` |
| 交易主路径 | `full_auto_trading_service._qaa_compute_unified` 改用新注册表 |
| legacy 兼容 | 20 个旧短名因子（rsi/macd/adx...）迁成 `BaseFactor` 子类，注册到新表 |
| funding_rate | 由 sentiment 版（DataFrame 列读取）统一提供，淘汰 legacy market_data 版 |
| 注册表总数 | **124 个因子**（20 legacy + 104 新），无冲突 |

## 三、调用方契约（L2 收敛）

**重构前**：3 个调用方各自手动调 `process_outcome` + `bus.dispatch`，靠注释维护顺序。

**重构后**：调用方只需一行：

```python
unified_learning.process_outcome(db, outcome)
# process_outcome 内部自动 registry.handle_all，无需手动 dispatch
```

3 个调用点：
- `trading_commands.py`（实盘平仓）
- `paper_trading_engine.py`（模拟平仓）
- `rebate_arb/engine.py`（套利平仓）

`LearningBus.dispatch` 保留为 deprecation wrapper（转发到 registry），仅供老代码兼容。

## 四、配置开关（L5 集中化）

| 环境变量 | 默认 | 作用 | 对应后端 |
|----------|------|------|----------|
| `LEARNING_LOOP_ENABLED` | true | 学习闭环总开关 | review/miner/pattern |
| `DRL_RETRAIN_AUTO` | false | DRL 自动重训 | (coordinator) |
| `NSGA2_ENABLED` | true | NSGA-II 多目标进化 | (evolution) |
| `AI_FACTOR_STRATEGY_JOINT_ENABLED` | false | 因子-策略贝叶斯 | FactorJointBackend |
| `AI_CONCEPT_DRIFT_DETECTION_ENABLED` | false | 概念漂移检测 | DriftDetectionBackend |
| `AI_CAUSAL_DISCOVERY_ENABLED` | false | 因果发现 | CausalDiscoveryBackend |

查看运行时状态：`GET /api/learning/dashboard/feature-flags`（含 `_learning_config` + `_backends` 字段）

## 五、新增后端指南

1. 在 `services/learning/backends/` 新建文件，继承 `LearningBackend`（或 `AsyncBackend`/`ThresholdBackend`）
2. 设置 `name`/`priority`，实现 `handle_outcome`
3. 在 `backend_loader.py` 的 `_backend_classes()` 添加该类
4. 无需改动 `process_outcome` —— 注册表自动调度

## 六、已移除的死代码

- `LEARNING_INTEGRATION_V2` 开关及 6 处 V1 分支（强制 V2）
- `_daily_narrative_build` 定时任务（OpenCode regime_journal 接管）
- `process_outcome` 内 6 个内联后端块（迁为插件）
- `LearningBus.dispatch` 内联路由逻辑（迁为插件，保留 wrapper）

## 七、后续待办（不在本次范围）

- `ai_attribution_service` / `autonomous_strategy_service` deprecated stub 仍被多处引用，需专项迁移
- `learning_feedback_layer/feedback_learner.py` 已标 DeprecationWarning，确认无引用后可删目录
- 旧 `FactorEngine.compute_all_factors` 的 16 处调用点仍走旧路径（行为不变，渐进迁移）
