# Hyper-Alpha-Arena 全面项目审查与功能测试报告

**报告日期**：2026-04-10
**版本**：v0.7.0
**测试性质**：代码静态审查 + 架构分析 + 数据链路追踪（只读，未修改任何文件）
**测试环境**：Windows 11，Python 3.12，Node.js，SQLite 本地开发

---

## 一、项目概览

### 1.1 项目定位

Hyper-Alpha-Arena 是一个**加密货币永续合约 AI 量化交易平台**，核心能力包括：

- AI 驱动的交易决策（LLM 大模型 + 规则引擎）
- 多交易所支持（Hyperliquid 为主，Binance 已移除）
- 多周期策略系统（short/mid/long tier）
- 模拟交易（Paper Trading）与实盘交易
- 智能信号系统 + 市场体制识别
- 回测引擎 + 策略进化
- RAG 知识库 + 交易智慧

### 1.2 技术栈

| 层 | 技术 |
|----|------|
| 后端 | FastAPI + Uvicorn + SQLAlchemy + Alembic |
| 前端 | React 18 + TypeScript + Vite + TailwindCSS + Radix UI |
| 图表 | Chart.js + Recharts + Lightweight Charts（TradingView） |
| 数据库 | SQLite（开发）/ PostgreSQL（Docker 生产） |
| AI/LLM | OpenAI SDK + Anthropic SDK |
| 实时通信 | WebSocket（自建，非 Socket.IO） |
| 桌面 | 简易 Launcher（Python + WebView） |
| 部署 | Docker Compose（PostgreSQL + App） |

### 1.3 项目规模

| 指标 | 数量 |
|------|------|
| 后端 Python 服务模块 | 100+ |
| API 路由模块 | 40+ |
| 数据库模型（表） | 67+ |
| 前端组件目录 | 31 个 |
| 前端页面 | 20+ |
| Context Providers | 6 个 |
| 文档/报告 MD 文件 | 100+ |

---

## 二、架构审查

### 2.1 后端架构（[backend/](Hyper-Alpha-Arena/backend/)）

#### 入口与启动链路

```
backend/main.py → FastAPI app
  ├── on_startup:
  │   ├── 前端文件监视器（自动构建）
  │   ├── Base.metadata.create_all（建表）
  │   ├── Paper 交易 schema 确保器
  │   ├── 后台延迟初始化线程（services/startup.py + startup_deferred.py）
  │   └── K 线采集器独立线程
  ├── 路由注册（40+ routers）
  ├── WebSocket 端点 /ws
  ├── SPA 前端服务（static 目录）
  └── GZip + CORS 中间件
```

#### API 路由清单

| 路由模块 | 路径前缀 | 功能 |
|----------|----------|------|
| market_data_routes | /api/market-data | 行情数据 |
| order_routes | /api/orders | 订单管理 |
| account_routes | /api/accounts | 账户管理 |
| config_routes | /api/config | 交易配置 |
| ranking_routes | /api/ranking | 排名/因子 |
| crypto_routes | /api/crypto | 加密货币价格 |
| arena_routes | /api/arena | 竞技场模式 |
| system_log_routes | /api/system-logs | 系统日志 |
| system_control_routes | /api/system | 系统控制 |
| prompt_routes | /api/prompts | 提示词管理 |
| sampling_routes | /api/sampling | 采样池 |
| ai_trading_routes | /api/ai-trading | AI 交易决策 |
| hyperliquid_action_routes | /api/hyperliquid/actions | Hyperliquid 交易所操作 |
| hyperliquid_routes | /api/hyperliquid | Hyperliquid 数据 |
| user_routes | /api/users | 用户管理 |
| kline_routes | /api/klines | K 线数据 |
| kline_analysis_routes | /api/klines/analysis | K 线分析 |
| market_flow_routes | /api/market-flow | 资金流向 |
| signal_routes | /api/signals | 信号系统 |
| market_regime_routes | /api/market-regime | 市场体制 |
| analytics_routes | /api/analytics | 数据分析 |
| dingtalk_routes | /api/dingtalk | 钉钉通知 |
| smart_signal_routes | /api/smart-signals | 智能信号 |
| ai_signal_prompt_routes | /api/ai-signals | AI 信号生成 |
| llm_config_routes | /api/llm-configs | LLM 配置库 |
| ai_strategy_routes | /api/ai-strategies | AI 策略 |
| prompt_training_routes | /api/prompt-training | 提示词训练 |
| paper_trading_routes | /api/paper-trading | 模拟交易 |
| strategy_template_routes | /api/strategy-templates | 策略模板 |
| full_auto_routes | /api/full-auto | 全自动交易 |
| notification_routes | /api/notifications | 通知管理 |
| backtest_routes | /api/backtest | 回测引擎 |
| intelligence_routes | /api/intelligence | 情报系统 |
| risk_routes | /api/risk | 风控监控 |
| llm_usage_routes | /api/llm-usage | LLM 用量统计 |
| rag_routes | /api/rag | RAG 知识库 |
| atas_routes | /api/atas | ATAS（旧版） |
| atas_v2_routes | /api/atas/v2 | ATAS V2 策略中心 |

