# 量化交易框架深度对标与升级方案

> **对标范围**：本系统（Hyper-Alpha-Arena，AI 原生加密永续合约交易）vs **15 个** GitHub 顶级开源量化框架
> **对标对象**（分两类）：
> - **传统/因子量化类**（7 个）：Microsoft Qlib、QUANTAXIS、vnpy/VeighNa、NautilusTrader、Freqtrade、Jesse、Hummingbot
> - **AI/ML/LLM 量化类**（8 个）：**FreqAI**（Freqtrade 的 ML/RL 模块）、**TradingAgents**（LLM 多智能体对冲基金模拟）、**FinRL / FinRL-Meta**（DRL 金融框架）、**FinGPT**（金融基础模型）、**TensorTrade**（RL 组合框架）、**ai-hedge-fund**（LLM 角色智能体）、**WebCryptoAgent**（LLM 反思式加密交易）、**Luo MAS**（LLM 辩论加密组合，arXiv 2501.00826）
> **覆盖维度**：周期交易逻辑 · 策略 · 指标 · 因子调用与因子训练 · 门禁 · 协调机制 · **AI/ML/LLM 训练管线** · **加密原生 alpha 源** · **LLM 工程化（延迟/成本/防幻觉）**
> **文档性质**：代码级、可执行的整改方案（所有断言均有 `文件:行号` 证据，所有业界做法均有具体框架/类名引用）
> **生成日期**：2026-07-09
> **版本**：v3（新增学习进化系统深度对标 + 元学习/演化策略/持续学习/LLM 自我改进/RAG 进阶）

---

## 目录

