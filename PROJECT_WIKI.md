# Heidalv-Alpha-Arena — AI驱动加密货币短线交易系统 项目Wiki

> 生成日期: 2026-07-16 | 版本: v1.0

---

## 一、项目概述

| 项 | 内容 |
|----|------|
| **主项目路径** | `d:/BaiduNetdiskDownload/001Alpha/001Alpha/Hyper-Alpha-Arena/` |
| **支撑框架** | `d:/BaiduNetdiskDownload/001Alpha/QAA通信协议构架/` (qaa v3.1.0, pip install -e) |
| **定位** | AI驱动的加密货币永续合约短线因子策略交易系统 |
| **规模** | ~2633个Python文件, 74个API路由, 309个service模块, 981个AI生成因子, 45个前端组件 |
| **后端** | Python FastAPI (port 8000) |
| **前端** | Next.js 16.2.10 (App Router) + React 19 + TypeScript (port 5273) |
| **数据库** | SQLite为默认(alpha_arena/market/analytics/snapshots 4库), 支持PostgreSQL |
| **LLM** | DeepSeek v4-flash/v4-flash |

---

## 二、启动流程

### 入口点
```
launcher.py (Tkinter GUI管理器) → python launcher.py
   或
python backend/main.py → uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

### main.py 启动顺序
1. **环境准备**: 加载.env → `framework_rollout.apply_aggressive_rollout()` (注入25+默认开关)
2. **环境校验**: `env_registry.validate_strict()` (严格校验环境变量)
3. **日志初始化**: `_bootstrap_logging()` → 双输出(RotatingFileHandler → logs/backend.log)
4. **生产安全门**: `ENVIRONMENT=production` 时强制要求 `BACKEND_API_KEY`
5. **FastAPI创建**: CORS + 3个中间件(Auth/APIKey → RateLimit → Trace)
6. **`on_startup`**:
   - anyio线程池扩容(40→100)
   - 4数据库建表 + 列补齐 + 索引创建
   - 前台: 同步服务初始化(StrategyManager/SystemCoordinator/ArbitragePlugin)
   - 后台: K线采集器 → 全历史回填 → EventBus → AutoCoinScheduler → KlineQualityRepair
7. **挂载50+路由** + WebSocket + SPA catch-all
8. **`on_shutdown`**: 停止服务/EventBus/DingTalk, Windows下硬退出防孤儿

---

## 三、4数据库架构

| 库 | 环境变量 | 默认SQLite路径 | 用途 |
|----|---------|---------------|------|
| **Core** | `DATABASE_URL` | `data/alpha_arena.db` | 交易核心(账户/订单/策略/仓位) |
| **Market** | `MARKET_DATABASE_URL` | `data/alpha_market.db` (~12MB) | 市场数据(K线/CVD/资金费率) |
| **Analytics** | `ANALYTICS_DATABASE_URL` | `data/alpha_analytics.db` | AI决策日志/风控事件/LLM用量 |
| **Snapshots** | `SNAPSHOT_DATABASE_URL` | `data/alpha_snapshots.db` | HL账户快照/历史记录 |

SQLite特性: WAL模式 + 单线程WriteQueue串行化commit + busy_timeout=120s
PG特性: 连接池20+20, pool_recycle=300s, idle_in_transaction_session_timeout=0, LeakGuard僵尸事务巡检

---

## 四、8层目标架构(升级方案L1-L7)

```
L7 监督层: BullBearDebateAgent(DEEP) + OversightAgent(DEEP) — 慢速,异步触发
L6 进化层: FactorEvolutionAgent + DriftWatcher(ADWIN) + MetaLearner(MAML) + ShadowJudge + CodegenAgent
L5 执行层: ExecutionAgent → AlgoRouter(TWAP/POV/FundingIS/SOR) → venue
L4 组合风控: PortfolioConstruction(Kelly/风险预算) + RiskAgent(fail-closed) + RegimeAgent(≥6态)
L3 Alpha层: UniverseAgent → FactorCompute(≤50因子) → AlphaEnsemble(River+LGBM+SAC+RecurrentPPO) → MetaLabel
L2 数据层: MarketDataAgent(自建L2重建) + OnChainDataAgent + QualityGateAgent + DerivativesData
L1 编排设施: HotRingBus(热路径) + EventBus(监督路径) + Cache(单一真相源) + QAA Workflow
L0 存储连接: PostgreSQL + ChromaDB(RAG) + Redis(热状态) + CCXT/HL SDK
```

### 8条不可逾越红线(R1-R8)
- **R1** 热路径零LLM(tick→信号→风控→下单全程无同步LLM)
- **R2** 回测/实盘同核(策略代码不感知引擎,只换ExecutionClient)
- **R3** 双轨常驻(paper+live永远同时跑,偏差作熔断源)
- **R4** 客观指标自动晋升/回滚(DSR/PBO/ICIR/容量触发)
- **R5** 风控fail-closed(超时/缺口/偏差→拒绝或冻结)
- **R6** 单一真相源(Cache事件溯源,只通过事件更新)
- **R7** 契约即边界(Lean5层dataclass强制跨层签名)
- **R8** 全量审计可回放(事件全落盘,任意一秒可复现)

---

## 五、当前9个AgentCard

| Agent | LLM Level | 职责 | 超时策略 |
|-------|-----------|------|---------|
| `market_data` | NONE | 获取统一数据快照 | cached |
| `factor_engine` | QUICK | V3因子计算+异常检测 | cached |
| `intel_signal` | QUICK | 8源加权汇流( funding/OI/whale/news) | skip |
| `risk_control` | NONE | 5硬规则+方向翻转检测(fail-closed) | hold |
| `mt_orchestrator` | QUICK | 长/中/短三周期独立分析+协调 | skip |
| `master_controller` | **DEEP** | 六路分析师+DebateLayer+LLM最终决策 | hold |
| `trade_execution` | NONE | 下单/平仓(幂等) | retry |
| `signal_bus` | NONE | 四源信号聚合(因子/情报/确认/融合) | cached |
| `genetic_optimizer` | NONE | 离线GA优化策略参数 | skip |

> ⚠ **P0问题**: `master_controller`是DEEP LLM在热路径上, 60s预算, 短线tick不容忍此延迟

---

## 六、交易三周期(Tier)体系

### 参数对照

| 参数 | short(短线/scalp) | mid(中线/swing) | long(长线/trend) |
|------|-------------------|-----------------|-------------------|
| K线周期 | 5m, 15m | 1h, 4h | 4h, 1d |
| SL范围 | 1.2-2.5% (ATR自适应) | 3.5-4.5% | 8-16.5% (波动带分层) |
| TP范围 | 1.8-5% (≥2:1 RR) | 7-9% | 分批TP(8%/15%/25%)+Trailing |
| 最大持仓 | 12h | 48h | 7d |
| 资金分配 | 15% | 35% | 40% |
| 杠杆 | 动态5-20x | 动态5-20x | 动态5-20x(偏保守) |
| 冷却时间 | 4h(严格模式600s+) | 30min | 4h |
| 日开仓上限 | — | — | Paper=30 / Live=12 |

### V5经济学门控(decision_core)
- `V5_MIN_RISK_REWARD=1.8` — 最低盈亏比
- `V5_MAX_TRADE_RISK_PCT=1.5%` — 单笔最大风险占权益
- `V5_DAILY_TRADE_CAP_ENABLED=true` — 日开仓上限
- `V5_SCALP_MIN_CONFIDENCE=70` — scalp最低置信度
- `ORCHESTRATOR_HARD_GATE` — Live强制拦截编排器否决

### 短线因子体系
- 活跃因子上限: 40 (`SCALP_ACTIVE_FACTOR_MAX`)
- 开仓分数门槛: 42分 (`SCALP_FACTOR_CONFIRM_THRESHOLD`, 严格模式)
- EV期望值闸门: 默认开启 (`SCALP_EV_GATE_ENABLED`)
- 置信度校准: PAVA保序回归 (`SCALP_CALIBRATOR_ENABLED`)
- 多周期一致性: 强制 (`SCALP_MTF_ENFORCE_ENABLED`)
- 短线可逆势(缩仓): `SCALP_ALLOW_COUNTER_TREND=true`
- 震荡均值回归模式: `SCALP_RANGING_MR_ENABLED=true`
- 因子类别去重: 单类最多40%权重 (`FACTOR_CATEGORY_DEDUP_ENABLED`)

---

## 七、核心配置速查 (config/settings.py 2078行)

### LLM配置
| 参数 | 值 | 说明 |
|------|-----|------|
| QUICK超时 | 90s | 非流式快速模型 |
| DEEP超时 | 240s | 深度推理模型 |
| 流式安全上限 | 120s | 防挂死 |
| 全局并发上限 | 0 (不限) | DeepSeek API限制远高于本系统用量 |
| K线LLM模式 | rotate | 每轮分析一批,其余用缓存 |

### 风控分层
| 层 | 开关 | 说明 |
|----|------|------|
| Stage E | `RISK_STAGE_E_ENABLED=true` | 波动带分层TP/SL/ATR/杠杆 |
| Stage P2 | `RISK_P2_ENABLED=true` | 动态杠杆+V2 TP/SL+long免疫+分批TP |
| Stage P3 | `RISK_P3_ENABLED=true` | master close/reduce硬事实门控 |
| 旧路径回滚 | `LEGACY_RISK_HARD_ROLLBACK` | env或data/stage_f_rollback.flag文件 |

### 激进Rollout(framework_rollout.py) — 默认开启项
因子混合加权、ML持续重训、神经嵌入/ChromaDB RAG、重排序、风控引擎、对抗辩论、语义缓存、MAP-Elites多样性、CMA-ES优化、PBO审计、EWC防遗忘、DDGDA分布预测、DSPy编译、事件溯源双写+Phase3投影、晋升门、资源隔离、Deribit期权源、LLM数值校验

### 模拟盘特殊配置
- `PAPER_FAST_TRIAL=true` — 快速试单(更高频率/放宽门控/加速学习)
- `PAPER_DISABLE_LOSS_LOCKS=true` — 不因亏损进入防守冻结
- `PAPER_NETTING_MODE=true` — 净额保证金(匹配Hyperliquid真实行为)
- `PAPER_ONE_WAY_REVERSE_NETTING=true` — 单向反手净额抵消

---

## 八、API路由分类(50+)

### 核心交易
`order_routes` / `account_routes` / `ai_trading_routes` / `full_auto_routes` / `paper_trading_routes` / `unified_account_routes`

### 市场数据
`market_data_routes` / `market_data_v2_routes` / `kline_routes` / `market_flow_routes` / `market_regime_routes` / `market_intelligence_routes` / `signal_routes` / `smart_signal_routes`

### 交易所
`hyperliquid_routes` / `hyperliquid_action_routes` / `exchange_routes` / `binance_routes`

### AI/学习/进化
`ai_strategy_routes` / `rl_routes` / `evolution_routes` / `learning_core_routes` / `intelligent_learning_routes` / `learning_loop_routes` / `prompt_training_routes` / `training_phase_routes`

### 套利
`arbitrage_routes` / `arbitrage_profile_routes` / `arbitrage_paper_routes` / `rebate_routes`

### 策略/回测
`strategy_config_routes` / `strategy_template_routes` / `strategy_prompt_routes` / `scalp_config_routes` / `backtest_routes` / `atas_routes` / `atas_v2_routes` / `visual_strategy_routes`

### 风控/监控
`risk_routes` / `system_monitor_routes` / `system_health_routes` / `system_control_routes` / `system_log_routes`

### RAG/QAA/其他
`rag_routes` / `qaa_routes` / `opencode_routes` / `hermes_routes` / `llm_config_routes` / `llm_usage_routes` / `auto_coin_routes` / `dashboard_routes` / `analytics_routes` / `ws.py`(WebSocket)

---

## 九、后端核心目录结构

```
backend/
├── main.py                        # FastAPI主应用(1616行)
├── config/
│   ├── settings.py                # 全局配置(2078行,200+环境变量)
│   └── framework_rollout.py       # 激进默认注入(25+项)
├── database/
│   ├── connection.py              # 4库引擎+WriteQueue+LeakGuard
│   └── models.py                  # ORM模型(3623行)
├── api/                           # 74个路由文件
├── services/
│   ├── full_auto_trading_service.py  # 全自动主循环(4316行)
│   ├── full_auto/                    # QAA V3 tick循环+master_execution
│   ├── qaa/cards.py                  # 9张AgentCard
│   ├── decision_core/                # V5门控:unified_gate+regime_agent+monte_carlo
│   ├── risk_management/              # 5硬规则fail-closed风控
│   ├── factor_engine/                # 因子注册/选择/加权/IC评估/衰减监控
│   ├── agents/                       # 交易分析师(K线/多头/空头/波段)
│   ├── ai/                           # AI决策服务+因子发现
│   ├── execution/                    # live_executor+hyperliquid_trading_client
│   ├── exchange/                     # 6交易所adapter
│   ├── backtest_engine/              # 向量化+事件驱动双模
│   ├── evolution/                    # 自进化(feedback/optimizer/learning_loop)
│   ├── learning_core/                # LearningOrchestrator+cmaes+map_elites
│   ├── mlto/                         # 中长线thesis编排(OWM在线权重)
│   ├── arbitrage/                    # 25模块( basis/funding/扫描/对冲)
│   ├── rebate_arb/                   # 45模块+10策略
│   ├── signal_engine/                # signal_bus四源融合
│   └── event_sourcing/               # 事件溯源Phase1-4
├── middleware/                    # auth, rate_limit, trace
├── schemas/                       # account, order, position等Pydantic模型
├── repositories/                  # account/order/position/strategy repo
└── utils/                         # api_version, encryption, monitoring等
```

---

## 十、新前端架构 (frontend-next/)

> 注: 旧 `frontend/` (Vite+React) 已废弃，当前使用 `frontend-next/`

### 技术栈
| 类别 | 技术 |
|------|------|
| 框架 | **Next.js 16.2.10** (App Router) |
| UI | **React 19** + **TypeScript 5** |
| 样式 | **Tailwind CSS v4** + `tw-animate-css` |
| 组件库 | **shadcn/ui** (button/card/input/tabs/badge/switch/tooltip/scroll-area/separator/label) |
| 状态管理 | **zustand 5** (market/trading/ui stores) |
| 数据获取 | **@tanstack/react-query 5** |
| 图表 | **lightweight-charts** + **recharts 3.9** |
| 图标 | **lucide-react** |
| 启动命令 | `next dev -p 5273` |

### 目录结构
```
frontend-next/
├── next.config.ts                 # Next.js配置(API代理→localhost:8000)
├── package.json                   # "dev": "next dev -p 5273"
├── tsconfig.json
├── components.json                # shadcn/ui配置
├── postcss.config.mjs             # Tailwind CSS v4
├── src/
│   ├── app/                       # Next.js App Router 页面(18个路由)
│   │   ├── layout.tsx             # 根布局
│   │   ├── page.tsx               # 首页
│   │   ├── globals.css            # 全局样式
│   │   ├── dashboard/             # 仪表盘
│   │   ├── scalp/                 # 短线(剥头皮)
│   │   ├── mid/                   # 中线
│   │   ├── long/                  # 长线
│   │   ├── strategy/              # 策略
│   │   ├── factors/               # 因子
│   │   ├── risk/                  # 风控
│   │   ├── evolution/             # 进化系统
│   │   ├── arbitrage/             # 套利
│   │   ├── exchange/              # 交易所
│   │   ├── hyperliquid/           # HL专用
│   │   ├── paper-trading/         # 模拟交易
│   │   ├── charts/                # 图表
│   │   ├── agent-monitor/         # AI Agent监控
│   │   ├── intel/                 # 情报
│   │   ├── prompts/               # 提示词
│   │   ├── logs/                  # 日志
│   │   └── settings/              # 设置
│   ├── components/
│   │   ├── ui/                    # shadcn/ui基础组件(10个)
│   │   ├── layout/                # AppShell, CommandPalette, Sidebar, TopBar
│   │   ├── trading/               # OrderForm, PriceTicker, SessionManager
│   │   ├── charts/                # EquityCurve
│   │   ├── config/                # DailyCapDashboard
│   │   └── providers/             # QueryProvider
│   ├── hooks/
│   │   ├── useTradingData.ts
│   │   └── useWebSocket.ts
│   └── lib/
│       ├── api.ts                 # API客户端(15.6KB)
│       ├── ws.ts                  # WebSocket客户端
│       ├── utils.ts               # 工具函数
│       └── stores/                # zustand状态
│           ├── market.ts
│           ├── trading.ts
│           └── ui.ts
└── public/                        # 静态资源
```

---

## 十一、数据库文件 (data/)

| 文件 | 大小 | 说明 |
|------|------|------|
| alpha_arena.db | ~2.3MB | 主交易库(账户/订单/策略/仓位/AI决策) |
| alpha_market.db | ~12MB | 市场数据(K线/CVD/资金费率) |
| alpha_analytics.db | ~884KB | 分析库(LLM用量/风控事件) |
| alpha_snapshots.db | 28KB | HL快照 |
| hermes_evolution.db | ~5MB | Hermes进化数据 |
| learning_core.db | 28KB | 学习核心 |
| rl_replay.db | 68KB | RL回放 |
| ai_feedback/ | 72文件 | AI反馈样本 |
| opencode_reports/ | 1307文件 | OpenCode提案/报告 |
| strategy_runtime_reports/ | 1493文件 | 策略运行时报告 |
| training_reports/ | 615文件 | 训练报告 |

---

## 十二、关键文件速查

| 用途 | 文件路径 |
|------|---------|
| 启动器 | `launcher.py` |
| 主应用 | `backend/main.py` (1616行) |
| 全局配置 | `backend/config/settings.py` (2078行) |
| 激进Rollout | `backend/config/framework_rollout.py` |
| 数据库连接 | `backend/database/connection.py` (4库) |
| ORM模型 | `backend/database/models.py` (3623行) |
| QAA Cards | `backend/services/qaa/cards.py` (9 AgentCard) |
| 全自动服务 | `backend/services/full_auto_trading_service.py` (4316行) |
| QAA V3 Tick | `backend/services/full_auto/qaa_v3_tick_cycle.py` |
| V5决策门控 | `backend/services/decision_core/unified_gate.py` |
| 风控 | `backend/services/risk_management/` |
| 因子引擎 | `backend/services/factor_engine/` |
| 学习协调器 | `backend/services/learning_core/orchestrator.py` |
| 演进调度 | `backend/services/evolution/learning_loop.py` |
| 实盘执行 | `backend/services/execution/live_executor.py` |
| HL客户端 | `backend/services/exchange/hyperliquid_trading_client.py` |
| 事件溯源 | `backend/services/event_sourcing/` |
| 新前端入口 | `frontend-next/src/app/layout.tsx` (Next.js 16 App Router) |
| 新前端首页 | `frontend-next/src/app/page.tsx` |
| 前端API | `frontend-next/src/lib/api.ts` (15.6KB) |
| WebSocket | `frontend-next/src/lib/ws.ts` |
| 状态管理 | `frontend-next/src/lib/stores/` (zustand: market/trading/ui) |

---

## 十三、当前P0问题与升级方案

### 三大P0问题
1. **因子动物园失控**: 981个AI生成因子 → 需正交化+DSR/PBO硬门砍至≤50
2. **QAA框架"两张皮"**: 独立qaa包仅content demo域, 交易域in-tree副本
3. **热路径LLM同步调用**: master_controller(DEEP)在主决策链, 秒级延迟不可接受

### 升级5阶段14周
| 阶段 | 名称 | 核心交付 |
|------|------|---------|
| P0 | 地基治理(W1-2) | Git化、目录清理、依赖锁定、配置中心化、CI |
| P1 | 因子纪律(W2-6) | DSL表达式引擎、981→≤50、生命周期状态机、Triple-Barrier+Meta-Label |
| P2 | 热路径重构(W2-8) | Lean5层契约、QAA统一、去LLM、HotRingBus、Cache |
| P3 | 安全执行(W5-10) | ExecutionClient双轨、TWAP/POV/IS/SOR、parity、熔断 |
| P4 | 自我进化(W8-13) | pool-aware挖掘、DriftWatcher闭环、MAML、ShadowJudge |
| P5 | 中长线协同(W10-14) | Alpha Bus、HedgeLedger、统一组合、跨horizon熔断 |

---

## 十四、交易所与品种

| 类型 | 交易所 | 接口 |
|------|--------|------|
| CEX | Binance, Bybit, OKX, GateIO | CCXT统一adapter |
| DEX | Hyperliquid | 原生EIP-712签名(SDK) |
| DEX | AsterDEX | CCXT |
| 计划 | dYdX, Drift, GMX | +Flashbots MEV防护 |

交易品种: BTC/ETH/SOL/BNB/ASTER/XPL/VIRTUAL等(自动选币动态管理)

---

## 十五、设计文档索引

| 文档 | 说明 |
|------|------|
| `../短线交易系统_全量升级设计与执行方案.md` | 47项交付物, 5阶段14周, 8条红线 |
| `../QAA短线策略系统_深度复盘与技术规划报告.md` | 1054行, 30+世界级体系对标 |
| `COMPREHENSIVE_TEST_REPORT_2026-04-10.md` | 全面功能测试报告 |
| `迁移指令.txt` | Mac→Windows迁移指南 |
| `CODE_WIKI.md` | 代码层面Wiki |
| `QUICK_START.md` / `DEV-README.md` | 开发快速入门 |

---

*Wiki完 — 基于对main.py(1616行)、settings.py(2078行)、cards.py(208行)、connection.py(497行)、framework_rollout.py(81行)、launcher.py(1114行)、以及两份设计方案的全面阅读整理*