#### 核心服务架构

```
services/
├── 交易引擎
│   ├── full_auto_trading_service.py     — 全自动交易会话
│   ├── paper_trading_engine.py          — 模拟交易引擎
│   ├── order_executor.py                — 订单执行器
│   ├── order_monitor.py                 — 订单监控
│   ├── order_scheduler.py               — 订单调度
│   └── trading_strategy.py              — 交易策略基础
│
├── AI 决策
│   ├── ai_decision_service.py           — AI 决策服务
│   ├── ai_strategy_engine.py            — ⚠ 已废弃（Phase 2）
│   ├── ai_prompt_generation_service.py  — 提示词生成
│   └── strategy_coordinator.py          — 策略协调器
│
├── 多周期编排
│   ├── multi_timeframe_orchestrator.py  — 多周期编排
│   ├── strategy_orchestrator/           — 策略编排器（含 long_term_planner）
│   └── adaptive_executor/               — 自适应执行器
│
├── Hyperliquid 交易所
│   ├── hyperliquid_trading_client.py    — 交易客户端
│   ├── hyperliquid_market_data.py       — 行情数据
│   ├── hyperliquid_snapshot_service.py  — 快照服务
│   └── hyperliquid_symbol_service.py    — 交易对服务
│
├── 市场分析
│   ├── market_regime_detector.py        — 市场体制检测
│   ├── market_regime_service.py         — 市场体制服务
│   ├── market_flow_collector.py         — 资金流采集
│   ├── sentiment_composite_service.py   — 情绪综合分析
│   ├── news_intelligence_service.py     — 新闻情报
│   └── whale_tracker_service.py         — 鲸鱼追踪
│
├── 信号系统
│   ├── signal_detection_service.py      — 信号检测
│   ├── signal_confirmation_engine.py    — 信号确认
│   ├── signal_analysis_service.py       — 信号分析
│   ├── smart_signal_generator.py        — 智能信号生成
│   └── intelligence_signal_engine.py    — 情报信号引擎
│
├── 回测与进化
│   ├── backtest_engine/                 — 回测引擎
│   ├── backtest_evolution_engine.py     — 进化引擎
│   ├── evolution_scheduler.py           — 进化调度
│   ├── genetic_optimizer.py             — 遗传优化器
│   └── strategy_evolver.py              — 策略进化器
│
├── 风控
│   ├── risk_control_service.py          — 风控服务
│   ├── risk_management/                 — 风控管理
│   ├── deterministic_risk_gate.py       — 确定性风控门
│   ├── fee_guard.py                     — 手续费保护
│   ├── profit_protection_manager.py     — 利润保护
│   └── position_sizer.py               — 仓位计算
│
├── 仓位管理
│   ├── position_tracker_service.py      — 仓位追踪
│   ├── position_memory_manager.py       — 仓位记忆
│   ├── sub_position_manager.py          — 子仓位管理
│   └── reentry_cooldown.py              — 重入冷却
│
├── 学习与知识
│   ├── unified_learning_service.py      — 统一学习服务
│   ├── rag_knowledge_service.py         — RAG 知识库
│   ├── experience_retriever.py          — 经验检索
│   ├── wisdom_tracker.py               — 智慧追踪
│   └── learning_feedback_layer/         — 学习反馈层
│
├── 钉钉通知
│   └── dingtalk/                        — 钉钉机器人推送
│
└── ATAS V2
    ├── atas_v2_executor.py              — ATAS V2 执行器
    ├── atas_api/                        — ATAS API
    ├── atas_core/                       — ATAS 核心
    └── atas_integration/               — ATAS 集成
```