- [第一部分　执行摘要](#第一部分执行摘要)
- [第二部分　本系统架构速览](#第二部分本系统架构速览)
- [第三部分　传统量化六维度深度对标](#第三部分传统量化六维度深度对标)
  - [3.1 周期交易逻辑](#31-周期交易逻辑)
  - [3.2 策略框架](#32-策略框架)
  - [3.3 指标体系](#33-指标体系)
  - [3.4 因子调用与因子训练](#34-因子调用与因子训练)
  - [3.5 门禁机制](#35-门禁机制)
  - [3.6 协调机制](#36-协调机制)
- [第四部分　AI 量化交易项目深度对标（v2 新增）](#第四部分ai-量化交易项目深度对标新增)
  - [4.1 FreqAI — ML/RL 训练管线的黄金标准](#41-freqai--mlrl-训练管线的黄金标准)
  - [4.2 TradingAgents — LLM 多智能体对冲基金模拟](#42-tradingagents--llm-多智能体对冲基金模拟)
  - [4.3 FinRL / FinRL-Meta — DRL 金融框架](#43-finrl--finrl-meta--drl-金融框架)
  - [4.4 其他 AI 项目：FinGPT / TensorTrade / ai-hedge-fund / WebCryptoAgent](#44-其他-ai-项目fingpt--tensortrade--ai-hedge-fund--webcryptoagent)
  - [4.5 本系统在 AI 量化生态中的定位](#45-本系统在-ai-量化生态中的定位)
- [第五部分　加密原生 alpha 源与 AI 工程化最佳实践（v2 新增）](#第五部分加密原生-alpha-源与-ai-工程化最佳实践新增)
  - [5.1 加密衍生品 alpha（对永续合约系统最高价值）](#51-加密衍生品-alpha对永续合约系统最高价值)
  - [5.2 链上数据与智能资金追踪](#52-链上数据与智能资金追踪)
  - [5.3 LLM 工程化：延迟、成本、防幻觉](#53-llm-工程化延迟成本防幻觉)
  - [5.4 强化学习在加密交易中的现实（成功与失败）](#54-强化学习在加密交易中的现实成功与失败)
- [第六部分　学习进化系统深度对标（v3 新增）](#第六部分学习进化系统深度对标v3-新增)
  - [6.1 本系统学习进化架构全景](#61-本系统学习进化架构全景)
  - [6.2 八维度深度对标](#62-八维度深度对标)
  - [6.3 学习进化能力矩阵](#63-学习进化能力矩阵)
- [第七部分　不足清单（分级）](#第七部分不足清单分级)
- [第八部分　代码级整改方案](#第八部分代码级整改方案)
- [第九部分　优先级路线图](#第九部分优先级路线图)
- [第十部分　实施前提、现状勘误与地基（2026-07-09 核验补充）](#第十部分实施前提现状勘误与地基2026-07-09-核验补充)
- [附录](#附录)

---

## 第一部分　执行摘要

### 一句话结论

> 本系统在**业务门禁深度**、**AI 多智能体决策 + 永续合约聚焦**、**多周期差异化调度**、**进化/学习闭环**、**防幻觉机制（证据链 + fact guard）**五个维度**领先**于全部 15 个对标框架——**没有任何开源项目同时覆盖 LLM 多智能体 + 永续合约 + 三周期 + 进化闭环**；但在**回测方法论的严谨性**、**ML 训练管线工程化**、**因子权重的科学性**、**回测真实性**四个维度**明显落后**于研究级最佳实践。

### 三个关键认知更新（v2 + v3）

**认知更新 1（v2）— LLM vs 规则的实证分界**：一份 **24,000+ 次实验**的实盘加密测试表明，对于**执行门（下单/SL/TP/仓位/逐笔）**，规则系统在**所有配置下**都胜过 LLM。LLM 的真正价值在**模糊、上下文相关、低频**的判断（新闻解读、情绪、regime 分类、多因子综合、"这是黑客攻击吗"）。这**验证了本系统已有的混合架构**（ScalpRouter 纯规则 <100ms vs 中长 SwingAgent/TrendAgent LLM），但也提示**不应将 LLM 引入短 tier 执行门**。

**认知更新 2（v2）— 本系统占据无人区**：15 个对标项目中，**没有一个同时做到**：(a) LLM 多智能体推理 + (b) 监督学习/RL 因子引擎含持续重训 + (c) 多周期（短/中/长）协调 + (d) 显式进化/学习闭环 + (e) 加密永续合约执行。TradingAgents 最近（仅 LLM、仅现货股票），FreqAI 最近（ML 管线强但无 LLM 层）。**本系统最可辩护的技术综合是：FreqAI 的 ML 管线严谨性 + TradingAgents 的 LLM 角色分类（辩论 + 风险门）。**

**认知更新 3（v3）— 学习进化的核心矛盾：数值层成熟，认知层已死**：本系统的**数值层学习（RuntimeGovernor 有界闭环）是生产级成熟的**——多源意图仲裁、TTL、硬夹紧走廊、双向自校正（差则收紧/好则放松）、NSGA-II 多目标进化、8 阶段血缘账本、因子衰减监控、冠军恢复、Granger 因果发现。但**认知层学习（LLM 自我改进）已死**——prompt 自动进化 **36/36 次全部失败**（LLM 返回 None 或 <120 字符），2026-06-11 禁用，改用 `v5_runtime_gates` 数值闭环代替。这意味着系统**无法让 LLM 从经验中学习**——只能调整数值阈值，不能改进推理本身。业界 SOTA（DSPy 编译 / Reflexion verbal RL / Voyager 技能库 / AlphaEvolve 代码演化）提供了**不依赖模板突变**的可行认知层学习路径。此外，QAA RAG 用 **hash 词袋嵌入（非神经）+ O(N) 线性扫描（无 ANN）**，无法语义匹配中文交易术语；RL 无**防遗忘机制（EWC）**。详见[第六部分](#第六部分学习进化系统深度对标v3-新增)。

### 核心发现矩阵

| 维度 | 本系统定位 | 标杆框架 | 差距性质 |
|------|-----------|---------|---------|
| 周期交易逻辑 | 🟢 **领先**（差异化分层调度 15/120/240s） | NautilusTrader（BarAggregator）、Hummingbot（Clock） | P2 增强：缺 volume/tick/Renko 聚合、缺统一时钟抽象 |
| 策略框架 | 🟢 **领先**（AI 多智能体 + MLTO 辩论 + 进化引擎） | Freqtrade（IStrategy）、Jesse（生命周期）、**TradingAgents（bull/bear 辩论）** | P1 债务：缺声明式参数空间、缺结构化辩论（本系统 MLTO 是 quant/qual，非 bull/bear 对抗） |
| 指标体系 | 🟢 **领先**（380+ 因子，10 大领域） | Qlib（Alpha158/360）、vnpy（TA-Lib） | P2 增强：缺 Qlib 表达式引擎 |
| 因子训练 | 🔴 **落后**（手工 regime 权重表 + GA 优化策略参数） | **Qlib（18 模型）、FreqAI（LightGBM/XGBoost/CatBoost/PyTorch/RL + 持续重训 + DataKitchen 上下文）** | **P0 方法论缺陷** |
| ML 训练管线工程化 | 🔴 **落后**（无统一的 train/predict 上下文、无前视防护的滚动窗口切片） | **FreqAI（FreqaiDataKitchen + train_period_days/backtest_period_days/live_retrain_hours 严格滚动）** | **P0 方法论缺陷（v2 新增重点）** |
| 回测真实性 | 🔴 **落后**（无子 K 线分辨率，SL/TP 按收盘价） | Freqtrade（`--timeframe-detail`）、NautilusTrader（纳秒延迟建模） | **P0 方法论缺陷** |
| Walk-Forward | 🔴 **落后**（grid search，单指标，无 PBO/purge-embargo） | 研究级最佳实践（CSCV/PBO/DSR）、**FreqAI 内建滚动窗口** | **P0 方法论缺陷** |
| LLM 多智能体 | 🟢 **领先**（SwingAgent/TrendAgent + 证据链 + fact_guard + MLTO 辩论） | **TradingAgents（bull/bear + 风险角色辩论 + ChromaDB 记忆 + 市场数据验证器）** | 见下：可借鉴 TradingAgents 的结构化对抗辩论 |
| LLM 防幻觉 | 🟢 **领先**（证据链 + fact guard） | **仅 TradingAgents 有明确机制（market_data_validator 确定性覆盖层）**；其余均无 | 本系统已强；可借鉴 TradingAgents 的确定性数据验证覆盖层 |
| 加密永续合约聚焦 | 🟢 **独家**（资金费率/OI/清算/杠杆/保证金全覆盖） | **所有 LLM 系统均仅现货；FinRL 加密也是现货** | 本系统独有差异化 |
| 门禁（业务层） | 🟢 **领先**（V5 门禁链，7+ 门，有界运行时可调，fail-closed） | NautilusTrader（RiskEngine）、TradingAgents（LLM 风险角色） | — |
| 门禁（引擎层） | 🟡 **偏弱**（仅有业务门禁） | NautilusTrader（精度/名义价值/重复成交去重/超额成交保护） | P1 债务 |
| 协调机制 | 🟡 **双轨**（QAA 框架 + 自实现 cards/rule_router） | NautilusTrader（事件溯源）、**TradingAgents（LangGraph StateGraph + checkpointer）** | P1 债务 |
| 加密原生 alpha 源 | 🟡 **部分**（已有 funding/OI/清算因子，但缺链上/期权偏度/智能资金） | Glassnode/Nansen/CoinGlass/Deribit 期权偏度 | P1 债务：缺链上 + 期权偏度 + 社交情绪 NLP（v2 新增） |
| 进化算法 | 🟡 **参数级**（NSGA-II 多目标，从零实现，3 天周期） | **GP（表达式级）/ CMA-ES（连续优化）/ MAP-Elites（多样性冠军库）** | P1 债务：仅演化固定策略骨架的参数，无表达式发现/多样性冠军库（v3 新增） |
| 元学习/分布漂移 | 🔴 **被动**（因子衰减检测，事后） | **DDG-DA / DoubleAdapt（主动预测下期分布预加权）** | P1 债务：被动 vs 主动（v3 新增） |
| 持续学习/防遗忘 | 🔴 **无**（RL 线性 Q，重训会灾难性遗忘） | **EWC（Fisher 惩罚）+ replay buffer** | P0 方法论缺陷（v3 新增） |
| 认知层学习（LLM 自我改进） | 🔴 **已死**（prompt 进化 36/36 失败，已禁用） | **DSPy（编译）/ Reflexion（verbal RL）/ Voyager（技能库）/ AlphaEvolve（代码演化）** | **P0 方法论缺陷（v3 新增重点）** |
| 记忆/RAG 检索质量 | 🔴 **弱**（hash 词袋嵌入非神经 + O(N) 线性扫描 + 无重排序） | **神经嵌入 + 混合检索（BM25+向量）+ ANN（FAISS/HNSW）+ 交叉编码器重排序** | P0 方法论缺陷（v3 新增） |
| 过拟合诊断（演化系统） | 🔴 **无 PBO 累计**（NSGA-II 每代增加 trial N 但 DSR 未跨代累计） | **CPCV + 累积 trial DSR + PBO-aware 血缘账本** | P1 债务（v3 新增） |

### 最需要立即处理的 P0 项

1. **P0-1　Walk-Forward 方法论薄弱** — `walk_forward.py` 是穷举 grid search、单一优化指标、过拟合评分仅为朴素收益差，无 PBO/CSCV、无 purge/embargo、无 DSR。
2. **P0-2　回测无子 K 线分辨率** — `backtest_engine.py` 的 SL/TP 按 K 线**收盘价**触发。Freqtrade `--timeframe-detail 1m` 是标杆。
3. **P0-3　因子权重是手工 regime 权重表** — `factor_weighting.py` 的 6×12 权重是硬编码字面量。Qlib 端到端 ML / FreqAI 的训练管线是标杆。
4. **P0-4　两套滑点模型不一致** — `backtest_engine.py:33`（base 0.0003 线性）vs `cost_model.py:56`（base 0.0005 分级），未对齐。
5. **P0-5（v2 新增）　缺 FreqAI 式训练管线** — 无统一的 train/predict 上下文对象（FreqAI 的 `FreqaiDataKitchen`）、无前视防护的严格滚动窗口切片、无内建持续重训节奏。这是 ML 工程化的核心缺失。
6. **P0-6（v2 新增）　LLM 智能体缺结构化对抗辩论** — 本系统的 MLTO 是 quant/qual 辩论，但非 TradingAgents 式的 bull/bear 对抗 + 独立风险角色层。对抗辩论是廉价且有效的防幻觉手段。
7. **P0-7（v3 新增）　认知层学习已死** — prompt 自动进化 36/36 次全部失败（`strategy_learning_service._evolve_prompt`），LLM 返回 None 或 <120 字符，2026-06-11 禁用。系统只能调数值阈值，无法让 LLM 从经验学习推理。**DSPy（编译而非手写）/ Reflexion / Voyager / AlphaEvolve 是不依赖模板突变的可行路径。**
8. **P0-8（v3 新增）　RAG 检索质量弱** — QAA 用 **hash 词袋嵌入（非神经）+ O(N) 线性扫描（无 ANN）+ 无重排序**，无法语义匹配中文交易术语（`交易教训`/`亏损复盘` 需字面匹配）。混合检索（BM25+向量）+ 交叉编码器重排序可提升 +26-31% NDCG。
9. **P0-9（v3 新增）　无防遗忘机制** — RL 核心是线性 Q-learning，因子学习层重训会**灾难性遗忘**旧 regime 模式。无 EWC（Fisher 信息惩罚）+ replay buffer。

---

## 第二部分　本系统架构速览

> 本节自包含，供不熟悉本系统的读者快速建立认知。所有路径相对 `001Alpha\Hyper-Alpha-Arena\backend\`。

### 2.1 系统定位

**Hyper-Alpha-Arena** 是一个 AI 驱动的**全自动加密货币永续合约交易平台**，核心特征：

- **三周期架构**：短（scalp，5m/15m）、中（swing，1h）、长（trend，4h/1d）三个交易层级独立运行、差异化调度
- **AI 多智能体决策**：LLM 驱动的 SwingAgent / TrendAgent / ScalpRouter，带证据链与反幻觉
- **V5 决策核心**：统一的入场门禁链，合并了历史上 7 个分散的门控，fail-closed 设计
- **因子引擎**：380+ 因子跨 10 大领域（技术/情绪/衍生品/链上/宏观/行为/复合等），含 855 个 AI 生成因子
- **进化/学习闭环**：DecisionRetrospective → 反馈 → v5_runtime_gates.json + NSGA-II 离线进化
- **套利中心**：返佣套利（S3/S8/SDN）、资金费率套利、基差套利（Paper 模式）
- **QAA 框架**：自研的 v3.1.0 多智能体协调框架（agent 注册/路由/事件总线/记忆/RAG）

**技术栈**：Python 3.12 / FastAPI / SQLAlchemy 2.0 / PostgreSQL 15 / ccxt / hyperliquid-python-sdk / ChromaDB + sentence-transformers（RAG）/ React 18 + Vite（前端）/ APScheduler（调度）

### 2.2 核心架构图

```
┌─────────────────────────────────────────────────────────────────┐
│              前端 (React 18 + Vite + Tailwind)                    │
│   Obsidia Core (插件壳) + 42 个功能面板                           │
└──────────────────────────┬──────────────────────────────────────┘
                           │ REST + WebSocket (:5173 ↔ :8000)
┌──────────────────────────┴──────────────────────────────────────┐
│                  后端 (FastAPI, Python 3.12)                      │
│                                                                  │
│   full_auto_trading_service.py  (19,727 行 — 主循环大脑)          │
│         │                                                        │
│         ├─► 三周期调度器 tier_tick_scheduler.py                   │
│         │     短(15s) / 中AI(120s) / 长AI(240s) 差异化节拍        │
│         │                                                        │
│         ├─► 因子引擎 factor_engine/ (380+ 因子)                   │
│         │     FactorService 统一入口(60s TTL缓存)                │
│         │     → FactorRegistry(依赖拓扑排序) → FactorCalculator   │
│         │     → FactorEvaluationPipeline(regime加权×衰减惩罚)     │
│         │                                                        │
│         ├─► 信号融合 signal_engine/                               │
│         │     factor 0.35 + intel 0.30 + confirm 0.20 + 0.15     │
│         │                                                        │
│         ├─► V5 决策核心 decision_core/ (门禁链)                   │
│         │     data_contract → DCP → EV → MonteCarlo → V5Gate     │
│         │                                                        │
│         ├─► 风控 risk_management/ (3层硬限制)                     │
│         │                                                        │
│         ├─► 套利引擎 arbitrage/ + rebate_arb/ (24文件)            │
│         │                                                        │
│         └─► 进化/学习 learning_core/                              │
│               retrospective → NSGA-II → v5_runtime_gates.json    │
└──────────────┬───────────────────────────────┬───────────────────┘
               │                               │
    ┌──────────▼─────────┐          ┌──────────▼──────────────┐
    │  PostgreSQL 15     │          │  交易所 (ccxt/SDK)       │
    │  4库(arena/market/ │          │  Hyperliquid/Binance/   │
    │  analytics/snap)   │          │  OKX/Bybit/Gate/Aster   │
    └────────────────────┘          └─────────────────────────┘
```

### 2.3 数据流（单次 90s tick）

```
交易所(Binance/Bybit/OKX/Gate/Hyperliquid/AsterD + paper)
   │  适配器: services/exchange/*_adapter.py, market_data_adapters/
   ▼
UnifiedDataPool (services/unified_data_pool.py)
   │  进程内实时快照(klines/orderflow/funding/OI)
   ▼
FactorService.compute()  [统一入口, 60s TTL缓存, per-key锁]
   │  → FactorRegistry.resolve_dependencies → FactorCalculator
   │  → 380+ 因子 → FactorEvaluationPipeline(regime加权×衰减惩罚)
   ▼
FactorSignalGenerator → CompositeSignal(direction, strength, confidence)
   │                     + DecisionFusionEngine(融合质量+orchestrator状态)
   ▼
UnifiedSignalBus  [factor 0.35, intel 0.30, confirm 0.20, fusion 0.15]
   ▼
决策核心门禁链  [decision_core/pipeline.py]
   │  data_contract → direction_coherence → EV gate → MonteCarlo →
   │  V5 unified_gate(daily_cap/confidence/RR/regime)
   ▼
RiskController.check_risk()  [账户/策略/交易3层硬限制]
   ▼
交易所 live_executor / paper_executor / adaptive_executor
   │
   ▼
结果 → LearningOrchestrator.emit()  [血缘账本]
   │  → 反馈 → v5_runtime_gates.json(有界运行时调参)
   │  → GeneticOptimizer(每周GA优化策略参数)
   │  → FactorDecayMonitor.record_ic(因子退役)
```

### 2.4 关键文件索引

| 组件 | 关键文件 | 行数 | 说明 |
|------|---------|------|------|
| 主循环 | `services/full_auto_trading_service.py` | 19,727 | 全自动交易大脑（**单体过大，见 P1-7**） |
| 周期调度 | `services/tier_tick_scheduler.py` | — | 分层节拍门控 |
| 时间框架映射 | `config/tier_timeframe_map.py` | — | tier→K线周期单一真相源 |
| 策略协调 | `services/strategy_coordinator.py` | 2,445 | 组件装配执行路径 |
| 多TF编排 | `services/multi_timeframe_orchestrator.py` | 2,368 | 三周期融合决策 |
| 因子基类 | `services/factor_engine/factor_base.py` | 343 | BaseFactor / FactorMetadata |
| 因子注册 | `services/factor_engine/factor_registry.py` | 330 | 依赖拓扑排序 |
| 因子评估 | `services/factor_engine/factor_evaluator.py` | 348 | IC/ICIR/decay/grading |
| 因子加权 | `services/factor_engine/factor_weighting.py` | 612 | **手工 regime 权重表（见 P0-3）** |
| 公式因子 | `services/factor_engine/custom_factor_store.py` | 287 | 受限 eval + candidate→active |
| 决策门面 | `services/decision_core/pipeline.py` | 395 | evaluate_open_decision 入口 |
| V5 门禁 | `services/decision_core/unified_gate.py` | 510 | 统一门禁，有界运行时可调 |
| EV 门禁 | `services/decision_core/midlong_ev_gate.py` | — | 期望值门 |
| 蒙特卡洛门 | `services/decision_core/monte_carlo_gate.py` | — | 尾部风险 |
| 回测引擎 | `services/backtest_engine/backtest_engine.py` | 548 | **无子K线分辨率（见 P0-2）** |
| 回测成本 | `services/backtest_engine/cost_model.py` | 159 | 分级滑点（**与引擎不一致，见 P0-4**） |
| Walk-Forward | `services/backtest_engine/walk_forward.py` | 382 | **方法论薄弱（见 P0-1）** |
| 风控 | `services/risk_management/risk_controller.py` | — | 3 层硬限制 |
| 遗传优化 | `services/genetic_optimizer.py` | — | 纯 Python GA |
| RL 核心 | `services/learning_core/rl_core/` | — | env/policy/replay_buffer/shadow |
| QAA 框架 | `QAA通信协议构架/qaa/` | — | v3.1.0 独立多智能体框架 |
| 交易侧QAA | `services/qaa/cards.py` + `rule_router.py` | — | 8 张交易 Agent Card（**与QAA双轨，见 P1-6**） |

---

## 第三部分　传统量化六维度深度对标

> 本部分对标 **传统/因子量化类** 框架（Qlib、QUANTAXIS、vnpy、NautilusTrader、Freqtrade、Jesse、Hummingbot），聚焦周期/策略/指标/因子/门禁/协调六个工程维度。AI/ML/LLM 专属对标见[第四部分](#第四部分ai-量化交易项目深度对标新增)。

### 3.1 周期交易逻辑

#### 本系统：差异化分层调度

本系统的周期逻辑是其**最强工程化部分**。核心是 `tier_tick_scheduler.py` + `config/tier_timeframe_map.py`：

- **tier → K线周期单一真相源**（`tier_timeframe_map.py`）：
  - `short` → 主 `15m`，确认 `[5m, 1m]`
  - `mid` → 主 `1h`，确认 `[4h, 15m]`
  - `long` → 主 `4h`，确认 `[1d, 1w]`
  - 加 `NATURE_TO_TIER`（scalp/intraday→short，swing→mid，trend_follow/position→long）
- **差异化节拍**（`tier_tick_scheduler.py`）：`get_due_ai_tiers()` 基于可配置间隔返回哪些 AI tier 到期：
  - 中 tier AI：`TIER_MID_AI_TICK_SEC` 默认 **120s**
  - 长 tier AI：`TIER_LONG_AI_TICK_SEC` 默认 **240s**
  - 短 tier 不在其中（有独立 ScalpRouter 循环）
  - 从 `PARAM_DEFS` 读取 `min` 下限，防止过频
- **轻量协调心跳 vs 重 AI 决策分离**：协调器心跳（无 LLM，15-45s）频繁运行做学习回填/持仓巡逻；重 LLM 决策只在 `get_due_ai_tiers()` 返回到期时触发。目标 <90s/tick。
- **`_run_midlong_independent`**：批量扫描符号（`MIDLONG_SCAN_BATCH`），中/长交替执行后 `mark_tier_run`。

#### 对标框架

| 框架 | 周期机制 | 与本系统对比 |
|------|---------|------------|
| **vnpy** | `BarGenerator`：tick→1min→Nmin/Nhour 链式合成；仅支持 MINUTE/HOUR，**不支持 DAILY/WEEKLY**；已知小时边界 bug（[Issue #2365](https://github.com/vnpy/vnpy/issues/2365)） | 本系统的 tier→timeframe 映射更清晰，无合成 bug |
| **NautilusTrader** | Rust 原生 `BarAggregator` trait：Time/Tick/Volume/Value/Renko 五种聚合，`BarType` 配置，多TF=多订阅 | **聚合种类远超本系统**；本系统仅有时间聚合 |
| **Freqtrade** | `process_only_new_candles=True`：节流循环（5s），仅在新 K线收盘时重算策略；`startup_candle_count` 预热 | 本系统的"差异化节拍"比 Freqtrade 的"统一节流"更精细 |
| **Hummingbot** | 固定速率 asyncio `Clock`（`tick_size`秒），每个 tick 对**所有**策略+连接器统一 `c_tick(timestamp)` | 本系统的差异化调度比 Hummingbot 的统一时钟更优（AI 成本随节拍增长，高层级应低频） |
| **Jesse** | K线驱动时序，`routes.py` 声明每个 (symbol, timeframe) 路由 | 本系统的三层架构比 Jesse 的路由更抽象 |
| **Qlib** | `freq` 参数（year/quarter/month/week/day/minute/second），多频各自 DataHandler | 日频成熟，分钟/秒频较新 |

#### 评估

- **🟢 本系统领先**：差异化分层调度是**独创优势**——正确认识到 AI 成本随节拍增长，故高层级（中/长）低频、短层级独立快速循环。没有任何开源框架有这种"按 tier 差异化 AI 调度"的设计。
- **🟡 不足**：
  - **缺统一时钟抽象**：Hummingbot 的 `Clock` + `TimeIterator.c_tick()` 是干净的单一时钟真相源；本系统用 `apscheduler` + `tier_tick_scheduler` + 各 scheduler 分散管理。
  - **缺非时间聚合**：NautilusTrader 支持 Volume/Tick/Value/Renko 聚合；本系统仅时间聚合。Volume-bar（按成交量合成）在加密货币市场对减少噪音有实证价值。
  - **缺决策节拍 vs 报价节拍解耦**：Hummingbot 的 `order_refresh_time` 与 `tick_size` 解耦，本系统的 `order_scheduler`（5s 轮询）与决策循环耦合较紧。

---

### 3.2 策略框架

#### 本系统：AI 多智能体 + MLTO 辩论 + 进化引擎

本系统**没有离散的 `strategies/` 文件夹**。策略是**混合架构**：规则模板信号 + LLM 审查 + 三周期编排器融合。

- **策略编排层**：
  - `strategy_coordinator.py`（2,445 行）：装配执行路径——市场环境分析→多空协调→动态风控参数→AI 决策→动态 SL/TP→记忆更新
  - `multi_timeframe_orchestrator.py`（2,368 行）：融合长/中/短 `TimeframeView` 为 `OrchestratorDecision`（允许方向、仓位倍数、最终 action/side/leverage/SL/TP、推荐 nature），含 `EVENT_OVERRIDE_RULES`（黑天鹅/鲸鱼/监管/恐慌）
- **策略类别**（DB `strategy_templates`，`strategy_library.compute_signals()` 派发）：
  - `trend/momentum/swing` → EMA 对齐(9/21/55) + MACD + RSI + 量能确认
  - `range/mean_reversion` → 布林带边缘 + RSI 极值
  - `breakout` → N 周期高低突破 + 量能 surge(1.3x)
- **专用子系统**：
  - **Scalp（短）**：`services/scalp/`（12 文件），纯规则，<100ms，无 LLM
  - **MLTO（中长）**：`services/mlto/`（14 文件），quant vs qual 辩论 → 分批入场
  - **套利**：`services/arbitrage/`（24 文件），scan→risk→execute→monitor
- **进化引擎**：`strategy_evolver.py`、`backtest_evolution_engine.py`、NSGA-II 多目标优化、`champion_recovery_service.py`（冠军持久化）

#### 对标框架

| 框架 | 策略模式 | 关键特征 |
|------|---------|---------|
| **Freqtrade** | `IStrategy`：dataframe-in/out，三方法 `populate_indicators` / `populate_entry_trend` / `populate_exit_trend`；类型化参数 `IntParameter(low,high,space='buy')` | **声明式参数空间**——策略自声明可优化范围；`minimal_roi` 时间阶梯；`Protections` 可组合风控链 |
| **Jesse** | `Strategy` ABC：命令式生命周期 `before→should_long/short→go_long/short→update_position→after`；`filters` 列表（全部返回 True 才入场）；`willing_to_loss` 硬性单笔亏损上限；`hp.int/float/choice` 参数声明 | **显式生命周期契约**；**filters 组合门**比散落 `if` 更清晰 |
| **Qlib** | `TopkDropoutStrategy`、`EnhancedIndexingStrategy`：模型分数→组合优化器（带风险模型约束） | **alpha 分数→约束优化器**，端到端 |
| **vnpy** | `CtaTemplate`：`on_init/on_start/on_tick/on_bar/on_trade/on_order`；`TargetPosTemplate`（声明目标仓位）；`CtaSignal`（可组合多信号）；`buy/sell/short/cover` 守卫 `trading` 标志 | **目标仓位模板**；**call_strategy_func try/except 隔离**（单策略异常不崩引擎） |
| **Hummingbot** | `StrategyBase(TimeIterator)`：`c_tick(timestamp)` + 事件回调；做市/套利模板 | 时钟+事件混合；`order_refresh_time` 解耦 |

#### 评估

- **🟢 本系统领先**：AI 多智能体（SwingAgent/TrendAgent 带证据链 + 反幻觉 fact_guard）、MLTO quant vs qual 辩论、进化引擎 + 冠军恢复——**没有任何开源框架有这种 AI 原生 + 进化的策略架构**。
- **🟡 不足**：
  - **缺声明式参数空间**（P1）：Freqtrade 的 `IntParameter/DecimalParameter` 和 Jesse 的 `hp.int/float/choice` 让策略自声明可优化范围，远比本系统的 `walk_forward.py` 临时 `param_grid` dict 干净。
  - **缺统一生命周期契约**（P1）：本系统的 AI agent cycle 是隐式的（散落在 `full_auto_trading_service.py` 19k 行中）；Jesse 的 `before→should→go→update→after` 是显式契约。
  - **缺 filters 组合门**（P2）：Jesse 的 `filters` 列表模式比本系统散落的 `if` 守卫更可组合、可测试。
  - **缺目标仓位模板**（P2）：vnpy 的 `TargetPosTemplate`（声明目标仓位，模板自动算 delta 下单）在本系统未见等价物。
  - **策略是 DB 行而非代码类**：`strategy_templates` 在 DB 中，源码无硬编码种子——策略清单依赖 DB 状态（2.27GB SQL 备份中），不利于版本管理与代码审查。

---

### 3.3 指标体系

#### 本系统：380+ 因子，10 大领域，插件注册

本系统的指标/因子库是**所有对标框架中最广的**：

- **基类**（`factor_base.py`，343 行）：`FactorMetadata`（factor_id/category/subcategory/lookback/dependencies/cache）、`BaseFactor(ABC)`（`get_metadata()` + `calculate(data)->pd.Series`）、三个抽象子类 `VectorizedFactor`/`RollingWindowFactor`/`CompositeFactor`
- **注册**（`factor_registry.py`，330 行）：单例 `FactorRegistry`，Kahn 拓扑排序 `resolve_dependencies()`，装饰器 `register_factor()`
- **计算器**（`factor_calculator.py`）：依赖排序→可选 `ProcessPoolExecutor` 并行→缓存→per-factor try/except 容错
- **统一入口**（`factor_service.py`）：`FactorService` 单例，收敛了历史上 3 条分叉 API，60s TTL 缓存 + per-key 锁防缓存击穿
- **因子清单**（10 大领域）：
  - `technical/`：trend（SMA/EMA/WMA/DMA/TRIX/SAR/Ichimoku）、momentum（RSI/Stochastic/CCI/Williams/ROC/UO）、volatility（Bollinger/Keltner）、volume（CMF/Force Index）
  - `legacy_compat/`：21 因子生产集（RSI/MACD/Momentum/ROC/ADX/BBWidth/ZScore/ATR/HV/Parkinson/OBV/VWAP/CVD/SuperTrend/TakerRatio/OI...）
  - `external/`：WorldQuant Alpha101（20 公式）、Alpha158、云端变体
  - `derivatives/`：Funding/OI 分歧、多空比、清算热图、期权结构
  - `sentiment/`：Funding Rate、牛熊、恐惧贪婪、买卖价差、订单失衡
  - `onchain/`：交易所净流、鲸鱼交易、TVL、活跃地址
  - `macro/`：跨市场相关性、风险偏好、全球流动性、BTC 主导
  - `behavioral/`：价格/量/波动异常、激进出/流入比、连胜、反转模式
  - `composite/`：RSI/量比、CVD 残差、趋势持续性、均值回归分
  - `ai_generated/`：**855 个 AI 生成因子文件**
- **因子生命周期**：`factor_cache_manager`、`factor_decay_monitor`、`factor_quality_evaluator`、`factor_selector`、`factor_signal_generator`、`decision_fusion_engine`（IC 加权/加权投票/门控网络融合）

#### 对标框架

| 框架 | 指标机制 | 关键特征 |
|------|---------|---------|
| **Qlib** | **表达式引擎**（`qlib/data/ops.py`）：因子是字符串表达式，非 Python 函数。算子：`Ref/Mean/Std/Var/Skew/Kurt/Max/Min/Quantile/Corr/Cov/Slope/Resi` + 算术。例：`Mean($close,5)/$close`、`Corr($close, Log($volume+1), 5)`。惰性求值+缓存。**Alpha158**（158 手工因子，kbar/price/volume/rolling 组）+ **Alpha360**（360 DL 优化特征） | **因子即字符串**——无样板代码，引擎处理对齐/缓存/向量化 |
| **vnpy** | `ArrayManager`：滚动 numpy 窗口（固定长度数组）+ TA-Lib 包装。`am.sma(n)/ema(n)/macd()/rsi(n)/boll(n,dev)/atr(n)`。`inited` 门控预热 | 简单直接，TA-Lib 生态成熟 |
| **QUANTAXIS** | `add_func`：`datastruct.add_func(QA_indicator_MA, 5)` 跨所有股票应用；通达信兼容基础指标；Cython 编译加速；Rust 核心 `qaalpha-rs`（单指标 ~70ns） | 函数式而非表达式式；**Rust 性能核心** |

#### 评估

- **🟢 本系统领先（数量与广度）**：380+ 因子 + 855 AI 生成，10 大领域覆盖技术/情绪/衍生品/链上/宏观/行为——**没有任何开源框架有这种跨领域广度**（Qlib 聚焦股票技术面，vnpy 仅 TA-Lib）。
- **🟡 不足**：
  - **缺表达式引擎**（P2）：Qlib 的"因子即字符串"是**最高价值模式**——研究者写公式而非代码，引擎处理对齐/缓存/向量化。本系统的 `custom_factor_store.py` 有受限 eval（`compile + {"__builtins__":{}}`，仅暴露 OHLCV+numpy+FORMULA_OPS），但这是**单因子公式存储**，不是 Qlib 式的**可组合算子树**。本系统新增因子仍需写完整 Python 类文件。
  - **因子数量过多可能是负担**：855 个 AI 生成因子意味着大量噪音，`factor_decay_monitor` + `factor_backtest_scorer` 的准入门槛是否足够严格是开放问题。
  - **缺 learn/infer 处理器分离**（P2）：Qlib 的 `DataHandlerLP` 区分 `_learn_processors`（仅在训练集 fit，如归一化）与 `_infer_processors`（推理时应用，如 fillna），防止归一化的前视偏差——本系统的 `factor_service` 未见此分离。

---

### 3.4 因子调用与因子训练

> **这是本系统与顶级框架差距最大的维度。**

#### 本系统：IC 评估强，但权重是手工表

**因子分析（强）**——`factor_evaluator.py`（348 行）计算教科书级指标：
- **IC**（Spearman rank 相关，非 Pearson，line 262）
- **ICIR** = ic_mean / (ic_std + 1e-10)
- **IC 正向占比**、**IC 衰减半衰期**（1..min(20,len//5) lag，首个 |IC| < 50% lag-1 IC 的 lag）
- **换手率**（1 - rank 自相关）、**IC 最大回撤**、**单调性**（5 分位）、**尾部风险**（top/bottom 5%）
- **Grading 阈值（精确）**：`IC≥0.05 & ICIR>0.5 → A`，`≥0.03 & >0.3 → B`，`≥0.015 → C`，`≥0.005 → D`，else `F`
- **正交性检查**：`|corr|>0.7` 标记冗余对

**因子准入门（强）**——`factor_backtest_scorer.py`：walk-forward 单因子回测，扣除往返手续费+滑点，严格样本外，仅 A/B 级进入 active 集。

**因子加权（弱——这是核心差距）**——`factor_weighting.py`（612 行）：
- **6 个 regime × 12 因子的权重是硬编码字面量**（`_init_regime_weights`，lines 71-200）：

| Regime | Top 权重 | 仓位修正 | SL(ATR) | TP(ATR) |
|--------|---------|---------|---------|---------|
| BREAKOUT | supertrend .15, sma_cross .12, momentum .12 | 1.0 | 1.5 | 2.5 |
| CONTINUATION | trend .15, momentum .12, sma_cross .10 | 0.9 | 1.5 | 2.0 |
| REVERSAL | rsi .15, bb_width .12, zscore .12 | 0.6 | 2.0 | 1.5 |
| ABSORPTION | volume_zscore .15, taker_ratio .12, cvd_ratio .12 | 0.5 | 2.5 | 2.0 |
| EXHAUSTION | rsi .12, funding_rate .12, momentum .10 | 0.5 | 2.0 | 2.5 |
| NOISE | atr .15, hv .12, parkinson_vol .10 | 0.5 | 2.0 | 1.2 |

- **有限的适应性**：`_fine_tune_weights`（±0.1）、`_smooth_transition`（EMA 0.3*new+0.7*old）、`apply_feedback_adjustments`（V3，±20%/因子）——这些是**对固定表的事后微调，不是学习得来的权重**。

**训练机制（进化式，非梯度式）**：
- `genetic_optimizer.py`：纯 Python GA（无 DEAP），30 代 × 20 个体，锦标赛选择，精英保留(2)，优化**策略参数**（stop_loss_pct/take_profit_pct/leverage/信号权重/RSI阈值...）
- `learning_core/rl_core/`：env/policy/replay_buffer/shadow——RL 脚手架（早期阶段）
- `learning_core/orchestrator.py`：统一 Hypothesis/Hermes/在线学习，血缘账本
- **因子权重本身不是被训练对象**（除衰减惩罚外）

#### 对标框架：Qlib（因子训练的黄金标准）

Qlib 的因子→训练 pipeline 是**端到端 ML 学习**：

```
因子表达式(DataLoader) → DataHandlerLP(处理器:归一化/fillna) →
DatasetH(按日期分train/valid/test) → Model(fit/predict) →
SigAnaRecord(IC/ICIR评估) → Strategy(组合优化) → Backtest →
PortAnaRecord
```

- **18+ ML 模型**（`qlib/contrib/model/`）：LightGBM、CatBoost、XGBoost、DoubleEnsemble、Linear、MLP、LSTM、GRU、ALSTM、Transformer、GATs、TRA、TFT、TabNet、TCTS、HIST、Localformer、PDIST——全部继承 `Model` 基类，`fit(dataset)` / `predict(dataset)` 接口统一，YAML 一行即可切换
- **RollingGen**（`qlib/workflow/task/utils.py`）：生成 walk-forward 滚动重训任务列表，step/trn_num/rtype 可配
- **DDG-DA 元学习**（AAAI 2022，`examples/benchmarks_dynamic/DDG-DA/`）：不是朴素"用近期数据重训"，而是训练元模型学习**市场状态→最优预测模型权重**的映射，处理分布漂移
- **Task Management**（`qlib/workflow/task/`）：TaskGen（RollingGen）→ TaskManager（去重/存储）→ TaskExecutor（多进程）——单任务 Workflow 变多任务引擎
- **learn/infer 处理器分离**：`_learn_processors`（仅训练集 fit）vs `_infer_processors`（推理应用）——防止归一化前视偏差
- **标签也是表达式**：`label = ["Ref($close, -2)/Ref($close, -1) - 1"]`（2日前瞻收益）
- **集成风险-收益优化**：alpha 分数 → 带风险模型约束的优化器（`EnhancedIndexingStrategy`）

#### 评估

- **🟢 本系统领先（因子分析）**：IC/ICIR/decay/正交性评估是教科书级实现，因子准入门（OOS 回测 + 扣费）是真正的科学门控。
- **🔴 本系统落后（因子权重——核心差距）**：
  - 权重是**手工 regime 字面量表**，不是从数据学习得来的。Qlib 是**端到端 ML**（因子→模型→IC 加权）。
  - 本系统的 GA 优化**策略参数**，但**因子权重本身不是优化对象**（除 ±20% 事后微调）。
  - 缺 walk-forward **滚动重训**（RollingGen）——本系统的 `train_period_days=252` 是静态单次。
  - 缺**分布漂移适应**（DDG-DA 元学习）。
  - 缺**模型抽象**——本系统无统一的 `Model.fit/predict` 接口，无法像 Qlib 那样一行 YAML 切换 LightGBM/GRU/Transformer。
- **这是整个对比中最大的方法论差距**：手工 regime 权重表本质上是"人类先验"，而量化研究的核心价值在于"让数据说话"——从 380+ 因子中**学习**最优加权，而非人工指定 6×12=72 个权重值。

---

### 3.5 门禁机制

#### 本系统：业务层门禁极强，引擎层偏弱

**业务层门禁（决策核心，极强）**——`decision_core/pipeline.py`（395 行，facade）：

精确门禁顺序（已核验 `pipeline.py` + `unified_gate.py`）：
```
evaluate_midlong_open 完整栈:
  persistence(连续N tick同向) → weekly cap(trend 6/周) →
  data_contract(严格数据质量) → DCP(方向一致性,penalty提升阈值) →
  [V5 evaluate_entry 内部子门]:
    disabled_natures → multi_freq_constraint(scalp豁免当ALLOW_COUNTER_TREND) →
    regime(extreme市场阻止) → daily_cap → confidence →
    cycle_prob(默认禁用) → short_tier(fail-closed) → risk_reward/min_tp →
  orchestrator soft(缩仓不阻止) → Monte Carlo(仅缩仓) →
  EV gate(负期望硬阻止,最后一道)
```

**V5 门禁硬边界（精确，`unified_gate.py` + `config/settings.py`）**：

| 参数 | 值 | settings.py 行 |
|------|----|----|
| `V5_MAX_DAILY_TRADES_LIVE` | **12** | 1904 |
| `V5_MAX_DAILY_TRADES_PAPER` | **30** | 1905 |
| `V5_MAX_SYMBOL_TRADES_PER_DAY_LIVE` | **4** | 1906 |
| `V5_MIN_RISK_REWARD` | **1.8** | 1928 |
| `V5_MIN_TP_PCT` | **1.2%** | 1931 |
| `V5_SCALP_MIN_CONFIDENCE` | **70** | 1934 |
| `V5_TREND_FOLLOW_MIN_CONFIDENCE` | **50** | 1937 |
| `V5_MAX_TRADE_RISK_PCT` | **1.5%** | 1940 |
| 信心下限(Paper/Live) | **45 / 40** | gate 内算 |
| 最小 RR 运行时上限 | **2.5** | 1760 |
| `TREND_MAX_OPENS_PER_WEEK` | **6**(midlong启用) / 2 | 1490 |

- **有界运行时可调**（`_runtime_overrides`）：反馈循环可夹紧 `max_daily_trades∈[3,20]`、`scalp_min_confidence∈[60,90]`、`min_risk_reward∈[1.5,2.5]`，读 `data/v5_runtime_gates.json`（60s 缓存）——反馈只能在安全走廊内调
- **fail-closed**：`V5_DECISION_CORE_ENABLED=false` 且 mode≠paper → **raise RuntimeError** 拒绝启动；Paper → 无条件放行
- **block 反馈回灌**（M4）：最近 15 分钟被拒决策回灌 LLM prompt，阻止重复提交会被拒的订单
- **蒙特卡洛门**（`monte_carlo_gate.py`）：GBM 1000 路径 × 48 bar 估尾部风险，>12% 权益则缩仓
- **EV 门**（`midlong_ev_gate.py`）：`EV_pct = p_win·(tp·realization) − (1−p_win)·(sl·1) − round_trip_cost`，仅当 `EV_pct ≥ NATURE_EV_MIN_PCT` 放行，有 shadow 模式

**风险层（账户/仓位）**——`risk_management/risk_controller.py`：3 层硬限制（账户/策略/交易）：最大敞口 95%、最大回撤 20%、日亏 5%、最大杠杆 3.0、最大单仓 10%、最大相关性 0.8、板块上限 40%。`full_auto_trading_service.py` 类级硬限制：单标的 3% 日亏→冻结、8% 全局极端日亏安全网、25% 全局回撤安全网、60min 冻结冷却。

#### 对标框架

| 框架 | 门禁深度 | 关键特征 |
|------|---------|---------|
| **NautilusTrader** | **引擎层深度风控**（`RiskEngine` + `nautilus-risk` Rust crate）：精度/触发价精度、正价格、数量精度与 min/max、GTD 未过期、`reduce_only` 不增仓、`max_notional_per_order`/`max_notional`、现金账户余额影响、提交/修改限流（`RATE_LIMIT_EXCEEDED`）、`TradingState`(ACTIVE/HALTED/REDUCING) 状态机。标准化 `OrderDenied` 原因码（QUANTITY_EXCEEDS_MAXIMUM/CUM_MARGIN_EXCEEDS_FREE_BALANCE/REDUCE_ONLY_WOULD_INCREASE_POSITION/TRADING_HALTED...）。**重复成交去重**、**超额成交保护**（默认拒绝）、OMS 类型(NETTING/HEDGING)强制 | **生产级引擎层硬风控**——业务无关的通用风控 |
| **vnpy** | `vnpy_riskmanager`：**浅**——order_flow_limit(订单/秒)、order_active_limit(活跃订单数)、order_size_limit(单单量)、trade_limit(日交易数) | 仅流量/数量/计数上限，无仓位/敞口/保证金检查 |
| **Freqtrade** | `Protections` 可组合链：`StoplossGuard`/`MaxDrawdown`/`CooldownPeriod`/`LowProfitPairs`/`MaxPairs`——逆向事件后锁定标的 | **声明式可组合风控链**，可重排 |

#### 评估

- **🟢 本系统领先（业务层门禁）**：V5 门禁链（7+ 门，有界运行时可调，fail-closed，block 反馈回灌 LLM，蒙特卡洛 + EV）是**所有框架中业务门禁最深的**。NautilusTrader 的 RiskEngine 是引擎层通用风控，但**不含 EV/蒙特卡洛/regime/置信度**这类业务决策门。
- **🟡 本系统落后（引擎层硬风控，P1）**：
  - 缺 NautilusTrader 级的**引擎层硬风控**：价格/触发价**精度**校验、**名义价值** min/max（per-order 和 per-instrument）、**保证金/自由余额**检查（现金账户）、**限流**（提交/修改速率）、`TradingState` 状态机（ACTIVE/HALTED/REDUCING）。
  - 缺**重复成交去重**（trade_id 4 字段匹配）和**超额成交保护**（默认拒绝超量成交）——这是引擎不变量，非策略关注点。
  - 缺**标准化拒绝原因码**（NautilusTrader 的 `OrderDenied` + `CATEGORY_CONDITION` + key=value context）——本系统的 block 日志 `[V5Gate] BLOCK symbol=... rule=...` 是文本，非结构化可编程接口。
- **🟡 缺声明式 Protections 链**（P2）：Freqtrade 的可组合、可重排 Protection 链比本系统的硬编码门禁顺序更灵活。

---

### 3.6 协调机制

#### 本系统：QAA 框架 + 交易侧自实现双轨

**QAA 框架**（`QAA通信协议构架/`，v3.1.0，独立 pip 可装包）——领域无关的多智能体协调：
- **事件总线**（`qaa/event_bus.py`）：双模式——(1) async pub/sub（`asyncio.PriorityQueue`，CRITICAL>HIGH>NORMAL>LOW，满时丢 LOW，通配订阅）+ 三态熔断器（CLOSED→OPEN→HALF_OPEN）；(2) 同步 agent-call 总线
- **通用注册**（`qaa/core/registry.py`）：`UniversalRegistry`，线程安全，追踪 AgentCard/handlers/DomainPlugins/event types/延迟预算——按域隔离
- **AgentCard**（`qaa/core/models.py`）：identity + capabilities + tools + guards + LLM level(NONE/QUICK/DEEP) + timeout/熔断策略 + dependencies + domain tag
- **工作流编排**（`qaa/workflow/orchestrator.py`）：`TickOrchestrator.run_tick()` 持久化单域运行，`WorkflowRun`/`WorkflowStep`，幂等键，tick 预算(120s)，P0/P1 步骤失败策略，成本/token 记账
- **持久化**（`qaa/workflow/store`）：JSONL/Postgres 后端，租户隔离 `{base}/{tenant}/{namespace}/events.jsonl`
- **跨域 DAG**（`qaa/crossdomain/engine.py`）：声明式多域 YAML DAG，`when:` 条件，并行步骤，skip/compensate
- **记忆/RAG**（`qaa/memory/` + `qaa/knowledge/`）：冷热分层，6 层记忆巩固

**交易侧自实现**（`backend/services/qaa/`）——**关键**：交易后端**不**将 QAA 作为已安装依赖用于实盘交易，而是**本地适配/重新实现** QAA 概念：
- `services/qaa/cards.py`：**自己的** 8 张交易 Agent Card（genetic_optimizer/market_data/factor_engine/intel_signal/risk_control...）
- `services/qaa/rule_router.py`：`RuleRouter`——明确设计目标"替代 per-tick LLM 规划"：**90% tick 由确定性规则路由(<1ms, $0)，仅异常回退 LLM(30-60s)**
- 交易后端通过 `from backend.services.qaa ...` 导入 QAA 事件总线和学习后端

#### 对标框架

| 框架 | 协调机制 | 关键特征 |
|------|---------|---------|
| **NautilusTrader** | **MessageBus** + Actor 模型 + **事件溯源**：pub/sub 主题 + 点对点端点；Command(SubmitOrder) vs Event(OrderFilled) 分离；状态是事件日志的**物化视图**（重放重建）；确定性 Clock；Rust 核心。**回测/实盘共享同一引擎代码**，仅事件源不同 | **事件溯源=回测实盘一致性的根本保证** |
| **vnpy** | `EventEngine`：单 `queue.Queue` + 单消费线程 + 定时线程；内存 pub/sub（按 event type 字符串）；无持久化、无优先级。`call_strategy_func` try/except 隔离单策略异常 | 极简，易理解，但单线程派发是吞吐天花板，慢 handler 阻塞全局 |
| **Hummingbot** | asyncio `Clock` + `TimeIterator.c_tick()` 统一周期派发；时钟+事件回调混合（`c_did_fill_order` 叠加 `c_tick`） | 时钟统一但无事件溯源 |

#### 评估

- **🟢 本系统领先（框架成熟度）**：QAA v3.1.0 是成熟的、文档完备的多智能体协调框架（持久化工作流、熔断、跨域 DAG、6 层记忆/RAG、RBAC/审计）——比 vnpy 的单总线、Hummingbot 的简单 Clock 都更完整。`rule_router` 的"90% 规则路由 + 10% LLM 回退"是务实的成本控制设计。
- **🔴 本系统落后（事件溯源，P1）**：
  - **无事件溯源**：NautilusTrader 的订单/仓位/账户是事件日志的物化视图，可重放、可审计、可时间旅行调试。本系统的状态是内存 mutable 对象（`full_auto_trading_service.py` 类属性），**回测引擎与实盘引擎是两套代码**——存在回测/实盘不一致风险。
  - **双轨重复**（P1）：`QAA通信协议构架/qaa/` 与 `backend/services/qaa/` 各自定义 `AgentCard`/`RuleRouter`——两套定义，边界模糊。
- **🟡 缺 Rust 性能核心**（P2）：QUANTAXIS（`quantaxis-rs`/`qaaccount-rs`）和 NautilusTrader 都用 Rust 核心（单指标 ~70ns，2 年分钟回测 ~500ms），本系统纯 Python。

---

## 第四部分　AI 量化交易项目深度对标（新增）

> 本部分对标 **AI/ML/LLM 量化类** 框架。传统量化框架（第三部分）的强项是工程严谨性（回测、因子、门禁）；AI 量化框架的强项是**学习与推理**。本系统的独特性恰在于横跨两者，故本部分是对比的核心价值所在。

### 4.1 FreqAI — ML/RL 训练管线的黄金标准

**项目**：FreqAI 是 Freqtrade 的 AI/ML 子模块（`freqtrade/freqai/`），是加密领域**最成熟的 ML 训练管线**。

#### 4.1.1 架构：三对象设计

FreqAI 明确定义三个核心对象（官方文档反复强调）：

1. **`IFreqaiModel`**（`freqai/freqai_interface/IFreqaiModel.py`）——ML 模型对象，抽象基类定义生命周期契约 `train()` / `predict()` / `data_cleaning()` / `make_labels()`。运行时通过 `--freqaimodel <Name>` 选择，从 `user_data/freqaimodels/` 加载。
2. **`FreqaiDataKitchen`**（`freqai/freqai_utils/DataKitchen.py`）——**逐品种训练上下文对象**（这是本系统最该借鉴的模式）。它携带：训练特征矩阵、标签序列、train/test 时间切分时间戳、当前训练窗口元数据、预测 dataframe、**精确的特征列名清单**（确保 predict 用与 train 完全相同的列集——消除特征漂移 bug）。
3. **Strategy 对象**——普通 Freqtrade 策略，实现三个 FreqAI 钩子：`feature_engineering_expand_all()`（定义特征空间）、`feature_engineering_standard()`（标准特征）、`set_freqai_targets()`（定义标签）。

#### 4.1.2 支持的模型（精确类名）

| 家族 | 类（`freqai/prediction_models/`） |
|------|--------------------------------|
| **LightGBM** | `LightGBMRegressor/Classifier/MultiTargetRegressor/MultiTargetClassifier` |
| **XGBoost** | `XGBoostRegressor/Classifier/MultiTarget*` |
| **CatBoost** | `CatboostRegressor/Classifier/MultiTarget*` |
| **PyTorch** | `PyTorchMLPRegressor`、`PyTorchTransformerRegressor`（均继承 `BasePyTorchModel`） |
| **强化学习** | `ReinforcementLearner` + `ReinforcementLearner_multiproc`；基类 `BaseReinforcementLearningModel`（`freqai/RL/`） |

RL 栈基于 **stable-baselines3**（默认 PPO，支持 SAC/TD3/A2C）。环境：`Base5ActionRLEnv`（中性/多开/多平/空开/空平）和 `Base3ActionRLEnv`。关键：RL 环境是**原始市场表示**，agent 自己学习止损/止盈策略，绕过 Freqtrade 常规 SL/TP 逻辑。

#### 4.1.3 特征工程与标签生成（`&` 前缀约定）

FreqAI 的特征/标签是**前缀声明式**的：

- **特征**：`feature_engineering_expand_all` 返回列名编码了**指标基 + 时间框架 + 平移/回看**，如 `rsi_{period}`。FreqAI 自动按 config 的 `feature_parameters` 展开每个基特征的移位副本和百分比变化副本：
  ```yaml
  feature_parameters:
    include_timeframes: ['15m', '1h', '4h']     # 多时间框架自动生成
    include_shifted_candles: 2                   # 滞后副本
    indicator_periods_candles: [10, 20]
    include_corr_pairlist: ['BTC/USDT', 'ETH/USDT']  # 跨资产特征
  ```
- **标签**（**`&` 前缀约定**强制）：
  ```python
  dataframe["&-s_close"] = dataframe["close"].shift(-period) / dataframe["close"]  # 回归
  dataframe["&-s_close"] = pd.qcut(future_return, [-np.inf, 0, np.inf], labels=[0,1])  # 分类
  ```

#### 4.1.4 持续重训管线（FreqAI 的核心价值）

FreqAI 是**显式的持续学习/周期重训系统**，非一次性训练：
- `train_period_days: 30`——每个训练窗口覆盖的历史
- `live_retrain_hours: 12`——**实盘重训节奏**（每 12h 滑动窗口重训新模型替换旧模型）
- `backtest_period_days: 7`——回测中每 7 天重训一次（仿真实盘节奏）
- `fit_live_predictions_candles: 300`——校准实盘预测的 K 线数
- `Continental: false`——持续学习标志（true 时从上次权重热启动）

**模型按品种持久化**：`user_data/models/<identifier>/<pair>/`。

#### 4.1.5 前视防护（FreqAI 最严谨之处）

FreqAI 用多层机制严格防前视：
1. **`FreqaiDataKitchen` 内显式时间切分**——切分点按时间戳强制，模型只训练 timestamp ≤ split 的行，无跨时间边界洗牌。
2. **`backtest_period_days` 与 `train_period_days` 严格滚动窗口**——回测每步训练 `[t-train_period, t]` 预测 `[t, t+backtest_period]`，预测窗永不重叠训练窗。这是**内建的 walk-forward**。
3. **`live_retrain_hours` 镜像 `backtest_period_days`**——回测重训节奏可配成与实盘一致，使回测结果近似实盘行为。
4. **Freqtrade 全局 `lookahead-analysis` 命令**——蒙特卡洛式工具，打乱 K 线顺序重跑回测以检测偷偷偷看未来 K 线的策略。
5. **特征列一致性**——`FreqaiDataKitchen` 在 train 时存储精确特征列清单，`predict()` 复用，杜绝标签列（`&` 前缀目标）被误当特征。
6. **无插值泄漏**——`data_cleaning()` 的参数（如要丢弃的目标值）仅在训练分区计算。

#### 4.1.6 与本系统的对比

| 维度 | FreqAI | 本系统 | 差距 |
|------|--------|--------|------|
| 模型类型 | 5 大类（LGBM/XGB/CatBoost/PyTorch/RL）统一接口 | RL 脚手架早期 + GA 优化策略参数 | **🔴 缺监督学习模型族** |
| 训练上下文 | `FreqaiDataKitchen`（特征+标签+切分+列清单） | 无统一上下文对象，散落各处 | **🔴 缺训练上下文对象** |
| 持续重训 | `live_retrain_hours`/`train_period_days` 显式节奏 | `walk_forward.py` 静态单次 `train_period_days=252` | **🔴 缺内建持续重训** |
| 前视防护 | 多层（时间切分+滚动窗+lookahead-analysis+列一致） | 朴素 train/test 相邻，无 purge/embargo | **🔴 前视防护弱** |
| 标签约定 | `&` 前缀声明式 | 无统一约定 | 🟡 工程化差距 |
| 多时间框架特征 | `include_timeframes` 自动展开 | 手工 | 🟡 |

**结论**：FreqAI 是本系统 ML 管线工程化的**首要对标对象**。本系统的 `learning_core/rl_core/` 有 RL 脚手架，但缺 FreqAI 式的 `FreqaiDataKitchen` 上下文、持续重训、严格滚动窗、统一模型接口。**这是 P0-5 整改的直接依据。**

---

### 4.2 TradingAgents — LLM 多智能体对冲基金模拟

**项目**：`TauricResearch/TradingAgents`——基于 LangGraph 的 LLM 多智能体交易系统，模拟对冲基金组织架构。**这是与本系统 LLM 多智能体部分最直接的对标对象。**

#### 4.2.1 智能体架构：三层 + 两层辩论

构建于 **LangGraph**（StateGraph + ToolNode + 条件路由 + checkpointer 状态持久化）：

```
分析师层(5个,顺序) → 研究辩论(牛 vs 熊, N轮) → 研究经理(综合) →
交易员(单一资产提案) → 风险辩论(激进 vs 保守 vs 中立, N轮) → 投资组合经理(最终决策)
```

- **分析师层**（`agents/analysts/`）：`market_analyst`（技术指标）、`fundamentals_analyst`（Alpha Vantage）、`news_analyst`、`sentiment_analyst`、`social_media_analyst`（Reddit/StockTwits）
- **研究辩论**（`agents/researchers/`）：`bull_researcher` vs `bear_researcher`——每个读取分析师输出，通过 `AIMessage` 追加论点。`conditional_logic.py` 的 `should_continue_debate` 路由回节点进行 N 轮迭代。
- **管理层**：`research_manager`（聚合辩论历史 `state["investment_debate"]`）、`portfolio_manager`（最终决策）
- **交易员**：`trader.py`——单一资产节点，读 `state["company_of_interest"]`
- **风险辩论层**（3 角色）：`aggressive_debator` / `conservative_debator` / `neutral_debator`——**注意无 memory 参数**（不同于分析师/研究员），每个读 `state["risk_debate_state"]`

#### 4.2.2 多智能体协调：两层对抗辩论

- **第一层：研究辩论**（`investment_debate`，牛 vs 熊）→ research_manager
- **第二层：风险辩论**（`risk_debate_state`，激进 vs 保守 vs 中立）→ portfolio_manager

这是**结构化对抗辩论**（非投票/非层级），通过 `should_continue_risk_analysis` 条件边进行多轮迭代。

#### 4.2.3 记忆/RAG（ChromaDB + OpenAI 嵌入）

`agents/utils/memory.py` 的 `FinancialSituationMemory`：
```python
import chromadb
from openai import OpenAI
class FinancialSituationMemory:
    def __init__(self, name, config): ...
```
使用 **ChromaDB 向量库 + OpenAI 嵌入（`text-embedding-3-small`）**检索过去相似金融情境供借鉴。通过通用 `create_X(llm, memory)` 工厂注入每个分析师/研究员（风险辩论者除外，仅接收 `llm`）。

#### 4.2.4 防幻觉：确定性市场数据验证覆盖层（最该借鉴）

`dataflows/market_data_validator.py`：
> "The market analyst is an LLM that can confabulate exact numbers — citing a Bollinger band..."

在 LLM 输出之上叠加**确定性的市场数据快照**，确保 LLM 引用的数字可与现实验证。这是与本系统 `fact_guard` 思路一致的防幻觉机制，但更系统化。

#### 4.2.5 风险管理：纯 LLM（无硬规则护栏）

TradingAgents 的风险完全由 3 个 LLM 角色智能体承担——**无硬编码风险规则**（止损/最大杠杆/最大回撤），**无永续合约的保证金/清算逻辑**。

#### 4.2.6 局限性

- **仅现货加密**（BTC-USD/ETH-USD via Yahoo Finance）+ 股票（Alpha Vantage）+ 宏观（FRED）——**不支持永续合约**
- **无规则风险护栏**（与 HAA 的 V5 门禁链相反）
- **单资产每次运行**（非多标的并行）
- **单时间周期**（非 5m/1h/1d 三周期）
- **回测有限**（LangGraph checkpointer 重放，无向量回测引擎）

#### 4.2.7 与本系统的对比

| 维度 | TradingAgents | 本系统 | 评价 |
|------|--------------|--------|------|
| LLM 协调 | 两层对抗辩论（牛/熊 + 风险三角色） | MLTO quant/qual 辩论 | 🟡 本系统辩论非对抗式；可借鉴 TradingAgents 的牛/熊结构 |
| 记忆/RAG | ChromaDB + OpenAI 嵌入 | ChromaDB + sentence-transformers | 🟢 本系统已有，技术栈接近 |
| 防幻觉 | 确定性 market_data_validator 覆盖层 | 证据链 + fact_guard | 🟢 本系统已强；可借鉴验证器覆盖层模式 |
| 风险 | 纯 LLM 角色 | V5 门禁链（规则 + fail-closed） | 🟢 **本系统远优**——规则护栏是实盘必需 |
| 永续合约 | 不支持 | 资金费率/OI/清算/杠杆/保证金 | 🟢 **本系统独家** |
| 多周期 | 单周期 | 5m/1h/1d 三周期 | 🟢 **本系统远优** |
| 执行层 | 无（仅决策） | 6 交易所 + ccxt + paper/live | 🟢 **本系统远优** |
| LLM 供应商 | 工厂模式（OpenAI/Anthropic/Google/Bedrock/Azure） | DeepSeek 单一 | 🟡 可借鉴工厂模式（多供应商容错） |

**结论**：TradingAgents 在**结构化对抗辩论**和**确定性数据验证覆盖层**上是本系统 LLM 层的可借鉴对象；但在风险、永续、多周期、执行上本系统**全面领先**。

---

### 4.3 FinRL / FinRL-Meta — DRL 金融框架

**项目**：`AI4Finance-Foundation/FinRL`（Columbia/AI4Finance，旗舰学术 RL-for-finance 框架）+ `FinRL-Meta`（NeurIPS 2022，DataOps 范式）。

#### 4.3.1 架构：三层

- **市场环境**（gym）：`env_stocktrading.py`（状态/动作/奖励，动作空间 `Box[-1,1]`，奖励=Δ组合价值）、`env_crypto.py`（加密变体）、`env_portfolio`（状态={协方差,MACD,RSI,CCI,ADX}，动作=权重，奖励=`log(p_{t+1}/p_t)`）
- **DRL agent**（`agents/`）：`DRLAgent` 包装 stable-baselines3，统一暴露 PPO/A2C/DDPG/TD3/SAC；**集成策略**（ICAIF 2020）结合三者
- **金融应用**：股票/加密/外汇/组合

#### 4.3.2 加密支持与 FinRL-Meta

加密是一等公民——有专门加密环境、5m OHLCV FinRL-加密竞赛、FinRL-AlphaSeek（因子挖掘）。FinRL-Meta 自动从 Yahoo Finance/WRDS/Binance 等构建 gym 风格环境（DataOps）。

#### 4.3.3 与本系统的对比

| 维度 | FinRL | 本系统 | 评价 |
|------|-------|--------|------|
| 核心范式 | DRL（PPO/SAC/集成） | LLM 多智能体 + GA + RL 脚手架 | 不同范式 |
| 环境抽象 | gym state/action/reward 标准化 | 无标准化 env | 🟡 可借鉴 gym 抽象 |
| 数据管线 | FinRL-Meta DataOps 自动构建 | 手工 | 🟡 可借鉴 DataOps |
| 实盘部署 | 研究导向（用户自理） | 6 交易所生产执行 | 🟢 本系统远优 |
| LLM | 无（FinRL-DeepSeek 是 LLM 信号 + RL 执行的近期探索） | LLM 多智能体原生 | 🟢 本系统前瞻 |

**结论**：FinRL 的 **gym 环境 state/action/reward 抽象**和 **FinRL-Meta DataOps 数据管线**是本系统 RL 层和 5m/1h/1d 数据管道可借鉴的工程模式。但 FinRL 是纯 RL，非 LLM——不直接可比。

---

### 4.4 其他 AI 项目：FinGPT / TensorTrade / ai-hedge-fund / WebCryptoAgent

#### 4.4.1 FinGPT（`AI4Finance-Foundation/FinGPT`）

**金融基础模型**——BloombergGPT 的轻量开源替代。基座：ChatGLM/BLOOM/LLaMA。微调：LoRA/RAFT/DPO。

- **多智能体**：**非原生**——FinGPT 是模型/库层，非编排器
- **RLHF**：用于个性化机器人顾问（风险厌恶/投资习惯建模）——与本系统风险画像相关
- **与本系统关系**：FinGPT 可作为本系统智能体的微调金融 LLM 基座，或情绪/新闻分类器，但**不能替代多智能体编排**

#### 4.4.2 TensorTrade（`tensortrade-org/tensortrade`，活跃分支 `tensortrade-ng`）

**纯 RL 组合框架**——整个系统是可组合的 OpenAI Gym 环境。

- **组件**（`tensortrade/env/default/`）：`ActionScheme`（`SimpleOrders`/`ManagedRiskOrders`/`BSH`）、`RewardScheme`（`SimpleProfit`/`RiskAdjustedReturns`）、`Observer`、`Stopper`（`MaxLossStopper`/`MaxDurationStopper`）、`Broker`、`Exchange`/`DataFeed`
- **可组合性是定义性优势**：`create(feed, portfolio, action_scheme, reward_scheme, observer, stopper, ...)` 工厂任意组合
- **局限**：原版 2020 后基本停更；**无内置交易所连接器**（数据自备）；**无 walk-forward 引擎**（前视防护全靠用户）
- **与本系统关系**：TensorTrade 的**可组合 `ActionScheme`/`RewardScheme`/`Stopper`** 模式值得本系统借鉴——即使 LLM 驱动，把"动作空间/目标奖励/停止条件"解耦为可插拔组件使实验更清晰

#### 4.4.3 ai-hedge-fund（`virattt/ai-hedge-fund`，~4.5 万星）

**LLM 角色智能体**——18 个投资者角色（格雷厄姆/巴菲特/芒格/伯里/凯西·伍德等），LangGraph 工作流。

- **流程**：投资者智能体（并行）→ 投资组合经理 → 风险经理 → 资金部（仓位规模）——比 TradingAgents 更清晰的层级
- **关键警告**：**明确仅供教育**，不用于真实交易
- **与本系统关系**：**角色驱动智能体**（基于角色的系统提示词）是可迁移模式；组合→风险→资金部的清晰分层值得借鉴。但仅股票、无永续、无实盘、无回测

#### 4.4.4 WebCryptoAgent（arXiv 2601.04687）

**面向加密的反思式智能体框架**——三组件：(a) **网络信息推理**（LLM 抓取/阅读网络作上下文）、(b) **上下文经验回放**（记忆过去相似交易情境）、(c) **反思**（交易后自我批评）。

- 论文声称：提高交易稳定性、减少虚假活动、增强尾部风险处理——直接针对 LLM 智能体的波动性问题
- **与本系统关系**：**反思 + 经验回放**循环与本系统的证据链/防幻觉目标高度一致。**三个模式值得借鉴**：反思（交易后自我批评）、上下文经验回放（RAG 过去决策）、网络信息推理（获取实时上下文）

#### 4.4.5 Luo MAS（arXiv 2501.00826）— 最接近本系统的学术同行

**LLM 驱动的加密组合管理**——三个模态专用智能体：加密 Agent（市场动态）、新闻 Agent（每周新闻）、交易 Agent（整合输出 + 组合状态）。

- **评估了三种架构**：序列式、协作式、辩论式——**辩论式表现最好**（夏普 2.07，优于单智能体基准）
- **与本系统关系**：这是学术文献中**最接近本系统的同行**——模态专用智能体、辩论式协调、聚焦加密。**直接比较点**：他们的加密/新闻/交易分工 vs 本系统的 SwingAgent/TrendAgent；他们的序列/协作/辩论消融为本系统的协调选择提供了理论依据

---

### 4.5 本系统在 AI 量化生态中的定位

#### 4.5.1 能力矩阵

| 系统 | LLM 多智能体 | ML/RL 管线 | 多周期 | 进化闭环 | 加密永续 | 实盘执行 | 防幻觉 |
|------|------------|-----------|--------|---------|---------|---------|--------|
| **本系统 HAA** | ✅ Swing/Trend+证据链 | 🟡 RL脚手架+GA | ✅ 5m/1h/1d | ✅ NSGA-II | ✅ 全覆盖 | ✅ 6交易所 | ✅ fact_guard |
| FreqAI | ❌ | ✅ 5模型族 | 🟡 include_timeframes | 🟡 持续重训 | ❌ 现货 | ✅ | ❌ |
| TradingAgents | ✅ 两层辩论 | ❌ | ❌ 单周期 | ❌ | ❌ 现货 | ❌ 仅决策 | ✅ 验证器 |
| FinRL | ❌ | ✅ DRL | 🟡 5m | ❌ | 🟡 现货加密 | ❌ 研究 | ❌ |
| FinGPT | 🟡 模型层 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| TensorTrade | ❌ | ✅ RL组合 | ❌ | ❌ | ❌ 自备 | ❌ | ❌ |
| ai-hedge-fund | ✅ 角色智能体 | ❌ | ❌ | ❌ | ❌ 股票 | ❌ 教育 | ❌ |
| WebCryptoAgent | ✅ 反思式 | ❌ | ❌ | 🟡 反思 | ✅ 现货 | ❌ | ✅ 反思 |
| Luo MAS | ✅ 辩论 | ❌ | ❌ 周度 | ❌ | ✅ 现货 | ❌ | 🟡 辩论制衡 |

#### 4.5.2 核心定位结论

> **本系统占据无人区**：没有任何对标项目同时做到 LLM 多智能体推理 + 监督/RL 因子引擎 + 多周期协调 + 进化/学习闭环 + 加密永续执行。
>
> - **最接近的 LLM 系统**（TradingAgents、ai-hedge-fund、WebCryptoAgent、Luo MAS）都**仅现货、无实盘执行、无多周期、无进化闭环**
> - **最接近的 ML 系统**（FreqAI）管线强但**无 LLM 层、仅现货**
> - **本系统的永续合约聚焦（保证金/杠杆/资金费率/清算）是所有 LLM 系统的独家差异化**
>
> **最可辩护的技术综合**：FreqAI 的 ML 管线严谨性（DataKitchen 上下文 + 持续重训 + 严格滚动窗 + 统一模型接口）+ TradingAgents 的 LLM 角色分类（对抗辩论 + 独立风险门 + 确定性数据验证覆盖层）。

#### 4.5.3 本系统 LLM 层已领先之处（保持并强化）

1. **防幻觉机制最完整**：证据链 + fact_guard + fail-closed。15 个对标中**仅 TradingAgents 有明确防幻觉机制**（`market_data_validator`），其余均依赖辩论/反思或完全没有。本系统已领先。
2. **规则风险护栏 + LLM 判断的混合**：本系统的 V5 门禁链（规则 fail-closed）+ LLM 智能体（判断）是**实证最优架构**（见 5.3 节 24k 实验结论）。
3. **永续合约永续合约永续合约**：资金费率/OI/清算/杠杆/保证金——所有 LLM 系统均缺。

#### 4.5.4 本系统 LLM 层可借鉴之处

1. **TradingAgents 的牛/熊对抗辩论**（P0-6）：本系统 MLTO 是 quant/qual 辩论，非对抗式。引入"一个 agent 必须支持交易、另一个必须反对"的对抗辩论，是廉价有效的防幻觉手段。
2. **TradingAgents 的确定性数据验证覆盖层**：在 LLM 输出之上叠加确定性市场数据快照校验。
3. **TradingAgents 的 LLM 供应商工厂模式**：本系统仅 DeepSeek，可引入工厂模式实现多供应商容错（OpenAI/Anthropic/Google/Bedrock/Azure）。
4. **FreqAI 的训练上下文对象**（P0-5）：`FreqaiDataKitchen` 模式——逐品种/逐周期上下文捆绑特征+标签+切分+列清单。
5. **FinRL 的 gym 环境抽象**：标准化 state/action/reward 用于 RL 层。
6. **TensorTrade 的可组合 ActionScheme/RewardScheme/Stopper**：解耦动作空间/目标/停止条件。
7. **WebCryptoAgent 的反思循环**：交易后自我批评（本系统有 DecisionRetrospective，可强化为显式反思 agent）。

---

## 第五部分　加密原生 alpha 源与 AI 工程化最佳实践（新增）

> 本部分覆盖两个维度：(1) 加密永续合约系统**应整合但本系统尚缺的 alpha 源**；(2) **LLM 在交易系统中的工程化最佳实践**（延迟/成本/防幻觉）。这些是传统量化框架不涉及、但 AI 原生加密系统必须考虑的。

### 5.1 加密衍生品 alpha（对永续合约系统最高价值）

> **本系统已有部分**（`factor_engine/derivatives/`：Funding/OI 分歧、多空比、清算热图、期权结构），但深度和广度可大幅增强。

#### 5.1.1 资金费率（本系统已有，可深化）

- **极端值反向信号**：资金费率极正 = 杠杆多头过度（反转时空头挤压风险）；极负 = 空头挤压风险
- **跨 DEX 资金费率套利面**：Hyperliquid/Aster/dYdX/EVEDEX 资金成本差异显著（Bitsgap 2026 对比）→ delta-neutral 套利表面
- **数据源**：CoinGlass API、Coinalyze（聚合 OI + 资金（真实+预测）+ 清算 + 基差）、The Block（含 CME 机构衍生品数据）
- **本系统现状**：已有 `factor_engine/sentiment/` 的 Funding Rate 系列因子 + `arbitrage/` 资金费率套利执行器。**建议**：接入 CoinGlass/Coinalyze API 做跨场所资金费率矩阵监控

#### 5.1.2 未平仓合约（OI）与价格背离（增强）

- **OI 上升 + 价格平稳/上升** = 杠杆头寸积累，突破在即
- **OI 下降 + 价格** = 去杠杆
- **本系统现状**：已有 `derivatives/` 的 OI Delta 因子。**建议**：加 OI-价格背离专门因子 + 背离阈值告警

#### 5.1.3 清算级联预测（本系统已有，可强化为 alpha）

- **清算热图**：特定价格水平的清算集群如同磁铁——预测级联水平可提前布局强制波动
- **清算磁铁**（`factor_engine/derivatives/` 已有 LiquidationMagnet）
- **建议**：将清算级联预测提升为一等 alpha 源，结合杠杆分布建模

#### 5.1.4 期权偏度（本系统缺——最高价值缺口）

> **这是本系统最值得新增的 alpha 源**——大多数永续系统忽略期权数据，但它反映方向性偏见。

- **Deribit 偏度**是 BTC/ETH 期权主要 alpha 源（Amberdata 文章：看跌期权买盘 = 对冲/恐惧）
- **alpha**：波动率风险溢价、偏度方向（put bid = 恐惧）、期限结构倒挂、做市商 gamma 定位（导致钉住/磁吸效应）
- **量化方法**：FDIC 2024 论文用卡尔曼滤波建模偏度动态——可直接适配为 alpha 信号
- **数据源**：Deribit（主）、Amberdata
- **本系统现状**：`derivatives/` 有 OptionsStructure 因子但可能较浅。**建议**：接入 Deribit API，实现偏度/gamma 定位因子

### 5.2 链上数据与智能资金追踪（本系统部分缺）

> 本系统已有 `factor_engine/onchain/`（交易所净流、鲸鱼交易、TVL、活跃地址），但可深化。

#### 5.2.1 一级平台与核心信号

| 平台 | 强项 | 核心 alpha 信号 |
|------|------|---------------|
| **Glassnode** | 机构级，200+ 指标 | MVRV z-score（均值回归 regime）、SOPR（获利了结压力）、交易所净流（存提失衡→局部顶底）、稳定币供应比（购买力）、矿工流出 |
| **Nansen AI** | 2.5 亿+ 标记钱包，实时 | **智能资金追踪 + 鲸鱼流跟随**——最高价值 alpha |
| **Santiment** | 链上+社交+开发活动 | 行为分析，低延迟信号 |

#### 5.2.2 学术验证

"Bitcoin Price Direction Prediction Using On-Chain Data and Feature Engineering"（ScienceDirect）直接评估链上特征的 ML 预测力——为本系统接入链上因子提供了实证依据。

#### 5.2.3 本系统建议

- 接入 Glassnode API（MVRV/SOPR/交易所净流）作为 regime 判断因子
- 接入 Nansen 智能资金/鲸鱼流作为事件驱动 alpha（大额钱包移动 → 预测抛压）
- 这些与 LLM 智能体高度互补：LLM 擅长解读"鲸鱼为何移动"这种模糊事件

### 5.3 LLM 工程化：延迟、成本、防幻觉

#### 5.3.1 关键实证：LLM vs 规则的分界（24,000+ 实验）

> **最重要发现**：一位从业者跑了 **24,000+ 次实验**测试 LLM vs 规则用于**实盘加密交易执行**，结论**明确：对于执行门（下单/SL/TP/仓位/逐笔），规则系统在所有配置下都胜过 LLM**。

**决策框架**（综合多源）：
- **速度关键、确定性执行**（下单/SL/TP/仓位/逐笔）→ **规则**。LLM 太慢且非确定性
- **模糊、上下文相关、低频决策**（新闻解读/情绪/regime 分类/多因子综合/"这是黑客攻击吗"）→ **LLM 有真价值**
- **最佳实践是混合**：确定性系统处理快速/重复层；推理 LLM 处理较慢的判断层

**对本系统的启示**：
- ✅ **本系统已正确**：ScalpRouter 纯规则 <100ms vs 中长 SwingAgent/TrendAgent LLM——正是这个分界
- ⚠️ **警示**：不应将 LLM 引入短 tier 执行门（现有 ScalpRouter 设计正确）
- 📌 **强化点**：将 LLM 智能体定位在**中长周期 + 事件/regime 分析**（本系统已如此）

#### 5.3.2 LLM 延迟管理

- **TTFT（首 token 时间）** 目标：现代服务栈 <500ms（vLLM）
- **语义缓存**：嵌入 prompt，对相似查询返回缓存响应。Percona 报告 40-80% 成本降低 + 250x 加速。**市场状态 prompt 高度可缓存**（"分析 BTC 资金=0.03%, OI=..."这类状态窗口会重复）
- **分层模型**：小/快模型（Llama-3-8B/Qwen）处理常规决策；大/推理模型（Claude/GPT）处理高信念或模糊事件
- **异步/发射后不管**：LLM 并行运行；确定性规则处理快路径；LLM 输出就绪时调整仓位偏向

**本系统现状**：已有 `LOCAL_LLM_GPU_HOST_GUIDE.md`、`LOCAL_LLM_SELF_TRAINING_DESIGN.md`、`LOCAL_LLM_TRADING_HOST_GUIDE.md`——**与最佳实践一致**。建议加语义缓存层。

#### 5.3.3 LLM 成本管理

- **语义缓存**是主导杠杆（40-90% 节省，GPTCache/Valkey/Azure Redis）
- **模型路由**：易查询走便宜模型，难查询走昂贵模型
- **逐周期 token 预算**：硬上限；结构化输出减少 token vs 自由文本
- **本系统建议**：加显式成本控制层（参考 "RAG Is Burning Money" 的成本控制层设计）

#### 5.3.4 防幻觉（本系统已领先，可强化）

**收敛技术**（跨源）：
1. **RAG**——将 LLM 响应锚定在已验证的市场数据/新闻
2. **结构化输出约束**——强制 JSON/Pydantic schema（本系统可用 Pydantic 自动生成 JSON schema）
3. **领域锚定的分层检索 + 验证**（arXiv 2603.17872）——多层验证架构拦截事实错误
4. **验证循环**——生成后对照源数据事实核查（Mem0 报告 95%+ 幻觉减少）
5. **接地记忆**——持久化已验证记忆存储，跨会话一致
6. **困惑度/置信度指标**——目标 85%+ 事实准确率

**本系统现状**：证据链 + fact_guard 已强。**建议借鉴 TradingAgents 的确定性 market_data_validator 覆盖层**——在 LLM 输出之上叠加确定性市场数据快照校验。

**关键警示**：在 15 个对标中，**仅 TradingAgents 有明确防幻觉机制**。FinGPT/FinRL/ai-hedge-fund 完全没有。本系统的 fact_guard + 证据链**比大多数现有系统更稳健**——这是值得保持和强调的优势。

### 5.4 强化学习在加密交易中的现实（成功与失败）

#### 5.4.1 当前最佳实践

- **算法**：PPO 和 SAC 主导。Freqtrade RL 模块用 SB3/RLlib
- **前沿**：Decision Transformer、AlphaStock（自注意力序列市场学习）
- **框架**：FinRL、TradeMaster（NeurIPS 2023 基准）、FinRL Contest 2025（RL + LLM 组合）

#### 5.4.2 RL 为何常在实盘失败（sim-to-real gap）

| 失败模式 | 细节 |
|---------|------|
| **sim-to-real gap** | PPO/SAC 从仿真零样本迁移到实盘持续表现不佳 |
| **非平稳/分布漂移** | 加密 regime 持续变化；平稳假设破裂。INFORMS："对非平稳环境施加平稳假设并称之为自适应 AI" |
| **奖励误指定** | 仅利润奖励忽略成本/滑点/regime → 脆弱策略 |
| **交易成本盲** | 忽略现实成本的回测产生膨胀结果（尤其永续资金费率） |
| **市场冲击** | 在被动数据上训练的 agent 不建模自己交易如何移动市场 |
| **尾部风险** | 罕见极端事件（清算级联、交易所宕机）在训练中欠表示 |
| **离线数据过拟合** | "MetaTrader"论文用保守目标直接解决此问题 |

**Reddit 从业者共识**：真正验证盈利的实盘 RL 部署很少公开记录——成功策略保持专有。

#### 5.4.3 对本系统的建议

> **RL 高风险高回报。谨慎防御性使用**：regime 分类、仓位规模、执行优化（动作空间有界、失败可恢复），**而非端到端方向性交易**。始终配对在线重训 + 保守目标。LLM 多智能体更适合方向/判断层。

**本系统现状**：`learning_core/rl_core/` 有 env/policy/replay_buffer/shadow 脚手架（早期阶段）。**建议**：将 RL 定位在仓位规模/regime 分类等有界任务，而非端到端方向决策；配对保守目标（参考"MetaTrader"双层 RL）；始终配对在线重训。

---

## 第六部分　学习进化系统深度对标（v3 新增）

> 本部分对标**学习/进化/自我改进**这一元层（meta-layer）——这是 v2 文档未覆盖、但对一个"全自动 AI 交易系统"至关重要的维度。传统量化框架（第三部分）对标工程严谨性，AI 量化框架（第四部分）对标学习推理，而本部分对标**系统如何从经验中改进自身**。
>
> **核心矛盾（代码级核验）**：本系统的**数值层学习是生产级成熟的**，但**认知层学习（LLM 自我改进）已死**。

### 6.1 本系统学习进化架构全景

> 所有断言均已逐文件核验，路径相对 `001Alpha\Hyper-Alpha-Arena\backend\`。

#### 6.1.1 学习进化完整闭环

```
┌─────────────────────────────────────────────────────────────────────┐
│  假设生成 (每6h, hypothesis_scan)                                     │
│  StrategyHypothesisEngine.run_full_cycle:                            │
│    LLM生成4假设 → 240bar回测验证(sharpe≥0.8/win≥0.45/mdd≤15%)       │
│    → promote_to_evolution(写StrategyTemplate, paper_only)            │
│    → [HYPOTHESIS_AUTO_EVOLVE] trigger_emergency_evolution           │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ EvolutionEnvelope: hypothesis→validate→deploy
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  进化优化 (每3天, weekly_evolution)                                   │
│  NSGAIIOptimizer.evolve_multi_objective (genetic_optimizer.py:365)   │
│    目标: profit_factor↑, max_drawdown↓, sharpe↑ (3目标)              │
│    20代×24个体, 快速非支配排序+拥挤距离, μ+λ替换                     │
│    ParetoFront.get_best_compromise() → 冠军基因组                    │
│  fallback: GeneticOptimizer.evolve (10×10紧急, 锦标赛k3, 精英2)      │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ EvolutionEnvelope: evolve
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  冠军持久化与晋升 (strategy_evolver.py)                              │
│  persist_genetic_result → _save_champion_with_lineage(BacktestRun)   │
│  _should_promote: Gate1(OOS sharpe/mdd/pf/overfit) + tier阈值        │
│  _promote_template: 更新rating/标签/风控参数/同步实盘策略/推送编排器  │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ EvolutionEnvelope: deploy
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  实盘观察 → 反馈 (decision_feedback_service.py, 922行)               │
│  build_net_attribution: net-of-fees P&L按nature/symbol分桶           │
│  _derive_net_lessons: 经济教训(注入prompt约束, advisory软约束)        │
│  apply_gate_adjustments: 4规则(双向自校正: 差则收紧/好则放松)         │
│    → submit_intent(source="decision_feedback", TTL=36h, prio=60)    │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ EvolutionEnvelope: feedback
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  RuntimeGovernor 仲裁 (runtime_governor.py, 618行) — 唯一写入者       │
│  多源意图: manual(100)>opencode(80)>decision_feedback(60)>           │
│           local_llm(55)>evolution_gc(50)>maturity(40)>default(30)    │
│  [实盘反馈优先级 > 回测冠军]                                          │
│  _reconcile_keys → runtime_tuning_store.apply_patches                │
│  硬夹紧走廊: min_risk_reward[1.5,2.5], confidence[60,90], daily[3,20]│
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  门禁执行 (每60s, unified_gate 重读 runtime_tuning.json)              │
│  V5门禁按新阈值生效, 无需重启进程                                     │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  冠军恢复 (每10min, champion_recovery_service.py, 71行)               │
│  暂停的champion_protected策略, 6h冷却后, 若StrategyMemory             │
│  (≥10交易/≥50%胜率/≤18%回撤)仍达标 → 恢复active                      │
└─────────────────────────────────────────────────────────────────────┘

旁路系统:
  • LearningLedger: 每个EvolutionEnvelope写入data/learning_core.db + WebSocket广播
  • 因子衰减监控: IC recent vs history → retire(<0.01)/reduce(<0.03) → 权重惩罚乘子
  • 因果发现(30交易触发): Granger OLS + 偏相关条件独立性 + LLM叙事 → discovered_rules
  • RL核心(线性Q, shadow三重门控, 仅手动API训练): replay_buffer←backtest交易seed
  • QAA 6层记忆+RAG: 交易outcome→trading_lessons.jsonl(hash嵌入+线性扫描)
```

#### 6.1.2 关键文件索引

| 组件 | 文件 | 行数 | 成熟度 |
|------|------|------|--------|
| 学习编排（门面） | `services/learning_core/orchestrator.py` | 103 | 🟢 生产 |
| EvolutionEnvelope 协议 | `services/learning_core/envelope.py` | 164 | 🟢 生产（8阶段血缘） |
| 血缘账本 | `services/learning_core/ledger.py` | 254 | 🟢 生产（SQLite+WS广播） |
| 回测结果摄入 | `services/learning_core/backtest_loop.py` | 163 | 🟢 生产 |
| **NSGA-II 多目标** | `services/genetic_optimizer.py` | 594 | 🟢 生产（从零实现） |
| 进化调度器 | `services/evolution_scheduler.py` | 1,383 | 🟢 生产（3天周期+紧急） |
| 策略进化器 | `services/strategy_evolver.py` | 2,300 | 🟢 生产（含晋升门+实盘同步） |
| 假设引擎 | `services/strategy_hypothesis_engine.py` | 776 | 🟢 生产（LLM生成+回测验证） |
| **RuntimeGovernor** | `services/runtime_governor.py` | 618 | 🟢 生产（多源仲裁） |
| 有界调参存储 | `services/runtime_tuning_store.py` | 328 | 🟢 生产（硬夹紧走廊） |
| 决策反馈 | `services/decision_feedback_service.py` | 922 | 🟢 生产（双向自校正） |
| 冠军恢复 | `services/champion_recovery_service.py` | 71 | 🟢 生产 |
| 因子衰减监控 | `services/factor_engine/factor_decay_monitor.py` | 152 | 🟢 功能 |
| **因果发现** | `services/causal_discovery_engine.py` | 929 | 🟢 功能（Granger+偏相关+LLM） |
| 概念漂移检测 | `services/concept_drift_detector.py` | 413 | 🟢 功能（KS+MMD+ADWIN） |
| RL 核心 | `services/learning_core/rl_core/` | ~733 | 🟡 早期（线性Q, shadow） |
| **prompt 进化** | `services/strategy_learning_service.py:_evolve_prompt` | — | 🔴 **已禁用（36/36失败）** |
| QAA AutoOptimizer | `qaa_architecture_package/qaa/evolution/optimizer.py` | 357 | 🟡 朴素（-10%扰动） |
| QAA 6 层记忆 | `qaa_architecture_package/qaa/knowledge/` + `memory/` | — | 🟡 部分（T2→T3固化） |
| QAA RAG | `qaa_architecture_package/qaa/knowledge/rag.py` | 165 | 🔴 弱（hash嵌入+线性扫描） |

#### 6.1.3 本系统学习进化的领先之处（保持并强化）

1. **NSGA-II 多目标进化是生产级成熟的**（`genetic_optimizer.py:365-421`）：从零实现快速非支配排序（`:466-499`）+ 拥挤距离（`:519-543`）+ 锦标赛选择（`:545`），3 目标（profit_factor↑/max_drawdown↓/sharpe↑），μ+λ 替换。**多数开源交易框架根本没有多目标进化**（Freqtrade hyperopt 是单目标 loss）。
2. **RuntimeGovernor 多源仲裁 + 双向自校正**是独创的：实盘反馈（prio 60, TTL 36h）优先级**高于**回测冠军（prio 50, TTL 7天），且有**双向自校正**（`decision_feedback_service.py:494-511`——差则收紧、好则放松，被 unified_gate 的 `[1.5,cap]/[60,90]` 硬边界保护，状态转差时自动回正）。这是比简单的"回测冠军即王"更成熟的反馈控制。
3. **8 阶段血缘账本**（`envelope.py:21-39`：hypothesis→validate→evolve→learn→rl_decide→deploy→observe→feedback）+ SQLite 持久化 + WebSocket 广播，提供了**完整可追溯的进化审计**。Qlib 的 online serving 有生命周期管理但没有这种粒度的血缘树。
4. **Granger 因果发现**（`causal_discovery_engine.py`，929 行）：纯 numpy OLS Granger 检验 + 偏相关条件独立性排除混淆因子 + LLM 叙事。**因果 > 相关**的设计哲学在开源交易系统中罕见。
5. **因子衰减监控→权重惩罚闭环**（`factor_decay_monitor.py:137-148`）：IC recent vs history 算指数半衰期，retire/reduce/keep 推荐→权重惩罚乘子喂入加权。

#### 6.1.4 本系统学习进化的核心弱点

1. **认知层学习已死**（P0-7）：prompt 自动进化 36/36 失败，系统只能调数值阈值，不能改进 LLM 推理本身。
2. **RAG 检索质量弱**（P0-8）：hash 词袋嵌入（非神经）+ O(N) 线性扫描 + 无重排序，无法语义匹配。
3. **无防遗忘**（P0-9）：RL/因子学习层重训会灾难性遗忘。
4. **演化仅参数级**（P1-11）：NSGA-II 演化固定策略骨架的参数，无表达式发现。
5. **元学习缺失**（P1-10）：因子衰减仅被动检测，无主动分布漂移预测。
6. **无 PBO 累计诊断**（P1-13）：NSGA-II 每代增加 trial N 但 DSR 未跨代累计。

---

### 6.2 八维度深度对标

#### 6.2.1 进化算法：NSGA-II（参数级）vs GP（表达式级）/ CMA-ES / MAP-Elites

**本系统**：NSGA-II（`genetic_optimizer.py`，594 行）演化**固定策略骨架的参数值**（stop_loss_pct/take_profit_pct/leverage/信号权重/RSI 阈值）。参数空间在 `evolution_scheduler._get_full_param_ranges()` 定义。

**业界 SOTA**：
- **遗传编程 GP**（arXiv 2504.05418 "Evolving Financial Trading Strategies with Vectorial GP"）：演化**策略表达式树本身**（如 `IF (RSI(t)*log_volume) > (MACD_hist-ATR) THEN ...`），而非骨架内参数。这是**策略发现**，严格强于参数调优。工具：DEAP、gplearn、DolphinDB Shark GPLearn。
- **CMA-ES**（Hansen）：连续实值参数上**收敛快于 GA**——它适应搜索分布的协方差以匹配地形几何，学习参数间相关性。GA 胜在离散/组合/多模态多样性；CMA-ES 胜在连续黑箱。本系统的 `runtime_tuning.json` 连续阈值用 CMA-ES 精调会比当前 NSGA-II 更快收敛。Optuna 原生支持。
- **OpenAI ES**（arXiv 1703.03864）：梯度-free 直接策略搜索，antithetic（配对±噪声）采样，可扩展到数千核。适合连续策略交易模型。
- **MAP-Elites / MOME**（Mouret & Clune 2015；MOME arXiv 2202.03057）：**质量-多样性**算法——将行为/特征空间分网格，每格只留精英。产生**一组行为多样的高性能策略库**（如最佳趋势 regime scalper、最佳震荡 regime scalper...），运行时按当前 regime 描述选 elite。MOME = NSGA-II + 行为多样性生态位。**严格升级单冠军恢复**。

**评估**：
- 🟢 本系统 NSGA-II 多目标进化**领先多数开源框架**（多目标本身已少见）
- 🔴 本系统**仅参数级**，缺 GP 表达式发现（策略发现能力）
- 🔴 缺 MAP-Elites 多样性冠军库（单 champion recovery → regime 索引 elite 网格）
- 🟡 连续参数精调可用 CMA-ES 加速（当前 NSGA-II 在连续空间非最优）

#### 6.2.2 元学习/分布漂移：被动检测 vs DDG-DA 主动预测

**本系统**：因子衰减监控（`factor_decay_monitor.py`）是**被动的**——IC 下降后检测到 `declining`/`dead` 才 retire/reduce。概念漂移检测（`concept_drift_detector.py`，KS+MMD+ADWIN，7d/14d 双窗口）也是事后触发。

**业界 SOTA**：
- **DDG-DA**（Qlib, AAAI 2022, arXiv 2201.04038）：**主动**而非被动。元模型预测**下一期的数据分布**，然后在重训前重加权历史样本以匹配预测分布。三步：(1) DDG 神经元模型预测近未来分布；(2) Domain-Attentive 注意力学习哪些历史时期最像即将到来的时期；(3) MAML 式 episodic 元训练——优化**下游预测性能**而非统计分布匹配。成本 ~45GB RAM。
- **DoubleAdapt**（arXiv 2306.09862）：DDG-DA 后继，同时适应数据**和**模型元参数。
- **MAML/MetaTradeNet**：学一个能快速适应新 regime 的初始模型状态。

**评估**：
- 🔴 本系统**被动 vs 主动**——这是从"事后补救"到"事前预防"的质的差距
- 🟡 本系统因子衰减监控 + 概念漂移检测提供了 DDG-DA 所需的**信号源**，升级路径清晰：把 `concept_drift_detector` 的输出从"触发回顾"改为"驱动 DDG-DA 预测+预加权"

#### 6.2.3 持续学习/防遗忘：无 EWC vs EWC+replay

**本系统**：RL 核心（`rl_core/policy.py`，线性 Q-learning）的 `update()` 是单步增量 TD 更新，**无防遗忘机制**。因子学习层（整改 #4/#10 规划中）重训也无防遗忘。`backtest_loop._seed_replay` 把回测交易 seed 进 replay_buffer，但这只是数据填充，不是 anti-forgetting。

**业界 SOTA**：
- **EWC（Elastic Weight Consolidation，PNAS 2017，13827 引用）**：在 loss 加二次惩罚 `λΣF_i(θ_i−θ*_i)²`，F_i 是权重 i 对旧任务的 Fisher 信息（重要性）。重要权重几乎不动，不重要的自由适应。直接适用于交易模型在新数据上重训时防旧 regime 模式遗忘。
- **EWC + replay 混合**（EVCL 2024, arXiv 2406.15972）：replay 旧 regime 代表性样本 + EWC 惩罚。
- **FreqAI continual_learning flag**：仅"从上次权重热启动"，**不防遗忘**——FreqAI 提供开关但不提供 anti-forgetting 机制。
- **Uncertainty-PER**（RLJ 2025）：PER（优先经验回放）在非平稳环境会过时，Uncertainty-PER 加入不确定性避免过时高 TD 误差。

**评估**：
- 🔴 本系统**完全无防遗忘**——RL/因子学习层重训会灾难性遗忘旧 regime
- 🟡 本系统已有 `replay_buffer`（SQLite）和 `concept_drift_detector`，EWC 的集成点清晰

#### 6.2.4 LLM 自我改进：prompt 进化 36/36 失败 vs DSPy/Reflexion/Voyager/AlphaEvolve

> **这是本系统学习进化的最大缺口，也是最高价值的修复方向。**

**本系统现状（代码级核验）**：
- prompt 自动进化在 `strategy_learning_service._evolve_prompt`（`:761-858`）：加载 PromptTemplate → 构造 evolution_instruction 让 LLM 微调 prompt（保持结构/占位符/增量/中文）→ `_call_llm_for_prompt_evolution_v2` → 验证门（optimized_text 非空且 `len≥120`）。
- **失败**（`config/settings.py:934-937`）："历史 36/36 次 LLM 改写提示词全部失败，改用 v5_runtime_gates 运行时门槛闭环代替"——LLM 一致返回 None 或 <120 字符。
- **替代**：2026-06-11 禁用后改用 RuntimeGovernor 数值闭环（调阈值，不调 prompt）。
- **失效根因**：无目标信号 + 无失败原因记录的 LLM 自我模板突变。TACL 论文（"When Can LLMs Actually Correct Their Own Mistakes?"）证实：**无外部信号的自我 prompt 突变不可靠**——这正是 36/36 失败的根因。

**业界 SOTA（三条不依赖模板突变的可行路径）**：

**路径 A — DSPy（编译而非手写 prompt）**：Stanford NLP `stanfordnlp/dspy`。写声明式模块（Signatures）+ metric + 少量示例，teleprompter/optimizer（如 `BootstrapFewShotWithRandomSearch`、`MIPRO`）自动搜索指令和 few-shot 示例以**最大化 metric**。把 prompt 当**编译输出**而非手写产物。**关键**：本系统已有丰富评估信号（V5 gate 通过率、IC、实盘统计），DSPy 可用这些作 metric 编译——这正好补上 36/36 缺失的"目标信号"。arXiv 2507.03620 证实 DSPy 可匹敌/超越手工 prompt。**这是最推荐的采用**。

**路径 B — Reflexion（verbal RL，无权重/prompt 模板变化）**：Shinn et al. NeurIPS 2023（arXiv 2303.11366）。Agent 循环：(1) Actor 行动 → (2) Evaluator 给信号 → (3) Self-Reflection LLM 生成自然语言批评 → (4) 批评存入**episodic memory** → 条件化下次尝试。**不更新权重，不突变模板——反思作为上下文追加**。**关键**：本系统已有 `ReflexionBackend`（priority 60，async）——组件已存在！缺口是把它接到"存储教训为检索上下文"而非"模板突变"。

**路径 C — Voyager 技能库（增长代码级能力）**：arXiv 2305.16291。三组件：(1) 自动课程（LLM 提下一个任务）；(2) **ever-growing 技能库**（每个完成的技能=可执行代码，按嵌入索引，检索复用于更难任务）；(3) 迭代 prompting（写代码→环境反馈含错误→修→重复）。翻译到交易：积累**命名的代码级技能**（如"detect_funding_divergence_entry"、"size_by_vol_target"）按语义相似检索。这是**能力增长**，严格强于且更可调试。

**支撑**：
- **AlphaEvolve / OpenEvolve**（DeepMind 2025, arXiv 2506.13131；开源 `algorithmicsuperintelligence/openevolve`）：LLM + 岛模型演化搜索 + 自动程序评估。**演化代码本身**，非 prompt 文本。是本系统 prompt 进化的"成熟版"——工作的机制（代码+目标 fitness+归档）正是模板突变缺失的。OpenEvolve 文档明确可用于"auto-improving Bitcoin algo-trading strategies with LLMs"。

**评估**：
- 🔴 本系统**认知层学习完全缺失**（36/38 失败后禁用，无替代）
- 🟢 **修复路径清晰且低风险**：DSPy（用现有 gate/IC 指标编译）/ Reflexion（已有 backend，改接检索上下文）/ Voyager（技能库）。三条路都共享 36/36 缺失的关键属性：**目标反馈信号 + 持久化但不触碰基础模板**。

#### 6.2.5 记忆/RAG：hash 嵌入+线性扫描 vs 神经嵌入+混合检索+ANN+重排序

**本系统（代码级核验）**：
- **嵌入**（`qaa/knowledge/embeddings.py:31-59`）：`HashEmbeddingProvider`——SHA-256 每个词→维度索引，符号累加，L2 归一化，128 维默认。**词袋 hash 投影，非神经嵌入**。ABC 允许插入 OpenAI/sentence-transformers，但**两份代码库都未接入真模型**（`factory.py:35` 恒实例化 HashEmbeddingProvider）。
- **存储/检索**（`qaa/knowledge/stores/jsonl.py:89-109`）：`JsonlKnowledgeStore`，每次 mutation 原子 JSONL 重写，检索是**O(N) 线性扫描全部 chunk 做余弦相似度**，无 ANN 索引。`factory.py` 说"production 可替换 pgvector/Qdrant"——**均未实现**。
- **查询**（`qaa_trade_memory_bridge.py:256`）：`build_qaa_rag_lessons_section` 查询 `["交易教训","亏损复盘","止损","入场","仓位"] + symbols + regime + nature`。**中文术语需字面匹配 hash**——"交易教训"与"复盘教训"余弦相似度可能为 0（不同 token），语义检索失效。
- **无重排序**，无混合检索（BM25+向量），无查询扩展。

**业界 SOTA（2024 默认架构）**：
```
Query → [BM25 稀疏 ‖ 稠密向量检索]
      → RRF/加权融合（混合，+26-31% NDCG 优于纯向量，Atlan）
      → Top-K 候选
      → 交叉编码器重排序（sentence-transformers cross-encoder/ms-marco）
      → Top-N 上下文 → LLM
```
- **混合（BM25+向量）现已是默认**，非可选。纯向量漏精确 ticker/关键词/OOV 词——交易中致命（符号代码、关卡名）。
- **ANN 索引**：FAISS（flat/IVF/HNSW）、HNSW（hnswlib/pgvector）、Qdrant。
- **神经嵌入**：sentence-transformers（如 `all-MiniLM-L6-v2` 多语言含中文），或 BGE（中文强）。

**评估**：
- 🔴 本系统 RAG 是**pre-2023 水平**——hash 词袋 + 线性扫描 + 无重排序 + 无混合
- 🟢 **低风险高收益升级**：神经嵌入 + 混合检索 + ANN + 重排序，量化 +26-31% NDCG，且直接修复中文交易术语语义匹配失效问题

#### 6.2.6 反馈闭环：RuntimeGovernor（多源仲裁，双向）vs PBT/shadow→canary→full

**本系统**：RuntimeGovernor（`runtime_governor.py`，618 行）多源意图仲裁 + runtime_tuning_store 硬夹紧 + 双向自校正。另有 `qaa_evolution_bridge.py`（642 行）实现了**符号级 A/B canary**（canary vs control 符号分桶，600s 观察，差异>0.005 确认/<-0.01 回滚，<3 交易回滚）——这是**真正的 A/B canary**，比 QAA 框架的 grayscale_pct 更成熟（bridge 实际没用 grayscale_pct 路由，而是按符号重实现 canary）。

**业界 SOTA**：
- **PBT（Population-Based Training，DeepMind）**：并行训练多模型随机超参，周期性最差者从最佳者复制权重+超参并扰动。发现**退火 schedule**（如学习率/探索衰减）而非静态点。Ray Tune `PopulationBasedTraining`。**PB2**：PBT+贝叶斯优化 bandit，对昂贵评估（策略回测）更 sample-efficient。本系统 NSGA-II 演化策略固定参数空间，PBT 演化模型**训练 schedule**——互补不重叠。
- **shadow→canary→full 自动晋升**（软件工程成熟实践，crossover 到交易）：mirror 实盘流量到候选不影实盘 → 逐渐增加资本分配 → 全量。**关键缺口**：本系统有 PaperExecutor + champion recovery，但缺**基于统计检验（如 live DSR 阈值）的自动从 shadow→canary→full 晋升门**。RuntimeGovernor 的意图仲裁（manual>opencode>feedback>evolution）是加这个晋升 rung 的自然位置。

**评估**：
- 🟢 本系统 RuntimeGovernor 多源仲裁 + 双向自校正**领先**（独创的"实盘反馈>回测冠军"优先级 + 自动回正）
- 🟢 本系统符号级 A/B canary（`qaa_evolution_bridge.py`）**已实现真 A/B**
- 🟡 缺 PBT 式训练 schedule 演化（与 NSGA-II 互补）
- 🟡 缺基于统计检验的自动 shadow→canary→full 晋升门（有组件缺编排）

#### 6.2.7 过拟合诊断（演化系统专有）：无 PBO 累计 vs CPCV+累积 trial DSR+PBO-aware 账本

**本系统**：NSGA-II 每代产生多个 trial，champion recovery 可能重引入之前丢弃的 in-sample 过拟合 champion——但**DSR 的 N 未跨代累计**，也无 PBO 诊断。血缘账本记录了 8 阶段但不记录 trial 计数。

**业界 SOTA**：
- **CPCV（Combinatorial Purged Cross-Validation，López de Prado AFML Ch.11-12）**：生成**多条**回测路径（非一条）同时 purge 标签泄漏，产生 OOS 性能的**分布**而非点估计。α(i)<1 方差低于标准 CV。
- **累积 trial DSR**：演化系统**持续跑 trial**，DSR 的 N 每代增长。两个含义：(1) N 必须**跨代累计**，否则 DSR 虚高乐观；(2) champion recovery 重引入算额外 trial。**血缘账本应在 8 阶段每阶段记录 trial N**，使 DSR/PBO 可诚实重算——这是具体可采用的"让账本 PBO-aware"模式。
- **PBO-aware 账本**：EvolutionEnvelope 每 emit 时附带当前累计 trial 计数 + 该 lineage 的 IS/OOS 表现，使 PBO 可在线计算。

**评估**：
- 🔴 本系统**无 PBO 累计诊断**——演化系统持续跑 trial 但未跨代累计 N，champion 可能 in-sample 过拟合却未被诊断
- 🟢 **升级路径清晰**：EvolutionEnvelope 已有 8 阶段 + metrics，只需加 `cumulative_trial_count` 字段，DSR/PBO 即可跨代累计

#### 6.2.8 因果/反思：Granger 因果（强）+ reflection 已弃用 vs Reflexion SOTA

**本系统**：
- **因果发现（强）**（`causal_discovery_engine.py`，929 行）：Granger OLS（p<0.01, max lag 12）+ 偏相关条件独立性排除混淆 + LLM 叙事 + counterfactual sandbox + concept drift。**因果 > 相关**哲学在开源交易系统罕见。
- **反思（已弃用）**（`qaa/reflection_engine.py`，220 行）：line 25-30 发 `DeprecationWarning`——"功能已由 UnifiedLearningService._generate_key_lessons + StrategyMemory.key_lessons 替代"。替代物是**规则式**（pnl>0 强化/pnl<-0.5% 提取教训按 reason_map）。

**业界 SOTA**：
- **Reflexion**（见 6.2.4 路径 B）：verbal RL 的核心是**LLM 生成自然语言批评**而非规则式诊断。本系统把 reflection_engine 弃用为规则式 `_generate_key_lessons`，丢掉了 LLM 反思的推理深度。

**评估**：
- 🟢 本系统**Granger 因果发现领先**（多数框架无因果层）
- 🟡 本系统 reflection 降级为规则式，丢掉了 LLM 反思深度——可结合 Reflexion SOTA 升级（与 6.2.4 路径 B 同源）

---

### 6.3 学习进化能力矩阵

| 维度 | 本系统 | FreqAI | Qlib online | TradingAgents | FinRL | **业界 SOTA** |
|------|--------|--------|------------|--------------|-------|--------------|
| 进化算法 | 🟢 NSGA-II 多目标(参数级) | ❌ | RollingGen | ❌ | ❌ | **GP(表达式级)/CMA-ES/MAP-Elites** |
| 元学习/漂移 | 🔴 被动检测 | ❌ | **DDG-DA/DoubleAdapt** | ❌ | ❌ | DDG-DA 主动预测 |
| 持续学习/防遗忘 | 🔴 无 | 🟡 continual flag(无防遗忘) | ❌ | ❌ | ❌ | **EWC+replay** |
| LLM 自我改进 | 🔴 **36/38 失败已禁用** | ❌ | ❌ | 🟡 ChromaDB 记忆 | ❌ | **DSPy/Reflexion/Voyager/AlphaEvolve** |
| 记忆/RAG | 🔴 hash+线性扫描 | ❌ | ❌ | 🟡 ChromaDB+OpenAI 嵌入 | ❌ | **神经嵌入+混合检索+ANN+重排序** |
| 反馈闭环 | 🟢 RuntimeGovernor 多源仲裁+双向 | 🟡 retrain | Online Manager | ❌ | ❌ | **PBT/shadow→canary→full** |
| 过拟合诊断(演化) | 🔴 无 PBO 累计 | ❌ | ❌ | ❌ | ❌ | **CPCV+累积trial DSR+PBO-aware账本** |
| 因果发现 | 🟢 Granger+偏相关+LLM | ❌ | ❌ | ❌ | ❌ | — |
| 血缘账本 | 🟢 8 阶段+SQLite+WS | ❌ | 🟡 生命周期 | 🟡 LangGraph checkpointer | ❌ | PBO-aware 血缘 |
| 冠军管理 | 🟢 recovery(单) | ❌ | ❌ | ❌ | ❌ | **MAP-Elites(多样性库)** |

**核心定位结论**：本系统学习进化在**数值层（NSGA-II/RuntimeGovernor/血缘账本/因果发现）领先**，但在**认知层（LLM 自我改进）+ 记忆质量（RAG）+ 防遗忘 + 元学习**四个维度**明显落后**于研究级 SOTA。最高价值的修复是**复活认知层学习（DSPy/Reflexion）+ 升级 RAG（神经嵌入+混合检索）+ 加 EWC 防遗忘**——这三者共同决定系统能否真正"从经验中学习"。

---

## 第七部分　不足清单（分级）

> 分级标准：**P0** = 方法论缺陷，直接影响回测/决策真实性；**P1** = 架构债务，影响可维护性/一致性；**P2** = 增强项，提升但不致命。
> 本部分综合了传统量化对标（第三部分）、AI 量化对标（第四部分）与学习进化对标（第六部分）发现的所有不足。**v2 新增项**标注（v2），**v3 新增项**标注（v3）。

### P0 级（方法论缺陷——必须优先处理）

#### P0-1　Walk-Forward 方法论薄弱

- **当前代码**：`services/backtest_engine/walk_forward.py`（382 行）
- **证据**：
  - **穷举 grid search**（`_optimize_params` lines 170-201，`_generate_param_combinations` 用 `itertools.product` lines 203-218）——不可扩展，无 TPE/Optuna/CMA-ES
  - **单一优化指标**（`optimization_metric: str = 'sharpe_ratio'`，line 24；`_get_metric_value` 仅 6 个选项 lines 220-231）——无多目标、无 loss registry
  - **过拟合评分 = 朴素收益差**（`_calculate_overfitting_score` lines 312-332）：`overfitting = max(0, (mean_train - mean_test)/mean_train)` ——这是最朴素的 train/test 收益差，**无 PBO/CSCV、无 Deflated Sharpe Ratio、无 MinBTL**
  - **一致性评分 = 朴素相关**（`_calculate_consistency` lines 292-310）：`consistency = (corrcoef(train,test)+1)/2` ——粗糙
  - **无 purge/embargo gap**（`_generate_periods` lines 134-168）：`test_start = train_end + timedelta(days=1)` ——训练测试**相邻**，对多 bar 持有期/前瞻标签有泄漏风险
  - **固定 `step_days=21`**（line 20）——与因子 IC 衰减无关，不耦合
- **业界做法**：
  - **CSCV/PBO**（Bailey, Borwein, López de Prado, Zhu 2014/2017）：N 等分，对称组合 N/2 为 IS、N/2 为 OOS，PBO = 最优 IS 策略在 OOS 排名低于中位数的组合占比。**PBO>0.5 过拟合，<0.1 稳健。**
  - **Deflated Sharpe Ratio (DSR)**：对观察到的 Sharpe 做多重检验校正
  - **Purged/Embargoed WFO**（López de Prado）：训练/测试间插入 purge 间隙 + embargo 几个 bar，杀标签/自相关泄漏
  - **Freqtrade loss registry**：SharpeHyperOptLoss/SortinoHyperOptLoss/CalmarHyperOptLoss/MaxDrawDownHyperOptLoss/ProfitDrawDownHyperOptLoss
- **影响**：**回测可信度根本缺陷**。当前 walk-forward 的"通过"不能排除过拟合，优化出的参数可能只是历史噪音拟合。

#### P0-2　回测无子 K 线分辨率

- **当前代码**：`services/backtest_engine/backtest_engine.py`（548 行）
- **证据**：
  - `_check_stop_loss_take_profit`（lines 304-319）：`pnl_pct` 对 `bar['close']` 评估，SL/TP 触发基于**K线收盘价**，**无 intrabar high/low 路径测试**，无"谁先触及"判定
  - 成交价 = `bar['close']`（lines 324, 382）——仅收盘价成交
  - 同一根 K 线内 SL 和 TP 都可能被触及，但系统无法区分先后——**乐观偏差**（假设先 TP 后 SL）或**悲观偏差**
  - 潜在 bug：`_run_vectorized` line 239 引用未定义的 `trades_mask`（若运行向量化模式会 NameError）
- **业界做法**：
  - **Freqtrade `--timeframe-detail 1m`**：用更细粒度数据重放主周期 K 线，使 K 线内 SL/TP/trailing 在 1m 分辨率解析——**最大的真实性升级**
  - **Jesse**：订单在下一根 K 线开盘成交（防前视）
  - **NautilusTrader**：L1/L2/L3 订单簿撮合、纳秒延迟建模、部分成交、排队位置
- **影响**：**回测收益被系统性高估或低估**。对 scalp/高频策略影响尤甚（本系统的短 tier）。

#### P0-3　因子权重是手工 regime 权重表

- **当前代码**：`services/factor_engine/factor_weighting.py`（612 行）
- **证据**：
  - 6 个 regime × 12 因子权重是**硬编码字面量**（`_init_regime_weights` lines 71-200，见 3.4 节表格）
  - `MarketRegime` 枚举（line 24）：BREAKOUT/CONTINUATION/REVERSAL/ABSORPTION/EXHAUSTION/NOISE
  - 有限适应：`_fine_tune_weights`(±0.1)、`_smooth_transition`(EMA 0.3)、`apply_feedback_adjustments`(V3,±20%/因子)——**事后微调固定表，非学习**
  - GA（`genetic_optimizer.py`）优化**策略参数**，**因子权重本身非优化对象**
- **业界做法**：
  - **Qlib**：因子→DataHandler→Dataset→Model(LightGBM/GRU/Transformer)→predict→IC 加权融合，端到端 ML 学习最优加权。18+ 模型统一 fit/predict 接口
  - **Qlib DDG-DA**（AAAI 2022）：元模型学习市场状态→最优预测模型权重映射，处理分布漂移
- **影响**：**量化研究的核心价值缺失**。手工 6×12=72 权重是"人类先验"，无法从 380+ 因子中学习最优组合。这是与 Qlib 差距最大的维度。

#### P0-4　两套滑点模型不一致

- **当前代码**：`backtest_engine.py:33` vs `cost_model.py:56`
- **证据**：
  - `backtest_engine.py` `calculate_slippage`（lines 33-43）：**线性 volume-ratio 模型**，base 0.0003，`market_impact = (order_size_usd / daily_volume_usd) * 0.1`
  - `cost_model.py` `calc_slippage_rate`（line 56）：**分级 size-adjusted 模型**，base 0.0005，按 notional 分 4 档（>$100k +0.0004 / $20k-$100k +0.0002 / $5k-$20k +0.0001 / <$5k +0），加 nature 调整（trend_follow -0.0001 / scalp +0.0003），止损 ×2.0，clamp [0, 0.003]
  - `cost_model.py` docstring 声称与 live `fee_guard.py` 对齐，但 `backtest_engine.py` 用自己的简化模型——**两者未对齐**
- **业界做法**：
  - **单一成本真相源**：NautilusTrader、Freqtrade 均有单一 cost/slippage 模块，回测与实盘共用
- **影响**：回测成本预期与实盘脱节，回测"盈利"策略在实盘可能因成本差异而亏损。

#### P0-5（v2 新增）　缺 FreqAI 式 ML 训练管线

- **当前代码**：`services/learning_core/rl_core/`（RL 脚手架早期）、`services/genetic_optimizer.py`（GA 优化策略参数）、`services/factor_engine/`（因子计算但无统一训练上下文）
- **证据**：
  - **无统一训练上下文对象**——FreqAI 的 `FreqaiDataKitchen` 捆绑每个品种/周期的特征矩阵+标签+train/test 时间切分+精确特征列清单。本系统的因子计算（`factor_service.py`）、标签生成、训练数据组装散落各处，无单一上下文对象传递
  - **无前视防护的严格滚动窗口切片**——FreqAI 的 `train_period_days`/`backtest_period_days`/`live_retrain_hours` 构成严格滚动窗，预测窗永不重叠训练窗。本系统 `walk_forward.py` 的 train/test 相邻（`test_start = train_end + timedelta(days=1)`），无 purge/embargo
  - **无内建持续重训节奏**——FreqAI 每 `live_retrain_hours`（默认 12h）自动滑窗重训替换模型。本系统 `walk_forward.py` 是静态单次 `train_period_days=252`，无实盘持续重训循环
  - **无统一模型接口**——FreqAI 的 `IFreqaiModel` 基类统一定义 `train()`/`predict()`，5 大模型族（LGBM/XGB/CatBoost/PyTorch/RL）一行配置切换。本系统无此抽象，RL 脚手架与 GA 是独立路径
  - **无 `&` 前缀标签约定**——FreqAI 用 `&-` 前缀区分特征与标签，强制结构化。本系统无统一约定
  - **无 lookahead-analysis 工具**——Freqtrade 全局 `lookahead-analysis` 蒙特卡洛式检测偷看未来。本系统无等价工具
- **业界做法**：
  - **FreqAI**（`freqtrade/freqai/`）：`IFreqaiModel` + `FreqaiDataKitchen` + `feature_engineering_expand_all` + `set_freqai_targets` + `train_period_days`/`backtest_period_days`/`live_retrain_hours` + `lookahead-analysis`
  - **Qlib**：`DataHandlerLP`（learn/infer 处理器分离）+ `DatasetH`（按日期分片）+ `RollingGen`（滚动重训任务生成）
- **影响**：**ML 工程化的核心缺失**。无法安全地用监督学习模型替代手工 regime 权重表（P0-3），因为缺少 FreqAI 式的训练管线来安全地训练、验证、部署、重训模型。这是 P0-3 的前置依赖。

#### P0-6（v2 新增）　LLM 智能体缺结构化对抗辩论

- **当前代码**：`services/mlto/`（14 文件：`orchestrator.py`、`quant_layer.py`、`qual_layer.py`、`debate_layer.py`、`decision_hub.py`、`tranche_gate.py`...）
- **证据**：
  - 本系统 MLTO 是 **quant vs qual 辩论**（`debate_layer.py`），但这是**分析视角辩论**（定量 vs 定性），**非对抗式**（一方支持交易、一方反对）
  - 无 TradingAgents 式的**牛/熊对抗辩论**（`bull_researcher` vs `bear_researcher`，每个必须论证己方立场，N 轮迭代）
  - 无**独立风险角色层**（TradingAgents 的激进/保守/中立三角色，独立于交易决策辩论）
  - 无**确定性市场数据验证覆盖层**（TradingAgents 的 `market_data_validator.py`——在 LLM 输出之上叠加确定性快照校验）
- **业界做法**：
  - **TradingAgents**（`tradingagents/graph/setup.py`）：两层对抗辩论——研究辩论（牛 vs 熔，`should_continue_debate` N 轮）+ 风险辩论（激进 vs 保守 vs 中立，`should_continue_risk_analysis` N 轮）
  - **Luo MAS**（arXiv 2501.00826）：学术验证——辩论式架构胜过序列式/协作式（夏普 2.07）
  - **WebCryptoAgent**：反思循环（交易后自我批评）
- **影响**：**防幻觉手段单一**。本系统的 fact_guard + 证据链已强，但对抗辩论是**廉价且有效的额外防幻觉层**——强迫 LLM 同时考虑正反两面，减少单边确认偏差。Luo MAS 实证辩论式优于其他协调模式。

#### P0-7（v3 新增）　认知层学习已死（prompt 进化 36/36 失败）

- **当前代码**：`services/strategy_learning_service.py:_evolve_prompt`（`:761-858`）；禁用开关 `config/settings.py:934-937`
- **证据**：
  - prompt 自动进化流程：加载 PromptTemplate → 构造 evolution_instruction 让 LLM 微调 prompt（保持结构/`{{占位符}}`/增量/中文）→ `_call_llm_for_prompt_evolution_v2` → 验证门（optimized_text 非空且 `len≥120`）
  - **失败**（`settings.py:934-937`）："历史 36/36 次 LLM 改写提示词全部失败，改用 v5_runtime_gates 运行时门槛闭环代替"——LLM 一致返回 None 或 <120 字符
  - 2026-06-11 禁用，改用 RuntimeGovernor 数值闭环（调阈值，不调 prompt）
  - **失效根因**：无目标信号 + 无失败原因记录的 LLM 自我模板突变。TACL 论文证实**无外部信号的自我 prompt 突变不可靠**——这正是 36/36 的根因
- **业界做法**：
  - **DSPy**（`stanfordnlp/dspy`）：编译而非手写 prompt——声明式 Signature + metric + 示例，teleprompter 自动搜索指令/示例以**最大化 metric**。本系统已有 V5 gate 通过率/IC/实盘统计作 metric
  - **Reflexion**（arXiv 2303.11366）：verbal RL——反思作为**上下文追加**而非模板突变。本系统已有 `ReflexionBackend`（priority 60），缺口是接检索上下文而非模板
  - **Voyager 技能库**（arXiv 2305.16291）：增长代码级技能而非改 prompt 文本
  - **AlphaEvolve/OpenEvolve**（arXiv 2506.13131）：LLM+演化循环发现代码，是 prompt 进化的"成熟版"
- **影响**：**系统无法让 LLM 从经验中学习推理**——只能调数值阈值（RuntimeGovernor），不能改进 LLM 的分析/决策推理本身。这是"全自动 AI 交易系统"的核心能力缺失。

#### P0-8（v3 新增）　RAG 检索质量弱（hash 嵌入+线性扫描+无重排序）

- **当前代码**：`qaa_architecture_package/qaa/knowledge/embeddings.py:31-59`、`stores/jsonl.py:89-109`、`factory.py:35`；桥接 `services/qaa_trade_memory_bridge.py:256`
- **证据**：
  - **嵌入**：`HashEmbeddingProvider`——SHA-256 词→维度索引符号累加 L2 归一化 128 维，**词袋 hash 投影非神经**。ABC 允许插 OpenAI/sentence-transformers，但**两份代码库均未接入**（`factory.py:35` 恒实例化 HashEmbeddingProvider）
  - **检索**：`JsonlKnowledgeStore.search` **O(N) 线性扫描全部 chunk 做余弦**，无 ANN 索引。`factory.py` 说"可替换 pgvector/Qdrant"——**均未实现**
  - **查询**：`["交易教训","亏损复盘","止损","入场","仓位"]` 中文术语需**字面匹配 hash**——"交易教训"与"复盘教训"余弦可能为 0，语义检索失效
  - **无重排序**，无混合检索（BM25+向量），无查询扩展
- **业界做法**：**2024 默认架构**——混合检索（BM25+向量，+26-31% NDCG）+ ANN（FAISS/HNSW/pgvector）+ 交叉编码器重排序（cross-encoder/ms-marco）+ 神经嵌入（sentence-transformers/BGE 中文强）
- **影响**：RAG 是 pre-2023 水平。交易教训检索语义失效，LLM 拿到的"历史相似教训"可能是噪音。混合+重排序是**低风险高收益**升级。

#### P0-9（v3 新增）　无防遗忘机制（灾难性遗忘旧 regime）

- **当前代码**：`services/learning_core/rl_core/policy.py`（线性 Q-learning `update()` 单步增量 TD）、规划中的因子学习层（整改 #4/#10）
- **证据**：
  - RL `LinearQPolicy.update()`（`policy.py:109`）单步 TD，**无 Fisher 信息惩罚**，新 regime 数据会覆盖旧权重
  - 因子学习层（LightGBM/GRU）重训会从头/热启动，**无防遗忘**
  - `backtest_loop._seed_replay`（`:121-159`）把回测交易 seed 进 replay_buffer，但仅数据填充非 anti-forgetting
  - 概念漂移检测（`concept_drift_detector.py`）触发回顾但**不驱动防遗忘重训**
- **业界做法**：
  - **EWC**（PNAS 2017，13827 引用）：loss 加 `λΣF_i(θ_i−θ*_i)²` Fisher 惩罚，重要权重不动，不重要自由适应
  - **EWC+replay 混合**（EVCL 2024）：旧 regime 代表性样本回放 + EWC
  - **Uncertainty-PER**（RLJ 2025）：非平稳环境的优先经验回放，避免过时高 TD 误差
- **影响**：RL/因子学习层在 regime 切换后重训会**灾难性遗忘**旧 regime 有效模式，导致"学了新的忘旧的"——在加密 regime 频繁切换的市场尤其致命。

### P1 级（架构债务）

#### P1-5　无事件溯源（回测/实盘不一致风险）

- **当前代码**：状态是内存 mutable 对象（`full_auto_trading_service.py` 类属性）；回测引擎（`backtest_engine.py`）与实盘引擎是两套代码
- **证据**：`backtest_engine.py` 的 `BacktestEngine` 与实盘的 `live_executor.py`/`paper_executor.py` 是独立实现，共享逻辑靠人工对齐
- **业界做法**：**NautilusTrader 事件溯源**——订单/仓位/账户是事件日志物化视图，重放重建；回测与实盘共享同一 `ExecutionEngine`/`RiskEngine`/`Cache`/`MessageBus`/`Portfolio`，仅 `ExecutionClient`/`DataClient` 不同
- **影响**：回测策略在实盘行为可能漂移；崩溃后状态无法重建；无完整审计轨迹

#### P1-6　QAA 双轨重复

- **当前代码**：`QAA通信协议构架/qaa/`（独立包）vs `backend/services/qaa/`（交易侧自实现）
- **证据**：两套 `AgentCard` 定义、两套 `RuleRouter`。ADR 明确"业务域在 consumer 项目，不在 qaa 包内"，但 `backend/services/qaa/` 重新实现了 cards/rule_router/models/reflection_engine/state_layers
- **业界做法**：单一框架定义，consumer 通过 DomainPlugin 扩展（QAA 本身已有 DomainPlugin 机制）
- **影响**：维护两套相似代码，概念漂移风险；新人理解成本高

#### P1-7　full_auto_trading_service.py 19,727 行单体

- **当前代码**：`services/full_auto_trading_service.py`（19,727 行）
- **证据**：主循环 + 中长独立循环 + scalp 独立循环 + 套利 tick + 学习集成 + MLTO 学习 tick + 维护周期 + 持仓超时 AI 审查全部在一个类/文件
- **业界做法**：vnpy 的 `CtaEngine` 与策略分离；NautilusTrader 的 Actor 模型——每个组件独立生命周期
- **影响**：**可维护性重大风险**——修改一处易引入回归；测试困难；认知负荷极高

#### P1-8　缺引擎层硬风控

- **当前代码**：仅有业务层门禁（V5 decision_core）+ 账户/仓位风控（risk_management）
- **证据**：缺价格/数量精度校验、名义价值 min/max、保证金/自由余额检查、限流、TradingState 状态机、重复成交去重、超额成交保护、标准化 OrderDenied 原因码
- **业界做法**：**NautilusTrader RiskEngine** + `nautilus-risk` Rust crate——业务无关的引擎层通用硬风控，标准化原因码（QUANTITY_EXCEEDS_MAXIMUM/CUM_MARGIN_EXCEEDS_FREE_BALANCE/REDUCE_ONLY_WOULD_INCREASE_POSITION/TRADING_HALTED...）
- **影响**：依赖业务层门禁兜底，引擎层无最后一道防线；异常订单（精度错误、超量）可能穿透到交易所被拒，浪费 API 配额

#### P1-9（v2 新增）　加密原生 alpha 源覆盖不全

- **当前代码**：`services/factor_engine/derivatives/`（Funding/OI 分歧、多空比、清算热图、清算磁铁、期权结构、OI）、`services/factor_engine/onchain/`（交易所净流、鲸鱼交易、TVL、活跃地址）、`services/factor_engine/sentiment/`（Funding Rate、牛熊、恐惧贪婪）
- **证据（已有但可深化/缺失的 alpha）**：
  - **期权偏度缺失/浅**：`derivatives/` 有 OptionsStructure 但可能较浅。Deribit 偏度是 BTC/ETH 期权主要 alpha 源（看跌 bid = 恐惧），FDIC 2024 论文用卡尔曼滤波建模偏度动态——**本系统未充分利用**
  - **链上数据深度不足**：`onchain/` 有基础因子，但缺 Glassnode 的 MVRV z-score（均值回归 regime）、SOPR（获利了结压力）、稳定币供应比（购买力）；缺 Nansen 智能资金/鲸鱼流标记
  - **社交情绪 NLP 缺失**：`sentiment/` 有恐惧贪婪等聚合指标，但无 X/Reddit/Discord 的 LLM 情绪 NLP pipeline（2025 研究：LLM 已超越 FinBERT/VADER 处理加密俚语/反讽）
  - **跨 DEX 资金费率矩阵监控缺失**：`arbitrage/` 有资金费率套利执行，但无 CoinGlass/Coinalyze API 的跨场所资金费率矩阵实时监控
- **业界做法**：
  - **Glassnode/Nansen/Santiment**（链上）：MVRV/SOPR/交易所净流/智能资金流
  - **CoinGlass/Coinalyze**（衍生品）：聚合 OI + 资金（真实+预测）+ 清算 + 基差
  - **Deribit/Amberdata**（期权）：偏度/gamma 定位/期限结构
  - **LLM 情绪 NLP**：X/Reddit/Discord firehose → LLM 打分 → 逐资产情绪 z-score（带衰减）
- **影响**：作为永续合约聚焦系统，期权偏度和智能资金流是**尚未充分开采的高价值 alpha**。LLM 智能体尤其适合解读"鲸鱼为何移动"这类模糊事件（规则难处理），本系统未发挥这一协同优势。

#### P1-10（v3 新增）　元学习缺失（因子衰减仅被动检测）

- **当前代码**：`services/factor_engine/factor_decay_monitor.py`（152 行）、`services/concept_drift_detector.py`（413 行）
- **证据**：因子衰减监控是**被动的**——IC 下降后检测 `declining`/`dead` 才 retire/reduce。概念漂移检测（KS+MMD+ADWIN）也是事后触发回顾，不预测下期分布
- **业界做法**：**DDG-DA**（Qlib AAAI 2022, arXiv 2201.04038）——元模型**主动预测下一期数据分布**，重训前重加权历史样本匹配预测分布，MAML 式 episodic 元训练优化下游预测性能。**DoubleAdapt**（arXiv 2306.09862）同时适应数据和模型元参数
- **影响**：本系统"事后补救"vs SOTA"事前预防"。在加密 regime 频繁切换市场，被动检测的滞后性意味着因子失效后已产生亏损才退役

#### P1-11（v3 新增）　演化仅参数级（无 GP 表达式发现/MAP-Elites 多样性库）

- **当前代码**：`services/genetic_optimizer.py`（594 行 NSGA-II）、`services/champion_recovery_service.py`（71 行单冠军恢复）
- **证据**：
  - NSGA-II 演化**固定策略骨架的参数值**（stop_loss_pct/take_profit_pct/leverage/信号权重/RSI 阈值，`evolution_scheduler._get_full_param_ranges`），**非表达式树**
  - champion_recovery 是**单冠军**恢复（6h 冷却），非多样性冠军库
- **业界做法**：
  - **遗传编程 GP**（arXiv 2504.05418）：演化策略**表达式树**（`IF (RSI*log_vol) > (MACD-ATR) THEN ...`）——策略发现严格强于参数调优。工具 DEAP/gplearn
  - **MAP-Elites/MOME**（arXiv 2202.03057）：行为特征空间分网格每格留精英——**一组行为多样的高性能策略库**（最佳趋势 scalper/最佳震荡 scalper...），运行时按 regime 选 elite。严格升级单冠军
- **影响**：本系统只能调参不能发现新策略结构；单冠军在 regime 切换时可能全失效，多样性库可保证总有适配当前 regime 的策略

#### P1-12（v3 新增）　QAA AutoOptimizer 是 -10% 朴素启发式

- **当前代码**：`qaa_architecture_package/qaa/evolution/optimizer.py`（357 行）
- **证据**：`optimizer.py:214-225` 的"调参"是**对所有数值参数统一 -10% 扰动**——无梯度、无贝叶斯、无元学习。评估/回滚机制健全（grayscale+auto_rollback），但"学习"本身是固定启发式
- **业界做法**：**CMA-ES**（连续参数收敛快于 GA）、**Optuna TPE/贝叶斯**、**PB2**（PBT+贝叶斯 bandit）
- **影响**：QAA 框架的"自动优化"名不副实——-10% 均匀扰动对相关性参数（如 stop_loss 与 leverage 联动）无感知，优化效果有限

#### P1-13（v3 新增）　无 PBO 累计诊断（演化系统过拟合盲区）

- **当前代码**：`services/learning_core/envelope.py`（164 行 8 阶段）、`services/genetic_optimizer.py`（NSGA-II）
- **证据**：NSGA-II 每代产生多 trial，champion recovery 可能重引入丢弃的 in-sample 过拟合 champion——但**DSR 的 N 未跨代累计**，无 PBO 诊断。EvolutionEnvelope 记录 8 阶段 metrics 但不记录 trial 计数
- **业界做法**：**累积 trial DSR**——演化系统持续跑 trial，N 每代增长，必须**跨代累计**否则 DSR 虚高；champion recovery 重引入算额外 trial。**PBO-aware 血缘账本**——EvolutionEnvelope 每 emit 附 `cumulative_trial_count`，使 DSR/PBO 可在线诚实重算
- **影响**：演化系统的 champion 可能 in-sample 过拟合却未被诊断（因 N 未累计），champion recovery 重引入加剧此风险。长期运行后"冠军"的 Sharpe 可能是选择偏差产物

### P2 级（增强项）

#### P2-9　缺非时间 K 线聚合（Volume/Tick/Renko）

- **当前**：仅时间聚合（`tier_timeframe_map.py`）
- **业界**：NautilusTrader `BarAggregator`（Time/Tick/Volume/Value/Renko）。Volume-bar 在加密市场减少噪音有实证价值
- **影响**：错失更优的 K 线合成方式

#### P2-10　缺声明式参数空间

- **当前**：`walk_forward.py` 临时 `param_grid` dict
- **业界**：Freqtrade `IntParameter(low,high,space='buy')`、Jesse `hp.int(min,max)`——策略类自声明可优化范围
- **影响**：参数优化需手动维护 grid，易遗漏/不一致

#### P2-11　缺 Jesse filters 组合门 + willing_to_loss 硬上限

- **当前**：散落 `if` 守卫
- **业界**：Jesse `filters` 列表（全部返回 True 才入场）+ `willing_to_loss` 单笔硬亏损上限
- **影响**：入场前置条件不可组合、不可测试

#### P2-12　缺声明式 Protections 链

- **当前**：硬编码门禁顺序
- **业界**：Freqtrade `Protections`（StoplossGuard/MaxDrawdown/CooldownPeriod/LowProfitPairs/MaxPairs）可组合、可重排
- **影响**：风控策略调整需改代码非改配置

#### P2-13　缺 Qlib 表达式引擎

- **当前**：`custom_factor_store.py` 仅单因子公式存储；新增因子需写完整 Python 类
- **业界**：Qlib 表达式引擎——因子即字符串（`Mean($close,5)/$close`），可组合算子树，惰性求值+缓存
- **影响**：因子研发迭代慢；研究者需写代码而非写公式

#### P2-14　缺 Rust 性能核心

- **当前**：纯 Python
- **业界**：QUANTAXIS（`quantaxis-rs`，单指标 ~70ns）、NautilusTrader（Rust 核心 + Python/Cython 控制面）
- **影响**：大规模回测/因子计算性能受限

#### P2-15　缺统一生命周期契约

- **当前**：AI agent cycle 隐式（散落 `full_auto_trading_service.py`）
- **业界**：Jesse `before→should→go→update→after` 显式契约
- **影响**：agent 行为不易测试/文档化

#### P2-16（v2 新增）　LLM 供应商单一（仅 DeepSeek）

- **当前**：LLM 调用绑定 DeepSeek
- **业界**：TradingAgents `llm_clients/factory.py` + `model_catalog.py` 工厂模式抽象 OpenAI/Anthropic/Google/Bedrock/Azure，`capabilities.py` 映射模型能力
- **影响**：DeepSeek 宕机/限流时无容错；无法按任务难度路由到不同模型（小模型处理常规、大模型处理模糊）

#### P2-17（v2 新增）　缺语义缓存与 LLM 成本控制层

- **当前**：LLM 每周期调用，无显式缓存/成本控制
- **业界**：语义缓存（GPTCache/Valery，嵌入 prompt 返回相似查询缓存，40-90% 成本节省）；逐周期 token 预算硬上限；结构化输出减少 token；分层模型路由
- **影响**：市场状态 prompt 高度可缓存（"分析 BTC 资金=0.03%, OI=..."这类状态窗口重复），未利用缓存浪费成本；无 token 预算可能在异常时失控

#### P2-18（v2 新增）　缺 lookahead-analysis 前视检测工具

- **当前**：无策略无关的前视检测工具
- **业界**：Freqtrade `lookahead-analysis`——蒙特卡洛式打乱 K 线顺序重跑回测，检测偷偷偷看未来 K 线的策略
- **影响**：策略/因子可能含隐性前视（如非因果 pandas 操作），无系统化检测手段

---

## 第八部分　代码级整改方案

> 本部分每项整改给出：目标文件、新建/重构标识、类/函数签名、关键数据结构、集成点、配置项、依赖。可直接据此开发。

### 整改 #1：Walk-Forward 方法论重建（对应 P0-1）

**目标**：将朴素的 grid search WFO 升级为研究级 walk-forward，含 CSCV/PBO、Deflated Sharpe Ratio、purge/embargo、多目标 loss registry、Optuna/CMA-ES 替代穷举。

#### 1.1 新建 `services/backtest_engine/overfitting_metrics.py`

```python
"""
研究级过拟合诊断指标
对标: López de Prado "Advances in Financial Machine Learning" Ch.11-15
依赖: numpy, scipy.stats
"""
import numpy as np
from dataclasses import dataclass
from typing import List, Optional, Tuple

@dataclass
class CSCVResult:
    """Combinatorially Symmetric Cross-Validation 结果"""
    pbo: float                    # Probability of Backtest Overfitting [0,1]; >0.5 过拟合, <0.1 稳健
    logit_pbo: float              # logit(PBO), 用于稳定性判断
    stochastic_dominance: np.ndarray  # 各 rank 的 OOS 表现分布


def compute_pbo_cscv(
    is_returns: np.ndarray,       # (n_strategies, n_is_periods) 样本内收益矩阵
    oos_returns: np.ndarray,      # (n_strategies, n_oos_periods) 样本外收益矩阵
    n_blocks: int = 16            # CSCV 分块数(偶数); López de Prado 建议 16
) -> CSCVResult:
    """
    CSCV 计算 PBO。
    - 将 IS+OOS 拼接后等分 n_blocks
    - 枚举所有对称组合(n/2 为 IS, n/2 为 OOS)
    - PBO = 最优 IS 策略在 OOS 排名低于中位数的组合占比
    复杂度: C(n_blocks, n_blocks/2); n_blocks=16 → 12870 组合
    """


def deflated_sharpe_ratio(
    observed_sharpe: float,
    n_trials: int,                # 测试的策略数(多重检验校正)
    returns: np.ndarray,          # 策略收益序列
    skew: Optional[float] = None,
    kurt: Optional[float] = None,
    risk_free: float = 0.0
) -> Tuple[float, float]:
    """
    返回 (dsr_value, p_value)。
    对观察到的 Sharpe 做多重检验校正(Inflation 因子来自 n_trials)。
    对标: Bailey & López de Prado (2014) "Deflated Sharpe Ratio"
    """


def min_backtest_length(
    sharpe: float,
    skew: float = 0.0,
    kurt: float = 3.0,
    conf_level: float = 0.95
) -> float:
    """
    返回使 Sharpe 估计可信所需的最小回测长度(年)。
    对标: Bailey & López de Prado (2012) "The Sharpe Ratio Efficient Frontier"
    """


def probabilistic_sharpe_ratio(
    sharpe: float,
    benchmark_sharpe: float = 0.0,
    n: int,
    skew: float = 0.0,
    kurt: float = 3.0
) -> float:
    """PSR: 观察 Sharpe 超过基准的概率, 考虑偏度峰度。返回 [0,1]。"""
```

#### 1.2 改造 `services/backtest_engine/walk_forward.py`

新增/修改的类与配置：

```python
@dataclass
class WalkForwardConfig:
    # 原有字段保留...
    train_period_days: int = 252
    test_period_days: int = 63
    step_days: int = 21
    optimization_metric: str = 'sharpe_ratio'

    # ===== 新增 =====
    # Purge/Embargo 防泄漏
    purge_days: int = 5           # train/test 间的 purge 间隙(López de Prado)
    embargo_days: int = 3         # test 后的 embargo(标签前瞻泄漏)
    # 多目标
    optimization_metrics: List[str] = None   # ['sharpe','sortino','calmar','max_drawdown']
    loss_function: str = 'sharpe'            # loss registry key
    # 优化器
    optimizer: str = 'optuna'                # 'grid'|'optuna'|'cma_es'
    n_optuna_trials: int = 100
    # 过拟合诊断
    run_cscv: bool = True
    cscv_n_blocks: int = 16
    run_dsr: bool = True
    # IC 衰减耦合
    decay_aware_stepping: bool = False       # step_days = f(ic_halflife)
    decay_halflife_source: str = 'factor_evaluator'  # 因子评估器的半衰期


@dataclass
class WalkForwardResult:
    # 原有字段保留...
    # ===== 新增 =====
    pbo: Optional[float] = None              # Probability of Backtest Overfitting
    pbo_verdict: str = ""                    # 'robust'|'borderline'|'overfit'
    deflated_sharpe: Optional[float] = None
    min_required_length_years: Optional[float] = None
    purge_embargo_applied: bool = False
```

关键方法改造：

```python
class WalkForwardAnalyzer:
    def _generate_periods(self, start, end) -> List[WalkForwardPeriod]:
        """改造: 插入 purge + embargo gap"""
        # 原: test_start = train_end + timedelta(days=1)
        # 新: purge_start = train_end + timedelta(days=1)
        #     purge_end = purge_start + timedelta(days=self.config.purge_days)
        #     test_start = purge_end + timedelta(days=1)
        #     test_end = test_start + timedelta(days=test_period_days)
        #     embargo_end = test_end + timedelta(days=self.config.embargo_days)

    def _optimize_params(self, factory, train_data, param_grid) -> Dict:
        """改造: optimizer 路由"""
        if self.config.optimizer == 'optuna':
            return self._optimize_optuna(factory, train_data, param_grid)
        elif self.config.optimizer == 'cma_es':
            return self._optimize_cma_es(factory, train_data, param_grid)
        else:
            return self._optimize_grid(...)  # 原逻辑保留

    def _optimize_optuna(self, factory, data, param_space) -> Dict:
        """
        用 Optuna TPE 替代穷举 grid。
        集成 loss registry: study = optuna.create_study(
            directions=['maximize','minimize']  # 多目标 NSGA-II
        )
        """

    def _compute_overfitting_diagnostics(self, periods) -> dict:
        """新方法: 调用 overfitting_metrics.py 算 PBO/DSR/MinBTL"""
        # 收集所有 IS/OOS 收益矩阵
        # is_returns, oos_returns = self._collect_returns_matrix(periods)
        # cscv = compute_pbo_cscv(is_returns, oos_returns, n_blocks)
        # dsr = deflated_sharpe_ratio(observed_sharpe, n_trials, returns)
        # return {pbo, dsr, min_length}
```

**Loss function registry**（新模块 `backtest_engine/loss_functions.py`）：

```python
"""对标 Freqtrade hyperopt_loss registry"""
LOSS_REGISTRY = {
    'sharpe':        lambda r: r.sharpe_ratio,
    'sortino':       lambda r: r.sortino_ratio,
    'calmar':        lambda r: r.calmar_ratio,
    'max_drawdown':  lambda r: -r.max_drawdown,       # 最小化
    'profit_factor': lambda r: r.profit_factor,
    'sharpe_dd':     lambda r: r.sharpe_ratio / (abs(r.max_drawdown) + 1e-9),  # 复合
    'ulcer':         lambda r: -r.ulcer_index,
}
```

**集成点**：
- `evolution_scheduler.py` 调用 `WalkForwardAnalyzer.analyze()` 处加 `run_cscv=True`，PBO>0.5 时拒绝该参数集晋升
- `config/settings.py` 新增 env：`WFO_PURGE_DAYS=5`、`WFO_EMBARGO_DAYS=3`、`WFO_OPTIMIZER=optuna`、`WFO_RUN_CSCV=true`

**依赖**：`optuna`（加入 `pyproject.toml`），`scipy`（已有）

---

### 整改 #2：子 K 线回测引擎（对应 P0-2）

**目标**：SL/TP 在更细粒度解析，区分"谁先触及"，消除 intrabar 偏差。对标 Freqtrade `--timeframe-detail`。

#### 2.1 改造 `services/backtest_engine/backtest_engine.py`

```python
@dataclass
class BacktestConfig:
    # 原有字段保留...
    # ===== 新增 =====
    timeframe_detail: Optional[str] = None    # e.g. '1m'; None=关闭(当前行为)
    fill_model: str = 'next_open'             # 'close'|'next_open'(默认,防前视)
    intrabar_resolution: bool = False         # True=启用 high/low 穿透判定


class BacktestEngine:
    def _check_stop_loss_take_profit(self, position, bar, detail_bars=None) -> Optional[str]:
        """
        改造: 支持三种模式
        1. 原模式(close): pnl_pct vs bar['close'] — 保留为回退
        2. high/low 穿透(intrabar_resolution=True, 无 detail 数据):
           - 若 bar['low'] <= sl_price: SL 先触发(保守)
           - 若 bar['high'] >= tp_price 且 bar['low'] > sl_price: TP 触发
           - 若两者都被触及: 假设 SL 先(保守, 防乐观偏差)
        3. detail 解析(timeframe_detail='1m', detail_bars 提供):
           - 在该 bar 对应的 1m 子 K 线序列中逐根扫描
           - 精确判定 SL/TP 谁先被触及及其时间
        """
        if detail_bars is not None and len(detail_bars) > 0:
            return self._check_intrabar_detailed(position, detail_bars)
        elif self.config.intrabar_resolution:
            return self._check_intrabar_highlow(position, bar)
        else:
            return self._check_close_price(position, bar)  # 原逻辑

    def _check_intrabar_highlow(self, position, bar) -> Optional[str]:
        """无 detail 数据时的保守 high/low 判定"""
        sl_price = position.entry_price * (1 - position.stop_loss_pct)  # 多头
        tp_price = position.entry_price * (1 + position.take_profit_pct)
        hit_sl = bar['low'] <= sl_price
        hit_tp = bar['high'] >= tp_price
        if hit_sl and hit_tp:
            return 'stop_loss'  # 保守: SL 先
        elif hit_sl:
            return 'stop_loss'
        elif hit_tp:
            return 'take_profit'
        return None

    def _check_intrabar_detailed(self, position, detail_bars: pd.DataFrame) -> Optional[str]:
        """对标 Freqtrade timeframe-detail: 逐根 1m 扫描"""

    def _resolve_fill_price(self, signal_bar, execution_bar, order_type: str) -> float:
        """成交价模型: next_open(默认) 或 close"""
        if self.config.fill_model == 'next_open':
            return execution_bar['open']   # 信号 K 线的下一根开盘
        return execution_bar['close']
```

#### 2.2 数据加载支持

```python
class BacktestEngine:
    def run(self, strategy, data: pd.DataFrame, detail_data: Optional[pd.DataFrame] = None):
        """
        改造 run 签名: 接受可选的 detail_data。
        - data: 主周期(如 15m)
        - detail_data: 更细周期(如 1m), 索引对齐到主周期 bar
        当 timeframe_detail 配置时, 预处理 detail_data 为 {bar_timestamp: detail_bars} 映射
        """
```

**集成点**：
- `walk_forward.py` 的 `analyze()` 加载 detail_data 传入
- 前端回测 UI 加 `timeframe_detail` 选择器
- `config/settings.py`：`BACKTEST_INTRABAR_RESOLUTION=true`、`BACKTEST_FILL_MODEL=next_open`

**回退策略**：`intrabar_resolution=False` + `fill_model='close'` 完全等价于当前行为，零风险切换。

---

### 整改 #3：滑点模型统一（对应 P0-4）

**目标**：单一成本真相源，回测与实盘共用。

#### 3.1 收敛到 `services/backtest_engine/cost_model.py`

```python
class CostModel:
    """单一成本真相源 — 回测与实盘共用"""

    def calc_slippage_rate(self, notional_usd, trade_nature="swing", is_sl=False) -> float:
        """保持现有分级模型(base 0.0005, 4档size, nature调整, SL×2, clamp[0,0.003])"""
        # 已实现, 仅需让它成为唯一入口

    def calculate_slippage(self, order_size_usd, daily_volume_usd, **kwargs) -> float:
        """DEPRECATED 兼容包装: 内部转调 calc_slippage_rate, 标记 DeprecationWarning"""
        import warnings
        warnings.warn("Use calc_slippage_rate(); volume-ratio model deprecated",
                      DeprecationWarning, stacklevel=2)
        return self.calc_slippage_rate(order_size_usd, **kwargs)
```

#### 3.2 删除 `backtest_engine.py` 的独立实现

```python
# backtest_engine.py 删除 lines 33-43 的 calculate_slippage 函数
# 改为:
from .cost_model import CostModel
_cost_model = CostModel()
# 原 calculate_slippage(order_size_usd, daily_volume_usd) 调用点
# → _cost_model.calc_slippage_rate(order_size_usd)
```

**验证步骤**（实施时）：
1. 全局搜索 `calculate_slippage` 调用点
2. 逐个替换为 `cost_model.calc_slippage_rate`
3. 对比替换前后回测结果差异，确认影响范围
4. 更新 `cost_model.py` docstring 移除"与 fee_guard 对齐"的旧声明，改为"回测与实盘单一真相源"

**集成点**：`live_executor.py` / `paper_executor.py` 也应导入同一 `CostModel`。

---

### 整改 #4：因子权重学习层（对应 P0-3）

**目标**：引入 Qlib 式 ML 学习的因子加权，与现有手工 regime 表并列（可切换），逐步迁移。这是与 Qlib 差距最大也是价值最高的整改。

#### 4.1 新建 `services/factor_engine/learned_weighting.py`

```python
"""
Qlib 式学习型因子加权层。
与手工 regime 权重表(factor_weighting.py)并列, 通过开关切换。
数据流: 因子值 → 特征工程 → 标签(前瞻收益) → Model.fit → predict(分数) → IC加权融合
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Type
from abc import ABC, abstractmethod


@dataclass
class FactorDataset:
    """对标 Qlib DatasetH: 特征矩阵 + 标签 + 时间分片"""
    features: pd.DataFrame         # (datetime, factor_id) → 因子值
    labels: pd.Series              # (datetime) → 前瞻收益
    segments: Dict[str, tuple]     # {'train':(start,end), 'valid':..., 'test':...}


@dataclass
class FactorProcessorConfig:
    """对标 Qlib DataHandlerLP 的 learn/infer 处理器分离 — 防前视偏差"""
    # learn_processors: 仅在训练集 fit
    learn_normalization: str = 'cs_rank'   # 'cs_zscore'|'cs_rank'|'robust_zscore'
    # infer_processors: 推理时应用
    infer_fillna: bool = True
    infer_dropna_label: bool = True


class FactorModel(ABC):
    """对标 Qlib Model 基类: 统一 fit/predict 接口"""
    @abstractmethod
    def fit(self, dataset: FactorDataset) -> None: ...
    @abstractmethod
    def predict(self, features: pd.DataFrame) -> pd.Series: ...  # → 预测分数
    @abstractmethod
    def save(self, path: str) -> None: ...
    @abstractmethod
    def load(self, path: str) -> None: ...


class LightGBMFactorModel(FactorModel):
    """LightGBM 学习因子非线性加权"""
    # fit: model.lgb.train(features → labels)
    # predict: 返回预测分数 [-1, 1] 区间


class GRUFactorModel(FactorModel):
    """GRU 时序模型, 捕捉因子序列动态"""
    # 对标 Qlib pytorch_gru.py


class ICWeightedFusion:
    """非参数 fallback: 按滚动 IC 加权融合(无需 ML)"""
    def fuse(self, factor_values: pd.DataFrame, ic_history: pd.DataFrame) -> pd.Series:
        """
        score_t = sum_i (rolling_ic_i,t * factor_value_i,t) / sum_i |rolling_ic_i,t|
        """


@dataclass
class LearnedWeightingConfig:
    enabled: bool = False              # 默认关, 与手工 regime 表并存
    model_type: str = 'lightgbm'       # 'lightgbm'|'gru'|'ic_weighted'
    label_horizon_bars: int = 5        # 前瞻收益计算窗口(对齐 factor_evaluator forward_period)
    retrain_frequency_hours: int = 24  # 重训频率(对标 RollingGen step)
    train_lookback_days: int = 90      # 训练回看窗口
    min_ic_to_include: float = 0.015   # 低于此 IC 的因子不参与训练
    purge_bars: int = 5                # train/valid 间 purge


class LearnedFactorWeighting:
    """学习型因子加权主类 — 与 DynamicFactorWeighting 同接口"""

    def __init__(self, config: LearnedWeightingConfig):
        self.config = config
        self.model: Optional[FactorModel] = None
        self.processor = FactorProcessorConfig()
        self.last_train_time = None

    def compute_weighted_signal(
        self,
        factor_values: pd.DataFrame,      # 当前所有因子值 (factor_id → value)
        factor_metadata: Dict,             # 因子元信息(category, ic_history)
        historical_data: pd.DataFrame      # 用于训练的历史数据
    ) -> pd.Series:
        """
        返回加权后的复合信号。
        1. 检查是否到重训时间 → 若是, 调 _train()
        2. model.predict(factor_values) → 预测分数
        3. 返回分数(方向 + 强度)
        """

    def _train(self, factor_values_history, returns_history):
        """
        对标 Qlib DataHandler → Dataset → Model pipeline:
        1. 构造 FactorDataset(features, labels, segments)
        2. learn_processor.fit(train_segment) — 仅训练集归一化
        3. model.fit(dataset)
        4. 评估: IC/ICIR on valid segment
        5. 持久化 model
        """

    def compute_ic_decay_coupled_retrain_interval(self, ic_halflife_bars) -> int:
        """重训频率 = f(IC半衰期); 半衰期短 → 更频繁重训"""
```

#### 4.2 集成到 `factor_evaluation_pipeline.py`

```python
class FactorEvaluationPipeline:
    def __init__(self):
        self.regime_weighting = DynamicFactorWeighting()           # 现有手工表
        self.learned_weighting = LearnedFactorWeighting(config)    # 新增学习层
        self.weighting_mode = os.getenv('FACTOR_WEIGHTING_MODE', 'regime')  # 'regime'|'learned'|'hybrid'

    def evaluate(self, factor_values, market_state):
        if self.weighting_mode == 'regime':
            return self.regime_weighting.compute(...)
        elif self.weighting_mode == 'learned':
            return self.learned_weighting.compute_weighted_signal(...)
        elif self.weighting_mode == 'hybrid':
            # A/B 对比: 两套并行, 按 IC 表现动态选择
            regime_sig = self.regime_weighting.compute(...)
            learned_sig = self.learned_weighting.compute_weighted_signal(...)
            return self._blend_by_confidence(regime_sig, learned_sig)
```

**渐进迁移策略**：
1. **Phase 1**（`mode='regime'`）：实现 `learned_weighting.py`，仅 shadow 运行（计算但不影响实盘），对比 IC
2. **Phase 2**（`mode='hybrid'`）：A/B 测试，learned IC 显著优于 regime 时切换
3. **Phase 3**（`mode='learned'`）：learned 成为主路径，regime 表降级为 fallback

**配置项**：`FACTOR_WEIGHTING_MODE=regime`、`LEARNED_MODEL_TYPE=lightgbm`、`LEARNED_RETRAIN_HOURS=24`

**依赖**：`lightgbm`（加入 pyproject.toml）、`torch`（GRU 可选）

---

### 整改 #5：引擎层硬风控（对应 P1-8）

**目标**：补齐 NautilusTrader 级的业务无关引擎层风控，作为最后一道防线。

#### 5.1 新建 `services/exchange/risk_engine.py`

```python
"""
引擎层硬风控 — 业务无关, 对标 NautilusTrader RiskEngine。
位于订单提交路径, 在 live_executor/paper_executor 之前。
标准化 OrderDenied 原因码, 可编程接口。
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict


class DenyCategory(Enum):
    QUANTITY_BELOW_MINIMUM = "quantity_below_minimum"
    QUANTITY_EXCEEDS_MAXIMUM = "quantity_exceeds_maximum"
    QUANTITY_PRECISION_INVALID = "quantity_precision_invalid"
    PRICE_PRECISION_INVALID = "price_precision_invalid"
    NOTIONAL_BELOW_MINIMUM = "notional_below_minimum"
    NOTIONAL_EXCEEDS_MAXIMUM = "notional_exceeds_maximum"
    NOTIONAL_EXCEEDS_MAX_PER_ORDER = "notional_exceeds_max_per_order"
    MARGIN_EXCEEDS_FREE_BALANCE = "margin_exceeds_free_balance"
    REDUCE_ONLY_WOULD_INCREASE = "reduce_only_would_increase_position"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    TRADING_HALTED = "trading_halted"
    TRADING_REDUCING_ONLY = "trading_state_reducing"
    DUPLICATE_ORDER = "duplicate_order"
    INSTRUMENT_NOT_FOUND = "instrument_not_found"
    INVALID_ORDER_SIDE = "invalid_order_side"


class TradingState(Enum):
    ACTIVE = "active"
    HALTED = "halted"          # 拒绝新单, 允许撤单
    REDUCING = "reducing"      # 仅允许减仓


@dataclass
class OrderDenied:
    """标准化拒绝事件, 对标 NautilusTrader OrderDenied"""
    category: DenyCategory
    context: Dict[str, float] = field(default_factory=dict)  # e.g. {'effective_qty':15,'max_qty':10}
    reason_text: str = ""

    def log_line(self) -> str:
        ctx = " ".join(f"{k}={v}" for k,v in self.context.items())
        return f"[RiskEngine] DENY category={self.category.value} {ctx} {self.reason_text}"


@dataclass
class InstrumentSpec:
    """交易品种规格(从交易所拉取/缓存)"""
    symbol: str
    price_precision: int
    quantity_precision: int
    min_quantity: float
    max_quantity: float
    min_notional: float
    max_notional: float
    tick_size: float


class RiskEngine:
    """引擎层风控 — 在订单提交前校验"""

    def __init__(self):
        self.trading_state = TradingState.ACTIVE
        self.specs: Dict[str, InstrumentSpec] = {}
        # 限流
        self._submit_timestamps: list = []
        self._max_submits_per_sec = 10
        # 重复检测
        self._seen_client_order_ids: set = set()
        # 超额成交
        self._allow_overfills = False

    def check_submit(self, order_request, account_state, position_state) -> Optional[OrderDenied]:
        """
        返回 None=通过, OrderDenied=拒绝。
        校验顺序(任一失败即返回):
        1. trading_state(HALTED拒新单, REDUCING仅减仓)
        2. 限流(rate_limit)
        3. 重复 client_order_id
        4. instrument 存在
        5. 价格精度 / 触发价精度
        6. 数量精度 / min/max
        7. 名义价值 min/max / per_order
        8. reduce_only 不增仓
        9. 保证金 ≤ 自由余额(非保证金账户)
        """

    def check_fill(self, fill_event, order_state) -> Optional[str]:
        """成交校验: 超额成交保护 / 重复成交去重"""
        # 对标 NautilusTrader: allow_overfills=False → 拒绝超量
        # trade_id 4字段去重(symbol,trade_id,price,qty)
```

**集成点**：
- `live_executor.py` `LiveExecutor.place_order()` 在 `trading_commands.place_ai_driven_order` 前调 `risk_engine.check_submit()`
- `paper_executor.py` 同样接入
- `exchange_factory.py` 初始化时创建单例 `risk_engine`
- 前端风控面板显示 `OrderDenied` 统计

**配置项**：`RISK_ENGINE_ENABLED=true`、`RISK_MAX_SUBMITS_PER_SEC=10`、`RISK_ALLOW_OVERFILLS=false`

---

### 整改 #6：声明式参数空间（对应 P2-10）

**目标**：策略类自声明可优化参数范围，替代 `walk_forward.py` 临时 `param_grid`。对标 Freqtrade `IntParameter` / Jesse `hp.*`。

#### 6.1 新建 `services/strategy/param_spaces.py`

```python
"""声明式参数空间 — 对标 Freqtrade IntParameter / Jesse hp.*"""
from dataclasses import dataclass
from typing import Any, List, Union
import random


class Parameter:
    """基类: 策略类属性, 自声明可优化范围"""
    def __init__(self, low, high, default, space='buy', optimize=True):
        self.low = low
        self.high = high
        self.default = default
        self.space = space       # 'buy'|'sell'|'roi'|'stoploss'|'protection'
        self.optimize = optimize
        self.value = default

    def __get__(self, obj, objtype=None):
        return self.value

    def __set__(self, obj, value):
        self.value = value

    def sample(self) -> Any:
        raise NotImplementedError

    def to_optuna(self, trial, name: str):
        raise NotImplementedError


class IntParameter(Parameter):
    def sample(self) -> int:
        return random.randint(self.low, self.high)

    def to_optuna(self, trial, name):
        return trial.suggest_int(name, self.low, self.high)


class FloatParameter(Parameter):
    def sample(self) -> float:
        return random.uniform(self.low, self.high)

    def to_optuna(self, trial, name):
        return trial.suggest_float(name, self.low, self.high)


class CategoricalParameter(Parameter):
    def __init__(self, categories: List, default, space='buy', optimize=True):
        super().__init__(0, len(categories)-1, default, space, optimize)
        self.categories = categories

    def sample(self):
        return random.choice(self.categories)

    def to_optuna(self, trial, name):
        return trial.suggest_categorical(name, self.categories)


def collect_parameters(strategy_class) -> dict:
    """反射收集策略类上所有 Parameter 描述符"""
    params = {}
    for name in dir(strategy_class):
        attr = getattr(strategy_class, name)
        if isinstance(attr, Parameter):
            params[name] = attr
    return params
```

#### 6.2 策略类使用示例

```python
class MyTrendStrategy(Strategy):
    # 自声明可优化参数 — 替代 walk_forward.py 的 param_grid dict
    ema_fast = IntParameter(5, 30, default=9, space='buy')
    ema_slow = IntParameter(20, 100, default=55, space='buy')
    rsi_threshold = FloatParameter(20, 50, default=30, space='buy')
    stop_loss_pct = FloatParameter(0.01, 0.05, default=0.02, space='stoploss')
```

#### 6.3 改造 `walk_forward.py` 消费参数空间

```python
class WalkForwardAnalyzer:
    def analyze(self, strategy_factory, data, param_grid=None):
        # 改造: 优先从策略类反射参数空间
        if param_grid is None:
            sample_strategy = strategy_factory({})
            param_grid = self._params_to_grid(collect_parameters(type(sample_strategy)))

    def _params_to_grid(self, params: dict) -> dict:
        return {name: [p.low, p.high] for name, p in params.items() if p.optimize}
```

**集成点**：现有 DB-backed `strategy_templates` 可选迁移；新策略直接用声明式。

---

### 整改 #7：QAA 双轨收敛（对应 P1-6）

**目标**：消除 `QAA通信协议构架/qaa/` 与 `backend/services/qaa/` 的重复定义。

#### 方案 A（推荐）：`backend/services/qaa/` 作为 QAA DomainPlugin

```python
# backend/services/qaa/trading_domain_plugin.py
"""
将交易侧的 cards/rule_router 实现为 QAA DomainPlugin,
而非重新定义 AgentCard/RuleRouter。
"""
from qaa.core.registry import UniversalRegistry
from qaa.core.models import AgentCard   # 从 qaa 包导入, 不再本地定义

class TradingDomainPlugin:
    """注册交易域的 8 张 AgentCard, 复用 QAA 的 RuleRouter"""
    def register(self, registry: UniversalRegistry):
        registry.register_agent(self._build_market_data_card())
        registry.register_agent(self._build_factor_engine_card())
        # ... 复用 qaa.core.models.AgentCard, 不本地重定义
```

**迁移步骤**：
1. 删除 `backend/services/qaa/cards.py` 的本地 `AgentCard`，改 `from qaa.core.models import AgentCard`
2. 删除 `backend/services/qaa/rule_router.py` 的本地 `RuleRouter`，改 `from qaa.router import RuleRouter`
3. 保留 `backend/services/qaa/` 中的**交易域特有逻辑**（交易卡定义、MarketSnapshot 适配），作为 DomainPlugin
4. 确保 QAA 作为 `pip install -e ../QAA通信协议构架` 依赖（检查 `pyproject.toml`）

**收益**：单一 AgentCard/RuleRouter 定义，消除概念漂移。

---

### 整改 #8：full_auto_trading_service.py 拆分（对应 P1-7）

**目标**：将 19,727 行单体拆分为职责清晰的模块。

#### 建议拆分

```
services/full_auto/                           # 新目录
├── orchestrator.py          # FullAutoOrchestrator (~500行) — 仅协调, 无业务逻辑
├── loops/                   # 各独立循环
│   ├── coordinator_loop.py  # _run_unified_loop / _run_trading_cycle
│   ├── midlong_loop.py      # _run_midlong_independent
│   ├── scalp_loop.py        # _run_scalp_independent
│   ├── arbitrage_loop.py    # _run_arbitrage_tick / _run_rebate_arb_tick
│   ├── learning_loop.py     # _run_learning_integration / _run_mlto_learning_tick
│   └── maintenance_loop.py  # _run_maintenance_cycle / _run_hold_timeout_ai_review
└── state.py                 # 提取类级状态(冻结冷却/日亏追踪) 为显式 State 对象
```

**原则**：
- 每个 loop 文件 < 1000 行
- `orchestrator.py` 仅做依赖注入和调度，不含业务逻辑
- State 从类属性提取为显式 `@dataclass`，便于测试与事件溯源（衔接整改 #9）

**回滚方案**：保留旧 `full_auto_trading_service.py` 为 thin shim，转发到新模块，灰度切换。

---

### 整改 #9：事件溯源（对应 P1-5）

**目标**：引入事件日志，使订单/仓位/账户状态可重放、可审计。对标 NautilusTrader。

#### 新建 `services/event_sourcing/`

```python
# services/event_sourcing/event_store.py
"""事件存储 — 对标 NautilusTrader 事件溯源"""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, List
import json


@dataclass(frozen=True)
class DomainEvent:
    """不可变领域事件"""
    event_id: str
    event_type: str          # 'OrderSubmitted'|'OrderFilled'|'PositionChanged'|'AccountUpdated'
    timestamp: datetime
    payload: dict
    aggregate_id: str        # order_id / position_id / account_id


class EventStore:
    """事件日志持久化(JSONL + 可选 Postgres)"""

    def append(self, event: DomainEvent) -> None:
        """追加事件(不可变)"""

    def replay(self, aggregate_id: str) -> List[DomainEvent]:
        """重放聚合的所有事件"""

    def replay_all(self, since: datetime = None) -> List[DomainEvent]:
        """重放全部(用于状态重建)"""


class PositionProjection:
    """仓位物化视图 — 从事件流投影"""

    def apply(self, event: DomainEvent) -> None:
        """应用事件更新投影"""

    @property
    def current_state(self) -> dict:
        """当前仓位状态"""


class EventSourcedPositionRepository:
    """事件溯源仓位仓库"""

    def __init__(self, store: EventStore):
        self.store = store
        self._projection = PositionProjection()

    def rebuild_from_events(self) -> None:
        """从事件日志重建当前状态(崩溃恢复)"""
        for event in self.store.replay_all():
            self._projection.apply(event)
```

**集成策略**（渐进）：
1. **Phase 1**：`EventStore` 仅 shadow 记录（不影响实盘），验证事件完整性
2. **Phase 2**：仓位/订单读路径走 Projection，写路径仍走原逻辑 + 事件追加
3. **Phase 3**：完全事件溯源，原 mutable 状态退役

**收益**：崩溃恢复、完整审计、为回测/实盘一致性铺路。

---

### 整改 #10：FreqAI 式 ML 训练管线（对应 P0-5，v2 新增）

**目标**：建立统一的 train/predict 上下文对象、严格滚动窗口、持续重训、统一模型接口。这是 P0-3（因子学习层）的前置依赖。对标 FreqAI `FreqaiDataKitchen` + `IFreqaiModel`。

#### 10.1 新建 `services/ml/training_context.py`

```python
"""
统一训练上下文对象 — 对标 FreqAI FreqaiDataKitchen。
逐品种/逐周期捆绑: 特征矩阵 + 标签 + train/test 时间切分 + 精确特征列清单。
消除特征漂移 bug(train 时特征列 ≠ predict 时)。
"""
from dataclasses import dataclass, field
from typing import List, Tuple, Optional
import pandas as pd


@dataclass
class TrainingContext:
    """逐品种/周期的训练上下文 — 在 train() 与 predict() 间传递"""
    # 标识
    symbol: str
    tier: str                              # 'short'|'mid'|'long'
    timeframe: str

    # 数据
    features: pd.DataFrame                 # (datetime, factor_id) 因子值矩阵
    labels: pd.Series                      # (datetime) 前瞻收益/分类标签
    feature_columns: List[str]             # 精确特征列清单(predict 复用, 杜绝漂移)

    # 时间切分(前视防护)
    train_start: pd.Timestamp
    train_end: pd.Timestamp                # train_period 边界
    purge_end: Optional[pd.Timestamp]      # purge gap 结束(前视防护)
    predict_start: pd.Timestamp            # predict 窗开始(永不重叠 train)
    predict_end: pd.Timestamp

    # 元数据
    model_identifier: str                  # 持久化路径标识
    retrain_count: int = 0                 # 重训次数


@dataclass
class RollingWindowConfig:
    """严格滚动窗口配置 — 对标 FreqAI train_period_days/backtest_period_days/live_retrain_hours"""
    train_period_days: int = 90            # 训练回看窗口
    predict_period_days: int = 7           # 预测窗(每 N 天重训)
    purge_days: int = 5                    # train/predict 间 purge gap
    embargo_days: int = 3                  # predict 后 embargo
    live_retrain_hours: int = 12           # 实盘重训节奏
    continual_warm_start: bool = False     # 从上次权重热启动


class RollingWindowGenerator:
    """生成严格滚动窗口序列 — 预测窗永不重叠训练窗"""

    def generate(self, data_start, data_end, config: RollingWindowConfig) -> List[Tuple]:
        """
        返回 [(train_start, train_end, purge_end, predict_start, predict_end), ...]
        每个窗口的 predict 窗严格在 purge 之后, 杜绝前视。
        """
```

#### 10.2 新建 `services/ml/model_base.py`

```python
"""
统一模型接口 — 对标 FreqAI IFreqaiModel / Qlib Model。
所有监督学习模型共享 fit/predict, 一行配置切换。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


class SupervisedModel(ABC):
    """统一监督学习模型接口"""
    @abstractmethod
    def fit(self, ctx: 'TrainingContext') -> None: ...
    @abstractmethod
    def predict(self, features: pd.DataFrame, feature_columns: list) -> pd.Series: ...
    @abstractmethod
    def save(self, path: str) -> None: ...
    @abstractmethod
    def load(self, path: str) -> None: ...
    @property
    @abstractmethod
    def model_type(self) -> str: ...        # 'lightgbm'|'catboost'|'pytorch_gru'


class LightGBMTrendModel(SupervisedModel):
    """LightGBM 学习因子非线性加权 — 对标 FreqAI LightGBMRegressor"""

class CatBoostModel(SupervisedModel):
    """CatBoost — 对标 FreqAI CatboostRegressor"""

class PyTorchGRUModel(SupervisedModel):
    """GRU 时序模型 — 对标 FreqAI PyTorchTransformerRegressor / Qlib GRU"""


MODEL_REGISTRY = {
    'lightgbm': LightGBMTrendModel,
    'catboost': CatBoostModel,
    'pytorch_gru': PyTorchGRUModel,
}

def get_model(model_type: str) -> SupervisedModel:
    return MODEL_REGISTRY[model_type]()
```

#### 10.3 新建 `services/ml/training_pipeline.py`

```python
"""
持续重训管线 — 对标 FreqAI 持续学习循环。
每 live_retrain_hours 滑窗重训, 替换旧模型。
"""
from datetime import datetime, timedelta


class ContinualTrainingPipeline:
    """持续重训编排器"""

    def __init__(self, config: RollingWindowConfig, model_type: str):
        self.config = config
        self.model_type = model_type
        self.last_retrain: dict = {}       # {symbol_tier: last_retrain_time}

    def check_and_retrain(self, symbol: str, tier: str, data: pd.DataFrame,
                          feature_columns: list, label_fn) -> Optional[SupervisedModel]:
        """
        若距上次重 train > live_retrain_hours, 触发重 train。
        1. RollingWindowGenerator 生成当前窗口
        2. label_fn 生成前瞻标签(&-前缀约定)
        3. 构建 TrainingContext(含 purge/embargo)
        4. model.fit(ctx)
        5. 持久化 model, 更新 last_retrain
        """
        key = f"{symbol}_{tier}"
        if self._due_for_retrain(key):
            model = get_model(self.model_type)
            ctx = self._build_context(symbol, tier, data, feature_columns, label_fn)
            model.fit(ctx)
            model.save(self._model_path(key))
            self.last_retrain[key] = datetime.now()
            return model
        return None
```

**集成点**：
- 与整改 #4（因子学习层）配合：`learned_weighting.py` 调用 `ContinualTrainingPipeline` 而非自己管训练
- `evolution_scheduler.py` 增加持续重训周期
- 标签用 `&-` 前缀约定（`&-fwd_return_5`）
- `config/settings.py`：`ML_TRAIN_PERIOD_DAYS=90`、`ML_PREDICT_PERIOD_DAYS=7`、`ML_PURGE_DAYS=5`、`ML_LIVE_RETRAIN_HOURS=12`、`ML_MODEL_TYPE=lightgbm`

**依赖**：`lightgbm`、`catboost`（可选）、`torch`（GRU 可选）

---

### 整改 #11：LLM 对抗辩论层（对应 P0-6，v2 新增）

**目标**：引入 TradingAgents 式牛/熊对抗辩论 + 独立风险角色层，作为 MLTO quant/qual 辩论的补充防幻觉手段。

#### 11.1 改造 `services/mlto/debate_layer.py`

```python
"""
对抗辩论层 — 对标 TradingAgents bull/bear debate。
与现有 quant/qual 辩论并列, 作为额外防幻觉层。
"""
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class DebateTurn:
    role: str           # 'bull'|'bear'|'risk_aggressive'|'risk_conservative'|'risk_neutral'
    argument: str
    evidence: list      # 证据引用(锚定市场数据)
    confidence: float


@dataclass
class AdversarialDebateResult:
    bull_turns: List[DebateTurn]
    bear_turns: List[DebateTurn]
    risk_turns: List[DebateTurn]
    final_verdict: str          # 'proceed'|'reduce'|'reject'
    net_sentiment: float        # 综合净倾向 [-1, 1]
    consensus_confidence: float


class AdversarialDebateLayer:
    """
    两层对抗辩论:
    Layer 1: 牛 vs 熊(交易论点) — N 轮迭代直到收敛
    Layer 2: 风险角色(激进/保守/中立) — 独立于交易辩论
    """

    def __init__(self, llm_client, max_rounds: int = 3):
        self.llm = llm_client
        self.max_rounds = max_rounds

    def debate(self, trade_proposal, market_context, evidence_chain) -> AdversarialDebateResult:
        """
        Layer 1: bull_agent 必须论证支持; bear_agent 必须论证反对
                 每轮读取对方论点反驳, 直到 net_sentiment 稳定或 max_rounds
        Layer 2: risk 角色 agents 评估幸存提案的风险面
        返回综合裁决 + 置信度
        """
        # Layer 1: 交易辩论
        bull_turns, bear_turns = self._run_trade_debate(trade_proposal, market_context)
        # Layer 2: 风险辩论(独立)
        risk_turns = self._run_risk_debate(trade_proposal, market_context)
        # 综合
        return self._synthesize(bull_turns, bear_turns, risk_turns)

    def _run_trade_debate(self, proposal, context) -> tuple:
        """牛/熊 N 轮对抗 — 每方必须引用证据链"""

    def _run_risk_debate(self, proposal, context) -> list:
        """3 风险角色独立评估"""
```

#### 11.2 新建 `services/ai/market_data_verifier.py`

```python
"""
确定性市场数据验证覆盖层 — 对标 TradingAgents market_data_validator.py。
在 LLM 输出之上叠加确定性市场数据快照, 校验 LLM 引用的数字。
防 LLM 幻觉具体数值(如"布林带上轨 X")。
"""
from dataclasses import dataclass
from typing import Dict, List


@dataclass
class VerificationResult:
    verified: bool
    discrepancies: List[dict]   # [{'llm_claim': 'RSI=72', 'actual': 68.3, 'delta': 3.7}]
    corrected_values: Dict[str, float]


class MarketDataVerifier:
    """LLM 输出的确定性验证器"""

    def verify(self, llm_output: str, market_snapshot: dict) -> VerificationResult:
        """
        1. 从 LLM 输出提取数值声明(RSI/MACD/价格/funding...)
        2. 对照 market_snapshot 的确定性计算值
        3. 标记 |delta| > tolerance 的差异
        4. 返回校正后的值(供下游使用, 而非 LLM 幻觉值)
        """
```

**集成点**：
- `mlto/orchestrator.py` 在 quant/qual 辩论后加 `AdversarialDebateLayer.debate()`
- 所有 LLM agent 输出经 `MarketDataVerifier.verify()` 再进下游
- V5 门禁可读取 `AdversarialDebateResult.final_verdict` 作为额外门
- `config/settings.py`：`ADVERSARIAL_DEBATE_ENABLED=true`、`DEBATE_MAX_ROUNDS=3`、`MARKET_DATA_VERIFIER_ENABLED=true`

---

### 整改 #12：加密原生 alpha 源扩展（对应 P1-9，v2 新增）

**目标**：补齐期权偏度、链上深化、社交情绪 NLP、跨 DEX 资金矩阵。

#### 12.1 新建 `services/factor_engine/factors/derivatives/options_skew.py`

```python
"""
期权偏度因子 — 接入 Deribit API。
最高价值缺口: 大多数永续系统忽略期权数据。
"""
from ..factor_base import BaseFactor, FactorMetadata


class DeribitSkewFactor(BaseFactor):
    """Deribit BTC/ETH 期权偏度 — 看跌 bid = 恐惧信号"""
    # 数据源: Deribit API (options chain)
    # 计算: 25-delta call - 25-delta put (skew)
    # 信号: skew < -阈值 → 恐惧(反向看多); skew > +阈值 → 贪婪(反向看空)


class GammaPositioningFactor(BaseFactor):
    """做市商 gamma 定位 — 钉住/磁吸效应"""
    # 参考: FDIC 2024 论文卡尔曼滤波建模偏度动态


class TermStructureFactor(BaseFactor):
    """期权期限结构倒挂 — 短期 IV > 长期 IV = 预期短期波动"""
```

#### 12.2 扩展 `services/factor_engine/factors/onchain/`

```python
# glassnode_factors.py — 接入 Glassnode API
class MVRVZScoreFactor(BaseFactor):
    """MVRV z-score — 均值回归 regime 判断"""

class SOPRFactor(BaseFactor):
    """Spent Output Profit Ratio — 获利了结压力"""

class StablecoinSupplyRatioFactor(BaseFactor):
    """稳定币供应比 — 购买力"""

# nansen_factors.py — 接入 Nansen AI API
class SmartMoneyFlowFactor(BaseFactor):
    """智能资金净流 — 标记钱包的流向"""

class WhaleMovementFactor(BaseFactor):
    """鲸鱼大额移动 — 预测抛压(事件驱动)"""
```

#### 12.3 新建 `services/sentiment/llm_sentiment_pipeline.py`

```python
"""
LLM 情绪 NLP pipeline — 2025 LLM 已超越 FinBERT/VADER。
替代传统情绪聚合指标。
"""
class LLMSentimentPipeline:
    """X/Reddit/Discord → LLM 情绪打分 → 逐资产 z-score(带衰减)"""

    def score(self, social_posts: list, symbol: str) -> float:
        """LLM 打分, 返回 [-1,1] 情绪"""

    def aggregate_with_decay(self, scores: list, halflife_hours: int = 6) -> float:
        """带衰减的聚合(近期帖子权重高)"""
```

#### 12.4 扩展 `services/arbitrage/` 跨 DEX 资金矩阵

```python
# funding_matrix_monitor.py — 接入 CoinGlass/Coinalyze API
class CrossDexFundingMatrix:
    """跨 DEX 资金费率矩阵实时监控 — Hyperliquid/Aster/dYdX/EVEDEX"""
    # 输出套利表面矩阵 → 喂给 arbitrage/orchestrator.py
```

**集成点**：
- 新因子注册到 `FactorRegistry`，加入 `midlong_active_factor_set`
- LLM 情绪 pipeline 输出作为 `intel_signal` 源之一进 `UnifiedSignalBus`
- 鲸鱼移动因子作为事件驱动 alpha，特别适合 LLM 智能体解读（"鲸鱼为何移动"）

**依赖**：Glassnode API key、Nansen API key、Deribit API（免费）、CoinGlass/Coinalyze API key、X/Reddit 数据源

---

### 整改 #13：LLM 供应商工厂 + 语义缓存（对应 P2-16、P2-17，v2 新增）

#### 13.1 新建 `services/ai/llm_factory.py`

```python
"""
LLM 供应商工厂 — 对标 TradingAgents llm_clients/factory.py。
多供应商容错 + 按任务难度路由。
"""
from abc import ABC, abstractmethod


class LLMClient(ABC):
    @abstractmethod
    def complete(self, prompt: str, **kwargs) -> str: ...


class DeepSeekClient(LLMClient): ...
class OpenAIClient(LLMClient): ...
class AnthropicClient(LLMClient): ...
class LocalVLLMClient(LLMClient): ...      # 本地 vLLM(已有 host guide)


CAPABILITY_MAP = {
    # 任务 → 推荐模型档位
    'routine_tick': 'local_vllm',          # 常规决策: 小/快/便宜
    'deep_analysis': 'deepseek',           # 深度分析: 中等
    'ambiguous_event': 'anthropic',        # 模糊事件: 大模型
}


def get_llm(task_type: str) -> LLMClient:
    """按任务类型路由到合适模型"""
    return _CLIENTS[CAPABILITY_MAP[task_type]]


class FailoverLLMClient(LLMClient):
    """容错客户端: primary 失败自动切 fallback"""
    def __init__(self, primary: LLMClient, fallbacks: list):
        self.primary = primary
        self.fallbacks = fallbacks

    def complete(self, prompt, **kwargs):
        for client in [self.primary] + self.fallbacks:
            try:
                return client.complete(prompt, **kwargs)
            except Exception:
                continue
        raise RuntimeError("All LLM providers failed")
```

#### 13.2 新建 `services/ai/semantic_cache.py`

```python
"""
语义缓存 — 市场状态 prompt 高度可缓存。
对标 GPTCache/Valery, 40-90% 成本节省。
"""
import numpy as np


class SemanticCache:
    """嵌入 prompt, 相似查询返回缓存响应"""

    def __init__(self, embedder, similarity_threshold: float = 0.95, ttl_seconds: int = 60):
        self.embedder = embedder
        self.threshold = similarity_threshold
        self.ttl = ttl_seconds
        self._store = {}       # {embedding: (response, timestamp)}

    def get(self, prompt: str) -> Optional[str]:
        """相似度 ≥ threshold 且未过期 → 返回缓存"""

    def set(self, prompt: str, response: str) -> None:
        """存入缓存"""


class CachedLLMClient(LLMClient):
    """带语义缓存的 LLM 客户端"""
    def __init__(self, inner: LLMClient, cache: SemanticCache):
        self.inner = inner
        self.cache = cache

    def complete(self, prompt, **kwargs):
        cached = self.cache.get(prompt)
        if cached:
            return cached
        response = self.inner.complete(prompt, **kwargs)
        self.cache.set(prompt, response)
        return response
```

**集成点**：
- 替换现有 DeepSeek 直接调用为 `FailoverLLMClient(CachedLLMClient(get_llm(task)))`
- 按任务类型路由（短 tier 不调 LLM；中 tier 用 local_vllm；长 tier 模糊事件用 anthropic）
- `config/settings.py`：`LLM_SEMANTIC_CACHE_ENABLED=true`、`LLM_CACHE_TTL_SECONDS=60`、`LLM_CACHE_THRESHOLD=0.95`

---

### 整改 #14：lookahead-analysis 前视检测工具（对应 P2-18，v2 新增）

**目标**：策略无关的前视检测工具，对标 Freqtrade `lookahead-analysis`。

#### 新建 `services/backtest_engine/lookahead_analysis.py`

```python
"""
前视检测工具 — 对标 Freqtrade lookahead-analysis。
蒙特卡洛式打乱 K 线顺序, 检测偷偷偷看未来 K 线的策略。
原理: 若策略真无前视, 打乱时间顺序后结果应显著不同;
      若结果相近 → 策略可能依赖了未来信息(前视 bug)。
"""
import numpy as np


class LookaheadAnalyzer:
    def analyze(self, strategy, data: pd.DataFrame, n_trials: int = 100) -> 'LookaheadReport':
        """
        1. 基线回测(原始顺序)
        2. n_trials 次打乱 K 线顺序回测
        3. 比较: 若打乱后 PnL 与基线相近(高 p-value) → 疑似前视
        """

    def _shuffle_blocks(self, data: pd.DataFrame, block_size: int = 10) -> pd.DataFrame:
        """块打乱(保留局部结构, 打乱块顺序)"""
```

**集成点**：作为 `walk_forward.py` 的可选校验步骤；CI/CD 中对新因子/策略自动跑。

---

### 整改 #15：DSPy 认知层编译器（对应 P0-7，v3 新增）

**目标**：用 DSPy 编译取代失败的 prompt 模板突变（36/36）。核心洞察：失败根因是"无目标信号的自我模板突变"，DSPy 用现有 gate/IC 指标作 metric 编译，正好补上目标信号。

#### 新建 `services/ai/prompt_compiler.py`

```python
"""
DSPy 认知层编译器 — 对标 stanfordnlp/dspy。
用现有 V5 gate 通过率/IC/实盘统计作 metric, 编译而非手写 prompt。
不触碰基础模板, 只优化 instruction + few-shot 示例选择。
"""
import dspy
from dataclasses import dataclass
from typing import Optional


@dataclass
class CompiledPrompt:
    """编译产物 — 替代手写 PromptTemplate"""
    signature_name: str              # 如 "swing_analysis"
    optimized_instruction: str       # DSPy 优化后的指令
    few_shot_examples: list          # 自动选择的示例
    compile_metric_score: float      # 编译时的 metric 得分
    trial_count: int                 # 搜索的 trial 数(喂 DSR)
    parent_signature_hash: str       # 基础模板 hash(不变)


class TradingPromptCompiler:
    """
    把 prompt 优化从'LLM 自我突变'(失败36/36)转为'DSPy metric 驱动编译'。
    metric 来源(本系统已有, 无需新建):
      - V5 gate 通过率(decision_core)
      - 因子 IC(factor_evaluator)
      - 实盘胜率/Sharpe(unified_learning)
    """

    def __init__(self, llm_client, metric_fn):
        self.llm = dspy.LM(model=...)  # 接现有 llm_factory
        self.metric_fn = metric_fn      # 评估函数(用 gate/IC)

    def compile(self, signature: dspy.Signature, train_examples: list,
                max_trials: int = 50) -> CompiledPrompt:
        """
        对标 DSPy BootstrapFewShotWithRandomSearch / MIPRO。
        1. 定义 Signature(输入/输出契约, 不含手写指令)
        2. teleprompter 按 metric 搜索指令+示例
        3. 返回 CompiledPrompt(含 trial_count 喂 PBO-aware 账本)
        """
        teleprompter = dspy.BootstrapFewShotWithRandomSearch(
            metric=self.metric_fn, max_threads=4, num_candidate_programs=max_trials
        )
        compiled = teleprompter.compile(dspy.Module(signature), train_examples)
        return CompiledPrompt(
            optimized_instruction=compiled.instructions,
            few_shot_examples=compiled.demos,
            compile_metric_score=self.metric_fn(compiled),
            trial_count=max_trials,
            parent_signature_hash=hash(signature),
        )

    def evaluate_live(self, compiled: CompiledPrompt, live_decisions: list) -> float:
        """实盘 metric 评估, 决定是否重新编译"""
```

**集成点**：
- 替换 `strategy_learning_service._evolve_prompt`（禁用路径）为 `TradingPromptCompiler.compile`
- metric_fn 用现有 `decision_core` gate 通过率 + `factor_evaluator` IC
- 编译产物存 DB（新表 `compiled_prompts`，含 trial_count 供整改 #21 PBO-aware）
- `config/settings.py`：`DSPY_COMPILE_ENABLED=true`、`DSPY_MAX_TRIALS=50`、`DSPY_RECOMPILE_INTERVAL_DAYS=7`

**回滚**：`DSPY_COMPILE_ENABLED=false` 回退到禁用状态（无 prompt 进化，用 RuntimeGovernor 数值闭环）

**依赖**：`dspy-ai`（加入 pyproject.toml）

---

### 整改 #16：混合 RAG 升级（对应 P0-8，v3 新增）

**目标**：神经嵌入 + 混合检索（BM25+向量）+ ANN 索引 + 交叉编码器重排序，替换 hash 词袋 + 线性扫描。

#### 改造 `qaa_architecture_package/qaa/knowledge/`

```python
# embeddings.py — 神经嵌入 provider 替换 hash
class NeuralEmbeddingProvider(EmbeddingProvider):
    """对标 sentence-transformers / BGE(中文强)"""
    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5"):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model_name)  # 512 维, 中文优化

    def embed(self, text: str) -> np.ndarray:
        return self.model.encode(text, normalize_embeddings=True)


# stores/faiss_store.py — ANN 索引替换线性扫描
class FaissKnowledgeStore(KnowledgeStore):
    """对标 FAISS HNSW / pgvector — O(log N) 近邻检索"""
    def __init__(self, dim: int = 512):
        import faiss
        self.index = faiss.IndexHNSWFlat(dim, 32)   # HNSW 图索引
        self.chunks: list = []                       # id→chunk 映射

    def upsert_batch(self, chunks: list):
        vecs = np.array([c.embedding for c in chunks])
        self.index.add(vecs)

    def search(self, query_vec, top_k=10, filters=None):
        distances, ids = self.index.search(query_vec[None, :], top_k*3)  # 过采样
        return [self.chunks[i] for i in ids[0] if self._match_filters(...)]


# retrieval/hybrid_retriever.py — 混合检索+重排序(对标 2024 SOTA)
class HybridRetriever:
    """
    Query → [BM25 稀疏 ‖ 稠密向量] → RRF 融合 → 交叉编码器重排序 → Top-N
    对标 Qdrant/Qdrant hybrid, +26-31% NDCG
    """
    def __init__(self, bm25_store, vector_store, reranker):
        self.bm25 = bm25_store
        self.vector = vector_store
        self.reranker = reranker        # cross-encoder/ms-marco 或 BGE-reranker

    def retrieve(self, query: str, top_n: int = 5):
        bm25_hits = self.bm25.search(query, top_k=20)
        vec_hits = self.vector.search(self.embed(query), top_k=20)
        fused = self._rrf_fuse(bm25_hits, vec_hits)      # Reciprocal Rank Fusion
        reranked = self.reranker.rerank(query, fused)    # 交叉编码器精排
        return reranked[:top_n]
```

#### 改造 `factory.py`

```python
def create_embedder(config) -> EmbeddingProvider:
    backend = config.get("QAA_EMBEDDING_BACKEND", "neural")  # 默认改神经
    if backend == "neural":
        return NeuralEmbeddingProvider(config.get("model", "BAAI/bge-small-zh-v1.5"))
    return HashEmbeddingProvider()  # 保留兼容

def create_store(config) -> KnowledgeStore:
    backend = config.get("QAA_KNOWLEDGE_BACKEND", "faiss")  # 默认改 FAISS
    if backend == "faiss":
        return FaissKnowledgeStore(dim=512)
    return JsonlKnowledgeStore(...)  # 保留兼容
```

**集成点**：
- `qaa_trade_memory_bridge.py` 的 `_RAG` pipeline 自动用新 provider/store（factory 切换）
- 重建现有 `trading_lessons.jsonl` 为 FAISS 索引（一次性迁移脚本）
- `config/settings.py`：`QAA_EMBEDDING_BACKEND=neural`、`QAA_KNOWLEDGE_BACKEND=faiss`、`QAA_RERANKER_ENABLED=true`

**回滚**：`QAA_EMBEDDING_BACKEND=hash` + `QAA_KNOWLEDGE_BACKEND=jsonl` 一键回退

**依赖**：`sentence-transformers`、`faiss-cpu`（或 `pgvector` 若已在 Postgres）

---

### 整改 #17：EWC + replay 防遗忘（对应 P0-9，v3 新增）

**目标**：RL/因子学习层重训加 Fisher 信息惩罚 + 旧 regime 代表性样本回放，防灾难性遗忘。

#### 新建 `services/learning_core/continual_learning.py`

```python
"""
持续学习防遗忘 — 对标 EWC(PNAS 2017) + EVCL(arXiv 2406.15972)。
适用于 RL policy(线性Q)和因子学习模型(LightGBM/GRU)。
"""
import numpy as np
from dataclasses import dataclass


@dataclass
class FisherInformation:
    """Fisher 信息矩阵(对角近似) — 权重重要性"""
    fisher_diag: dict      # {param_name: importance_vector}
    theta_star: dict       # 旧任务最优参数(锚点)


def compute_fisher(model, old_data_sample, n_samples: int = 500) -> FisherInformation:
    """
    对标 EWC: 在旧任务数据上算每个参数的 Fisher 信息。
    Fisher_i = E[(∂log p/∂θ_i)²] — 衡量参数 i 对旧任务的重要性。
    对线性Q: 解析算; 对 LightGBM: 用 leaf importance 近似; 对 GRU: Empirical Fisher。
    """


class EWCTrainer:
    """带 EWC 惩罚的训练器"""

    def __init__(self, ewc_lambda: float = 400.0):
        self.ewc_lambda = ewc_lambda     # 惩罚强度(论文默认400)
        self.fisher_history: list = []   # 多任务 Fisher 栈

    def penalized_loss(self, model, new_loss, current_params):
        """
        total_loss = new_loss + λ Σ F_i (θ_i − θ*_i)²
        重要参数(F_i 大)几乎不动, 不重要参数自由适应新数据。
        """
        penalty = 0.0
        for fisher_info in self.fisher_history:
            for name, fisher in fisher_info.fisher_diag.items():
                theta_star = fisher_info.theta_star[name]
                theta = current_params[name]
                penalty += np.sum(fisher * (theta - theta_star) ** 2)
        return new_loss + self.ewc_lambda * penalty


class ReplayAugmentedTrainer:
    """旧 regime 代表性样本回放 — 对标 EVCL"""

    def __init__(self, replay_ratio: float = 0.3):
        self.replay_ratio = replay_ratio

    def mix_batch(self, new_batch, old_regime_buffer):
        """
        新 batch 掺 30% 旧 regime 代表性样本(按 regime 分层采样)。
        配合 EWC 双重防遗忘。
        """
        n_replay = int(len(new_batch) * self.replay_ratio)
        replay_samples = old_regime_buffer.sample_stratified(n_replay)  # 按 regime 分层
        return new_batch + replay_samples
```

**集成点**：
- `rl_core/policy.py` 的 `train_from_replay` 包一层 `EWCTrainer`：每次重训前算当前 Fisher，训练时加惩罚
- 因子学习层（整改 #4/#10）的 `SupervisedModel.fit` 接 `EWCTrainer`
- `concept_drift_detector` 触发重训时，驱动 `EWCTrainer.fisher_history.append(compute_fisher(...))`
- 旧 regime buffer 从现有 `replay_buffer` 按 regime 标签分层采样
- `config/settings.py`：`EWC_ENABLED=true`、`EWC_LAMBDA=400`、`REPLAY_RATIO=0.3`

**回滚**：`EWC_ENABLED=false` 回退到无防遗忘（当前行为）

---

### 整改 #18：DDG-DA 主动分布预测（对应 P1-10，v3 新增）

**目标**：把因子衰减监控从被动检测升级为主动预测下期分布，重训前预加权。对标 Qlib DDG-DA（AAAI 2022）。

#### 新建 `services/learning_core/distribution_forecaster.py`

```python
"""
DDG-DA 主动分布漂移预测 — 对标 Qlib DDG-DA(arXiv 2201.04038)。
把 concept_drift_detector 的输出从'事后触发回顾'改为'驱动预测+预加权'。
"""
from dataclasses import dataclass


@dataclass
class DistributionForecast:
    """下一期数据分布预测"""
    predicted_regime: str
    sample_weights: dict        # {历史样本idx: 权重} — 预加权用
    confidence: float
    attention_weights: dict     # 哪些历史时期最像未来


class DDGDAForecaster:
    """
    三步(对标 DDG-DA):
    1. DDG: 神经元模型预测近未来数据分布
    2. Domain-Attentive: 注意力学哪些历史时期最像未来
    3. 重训前用 sample_weights 重加权历史样本匹配预测分布
    """

    def __init__(self, meta_model):
        self.meta_model = meta_model     # 轻量 MLP 预测分布

    def forecast_next_distribution(self, factor_history, drift_signal) -> DistributionForecast:
        """
        输入: 因子历史序列 + concept_drift_detector 的漂移信号
        输出: 下期分布预测 + 历史样本预加权
        """

    def reweight_training_data(self, data, forecast: DistributionForecast):
        """重训前调用 — 按 forecast.sample_weights 重加权历史样本"""


# 渐进实现路径(降低复杂度):
class DriftTriggeredReweighter:
    """DDG-DA 的简化版: 用 drift 信号直接调整样本权重, 非完整元学习"""
    def reweight(self, data, drift_score):
        """drift 强 → 近期样本权重高; drift 弱 → 均匀权重"""
```

**集成点**：
- `concept_drift_detector` 检测到漂移时，不再只触发回顾，而是调 `DDGDAForecaster.forecast_next_distribution`
- 因子学习层 `ContinualTrainingPipeline`（整改 #10）重训前调 `reweight_training_data`
- 渐进路径：先实现 `DriftTriggeredReweighter`（简化版），验证有效后再上完整 DDG-DA
- `config/settings.py`：`DDGDA_ENABLED=true`、`DDGDA_MODE=simplified`（先简化版）

**回滚**：`DDGDA_ENABLED=false` 回退到被动检测（当前行为）

**依赖**：`torch`（元模型，可选）

---

### 整改 #19：MAP-Elites 多样性冠军库（对应 P1-11，v3 新增）

**目标**：替换单 champion recovery 为 regime 索引的 elite 网格——一个 regime 一个 elite，运行时按当前 regime 选。

#### 新建 `services/learning_core/map_elites_archive.py`

```python
"""
MAP-Elites 质量-多样性冠军库 — 对标 Mouret&Clune 2015 / MOME(arXiv 2202.03057)。
替换单 champion_recovery 为行为特征空间网格, 每格留精英。
"""
from dataclasses import dataclass
import numpy as np


@dataclass
class BehaviorDescriptor:
    """行为特征(网格维度) — 用 regime 维度"""
    regime: str              # trending_up/trending_down/ranging/volatile/extreme
    timeframe: str           # short/mid/long
    volatility_bucket: str   # low/med/high


@dataclass
class EliteEntry:
    champion_genome: dict
    fitness: float
    behavior: BehaviorDescriptor
    metrics: dict            # sharpe/win_rate/mdd
    cumulative_trial_count: int   # 喂 PBO(整改 #21)


class MAPelitesArchive:
    """
    行为特征空间分网格, 每格只留 fittest 个体。
    运行时按当前 regime/timeframe/vol 选 elite。
    """

    def __init__(self, dims: list):
        self.grid: dict = {}     # {BehaviorDescriptor: EliteEntry}

    def add(self, genome, fitness, behavior, metrics, trial_count):
        """若该格为空或新个体更优 → 替换"""
        key = behavior
        if key not in self.grid or fitness > self.grid[key].fitness:
            self.grid[key] = EliteEntry(genome, fitness, behavior, metrics, trial_count)

    def select_elite(self, current_behavior: BehaviorDescriptor) -> EliteEntry:
        """运行时按当前 regime 描述选适配 elite"""
        if current_behavior in self.grid:
            return self.grid[current_behavior]
        return self._nearest_behavior(current_behavior)  # 最近邻行为

    def all_elites(self) -> list:
        """返回所有行为格的精英(多样性库)"""


class MOMEArchive(MAPelitesArchive):
    """MOME 扩展: 每格维持 Pareto 前沿(非单精英) — 多目标+多样性"""
    def add(self, genome, objectives: dict, behavior, trial_count):
        """每格 NSGA-II 非支配排序维持小 Pareto 前沿"""
```

**集成点**：
- `strategy_evolver.persist_genetic_result` 在保存单 champion 时，同时写入 `MAPelitesArchive.add`
- `champion_recovery_service` 扩展：恢复时按当前 regime 从 archive `select_elite`
- `multi_timeframe_orchestrator` 按 regime 调用 `select_elite` 选策略（而非固定冠军）
- `config/settings.py`：`MAP_ELITES_ENABLED=true`、`MAP_ELITES_MODE=mome`（多目标）

**回滚**：`MAP_ELITES_ENABLED=false` 回退到单 champion recovery

---

### 整改 #20：CMA-ES 连续参数精调（对应 P1-11/P1-12，v3 新增）

**目标**：用 Optuna CMA-ES 精调每个 champion 的 runtime_tuning 连续参数，替换 QAA -10% 朴素启发式。

#### 改造 `qaa_architecture_package/qaa/evolution/optimizer.py` + 新建 CMA-ES 包装

```python
"""
CMA-ES 连续精调 — 对标 Hansen CMA-ES / Optuna CMA-ES sampler。
替换 QAA AutoOptimizer 的 -10% 均匀扰动。
"""
import optuna


class CMAESOptimizer:
    """Optuna CMA-ES 包装 — 连续参数协方差自适应优化"""

    def optimize(self, objective_fn, param_space: dict, n_trials: int = 100):
        """
        objective_fn: genome → fitness(跑回测)
        CMA-ES 适应搜索分布协方差, 学习参数相关性(如 stop_loss 与 leverage 联动)
        """
        def optuna_objective(trial):
            params = {k: trial.suggest_float(k, lo, hi) for k, (lo, hi) in param_space.items()}
            return objective_fn(params)

        sampler = optuna.samplers.CMAESSampler(seed=42)
        study = optuna.create_study(direction='maximize', sampler=sampler)
        study.optimize(optuna_objective, n_trials=n_trials)
        return study.best_params, study.best_value


# QAA AutoOptimizer 替换 -10% 逻辑(optimizer.py:214-225)
class RealAutoOptimizer:
    """替换 AutoOptimizer 的 -10% 为 CMA-ES"""
    def _tune_config(self, current_config, eval_fn):
        param_space = {k: (v*0.7, v*1.3) for k, v in current_config.items()
                       if isinstance(v, (int, float))}
        best, score = CMAESOptimizer().optimize(eval_fn, param_space)
        return best  # 非 -10%, 而是协方差感知的优化
```

**集成点**：
- `qaa_evolution_bridge.StrategyTuner` 的 `AutoOptimizer.run_cycle` 用 `RealAutoOptimizer` 替换
- 冠军基因组的 runtime_tuning 连续参数（min_risk_reward/confidence）可用 CMA-ES 精调（在 RuntimeGovernor 走廊内）
- `config/settings.py`：`QAA_OPTIMIZER=cmaes`（替代 `naive_minus_10`）、`CMAES_TRIALS=100`

**回滚**：`QAA_OPTIMIZER=naive_minus_10` 回退

**依赖**：`optuna`（v3 整改 #1 已加入）

---

### 整改 #21：PBO-aware 血缘账本（对应 P1-13，v3 新增）

**目标**：EvolutionEnvelope 每阶段记录累计 trial N，使 DSR/PBO 可跨代在线诚实重算。

#### 改造 `services/learning_core/envelope.py` + `ledger.py`

```python
# envelope.py — EvolutionEnvelope 加 PBO 字段
@dataclass
class EvolutionEnvelope:
    # ... 原有字段 ...
    # ===== v3 新增: PBO-aware =====
    cumulative_trial_count: int = 0    # 该 lineage 累计试验数(跨代)
    is_oos: bool = False               # 样本内/外标记
    selection_rank: Optional[int] = None  # 在当代的选择排名(供 PBO)


# ledger.py — 加 PBO 重算方法
class LearningLedger:
    def compute_cumulative_dsr(self, observed_sharpe: float) -> dict:
        """
        累积 Deflated Sharpe Ratio — 对标 Bailey&López de Prado 2014。
        N = 所有 lineage 的累计 trial 总和(非单次运行)。
        返回 {dsr, p_value, deflated_sharpe, min_required_years}。
        """

    def compute_pbo_cscv(self, n_blocks: int = 16) -> float:
        """
        组合对称交叉验证 PBO — 对标 López de Prado。
        用所有 lineage 的 IS/OOS 表现矩阵算 PBO。
        PBO>0.5 过拟合, <0.1 稳健。
        """

    def champion_overfit_audit(self, champion_lineage_id: str) -> dict:
        """审计 champion 是否 in-sample 过拟合(含 recovery 重引入的额外 trial)"""
```

**集成点**：
- `ledger.record(env)` 时自动 `env.cumulative_trial_count = self._count_trials(env.lineage_id) + 1`
- `champion_recovery_service` 恢复时，recovery 计为额外 trial，累计入账本
- `evolution_scheduler.weekly_evolution` 选 champion 前，先查 `compute_pbo_cscv()`，PBO>0.5 则拒绝该代晋升
- 前端"进化 hub"显示 PBO/DSR 仪表盘
- `config/settings.py`：`PBO_AUDIT_ENABLED=true`、`PBO_REJECT_THRESHOLD=0.5`

**回滚**：`PBO_AUDIT_ENABLED=false`（仅不审计，不影响进化本身）

**依赖**：复用整改 #1 的 `overfitting_metrics.py`（CSCV/PBO/DSR）

---

## 第九部分　优先级路线图

### 9.1 总体优先级

```
┌──────────────────────────────────────────────────────────────────────┐
│  P0 方法论真实性 + ML 管线(最高优先)                                   │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌──────────────┐            │
│  │ #3 滑点  │→│ #2 子K线 │→│ #1 WFO   │→│ #10 ML训练管线│            │
│  │ 统一    │  │ 回测     │  │ 方法论   │  │ (FreqAI式,    │            │
│  │         │  │          │  │          │  │  P0-3前置)    │            │
│  └─────────┘  └─────────┘  └─────────┘  └──────┬───────┘            │
│   工作量:S      工作量:S     工作量:M            │                    │
│                                                  ▼                    │
│                                       ┌──────────────┐               │
│                                       │ #4 因子学习层 │               │
│                                       │ (最大价值)    │               │
│                                       └──────────────┘               │
│                                        工作量:L                      │
├──────────────────────────────────────────────────────────────────────┤
│  P0 认知层复活 + 记忆升级 + 防遗忘(v3 新增, 学习进化核心)             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │
│  │ #15 DSPy认知  │  │ #16 混合RAG   │  │ #17 EWC防遗忘│               │
│  │ 层编译器      │  │ 神经嵌入+ANN  │  │ (RL/因子层)  │               │
│  │ (36/36修复)   │  │ +重排序       │  │              │               │
│  └──────────────┘  └──────────────┘  └──────────────┘               │
│   工作量:M          工作量:M           工作量:M                       │
├──────────────────────────────────────────────────────────────────────┤
│  P0 LLM 层强化(v2 新增)                                              │
│  ┌──────────────────────────────────────┐                            │
│  │ #11 对抗辩论 + 数据验证覆盖层          │                            │
│  │   (TradingAgents式, 防幻觉)           │                            │
│  └──────────────────────────────────────┘                            │
│   工作量:M                                                            │
├──────────────────────────────────────────────────────────────────────┤
│  P1 架构债务 + 加密 alpha + 演化进阶                                  │
│  ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌─────────────┐  │
│  │ #5 引擎  │ │ #7 QAA  │ │ #8 拆分  │→│ #9 事件  │ │#12 加密alpha │  │
│  │ 硬风控   │ │ 双轨收敛 │ │ monolith │ │ 溯源     │ │(期权/链上)   │  │
│  └─────────┘ └─────────┘ └──────────┘ └──────────┘ └─────────────┘  │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌────────────┐ │
│  │#18 DDG-DA主动 │ │#19 MAP-Elites│ │#20 CMA-ES    │ │#21 PBO-aware│ │
│  │ 分布预测      │ │ 多样性冠军库  │ │ 连续精调     │ │ 血缘账本    │ │
│  └──────────────┘ └──────────────┘ └──────────────┘ └────────────┘ │
├──────────────────────────────────────────────────────────────────────┤
│  P2 增强(按需)                                                       │
│  #6 参数空间 | Volume Bar | filters | Protections | 表达式引擎        │
│  #13 LLM工厂+缓存 | #14 lookahead-analysis | Rust核心 | 生命周期契约   │
└──────────────────────────────────────────────────────────────────────┘
```

### 9.2 分阶段实施计划

#### 阶段一：回测真实性修复（P0，2-3 周）

| 序号 | 整改 | 工作量 | 依赖 | 验收标准 | 回滚方案 |
|------|------|--------|------|---------|---------|
| 1 | #3 滑点统一 | S | 无 | 全局仅 1 个 `calc_slippage` 实现；回测/实盘同源 | 保留旧函数 DeprecationWarning |
| 2 | #2 子K线回测 | S | #1 | `intrabar_resolution=True` 时 SL/TP 用 high/low；`timeframe_detail=1m` 可用 | `fill_model='close'` 完全等价旧行为 |
| 3 | #1 WFO 方法论 | M | 无 | PBO/DSR/MinBTL 可计算；purge/embargo 生效；Optuna 可用 | `optimizer='grid'` 回退 |
| 4 | #14 lookahead-analysis | S | 无 | 打乱 K 线检测工具可用；现有策略跑无前视告警 | 可选步骤，不影响现有 |

**阶段一验收**：用现有策略跑 walk-forward，PBO 应 < 0.3；对比子K线回测前后收益差异（预期 scalp 策略差异最大）。

#### 阶段二：ML 训练管线 + 因子学习层（P0，6-10 周）

| 序号 | 整改 | 工作量 | 依赖 | 验收标准 | 回滚方案 |
|------|------|--------|------|---------|---------|
| 5 | #10 FreqAI 式训练管线 | L | #1（WFO 滚动窗） | `TrainingContext` 上下文对象可用；持续重训循环运行；前视防护通过 | 新建模块，不影响现有 |
| 6 | #4 因子学习层 | L | #10（训练管线） | `mode='learned'` 的 OOS IC 显著优于 `mode='regime'` | `FACTOR_WEIGHTING_MODE=regime` 一键回退 |

**阶段二验收**：shadow 模式运行 2 周，learned 的滚动 IC ≥ regime 的 IC；hybrid 模式 A/B 测试 learned 胜率 > 60%；训练管线 lookahead-analysis 无前视告警。

#### 阶段 2.5：认知层复活 + 记忆升级 + 防遗忘（P0-7/8/9，v3 新增，4-6 周）

| 序号 | 整改 | 工作量 | 依赖 | 验收标准 | 回滚方案 |
|------|------|--------|------|---------|---------|
| 7 | #15 DSPy 认知层编译器 | M | 无 | DSPy 编译 prompt 的 gate 通过率 ≥ 手写模板；不再 36/36 失败 | `DSPY_COMPILE_ENABLED=false` |
| 8 | #16 混合 RAG 升级 | M | 无 | 神经嵌入+FAISS+混合检索+重排序上线；NDCG 提升 ≥20%；中文术语语义匹配通过 | `QAA_EMBEDDING_BACKEND=hash` |
| 9 | #17 EWC + replay 防遗忘 | M | #4（因子学习层） | RL/因子层重训后旧 regime 性能保持率 ≥80%（无灾难性遗忘） | `EWC_ENABLED=false` |

**阶段 2.5 验收**：DSPy 编译的 prompt 在 Paper 模式实盘胜率 ≥ 手写模板；RAG 检索中文"交易教训"能召回语义相似（非字面匹配）的"复盘教训"；EWC 重训前后旧 regime 回测 Sharpe 衰减 <20%。**这是系统"从经验中学习"能力的核心修复**。

#### 阶段三：LLM 层强化 + 架构债务（P0-6 + P1，6-10 周）

| 序号 | 整改 | 工作量 | 依赖 | 验收标准 | 回滚方案 |
|------|------|--------|------|---------|---------|
| 10 | #11 对抗辩论 + 数据验证 | M | 无 | 牛/熊辩论层运行；`MarketDataVerifier` 校验 LLM 数值；裁决进 V5 门 | `ADVERSARIAL_DEBATE_ENABLED=false` |
| 11 | #5 引擎硬风控 | M | 无 | OrderDenied 原因码标准化；精度/名义价值/限流生效 | `RISK_ENGINE_ENABLED=false` |
| 12 | #7 QAA 收敛 | S | 无 | 单一 AgentCard 定义；`backend/services/qaa/` 仅含交易域逻辑 | 保留本地定义作 fallback |
| 13 | #8 monolith 拆分 | L | 无 | `full_auto_trading_service.py` < 1000 行；各 loop 独立可测 | thin shim 转发 |
| 14 | #9 事件溯源 | L | #8（拆分后） | 事件日志可重放重建仓位；崩溃恢复验证 | shadow 模式，不影响写路径 |
| 15 | #12 加密 alpha 扩展 | M | 无 | 期权偏度/链上深化/社交 NLP/跨 DEX 资金矩阵因子入注册表 | 新增因子，不影响现有 |

**阶段三验收**：对抗辩论使 LLM 决策的幻觉率下降；引擎层风控拦截 ≥1 类异常订单；monolith 拆分后回归测试全通过；新加密 alpha 因子 IC > 0.015。

#### 阶段 3.5：演化进阶（P1-10/11/12/13，v3 新增，6-8 周）

| 序号 | 整改 | 工作量 | 依赖 | 验收标准 | 回滚方案 |
|------|------|--------|------|---------|---------|
| 16 | #18 DDG-DA 主动分布预测 | L | #4（因子层） | 漂移触发→预加权→重训的 OOS 性能优于被动重训；先简化版验证 | `DDGDA_ENABLED=false` |
| 17 | #19 MAP-Elites 多样性冠军库 | M | 无 | regime 切换时从 archive 选 elite 的表现 ≥ 单 champion；多样性库 ≥3 行为格 | `MAP_ELITES_ENABLED=false` |
| 18 | #20 CMA-ES 连续精调 | S | 无 | CMA-ES 精调 runtime_tuning 收敛 trial 数 ≤ QAA -10% 的 50%；最终 fitness 更优 | `QAA_OPTIMIZER=naive_minus_10` |
| 19 | #21 PBO-aware 血缘账本 | M | #1（过拟合指标） | EvolutionEnvelope 记录累计 trial；DSR 跨代累计；PBO>0.5 拒绝晋升生效；champion 过拟合审计可用 | `PBO_AUDIT_ENABLED=false` |

**阶段 3.5 验收**：DDG-DA 预测的 regime 准确率 > 60%；MAP-Elites 库覆盖 ≥5 regime；CMA-ES 收敛快于 QAA；PBO-aware 使过拟合 champion 被识别并拒绝晋升。

#### 阶段四：增强项（P2，按需）

低优先，按业务需求逐步引入：
- `#6 参数空间`（提升研发效率）
- `#13 LLM 工厂 + 语义缓存`（成本/容错）
- 表达式引擎（因子研发加速）
- Volume/Tick Bar 聚合、Jesse filters、Protections 链、Rust 核心、统一生命周期契约

### 9.3 风险控制原则

1. **所有 P0/P1 整改必须有回滚开关**（env var 一键回退到旧行为）
2. **shadow 模式先行**：#4 因子学习层、#5 引擎风控、#9 事件溯源、#10 训练管线、#11 对抗辩论、#15 DSPy、#18 DDG-DA 均先 shadow 运行验证
3. **A/B 对比**：#4 必须证明 OOS IC 优于手工表才切换主路径；#11 必须证明幻觉率下降；#15 DSPy 编译的 prompt 必须证明 gate 通过率 ≥ 手写；#17 EWC 必须证明旧 regime 保持率
4. **不破坏 V5 门禁**：所有整改不得削弱现有 fail-closed 门禁链
5. **Paper 模式验证**：所有整改先在 Paper 模式跑通再进 Live
6. **LLM 不进执行门**（v2）：遵循 24k 实验结论——LLM 用于中长周期 + 事件/regime 分析，**短 tier 执行门保持纯规则**
7. **PBO 诚实累计**（v3 新增）：NSGA-II 每代 trial + champion recovery 重引入必须累计入账本，DSR 跨代算，防止"冠军 Sharpe 是选择偏差产物"

---

## 第十部分　实施前提、现状勘误与地基（2026-07-09 核验补充）

> 本部分由 2026-07-09 一次独立代码核验补充：对全文 P0/P1 断言逐条到代码库比对，并给出开工前必须落地的"地基"。核验方式为三路只读探查交叉验证（回测 / 因子·ML·RL / LLM·RAG·QAA）。

### 10.1 核验结论：方案断言与代码高度吻合（约 95% 属实）

抽样核验的 P0-1/2/3/4/5/7/9、P1-11 全部**属实**（文件、行号、函数名、缺陷均对得上）。方案是基于真实代码撰写的，可作为可靠的实施依据。以下 4 处需按实际代码勘误。

### 10.2 现状勘误（4 处，修正后再据此实施）

1. **`walk_forward.py` 的 `step_days=21`**：是 `WalkForwardConfig` 的**可配置默认值**，非写死常量。表述应改为"默认 step_days=21（可配置）"。
2. **`factor_weighting.py` 的 `_fine_tune_weights` ±0.1**：是调整**上限**（`min(0.1, |normalized|*0.05)`），非固定步长。
3. **P0-6 的 quant/qual 归属**：quant/qual 分属 `mlto/quant_layer.py`（确定性，约 85% 权重）+ `mlto/qual_layer.py`（LLM thesis），经 `decision_hub.fuse_signals` 融合；`mlto/debate_layer.py` 实为**规则式牛熊灰区调节器（明确 `without extra LLM`，仅约 2% 权重）**，并非"quant vs qual 辩论"。#11 对抗辩论仍成立（缺 LLM 多轮 bull/bear + 独立风险角色 + `market_data_validator`），但现状描述按此修正。
4. **P0-8 的 RAG 现状（重要）**：全库**并非**统一的 hash+线性扫描。**弱的只是 QAA 交易记忆这条链路**（`qaa/knowledge` 恒用 `HashEmbeddingProvider` + O(N) 余弦，`qaa_trade_memory_bridge.py` 走此路）。**后端另有一套成熟神经 RAG**：`services/rag_knowledge_service.py`（`BAAI/bge-large-zh-v1.5` + ChromaDB + HNSW ANN），供主控 LLM 使用。
   - **由此收窄 #16 范围**：无需从零自建"神经嵌入 + FAISS + 重排序"，**首选把 QAA 交易记忆改接已有的 ChromaDB 神经 RAG**（`rag_knowledge_service`），低风险、低成本、见效快。重排序（cross-encoder）作为可选增强再议。

### 10.3 开工前应先止血的真 Bug（不在原 21 项内）

- **`backtest_engine.py::_run_vectorized` 第 239 行引用未定义的 `trades_mask`（NameError）**，而 `BacktestMode.VECTORIZED` 是**默认模式**——即任何默认向量化回测都会直接崩溃。工作量极小，建议纳入"第 0 步"随手修掉（本身也是 #1/#2 回测整改的前置）。

### 10.4 实施策略调整（业主决策 2026-07-09）

- **不预先卡"数据充足性/基线"门**：不因"样本够不够"阻塞开工。改为**开工后用 shadow 影子模式边跑边验**——`#4 因子学习层`、`#10 训练管线` 先 shadow 计算不影响实盘，若 OOS IC 稳定优于手工表再切换；样本不足时 shadow 指标自然不达标，即为"暂不切换"的客观信号，不做前置硬门。
- 该决策**不削弱** 9.3 的回滚/AB/Paper 先行等安全原则。

### 10.5 地基清单（"第 0 步"，开工前完成）

| 项 | 内容 | 目的 |
|----|------|------|
| G1 | **打包备份**当前代码+配置+文档+git 历史（排除 logs/依赖/大数据缓存） | 可一键回滚的还原点 |
| G2 | 修 `trades_mask` 真 bug（10.3） | 扫掉进场地雷 |
| G3 | 盘点关键路径现有测试覆盖；`#8 拆分 monolith`、`#9 事件溯源` **开工前必须先补特征化测试(characterization tests)** | 无测试网不拆 2 万行巨兽 |
| G4 | 训练与实盘热路径**资源隔离**原则入宪：所有 lightgbm/torch/DSPy 训练**离进程/离峰**运行，禁止占用短线扫描热路径 | 保住刚修好的秒级延迟，别被训练重新拖垮 |

### 10.6 调整后的开工顺序（先易后难、先立地基）

1. **第 0 步（地基）**：G1 备份 → G2 修 bug → G3 摸测试底 → G4 定资源隔离原则。
2. **第 1 步（低风险高回报，快速建立信心）**：`#3 滑点统一`(S) → `#2 子K线回测`(S) → `#14 前视检测`(S) → **`#16 缩水版`**（QAA 记忆改接已有 ChromaDB RAG）。
3. **第 2 步（重头戏，严格 shadow→AB→切换）**：`#1 WFO`(M) → `#10 训练管线`(L) → `#4 因子学习层`(L)；并行 `#11 对抗辩论`、`#5 引擎硬风控`。
4. **最后（等测试网建好再碰）**：`#8 拆分` / `#9 事件溯源` / `#15 DSPy`（当作有"止损线"的技术探索：限期证明不了 gate 通过率 ≥ 手写即砍）。

### 10.7 保障铁律（补充 9.3）

- **每一项 = env 开关 + shadow + A/B + 明确"止损线"**（限期证明不了收益就回滚，避免无止境投入）。
- **训练永远与实盘热路径资源隔离**（G4）。
- **切换主路径前用基线数字证明"确实更好"**，而非"感觉更好"。
- **#15 DSPy 视为研究赌注**：36/36 历史失败说明此场景 LLM 自我改进极难，不列为必交付。

### 10.8 最终交付状态（2026-07-09 落地回填）

> 全部 21 项 + 地基项已按"零风险交付"原则落地：默认开关关闭 / 优雅降级 / 不改实盘热路径 / 独立冒烟测试通过 / lint 干净 / 配置项已写入 `backend/.env.example`。可选重依赖（`cmaes`/`sentence-transformers`/`dspy`/`torch`/`lightgbm`/`catboost`）缺失时自动降级，不阻塞离线运行。

| 项 | 状态 | 落地文件（新增/改造） | 回滚开关（默认值） |
|----|------|----------------------|-------------------|
| G1 备份 | ✅ | 打包还原点 | — |
| G2 trades_mask bug | ✅ | `backtest_engine/backtest_engine.py` | — |
| #3 滑点统一 | ✅ | `backtest_engine/cost_model.py`→`fee_guard.calc_slippage_rate` 单一真源 | 回退旧法 |
| #2 子K线回测 | ✅ | `backtest_engine/backtest_engine.py`（close→intrabar high/low→sub-bar 扫描） | — |
| #14 前视检测 | ✅ | `backtest_engine/lookahead_analysis.py` | 可选步骤 |
| #16 混合RAG（缩水版） | ✅ | QAA 记忆接 `ChromaKnowledgeStore` + 神经嵌入 provider + factory | `QAA_EMBEDDING_BACKEND=hash` / `QAA_KNOWLEDGE_BACKEND=jsonl` |
| #1 WFO 方法论 | ✅ | `walk_forward.py`＋`overfitting_metrics.py`（PBO/CSCV/DSR）＋`loss_functions.py`＋Optuna/purge-embargo | 缺 optuna 回退 grid |
| #10 FreqAI 训练管线 | ✅ **主路径已接线** | `ml/{training_context,model_base,training_pipeline,activation_service}.py`；`learning_loop` 维护周期异步重训 | `ML_PIPELINE_ENABLED=false` |
| #4 因子学习层 | ✅ **hybrid 主路径已接线** | `factor_engine/learned_weighting.py` + `factor_evaluation_pipeline` 融合 | `FACTOR_WEIGHTING_MODE=regime` |
| #11 对抗辩论+数据验证 | ✅ | `mlto/debate_layer.py::AdversarialDebateLayer`＋`ai/market_data_verifier.py` | `ADVERSARIAL_DEBATE_ENABLED=false` |
| #5 引擎层硬风控 | ✅ | `exchange/risk_engine.py` | `RISK_ENGINE_ENABLED=false`（透传） |
| #7 QAA 双轨收敛 | ✅（模型层） | `qaa/models.py` shim + `models_local.py` fallback；RuleRouter 行为级收敛按铁律缓影子 | 本地定义 fallback |
| #12 加密 alpha 扩展 | ✅ **主路径已接线** | `deribit_options.py` + `factor_bridge.inject_deribit_into_klines` → `compute_all_factors` / Registry | `DERIBIT_OPTIONS_ENABLED=false` |
| #15 DSPy 认知层编译器 | ✅ | `ai/prompt_compiler.py`（gate/IC 作 metric 驱动 few-shot 搜索；dspy 有则接真编译） | `DSPY_COMPILE_ENABLED=false`（no-op） |
| #16b 交叉编码器重排序 | ✅ | `qaa/knowledge/reranker.py`（CrossEncoder+词法回退+RRF）；`RAGPipeline` opt-in | `QAA_RERANKER_ENABLED=false` |
| #17 EWC+replay 防遗忘 | ✅ **重训路径已接线** | `activation_service` → `ReplayAugmentedTrainer` + `EWCTrainer.consolidate` | `EWC_ENABLED=false`（penalty=0） |
| #18 DDG-DA 主动分布预测 | ✅ **重训前已接线** | `activation_service` → `reweight_training_data` + `TrainingContext.sample_weights` | `DDGDA_ENABLED=false`（全1权重） |
| #19 MAP-Elites 多样性库 | ✅ | `learning_core/map_elites_archive.py`（含 MOME） | `MAP_ELITES_ENABLED=false` |
| #20 CMA-ES 连续精调 | ✅ | `learning_core/cmaes_optimizer.py`（optuna 缺失回退随机搜索） | `QAA_OPTIMIZER=naive_minus_10` |
| #21 PBO-aware 血缘账本 | ✅ | `learning_core/pbo_audit.py`＋`envelope.py` 兼容加字段（复用 #1 指标） | `PBO_AUDIT_ENABLED=false`（恒放行） |
| #6 声明式参数空间 | ✅ | `strategy/param_spaces.py` | 新增，不影响现有 |
| #13 LLM 工厂+语义缓存 | ✅ | `ai/{semantic_cache,llm_factory}.py` | `LLM_SEMANTIC_CACHE_ENABLED=false` |
| #8 monolith 拆分 | ✅ Phase 2（持续推进，未 100% 完成） | `full_auto/orchestrator.py` + 47 个 Host/shim 模块（2026-07-10 A–G 批：paper/orch/sizing/tp_sl/live/midlong/data_health；续批新增：analyst_system_cycle/qaa_v3_tick_cycle/qaa_legacy_cycle/proposal_execution/mlto_cycle/quick_orchestrator_eval/hold_timeout_trend_review/light_trading_cycle/v3_factor_pipeline/strategy_lifecycle/refresh_positions/strategy_creation/symbol_risk）；monolith 降至 ≈ **3753** 行（非空行） | 公开 API 与少量收尾辅助仍留 monolith，未彻底拆完 |
| #9 事件溯源 | ✅ Phase 4 **Paper 已激活** | `phase4.py` event-first 写 + DB 镜像同步 | `.env` 显式 `WRITE_RETIRE_DB=true` |

**#8 Phase 1 已完成（2026-07-09）**：七大 loop 已全部拆至 `full_auto/loops/`。Monolith 仍保留执行层辅助方法。

### 10.9 解锁 #8/#9 破坏性重构的特征化测试网清单（下一里程碑）

> 目的：为 `full_auto_trading_service.py`（约 2 万行）建立"改造前后行为等价"的安全网，之后才允许拆 loop / 切事件溯源读写路径。原则：**先钉住现状行为（哪怕现状不完美），再重构**。

| 编号 | 测试对象 | 特征化断言（钉住现状 I/O） | 优先级 |
|------|---------|--------------------------|--------|
| C1 | `FullAutoState` 边界（已可测） | 日亏/连亏/跨日重置、冻结自动解冻、符号冷却过期清理、回撤缩仓阈值 | ✅ 已覆盖 |
| C2 | 各独立 loop 的一次 tick（coordinator/midlong/scalp/arbitrage/learning/maintenance） | 给定固定行情快照+DB stub，断言"产出的开/平仓意图集合"与现状逐字节一致（golden snapshot） | ✅ guard-path 6 场景 + happy-path 4 场景 |
| C3 | 冻结/冷却/日亏门禁在 loop 中的联动 | 触发日亏上限后本 tick 不再产开仓意图；冻结期内 scalp/midlong 均跳过 | ✅ 已覆盖 |
| C4 | 预算单位（名义 vs 保证金）分配 | 复现已修的"短线单位不匹配"场景，钉住修复后行为不回归 | ✅ 已覆盖 |
| C5 | V5 门禁链 fail-closed | 缺 `volatility_value`/`indicators_1w` 时按现状拦截；MR 模式 tight TP/SL 不被误判 placeholder | ✅ 已覆盖 |
| C6 | DB 事务边界（leak guard） | midlong/scalp tick、fee_context、decision_feedback 在连接超时后按现状重建短连接、不泄漏 | ✅ 已覆盖 |
| C7 | 仓位状态机（供 #9 对拍） | 用 C2 的 golden 轨迹回放为 `DomainEvent` 流，`EventSourcedPositionRepository.rebuild` 结果 == monolith 内存仓位 | ✅ 已覆盖 |

**推进方式**：C1–C7 全绿后，先做 #9 Phase 2（读路径走 Projection、写路径双写事件，用 C7 持续对拍），再做 #8 loop 拆分（每拆一个 loop 用 C2/C3 回归，旧文件降为 thin shim 转发，灰度切换）。任一步 golden 对拍失败即停并回滚。

**§10.9 测试网状态（2026-07-09）**：C1–C7 **全部绿灯**（**53 项**常驻回归 = 原 47 + ML 全激活接线 6 项）。**#8 Phase1 + #9 Phase3 + ML 全激活主路径已交付**。

**激进 rollout 主路径接线（2026-07-09 续）**：
- `framework_rollout.py` 默认：`ML_PIPELINE_ENABLED=true`、`LEARNED_WEIGHTING_ENABLED=true`、`DERIBIT_OPTIONS_ENABLED=true`
- `learning_loop` 维护 tick → `ml/activation_service.run_ml_activation_tick`（异步离峰）
- `factor_evaluation_pipeline` hybrid 模式融合 learned 分数
- `GET /api/health` → `ml_activation` 可观测字段

**仍未交付（文档诚实项）**：~~#8 Phase2 orchestrator~~、~~#9 写路径 DB 退役~~、~~§9.2 阶段验收脚本~~、~~shadow→canary→full 晋升门~~、~~G4 资源隔离代码强制~~。

**2026-07-09 续（全部交付）**：
- `#8 Phase2`：`full_auto/orchestrator.py` — 循环注册/注销/分发；monolith thin shim 转发
- `#9 Phase4`：`event_sourcing/phase4.py` — event-first 写 + 维护周期 DB 镜像同步（`EVENT_SOURCING_WRITE_RETIRE_DB`）
- `promotion_gate_service.py` — shadow→canary→full DSR/胜率/回撤统计门 + RuntimeGovernor 待审批 patch
- `promotion_scan_service.py` — **已接 learning_loop**：收集 ML/因子/QAA/策略候选 → 扫描晋升 → hybrid 融合权重随阶段变化
- `resource_guard.py` — G4 热路径 depth 标记 + 训练离峰 defer（scalp/unified tick 已接线）
- `scripts/quant_framework_phase_acceptance.py` — §9.2 分阶段验收（import + pytest 子集）
- `/api/health` 扩展：`full_auto_orchestrator` / `resource_guard` / `phase4_stats`

---

## 附录

### 附录 A：传统量化框架速查对照矩阵（7 个）

| 维度 | Qlib | QUANTAXIS | vnpy | NautilusTrader | Freqtrade | Jesse | Hummingbot | **本系统** |
|------|------|-----------|------|---------------|-----------|-------|-----------|----------|
| **定位** | AI 研究 | 多资产生产 | CTA 实盘 | 高性能 | 加密 bot | 加密量化 | 做市/套利 | **AI 加密全自动** |
| **语言** | Python | Python+Rust | Python | Rust+Python | Python | Python | Python | **Python** |
| **因子定义** | **表达式引擎** | add_func | TA-Lib | 策略自选 | TA-Lib | 自实现 | N/A | **Python类+受限eval** |
| **因子训练** | **18 ML模型+元学习** | 无 | 无 | 无 | hyperopt | GA | 无 | **手工regime表+GA** |
| **WFO** | **RollingGen+DDG-DA** | 无 | 无 | 无 | 无原生 | 单split | 无 | **基础(grid)** |
| **回测真实性** | 一般 | Rust快 | 一般 | **纳秒延迟** | **timeframe-detail** | 良好 | 弱 | **无子K线** |
| **周期机制** | freq参数 | 多TF原生 | BarGenerator | **BarAggregator(5种)** | new_candle | routes | Clock | **差异化分层调度** |
| **事件系统** | Workflow | RESTful | 单总线 | **MessageBus+事件溯源** | 循环 | 循环 | Clock+事件 | **QAA双轨** |
| **风控深度** | 风险模型优化 | 统计后验 | 浅 | **引擎层深度** | Protections链 | willing_to_loss | inventory_skew | **业务层极深,引擎层弱** |
| **多交易所** | N/A | 多源 | 多gateway | 多venue | ccxt | 有限 | 多 | **6交易所+ccxt** |

### 附录 A2：AI/ML/LLM 量化框架速查对照矩阵（8 个，v2 新增）

| 维度 | FreqAI | TradingAgents | FinRL | FinGPT | TensorTrade | ai-hedge-fund | WebCryptoAgent | Luo MAS | **本系统** |
|------|--------|--------------|-------|--------|-------------|---------------|----------------|---------|----------|
| **核心范式** | 监督ML+RL | LLM多智能体辩论 | DRL | 金融LLM基座 | RL组合 | LLM角色智能体 | LLM反思式 | LLM辩论 | **LLM多智能体+GA+RL脚手架** |
| **LLM** | ❌ | ✅ 两层辩论 | ❌ | ✅ 模型层 | ❌ | ✅ 18角色 | ✅ 反思 | ✅ 模态辩论 | ✅ Swing/Trend+证据链 |
| **ML/RL 管线** | **✅ 5模型族+持续重训** | ❌ | ✅ DRL | ❌ | ✅ RL组合 | ❌ | ❌ | ❌ | 🟡 RL脚手架早期+GA |
| **多周期** | 🟡 include_timeframes | ❌ 单周期 | 🟡 5m | ❌ | ❌ | ❌ | ❌ | ❌ 周度 | **✅ 5m/1h/1d** |
| **进化/学习闭环** | 🟡 持续重训 | ❌ | ❌ | ❌ | ❌ | ❌ | 🟡 反思 | ❌ | **✅ NSGA-II** |
| **加密永续** | ❌ 现货 | ❌ 现货 | 🟡 现货加密 | ❌ | ❌ 自备 | ❌ 股票 | ✅ 现货 | ✅ 现货 | **✅ 全覆盖** |
| **实盘执行** | ✅ | ❌ 仅决策 | ❌ 研究 | ❌ | ❌ | ❌ 教育 | ❌ | ❌ | **✅ 6交易所** |
| **防幻觉** | ❌ | ✅ 验证器 | ❌ | ❌ | ❌ | ❌ | ✅ 反思 | 🟡 辩论制衡 | **✅ fact_guard+证据链** |
| **记忆/RAG** | ❌ | ✅ ChromaDB | ❌ | ❌ | ❌ | ❌ | ✅ 经验回放 | 🟡 组合状态 | ✅ ChromaDB |
| **训练上下文对象** | **✅ FreqaiDataKitchen** | ❌ | 🟡 gym env | ❌ | 🟡 env | ❌ | ❌ | ❌ | ❌ |
| **前视防护** | **✅ 多层严格** | ❌ | ❌ | ❌ | ❌ 用户自理 | ❌ | ❌ | ❌ | 🟡 朴素 |

### 附录 B：V5 门禁当前精确阈值表（已核验）

| 参数 | Live | Paper | 来源(settings.py 行) |
|------|------|-------|---------------------|
| 日交易上限 | 12 | 30 | 1904/1905 |
| 单标的日交易上限 | 4 | 8 | 1906/1909 |
| 最小风险回报 | 1.8 | 1.5 | 1928 |
| 最小 RR 运行时上限 | 2.5 | 2.5 | 1760 |
| 最小 TP% | 1.2% | 0.8% | 1931 |
| Scalp 最低信心 | 70 | 65 | 1934 |
| Trend 最低信心 | 50 | 38 | 1937 |
| 信心下限 | 40 | 45 | gate 内算 |
| 高信心阈值 | 68 | 68 | 1756 |
| 高信心 RR | 2.0 | 2.0 | 1758 |
| 单笔最大风险 | 1.5% | 1.5% | 1940 |
| Trend 周开仓上限 | 6(midlong)/2 | 同 | 1490 |
| 持仓连续 tick | 1 | 1 | 1478 |
| Cycle prob 门 | 禁用 | 禁用 | 1768 |
| 运行时夹紧范围 | daily[3,20] | conf[60,90] | rr[1.5,2.5] | _runtime_overrides |

### 附录 C：因子评估当前精确阈值表（已核验）

| 指标 | A 级 | B 级 | C 级 | D 级 | F 级 |
|------|------|------|------|------|------|
| IC 绝对值 | ≥0.05 | ≥0.03 | ≥0.015 | ≥0.005 | <0.005 |
| ICIR | >0.5 | >0.3 | — | — | — |
| 冗余阈值 | |corr|>0.7 | | | | |
| IC 类型 | Spearman rank（非 Pearson） | | | | |
| 前瞻周期 | 5 bar（默认） | | | | |
| 衰减半衰期 | 1..min(20,len//5) lag | | | | |
| 最小数据量 | len ≥ forward_period + 20；≥30 对齐后；≥5 有效 IC 点 | | | | |

### 附录 D：参考资料

#### 对标框架（GitHub + 官方文档）

**Qlib（Microsoft）**
- Repo: https://github.com/microsoft/qlib
- 文档: https://qlib.readthedocs.io/
- 表达式算子源码: `qlib/data/ops.py`
- Alpha158/360: `qlib/contrib/data/handler.py` / `loader.py`
- 18 模型: `qlib/contrib/model/*.py`
- DDG-DA: `examples/benchmarks_dynamic/DDG-DA/workflow.py`
- 论文: arXiv 2009.11189

**QUANTAXIS**
- Repo: https://github.com/QUANTAXIS/QUANTAXIS
- Rust 核心: https://github.com/yutiansut/qaaccount-rs
- 文档: https://gitee.com/yutiansut/QUANTAXIS

**vnpy / VeighNa**
- Repo: https://github.com/vnpy/vnpy
- CTA 策略: https://github.com/vnpy/vnpy_ctastrategy
- 风控: https://github.com/vnpy/vnpy_riskmanager
- BarGenerator bug: https://github.com/vnpy/vnpy/issues/2365

**NautilusTrader**
- Repo: https://github.com/nautechsystems/nautilus_trader
- 文档: https://nautilustrader.io/docs/
- 消息总线: concepts/message_bus
- 事件溯源: concepts/event_sourcing
- 执行/风控: concepts/execution
- BarAggregator(Rust): https://docs.rs/nautilus-data/latest/nautilus_data/aggregation/
- nautilus-risk: https://docs.rs/nautilus-risk

**Freqtrade**
- Repo: https://github.com/freqtrade/freqtrade
- 文档: https://www.freqtrade.io/
- 策略回调: strategy-callbacks
- 回测: backtesting
- Hyperopt: hyperopt

**Jesse**
- Repo: https://github.com/jesse-ai/jesse
- 文档: https://docs.jesse.trade/

**Hummingbot**
- Repo: https://github.com/hummingbot/hummingbot
- 架构博客: hummingbot.org

#### AI/ML/LLM 量化项目（v2 新增）

**FreqAI（Freqtrade 的 ML 模块）—— ML 管线黄金标准**
- Repo: https://github.com/freqtrade/freqtrade/tree/develop/freqai
- 文档: https://www.freqtrade.io/en/latest/freqai/
- 特征工程: https://www.freqtrade.io/en/latest/freqai-feature-engineering/
- RL: https://www.freqtrade.io/en/stable/freqai-reinforcement-learning/
- 开发者文档: https://www.freqtrade.io/en/stable/freqai-developers/
- lookahead-analysis: https://www.freqtrade.io/en/stable/lookahead-analysis/

**TradingAgents —— LLM 多智能体对冲基金模拟（最直接对标）**
- Repo: https://github.com/TauricResearch/TradingAgents
- 论文: arXiv 2412.20138
- 图编排源码: `tradingagents/graph/setup.py`
- 记忆(RAG): `tradingagents/agents/utils/memory.py`（ChromaDB + OpenAI 嵌入）
- 防幻觉: `tradingagents/dataflows/market_data_validator.py`
- LLM 工厂: `tradingagents/llm_clients/factory.py`

**FinRL / FinRL-Meta —— DRL 金融框架**
- Repo: https://github.com/AI4Finance-Foundation/FinRL
- Meta: https://github.com/ai4finance-foundation/finrl-meta
- 加密环境: `finrl/meta/env_cryptocurrency_trading/env_crypto.py`
- 组合分配: https://finrl.readthedocs.io/en/latest/tutorial/Introduction/PortfolioAllocation.html
- FinRL-Meta 论文(NeurIPS 2022): DataOps 范式

**FinGPT —— 金融基础模型**
- Repo: https://github.com/ai4finance-foundation/fingpt
- 主页: https://fingpt.io/
- FinGPT-Trader(量化平台): https://github.com/ashioyajotham/fingpt_trader

**TensorTrade —— RL 组合框架**
- 原版: https://github.com/tensortrade-org/tensortrade（基本停更）
- 活跃分支: https://github.com/erhardtconsulting/tensortrade-ng
- 文档: https://tensortrade-ng.io/

**ai-hedge-fund —— LLM 角色智能体（教育）**
- Repo: https://github.com/virattt/ai-hedge-fund
- 18 投资者角色智能体，LangGraph 工作流

**WebCryptoAgent —— LLM 反思式加密交易**
- 论文: arXiv 2601.04687
- 三组件：网络信息推理 + 上下文经验回放 + 反思

**Luo MAS —— LLM 辩论加密组合（学术最接近同行）**
- 论文: arXiv 2501.00826
- 评估序列/协作/辩论三种架构，辩论胜出（夏普 2.07）

**其他 AI 交易生态**
- QuantNaut（RL on NautilusTrader）: https://github.com/ojies/quantinaut
- OctoBot: https://github.com/Drakkar-Software/OctoBot
- TradeMaster（NeurIPS 2023 RL 基准）: https://neurips.cc/virtual/2023/poster/73483
- FinRL Contest 2025（RL+LLM）: https://github.com/Open-Finance-Lab/FinRL_Contest_2025
- Awesome Applied Agents for Investment: https://github.com/Sasha-Cui/Awesome-Applied-Agents-for-Investment

#### 加密原生 alpha 源与数据平台（v2 新增）

**衍生品数据**
- CoinGlass（清算热图/OI/资金）: https://www.coinglass.com/
- Coinalyze（聚合 OI+资金+清算+基差）: https://coinalyze.net/
- The Block（含 CME 机构衍生品）: https://www.theblock.co/data/crypto-markets/futures

**链上数据**
- Glassnode（机构级 200+ 指标）: https://glassnode.com/
- Nansen AI（2.5 亿+标记钱包，智能资金）: https://nansen.ai/
- Santiment（链上+社交+开发）: https://santiment.net/

**期权数据**
- Deribit（BTC/ETH 期权主场所）
- Amberdata 期权偏度: https://blog.amberdata.io/volatility-skew-how-to-uncover-market-sentiment-shifts

**永续 DEX**
- Hyperliquid Python SDK: https://github.com/hyperliquid-dex/hyperliquid-python-sdk
- Hyperliquid API 文档: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint
- 资金费率套利示例: https://github.com/ksmit323/funding-rate-arbitrage
- Chainstack Hyperliquid 资金套利教程: https://docs.chainstack.com/docs/hyperliquid-funding-rate-arbitrage

**LLM 情绪 NLP 研究**
- 加密 Twitter 情绪分类: arXiv 2501.09777
- Attention CNN-LSTM Twitter→加密预测: Nature Sci Reports https://www.nature.com/articles/s41598-025-18245-x

#### LLM 工程化与防幻觉（v2 新增）

**LLM vs 规则实证**
- 24k 实验（执行门规则胜过 LLM）: https://www.reddit.com/r/algotrading/comments/1ssiiv9/
- Agentic Trading 综述（reactive vs reflective reasoning）: arXiv 2605.19337

**LLM 推理/成本**
- vLLM（生产标准服务）: https://vllm.ai/blog/2025-09-05-anatomy-of-vllm
- 语义缓存（40-80% 成本节省）: https://www.percona.com/blog/semantic-caching-for-llm-apps-reduce-costs-by-40-80-and-speed-up-by-250x/
- RAG 成本控制层: https://towardsdatascience.com/rag-is-burning-money-i-built-a-cost-control-layer-to-fix-it/
- 测试时计划缓存: arXiv 2506.14852

**防幻觉**
- 结构化输出 vs function calling: https://machinelearningmastery.com/structured-outputs-vs-function-calling-which-should-your-agent-use/
- 分层检索验证: arXiv 2603.17872
- Grounded memory（95%+ 幻觉减少）: https://mem0.ai/blog/reducing-hallucinations-llms-with-grounded-memory
- Promptfoo 防幻觉指南: https://www.promptfoo.dev/docs/guides/prevent-llm-hallucinations/

**强化学习（加密交易现实）**
- DRL 加密交易综述 2020-2025: https://www.cureusjournals.com/articles/12720
- RL sim-to-online 迁移: arXiv 2602.20220
- 保守目标双层 RL（"MetaTrader"）: https://openreview.net/forum?id=zaDU4vMAUr
- 奖励函数设计: https://www.mdpi.com/2227-7390/14/5/794

#### 研究级方法论（过拟合诊断）

- Bailey, Borwein, López de Prado, Zhu. "The Probability of Backtest Overfitting." Journal of Computational Finance (2014/2017). — **CSCV/PBO**
- Bailey, López de Prado. "The Sharpe Ratio Efficient Frontier." Journal of Risk (2012). — **MinBTL**
- Bailey, López de Prado. "Deflated Sharpe Ratio." Journal of Portfolio Management (2014). — **DSR**
- López de Prado. *Advances in Financial Machine Learning* (2018). Ch.11-15. — **Purged K-Fold, Embargo, CSCV**
- Quantopian Alphalens — IC 分析工具: https://github.com/quantopian/alphalens

#### 学习进化 SOTA（v3 新增）

**元学习/分布漂移**
- DDG-DA（Qlib, AAAI 2022）: arXiv 2201.04038 — 主动分布预测
- DoubleAdapt: arXiv 2306.09862 — 数据+模型元参数双适应
- MAML（Finn et al. ICML 2017）: https://github.com/cbfinn/maml

**演化策略进阶**
- CMA-ES（Hansen）: https://cmap.polytechnique.fr/~nikolaus.hansen/cmaesintro.html | Optuna CMA-ES: https://medium.com/optuna/introduction-to-cma-es-sampler-ee68194c8f88
- OpenAI ES（Salimans 2017）: arXiv 1703.03864 | https://github.com/openai/evolution-strategies-starter
- 遗传编程 GP（向量 GP 金融交易）: arXiv 2504.05418 | gplearn https://gplearn.readthedocs.io | DEAP
- MAP-Elites（Mouret & Clune 2015）: https://members.loria.fr/jbmouret/qd.html | MOME: arXiv 2202.03057
- stratevo（策略演化）: https://github.com/NeuZhou/stratevo

**持续学习/防遗忘**
- EWC（Kirkpatrick et al. PNAS 2017）: https://pnas.org/doi/10.1073/pnas.1611835114
- EVCL 2024（EWC+replay）: arXiv 2406.15972
- Uncertainty-PER（RLJ 2025）: https://rlj.cs.umass.edu/2025/papers/RLJ_RLC_2025_45.pdf
- FreqAI continual_learning: https://www.freqtrade.io/en/stable/freqai-running/

**LLM 自我改进（36/36 修复路径）**
- DSPy（Stanford NLP）: https://github.com/stanfordnlp/dspy | https://dspy.ai | arXiv 2507.03620
- Reflexion（Shinn et al. NeurIPS 2023）: arXiv 2303.11366 | https://github.com/noahshinn/reflexion
- Voyager（技能库）: arXiv 2305.16291 | https://github.com/MineDojo/Voyager
- AlphaEvolve（DeepMind 2025）: arXiv 2506.13131 | https://github.com/google-deepmind/alphaevolve_results
- OpenEvolve（开源 AlphaEvolve）: https://github.com/algorithmicsuperintelligence/openevolve
- Self-Refine: arXiv 2303.17651 | 何时 LLM 能自我纠错（TACL）: https://direct.mit.edu/tacl/article/doi/10.1162/tacl_a_00713

**记忆/RAG 进阶**
- 混合检索+重排序（+26-31% NDCG）: Atlan | Qdrant hybrid https://qdrant.tech/articles/hybrid-search/
- ANN 索引: FAISS https://github.com/facebookresearch/faiss | HNSW pgvector
- 神经嵌入: sentence-transformers https://www.sbert.net | BGE 中文 https://huggingface.co/BAAI/bge-small-zh-v1.5
- Neural Episodic Control（Pritzel PMLR 2017）

**AutoML**
- PBT（DeepMind）: https://deepmind.google/blog/population-based-training-of-neural-networks/ | Ray Tune https://docs.ray.io/en/latest/tune/examples/pbt_guide.html
- PB2（PBT+贝叶斯）: https://anyscale.com/blog/population-based-bandits
- Optuna: https://optuna.org

**过拟合（演化系统专有）**
- CPCV（López de Prado AFML Ch.11-12）| 实践代码 https://quantbeckman.com/p/with-code-combinatorial-purged-cross
- 回测过拟合综述 2024: SSRN 4686376 / Knowledge-Based Systems S0950705124011110

**Qlib online serving（生命周期管理参考）**
- https://qlib.readthedocs.io/en/v0.8.3/component/online.html（Online Manager/Strategy/Tool/Updater + DelayTrainer）

#### 本系统内部参考文档

- `README.md`（677 行，V5 决策核心、三周期 agent、进化系统权威文档）
- `docs/AI_TRADING_PIPELINE_DEEP_AUDIT_2026-07-06.md`（36/36 prompt 进化失败、FinCon 学术同行）
- `docs/learning_center_architecture.md`（11 后端注册表含 ReflexionBackend p=60/QaaBackend p=120/DriftDetectionBackend p=140）
- `docs/GAP_CLOSURE_AND_SURPASS_DESIGN_2026-07-05.md`（RuntimeGovernor 意图仲裁优先级、prompt 进化失败）
- `docs/LOCAL_LLM_SELF_TRAINING_DESIGN.md`（RuntimeGovernor 作为数值调参安全入口）
- `docs/CYCLE_DIRECTION_RESEARCH_2026-07-06.md`
- `docs/FACTOR_SYSTEM_UPGRADE.md`
- `QAA通信协议构架/QAA_V3_SYSTEM_DESIGN.md`

---

### 附录 E：本系统学习进化文件索引（v3 新增，已核验）

| 组件 | 文件 | 行数 | 成熟度 | 关键类/方法 |
|------|------|------|--------|------------|
| 学习编排门面 | `services/learning_core/orchestrator.py` | 103 | 🟢 | `LearningOrchestrator.emit/overview` |
| 血缘协议 | `services/learning_core/envelope.py` | 164 | 🟢 | `EvolutionEnvelope`（8 阶段, root/child） |
| 血缘账本 | `services/learning_core/ledger.py` | 254 | 🟢 | `LearningLedger.record/get_lineage`（SQLite+WS） |
| 回测结果摄入 | `services/learning_core/backtest_loop.py` | 163 | 🟢 | `BacktestLoop.ingest_result/_seed_replay` |
| **NSGA-II** | `services/genetic_optimizer.py` | 594 | 🟢 | `NSGAIIOptimizer.evolve_multi_objective`（非支配排序+拥挤距离） |
| 进化调度器 | `services/evolution_scheduler.py` | 1,383 | 🟢 | `weekly_evolution`（3天）+ `trigger_emergency_evolution` |
| 策略进化器 | `services/strategy_evolver.py` | 2,300 | 🟢 | `composite_fitness/persist_genetic_result/_promote_template` |
| 假设引擎 | `services/strategy_hypothesis_engine.py` | 776 | 🟢 | `run_full_cycle`（LLM生成+240bar验证） |
| **RuntimeGovernor** | `services/runtime_governor.py` | 618 | 🟢 | `submit_intent/_reconcile_keys`（多源仲裁 prio 100→30） |
| 有界调参 | `services/runtime_tuning_store.py` | 328 | 🟢 | `apply_patches`（硬夹紧走廊） |
| 决策反馈 | `services/decision_feedback_service.py` | 922 | 🟢 | `apply_gate_adjustments`（双向自校正 4 规则） |
| 冠军恢复 | `services/champion_recovery_service.py` | 71 | 🟢 | `run_champion_recovery`（6h 冷却） |
| 因子衰减 | `services/factor_engine/factor_decay_monitor.py` | 152 | 🟢 | `evaluate_factor/get_factor_weight_penalty` |
| **因果发现** | `services/causal_discovery_engine.py` | 929 | 🟢 | `discover`（Granger OLS+偏相关+LLM叙事） |
| 概念漂移 | `services/concept_drift_detector.py` | 413 | 🟢 | KS+MMD+ADWIN（7d/14d 双窗口） |
| RL 核心 | `services/learning_core/rl_core/` | ~733 | 🟡 | `LinearQPolicy`（线性Q, shadow 三重门控） |
| **prompt 进化** | `services/strategy_learning_service.py:_evolve_prompt` | — | 🔴 | **36/38 失败已禁用** |
| QAA AutoOptimizer | `qaa_architecture_package/qaa/evolution/optimizer.py` | 357 | 🟡 | `-10%` 朴素扰动 |
| QAA 6 层记忆 | `qaa_architecture_package/qaa/knowledge/base.py:23` | — | 🟡 | `MemoryTier` T0-T5（仅 T2→T3 固化） |
| **QAA RAG** | `qaa_architecture_package/qaa/knowledge/embeddings.py:31` | 165 | 🔴 | `HashEmbeddingProvider`（非神经）+ `jsonl.py:89` O(N) 线性扫描 |

---

> **文档结束**
>
> 本文档所有对本系统的断言均有 `文件:行号` 证据支撑（已在 2026-07-09 逐文件核验）。所有"业界做法"均有具体框架、类名、机制、论文 arXiv ID 引用。整改方案代码级可执行，含类/函数签名、数据结构、集成点、配置项，可直接作为后续开发任务的需求规格。
>
> **v3 版本增量**：在 v2（传统量化 + AI 量化 + 加密 alpha + LLM 工程化）基础上，新增**第六部分：学习进化系统深度对标**——八维度对标（进化算法/元学习/持续学习/LLM 自我改进/记忆 RAG/反馈闭环/过拟合诊断/因果反思）。新增 7 个不足项（P0-7 认知层学习已死、P0-8 RAG 检索质量弱、P0-9 无防遗忘、P1-10 元学习缺失、P1-11 演化仅参数级、P1-12 QAA -10% 朴素、P1-13 无 PBO 累计）、7 个代码级整改（#15 DSPy 编译器、#16 混合 RAG、#17 EWC 防遗忘、#18 DDG-DA 主动预测、#19 MAP-Elites 多样性库、#20 CMA-ES 连续精调、#21 PBO-aware 账本）。
>
> **核心结论重申**：本系统在业务门禁、AI 多智能体 + 永续聚焦、防幻觉、差异化调度上**领先**；学习进化的**数值层（NSGA-II 多目标/RuntimeGovernor 多源仲裁双向自校正/8 阶段血缘账本/Granger 因果发现）领先**多数开源框架；但在**认知层学习（prompt 进化 36/38 失败已死）**、**RAG 检索质量（hash 嵌入+线性扫描）**、**防遗忘（无 EWC）**、**元学习（被动 vs DDG-DA 主动）**、**演化深度（参数级 vs GP 表达式级）**、**回测方法论/ML 管线工程化**上**落后**研究级 SOTA。
>
> **最高价值修复**：v3 的认知层复活三件套（#15 DSPy + #16 混合 RAG + #17 EWC）共同决定系统能否真正"从经验中学习"——这是"全自动 AI 交易系统"的核心能力。建议阶段 2.5 优先完成这三项，再推进演化进阶（阶段 3.5：DDG-DA + MAP-Elites + CMA-ES + PBO-aware）。