#### 数据库模型审查

共 **67+ 张表**，核心关系如下：

```
User → Account → {Order, Trade, Position, AIDecisionLog, ...}
Account → LLMConfiguration（多对一）
Account → AccountPromptBinding → PromptTemplate
Account → TraderPersonality（一对一）
Account → PaperBalance（一对一）
Account → HyperliquidWallet（一对多）
Account → RiskControlConfig（一对一）
Account → TraderMentalState（一对一）

AIStrategy → {StrategyAnalysisLog, StrategyOptimizationLog, StrategyMemory, StrategyTrade}
SignalPool → SignalDefinition → SignalTriggerLog
BacktestRun → BacktestTrade

ATAS V2: ATASStrategy, ATASFactor, ATASFactorCache, ATASPromptTemplate, ATASAIGenerationHistory
```

### 2.2 前端架构（[frontend/](Hyper-Alpha-Arena/frontend/)）

#### 技术选型

- **框架**：React 18 + TypeScript
- **构建**：Vite 4
- **UI 库**：Radix UI + TailwindCSS + class-variance-authority
- **图表**：Chart.js + Recharts + Lightweight Charts（TradingView K 线）
- **状态管理**：React Context（6 个 Provider） + useState/useRef
- **路由**：Hash 路由（`window.location.hash`），无 react-router
- **国际化**：i18next + react-i18next
- **可视化策略设计**：@xyflow/react（React Flow）

#### Context Providers

```
ThemeProvider → AuthProvider → ExchangeProvider → TradingModeProvider → ArenaDataProvider → BacktestProvider
```

#### 页面与组件

| 页面 Key | 组件 | 功能 |
|----------|------|------|
| comprehensive | UnifiedDashboardView | 统一仪表盘（首页） |
| modern-dashboard | ModernTradingDashboard | 现代交易面板 |
| strategy | StrategyPage | 策略管理 |
| risk | RiskPage | 风控监控 |
| settings | SettingsPage | 系统设置 |
| atas-console | TradingConsole | ATAS 控制台 |
| atas-v2 | ATASV2Page | AI 决策中心 |
| modern-signals | ModernSignalManager | 信号系统 |
| smart-signal-generator | SmartSignalGenerator | AI 信号生成器 |
| system-logs | SystemLogs | 系统日志 |
| prompt-management | PromptManager | 提示词管理 |
| signal-management | ModernSignalManager | 信号管理 |
| attribution | AttributionAnalysis | 归因分析 |
| analytics | AnalyticsPage | 数据分析 |
| trader-management | TraderManagement | AI 交易员管理 |
| hyperliquid | UnifiedTradingPage | Hyperliquid 交易 |
| klines | KlinesView | K 线图表 |
| data-center | DataCenterView | 数据中心 |
| user-guide | UserGuide | 使用指南 |
| binance | (已移除) | Binance 已下线 |

#### 实时通信

- WebSocket 连接 `/ws`，单例模式防止重复连接
- 自动重连机制（3 秒延迟）
- 消息类型：bootstrap → get_snapshot → 各种实时更新
- Keep-Alive 页面缓存机制（已访问的页面不销毁，用 display:none 隐藏）

### 2.3 桌面应用（[desktop/](Hyper-Alpha-Arena/desktop/)）

- 简易 Launcher：`launcher.py` + `splash.html`
- 负责：启动后端服务 + 打开浏览器窗口
- 无 Electron/Tauri 包装，使用 Python WebView

---

## 三、数据链路分析

### 3.1 交易决策完整链路

```
[触发器] → 信号检测/定时调度/手动触发
    ↓
[市场数据采集] → market_data.py / hyperliquid_market_data.py / kline_collectors.py
    ↓
[多周期编排] → multi_timeframe_orchestrator.py
    ↓           ├── short tier (5min-15min)
    │           ├── mid tier (1h-4h)
    │           └── long tier (1d+)
    ↓
[体制识别] → market_regime_detector.py → breakout/absorption/trap/exhaustion/noise
    ↓
[信号确认] → signal_confirmation_engine.py → 维度评分 + 确认级别
    ↓
[AI 决策] → ai_decision_service.py
    │         ├── 提示词组装 (ai_prompt_generation_service.py)
    │         ├── LLM 调用 (OpenAI/Anthropic SDK)
    │         ├── 角色注入 (TraderPersonality)
    │         └── 智慧注入 (TradingWisdom + RAG)
    ↓
[风控门] → deterministic_risk_gate.py
    │        ├── 单仓位限制
    │        ├── 日亏损熔断
    │        ├── 总仓位限制
    │        └── 保证金使用率
    ↓
[仓位计算] → position_sizer.py + position_memory_manager.py
    ↓
[订单执行] → order_executor.py → hyperliquid_trading_client.py
    ↓
[仓位追踪] → position_tracker_service.py
    ↓
[利润保护] → profit_protection_manager.py → TP/SL/Trailing Stop
    ↓
[学习反馈] → unified_learning_service.py → 交易智慧 + 经验库
```

### 3.2 WebSocket 实时数据流

```
前端 → WS connect (/ws)
     → bootstrap {username, initial_capital, trading_mode}
     ← bootstrap_ok {user, account}
     → get_snapshot {trading_mode}
     ← snapshot {overview, positions, orders, trades, ai_decisions, asset_curves}
     ← trade_update {trade}  （实时）
     ← position_update {positions}  （实时）
     ← order_filled / order_pending  （实时）
     ← asset_curve_update {data}  （实时）
```

### 3.3 回测与进化链路

```
策略模板 → backtest_engine（历史数据回放）
         → backtest_evolution_engine（遗传优化）
         → strategy_evolver（参数变异）
         → wisdom_tracker（提取交易智慧）
         → 注入 AI 决策提示词
```

---

## 四、功能模块测试评估

### 4.1 核心功能可用性评估

| 模块 | 状态 | 评估 |
|------|------|------|
| 后端启动与路由 | ✅ 正常 | FastAPI 正常启动，40+ 路由全部注册 |
| 数据库连接 | ✅ 正常 | SQLite/PostgreSQL 双模式，连接池监控正常 |
| 前端构建 | ✅ 正常 | `npm run build` 成功，dist 目录存在 |
| WebSocket | ✅ 正常 | 可连接、bootstrap、snapshot 通信 |
| AI 交易决策 | ⚠️ 部分 | AIStrategyEngine 已废弃，需走 strategy_coordinator 新路径 |
| Paper Trading | ✅ 正常 | 模拟引擎 + 资金管理 + TP/SL 完整 |
| Hyperliquid 集成 | ✅ 正常 | 交易客户端 + 快照 + 行情完整 |
| 信号系统 | ✅ 正常 | 定义 → 检测 → 确认 → 触发 完整链路 |
| 市场体制识别 | ✅ 正常 | breakout/absorption/trap/exhaustion/noise 分类完整 |
| 风控系统 | ✅ 正常 | 多层风控门 + 熔断机制 + 事件日志 |
| 钉钉通知 | ✅ 正常 | 机器人配置 + 事件推送 + 统计 |
| 回测引擎 | ✅ 正常 | 模板 → 运行 → 进化 → 智慧提取 |
| RAG 知识库 | ⚠️ 条件性 | 依赖 ChromaDB + BGE 模型，首次加载需下载 |
| K 线采集 | ✅ 正常 | 实时采集器 + 历史回补 + AI 分析 |
| LLM 配置管理 | ✅ 正常 | 统一配置库 + 多 Provider 支持 |
| 分层置信度 | ✅ 正常 | 32/32 测试通过 |
| 全自动交易 | ✅ 正常 | 会话管理 + 策略自动创建 + 风控 |
| 策略模板库 | ✅ 正常 | 内置 + 导入 + 晋升机制 |
| 交易员性格 | ✅ 正常 | 性格档案 + 心理状态机 |
| 交易记忆 | ✅ 正常 | 历史交易上下文 + 模式匹配 |

### 4.2 API 端点健康检查

| 端点 | 方法 | 预期 | 状态 |
|------|------|------|------|
| /api/health | GET | JSON healthy | ✅ |
| /docs | GET | Swagger UI | ✅ |
| /openapi.json | GET | OpenAPI Schema (~348KB) | ✅ |
| /api/atas/v2/info | GET | ATAS V2 信息 | ✅ |
| /health | GET | ⚠️ 返回 HTML（SPA 回退） | ⚠️ |
| /ws | WS | WebSocket 连接 | ✅ |
| /auth-config.json | GET | 认证配置 | ✅ |
| / (root) | GET | 前端 index.html | ✅ |

---

## 五、问题清单（按严重程度排序）

### P0 — 阻塞级

| ID | 问题 | 影响 | 位置 |
|----|------|------|------|
| P0-1 | `/health` 与 `/api/health` 行为不一致 | 监控脚本若用 `/health` 得到 HTML 而非 JSON，导致假阳性健康状态 | `backend/main.py:581-602` SPA catch-all 路由 |
| P0-2 | ATAS V2 集成测试端口不一致 | 测试脚本硬编码 8802，`package.json` dev:backend 用 8000，Docker 用 8802 | `test_atas_v2_integration.py` vs `package.json` |
| P0-3 | CORS 配置 `allow_origins=["*"]` | 生产环境允许任意跨域请求，存在安全风险 | `backend/main.py:116-121` |

### P1 — 功能风险

| ID | 问题 | 影响 | 位置 |
|----|------|------|------|
| P1-1 | AIStrategyEngine 已废弃但仍被引用 | Phase 2 重构后旧引擎仍有 import 路径，新代码应使用 strategy_coordinator | `services/ai_strategy_engine.py` |
| P1-2 | 前端无路由库，使用 hash 手动路由 | 无法支持浏览器前进/后退、无法做路由守卫、URL 不可分享 | `frontend/app/main.tsx:143-258` |
| P1-3 | WebSocket 单例全局变量 `__WS_SINGLETON__` | 模块级变量在 HMR 时可能冲突，React StrictMode 双渲染也有风险 | `frontend/app/main.tsx:19` |
| P1-4 | 数据库迁移用原始 SQL `_ensure_columns_safe` | 手写 SQL 字符串拼接，存在 SQL 注入风险（虽为内部启动代码） | `backend/main.py:237-313` |
| P1-5 | 前端 Keep-Alive 所有已访问页面 | 内存持续增长，长时间运行后可能导致性能问题 | `frontend/app/main.tsx:684-693` |
| P1-6 | Binance 路由/模块已移除但数据模型保留 | `BinancePosition` 表和部分 binance 字段仍存在于 Account 模型中 | `backend/database/models.py:1340-1393` |
| P1-7 | 密钥加密密钥硬编码在 .env 中 | `BINANCE_ENCRYPTION_KEY` 明文存储在版本控制中 | `.env:5` |

### P2 — 技术债 / 改进建议

| ID | 问题 | 说明 |
|----|------|------|
| P2-1 | 无统一 pytest 套件 | 测试为独立脚本，无 conftest.py，无 pytest.ini |
| P2-2 | API 密钥存储在数据库中 | LLMConfiguration.api_key 虽加密但密钥管理不完善 |
| P2-3 | 100+ 个 MD 文档散落在根目录 | 文档管理混乱，应归档到 docs/ |
| P2-4 | 大量 `tmpclaude-*` 临时目录未清理 | 后端目录中有 20+ 个临时工作目录 |
| P2-5 | 前端组件目录 31 个但无统一规范 | 部分组件功能重叠（如 signal-management vs modern-signals） |
| P2-6 | RAG 首次加载依赖外网下载模型 | 离线部署需预缓存 BGE 模型 |
| P2-7 | Docker 端口映射不一致 | Docker 内 8802，package.json dev 用 8000，文档不一致 |
| P2-8 | `@on_event("startup")` 已弃用 | FastAPI 推荐使用 lifespan 上下文管理器 |

---

## 六、代码质量评估

### 6.1 后端代码质量

| 维度 | 评分(1-5) | 说明 |
|------|-----------|------|
| 模块化 | 4 | 服务拆分细致，职责明确 |
| 可维护性 | 3 | 部分服务过于庞大（如 paper_trading_engine），需拆分 |
| 错误处理 | 3 | try/except 覆盖广泛但粒度不一，部分异常被吞没 |
| 类型安全 | 2 | 大量 `Any` 类型和 `# type: ignore`，Pydantic Schema 不完整 |
| 测试覆盖 | 2 | 核心逻辑缺少单元测试，多数为集成/手动脚本 |
| 文档 | 3 | API 有 OpenAPI 自动文档，代码注释中等 |
| 安全 | 2 | CORS 全开、密钥管理、SQL 拼接存在风险 |

### 6.2 前端代码质量

| 维度 | 评分(1-5) | 说明 |
|------|-----------|------|
| 组件化 | 4 | 组件拆分合理，功能模块清晰 |
| 状态管理 | 3 | Context + useState 基本可用，但主组件 state 过多 |
| 类型安全 | 3 | TypeScript 使用但部分 `any` 类型 |
| 性能 | 3 | Keep-Alive 页面缓存有内存泄漏风险 |
| 代码规范 | 3 | ESLint/Prettier 未配置，风格不统一 |
| 响应式 | 3 | Win95 主题下响应式有限 |

---

## 七、测试覆盖缺口

### 7.1 未测试的关键链路

| 链路 | 风险 | 建议测试方式 |
|------|------|-------------|
| 实际下单 → 交易所成交确认 | 高 | Mock Hyperliquid API 的集成测试 |
| 风控熔断 → 冷却恢复 | 高 | 触发连续亏损的自动化测试 |
| 利润保护（TP/SL/Trailing） | 高 | Paper Trading 场景模拟 |
| LLM 调用 → 决策解析 | 中 | Mock LLM 响应测试 |
| 回测引擎完整流水线 | 中 | 历史数据回放验证 |
| RAG 知识检索 | 中 | 预建知识库测试 |
| 钉钉通知推送 | 中 | Webhook Mock 测试 |
| 用户认证流程 | 中 | OAuth 流程 E2E |
| 前端 E2E 全流程 | 中 | Playwright/Cypress |
| 并发交易安全 | 高 | 多策略同时交易的竞态测试 |
| 数据库连接池耗尽 | 中 | 压力测试 |
| WebSocket 重连 | 中 | 网络断开模拟 |

---

## 八、依赖安全与兼容性

### 8.1 后端依赖

| 依赖 | 版本 | 状态 |
|------|------|------|
| fastapi | 0.115.0 | 稳定 |
| sqlalchemy | 2.0.35 | 稳定 |
| openai | 1.52.2 | 建议更新（API 可能变化） |
| anthropic | 0.39.0 | 建议更新 |
| pydantic | 2.9.2 | 稳定 |
| cryptography | 43.0.1 | 稳定 |
| websockets | 13.1 | 稳定 |

### 8.2 前端依赖

| 依赖 | 版本 | 状态 |
|------|------|------|
| react | 18.2.0 | 稳定（React 19 已发布） |
| vite | 4.4.0 | 建议更新至 5.x |
| tailwindcss | 3.3.3 | 稳定 |
| chart.js | 4.5.1 | 稳定 |
| recharts | 2.13.3 | 稳定 |

---

## 九、部署架构审查

### 9.1 Docker Compose 配置

- PostgreSQL 14 + App 容器
- 热重载卷挂载（选择性挂载，避免覆盖 static）
- 加密密钥自动生成
- 健康检查配置（pg_isready）
- 端口映射：5432（PG）、8000（App）

### 9.2 启动脚本

- Windows `.bat` 脚本：`Run.bat`、`start-dev-mode.bat`、`启动AlphaArena.bat`
- Python Launcher：`launcher.py`
- 混合使用 `pnpm` / `npm` / `uv`

---

## 十、总结与建议

### 10.1 总体评价

Hyper-Alpha-Arena 是一个**功能极其丰富的量化交易平台**，涵盖了从信号检测、AI 决策、风控、执行到学习反馈的完整交易闭环。项目架构合理，模块化程度高，支持多交易所、多周期、多策略。

### 10.2 核心风险

1. **安全**：CORS 全开、密钥管理、SQL 拼接
2. **测试**：核心交易逻辑缺少自动化测试
3. **一致性**：端口/配置在不同环境间不一致
4. **技术债**：废弃代码未清理、临时文件未删除

### 10.3 优先修复建议

| 优先级 | 建议 |
|--------|------|
| 立即 | 1. 统一健康检查路径（修复 SPA catch-all 对 `/health` 的误捕获）|
| 立即 | 2. CORS 配置限制为已知域名 |
| 短期 | 3. 建立统一 pytest 测试套件，覆盖核心交易逻辑 |
| 短期 | 4. 清理废弃模块和临时文件 |
| 中期 | 5. 将 `@on_event("startup")` 迁移到 lifespan |
| 中期 | 6. 引入前端路由库（react-router）|
| 中期 | 7. 加强类型安全（减少 `Any`）|
| 持续 | 8. 建立 CI/CD 流水线 + 自动化测试 |

---

*本报告由代码静态审查 + 架构分析生成，未启动服务或连接外部 API。生产上线前请结合实际环境联调测试。*
