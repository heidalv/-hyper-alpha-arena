# Hyper-Alpha-Arena — Code Wiki

> 版本：基于 2026-05-07 仓库快照生成  
> 后端版本：`0.5.0`（`backend/pyproject.toml`）  
> 前端版本：`0.7.0`（`frontend/package.json`）

---

## 目录

1. [项目概述](#1-项目概述)
2. [整体架构](#2-整体架构)
3. [目录结构](#3-目录结构)
4. [后端详解](#4-后端详解)
   - 4.1 [入口文件 main.py](#41-入口文件-mainpy)
   - 4.2 [数据库层](#42-数据库层)
   - 4.3 [API 路由层（45 个模块）](#43-api-路由层)
   - 4.4 [Services 服务层（170+ 文件）](#44-services-服务层)
   - 4.5 [配置与版本](#45-配置与版本)
5. [前端详解](#5-前端详解)
   - 5.1 [入口 main.tsx](#51-入口-maintsx)
   - 5.2 [组件体系](#52-组件体系)
   - 5.3 [状态管理与 Contexts](#53-状态管理与-contexts)
   - 5.4 [前端依赖](#54-前端依赖)
6. [WebSocket 实时通信](#6-websocket-实时通信)
7. [数据库模型速查](#7-数据库模型速查)
8. [关键业务流程](#8-关键业务流程)
9. [定时任务总览](#9-定时任务总览)
10. [依赖关系图](#10-依赖关系图)
11. [项目运行方式](#11-项目运行方式)
12. [环境变量参考](#12-环境变量参考)
13. [测试体系](#13-测试体系)

---

## 1. 项目概述

**Hyper-Alpha-Arena** 是一个面向加密货币永续合约市场的 **AI 驱动全自动交易平台**。

核心特性：

| 特性 | 说明 |
|------|------|
| AI 多账户交易 | 每个账户可绑定不同 LLM（GPT-4o、DeepSeek、Claude、本地模型等），独立决策 |
| 多交易所支持 | Hyperliquid（主力）、Binance（期货/现货），支持主网/测试网切换 |
| 模拟交易引擎 | Paper Trading 模式，无需真实资金验证策略 |
| 策略模板库 | 内置短线/中线/长线策略模板，支持拖拽式可视化策略设计器 |
| 回测引擎 | 基于历史 K 线的高保真回测，带进化优化（遗传算法 + DRL） |
| 强化学习 | DRL（深度强化学习）+ Kelly 仓位管理 + 投资组合风险控制 |
| RAG 知识库 | 基于 ChromaDB + sentence-transformers 的策略知识检索 |
| 智能告警 | 钉钉通知、爆仓预警、止损保护、触发频率监控 |
| 全自动模式 | Full-Auto 会话自动下单，支持崩溃恢复 |
| Win95 UI 风格 | 前端采用复古 Windows 95 桌面主题，现代 React + Tailwind 实现 |

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                     浏览器 / 桌面客户端                       │
│          React 18 + Vite SPA  (port 5173 开发模式)           │
│          Win95 主题 UI  ←→  REST API + WebSocket             │
└───────────────────────┬─────────────────────────────────────┘
                        │ HTTP /api/*  +  WS /ws
┌───────────────────────▼─────────────────────────────────────┐
│              FastAPI  (port 8000)                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────────┐  │
│  │ API路由层 │  │ WS 广播  │  │  静态文件 backend/static  │  │
│  │ 45个模块  │  │ ws.py    │  │  (前端构建产物)           │  │
│  └────┬─────┘  └────┬─────┘  └──────────────────────────┘  │
│       │              │                                        │
│  ┌────▼──────────────▼──────────────────────────────────┐   │
│  │                 Services 服务层 (170+ 文件)            │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐  │   │
│  │  │ 交易执行  │ │ 策略引擎  │ │  AI决策  │ │ 数据采集│  │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └─────────┘  │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐  │   │
│  │  │ 风险管理  │ │ K线管理  │ │ 学习进化  │ │ 监控告警│  │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └─────────┘  │   │
│  └────────────────────────────────┬──────────────────────┘   │
│                                   │                           │
│  ┌────────────────────────────────▼──────────────────────┐   │
│  │         Database 层 (SQLAlchemy + SQLite/PostgreSQL)   │   │
│  │   models.py (~2800行，30+ 数据表)                      │   │
│  └────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                        │
        ┌───────────────▼────────────────┐
        │    外部服务                     │
        │  Hyperliquid API (主网/测试网)  │
        │  Binance API (期货/现货)        │
        │  OpenAI / DeepSeek / Claude    │
        │  ChromaDB (本地 RAG 向量库)    │
        │  DingTalk Webhook              │
        └────────────────────────────────┘
```

---

## 3. 目录结构

```
001Alpha/
└── Hyper-Alpha-Arena/               ← 主工程根目录
    ├── backend/                     ← FastAPI 后端
    │   ├── main.py                  ← 应用入口，路由挂载，启动/关闭钩子
    │   ├── version.py               ← __version__ = "0.5.0"
    │   ├── start_server.py          ← 生产启动脚本
    │   ├── models.py                ← (旧) 模型文件，已迁移到 database/
    │   ├── api/                     ← 45 个路由模块
    │   │   ├── account_routes.py
    │   │   ├── ai_trading_routes.py
    │   │   ├── ai_strategy_routes.py
    │   │   ├── analytics_routes.py
    │   │   ├── arena_routes.py
    │   │   ├── atas_routes.py       ← ATAS v1
    │   │   ├── atas_v2_routes.py    ← ATAS v2（回测/风险/监控）
    │   │   ├── backtest_routes.py
    │   │   ├── config_routes.py
    │   │   ├── crypto_routes.py
    │   │   ├── dingtalk_routes.py
    │   │   ├── evolution_routes.py
    │   │   ├── exchange_routes.py
    │   │   ├── full_auto_routes.py
    │   │   ├── hyperliquid_routes.py
    │   │   ├── hyperliquid_action_routes.py
    │   │   ├── intelligence_routes.py
    │   │   ├── kline_routes.py
    │   │   ├── kline_analysis_routes.py
    │   │   ├── learning_loop_routes.py
    │   │   ├── llm_config_routes.py
    │   │   ├── llm_usage_routes.py
    │   │   ├── market_data_routes.py
    │   │   ├── market_flow_routes.py
    │   │   ├── market_regime_routes.py
    │   │   ├── notification_routes.py
    │   │   ├── order_routes.py
    │   │   ├── paper_trading_routes.py
    │   │   ├── prompt_routes.py
    │   │   ├── prompt_training_routes.py
    │   │   ├── rag_routes.py
    │   │   ├── ranking_routes.py
    │   │   ├── risk_routes.py
    │   │   ├── rl_routes.py
    │   │   ├── sampling_routes.py
    │   │   ├── signal_routes.py
    │   │   ├── smart_signal_routes.py
    │   │   ├── strategy_template_routes.py
    │   │   ├── system_control_routes.py
    │   │   ├── system_log_routes.py
    │   │   ├── system_monitor_routes.py
    │   │   ├── user_routes.py
    │   │   ├── visual_strategy_routes.py
    │   │   ├── ai_signal_prompt_integration_routes.py
    │   │   └── ws.py                ← WebSocket 端点
    │   ├── config/
    │   │   └── settings.py          ← 全局配置常量 (DEFAULT_TRADING_CONFIGS 等)
    │   ├── database/
    │   │   ├── connection.py        ← engine, Base, SessionLocal
    │   │   ├── models.py            ← 全部 SQLAlchemy 模型（~2800行）
    │   │   ├── schema_validator.py  ← 启动时自动修复缺失列
    │   │   └── migrations/          ← Alembic 迁移脚本
    │   ├── repositories/            ← 数据访问层（Repository 模式）
    │   ├── schemas/                 ← Pydantic 请求/响应模型
    │   ├── services/                ← 核心业务逻辑（170+ 文件）
    │   │   ├── 子包/                ← adaptive_executor, ai_strategy_generator,
    │   │   │                           arbitrage, atas_api, atas_core,
    │   │   │                           atas_integration, backtest_engine,
    │   │   │                           backtest_reporting, deployment,
    │   │   │                           dingtalk, exchange, factor_engine,
    │   │   │                           learning_feedback_layer, monitoring,
    │   │   │                           risk_management, rl,
    │   │   │                           strategy_optimizer,
    │   │   │                           strategy_orchestrator,
    │   │   │                           system_monitoring, testing
    │   │   └── *.py                 ← 143+ 平铺服务文件（见第4.4节）
    │   ├── utils/
    │   │   └── monitoring.py        ← 监控端点（/metrics 等）
    │   └── static/                  ← 前端构建产物（pnpm build → dist → 复制到此）
    │
    ├── frontend/                    ← Vite + React 18 前端
    │   ├── index.html               ← SPA HTML 壳，挂载 app/main.tsx
    │   ├── vite.config.ts           ← 构建配置，代理 /api → :8000
    │   ├── tsconfig.json            ← "@/*" → "./app/*" 路径别名
    │   ├── components.json          ← shadcn/ui 配置
    │   ├── app/
    │   │   ├── main.tsx             ← React 根组件，WebSocket 管理，全局状态
    │   │   ├── index.css            ← 全局样式
    │   │   ├── i18n.ts              ← i18next 国际化配置
    │   │   ├── components/
    │   │   │   ├── ui/              ← shadcn/ui 基础组件（Button, Dialog, etc.）
    │   │   │   ├── win95/           ← Win95 主题外壳组件
    │   │   │   ├── layout/          ← Header, Sidebar, SystemLogs
    │   │   │   ├── portfolio/       ← 资产面板、仪表盘
    │   │   │   ├── trading/         ← 交易表单、订单操作
    │   │   │   ├── trading-console/ ← AI 决策面板、信号面板
    │   │   │   ├── trader/          ← AI 交易员管理
    │   │   │   ├── strategy/        ← 策略配置
    │   │   │   ├── signal/          ← 信号管理
    │   │   │   ├── prompt/          ← Prompt 管理
    │   │   │   ├── klines/          ← K 线图表（TradingView）
    │   │   │   ├── analytics/       ← 分析统计
    │   │   │   ├── settings/        ← 设置页面
    │   │   │   ├── settings-page/   ← 全局设置
    │   │   │   ├── monitor/         ← 因子评估、假设面板
    │   │   │   ├── risk/            ← 风险管理
    │   │   │   ├── market-scanner/  ← 市场扫描
    │   │   │   ├── premium/         ← 高级功能
    │   │   │   ├── shared/          ← 共享组件（ErrorBoundary等）
    │   │   │   └── atas-v2/         ← ATAS V2 策略中心 UI
    │   │   ├── contexts/            ← React Contexts
    │   │   ├── hooks/               ← 自定义 Hooks
    │   │   └── lib/                 ← 工具函数（cn, api等）
    │   └── public/                  ← 静态资源
    │
    ├── desktop/
    │   └── launcher.py              ← pywebview 桌面 App 入口
    ├── data/
    │   └── alpha_arena.db           ← SQLite 主数据库（生产可换 PostgreSQL）
    ├── logs/                        ← 运行日志
    │   ├── backend.log
    │   ├── frontend.log
    │   └── launcher.log
    ├── scripts/                     ← PowerShell 启动/停止脚本
    ├── tests/                       ← pytest 测试套件
    ├── docs/                        ← 技术文档
    ├── DEV-README.md                ← 开发指南（推荐首先阅读）
    ├── QUICK_START.md
    ├── package.json                 ← monorepo 根包（pnpm workspace）
    ├── pnpm-workspace.yaml
    └── requirements.txt             ← Python 依赖（pip 安装用）
```

---

## 4. 后端详解

### 4.1 入口文件 main.py

**路径**：`backend/main.py`（901 行）

**职责**：
- 创建 `FastAPI` 应用实例（`title="Hyper Alpha Arena API"`）
- 配置 CORS 中间件（允许所有来源）
- 挂载静态文件目录 `/static` 和 `/assets`
- 注册全部 45 个路由模块
- 定义 SPA 回退路由（`/{full_path:path}` → 返回 `index.html`）
- 启动/关闭事件钩子

#### 关键函数速查

| 函数/端点 | 说明 |
|-----------|------|
| `on_startup()` | 启动钩子：初始化数据库表、启动前端文件监听、DingTalk 后台任务、初始化 sampling pool、注册各类定时任务、启动异步服务 |
| `on_shutdown()` | 关闭钩子：停止策略管理器、市场流、K 线采集、事件总线 |
| `build_frontend()` | 触发 pnpm/npm 前端构建，并将 dist 复制到 `backend/static/` |
| `watch_frontend_files()` | 后台线程每 2 秒轮询前端源文件变化，触发自动重建 |
| `_ensure_columns_safe()` | 启动时自动为已有表添加缺失列（兼容 SQLite 和 PostgreSQL） |
| `GET /api/health` | 健康检查，返回版本号 |
| `POST /api/rebuild-frontend` | 手动触发前端构建 |
| `WS /ws` | WebSocket 端点 |
| `GET /` | 返回 `index.html`（SPA 根） |
| `GET /{full_path}` | SPA 回退，跳过 `/api`、`/static`、`/docs` 等路径 |

---

### 4.2 数据库层

**路径**：`backend/database/`

#### connection.py

```python
engine = create_engine(DATABASE_URL)  # SQLite 默认，可配置 PostgreSQL
Base = declarative_base()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
```

数据库 URL 优先从环境变量 `DATABASE_URL` 读取，默认指向 `data/alpha_arena.db`（SQLite）。

#### models.py（~2800 行，30+ 数据表）

**核心数据模型速查**：

| 模型类 | 表名 | 说明 |
|--------|------|------|
| `User` | `users` | 用户，默认用 `default` 用户，支持多用户扩展 |
| `Account` | `accounts` | AI/手动交易账户，含 Hyperliquid/Binance 配置、资金余额 |
| `LLMConfiguration` | `llm_configurations` | LLM 模型配置库（支持 OpenAI/DeepSeek/Qwen/本地模型等） |
| `Position` | `positions` | 持仓记录（品种、方向、数量、均价、杠杆等） |
| `Order` | `orders` | 订单记录 |
| `TradingConfig` | `trading_configs` | 交易所手续费、最小下单量等配置 |
| `CryptoKline` | `crypto_klines` | K 线数据（exchange/symbol/market/period/timestamp/environment 联合唯一索引） |
| `AIStrategy` | `ai_strategies` | AI 策略配置（交易对、时间框架、杠杆、雪球策略等） |
| `AIDecisionLog` | `ai_decision_logs` | AI 决策日志（含 prompt/reasoning/decision 快照） |
| `PromptTemplate` | `prompt_templates` | Prompt 模板 |
| `AccountPromptBinding` | `account_prompt_bindings` | 账户-Prompt 绑定关系 |
| `SystemConfig` | `system_configs` | 系统全局配置（key-value 存储） |
| `StrategyTemplate` | `strategy_templates` | 内置策略模板（短线/中线/长线） |
| `PaperBalance` | `paper_balances` | 模拟交易余额 |
| `GlobalSamplingConfig` | `global_sampling_configs` | 全局采样深度配置 |
| `KlineCollectionTask` | `kline_collection_tasks` | K 线历史补全任务 |
| `MarketFlowData` | `market_flow_data` | 市场流数据（成交量、订单簿、OI、资金费率） |
| `SignalRecord` | `signal_records` | 信号记录 |
| `UserSubscription` | `user_subscriptions` | 用户订阅（高级功能权限） |
| `UserAuthSession` | `user_auth_sessions` | 用户认证会话 |
| `AccountAssetSnapshot` | `account_asset_snapshots` | 账户资产快照（资产曲线用） |
| `TraderPersonality` | `trader_personalities` | AI 交易员个性配置 |
| `LLMUsageLog` | `llm_usage_logs` | LLM API 使用日志 |

#### schema_validator.py

启动时自动检测并修复数据库 Schema 与 ORM 模型的差异，确保增量部署兼容性。

---

### 4.3 API 路由层

全部 45 个路由模块均位于 `backend/api/`，通过 `app.include_router()` 挂载。

**路由分组速查**：

| 分组 | 文件 | 主要端点前缀 | 核心功能 |
|------|------|--------------|----------|
| 账户管理 | `account_routes.py` | `/api/accounts` | CRUD 账户、LLM 绑定、策略配置、余额查询 |
| AI 交易 | `ai_trading_routes.py` | `/api/ai-trading` | AI 自动交易启动/停止、状态查询 |
| AI 策略 | `ai_strategy_routes.py` | `/api/ai-strategies` | AI 策略 CRUD、策略参数 |
| 分析统计 | `analytics_routes.py` | `/api/analytics` | 绩效分析、归因分析 |
| 竞技场 | `arena_routes.py` | `/api/arena` | AI 交易员排行榜、对战数据 |
| ATAS v1 | `atas_routes.py` | `/api/atas` | 高级自动化交易系统 v1 |
| ATAS v2 | `atas_v2_routes.py` | `/api/atas/v2` | 回测引擎、风险管理、系统监控 |
| 回测 | `backtest_routes.py` | `/api/backtest` | 策略回测、回测报告 |
| 配置 | `config_routes.py` | `/api/config` | 交易参数配置 |
| 加密货币 | `crypto_routes.py` | `/api/crypto` | 行情数据、价格查询 |
| 钉钉 | `dingtalk_routes.py` | `/api/dingtalk` | 钉钉通知配置与推送 |
| 进化优化 | `evolution_routes.py` | `/api/evolution` | 遗传算法策略进化 |
| 交易所 | `exchange_routes.py` | `/api/exchange` | 交易所配置管理 |
| 全自动 | `full_auto_routes.py` | `/api/full-auto` | 全自动交易会话 |
| Hyperliquid | `hyperliquid_routes.py` | `/api/hyperliquid` | 持仓、账户、历史查询 |
| Hyperliquid 操作 | `hyperliquid_action_routes.py` | `/api/hyperliquid` | 下单、撤单、修改仓位 |
| 情报引擎 | `intelligence_routes.py` | `/api/intelligence` | 多周期情报信号 |
| K 线 | `kline_routes.py` | `/api/klines` | K 线查询、历史补全 |
| K 线分析 | `kline_analysis_routes.py` | `/api/kline-analysis` | AI K 线分析 |
| 学习循环 | `learning_loop_routes.py` | `/api/learning-loop` | 策略学习复盘 |
| LLM 配置 | `llm_config_routes.py` | `/api/llm-configs` | LLM 配置 CRUD、连通测试 |
| LLM 用量 | `llm_usage_routes.py` | `/api/llm-usage` | Token 使用统计 |
| 市场数据 | `market_data_routes.py` | `/api/market-data` | 实时行情、深度 |
| 市场流 | `market_flow_routes.py` | `/api/market-flow` | 成交流、OI、资金费率 |
| 市场状态 | `market_regime_routes.py` | `/api/market-regime` | 牛/熊/震荡状态判断 |
| 告警 | `notification_routes.py` | `/api/notifications` | 通知消息管理 |
| 订单 | `order_routes.py` | `/api/orders` | 手动下单、订单查询 |
| 模拟交易 | `paper_trading_routes.py` | `/api/paper-trading` | 模拟账户、模拟仓位 |
| Prompt | `prompt_routes.py` | `/api/prompts` | Prompt 模板管理、预览 |
| Prompt 训练 | `prompt_training_routes.py` | `/api/prompt-training` | Prompt 进化训练 |
| RAG | `rag_routes.py` | `/api/rag` | 知识库检索增强 |
| 排名 | `ranking_routes.py` | `/api/ranking` | 账户绩效排名 |
| 风险 | `risk_routes.py` | `/api/risk` | 风险指标查询 |
| 强化学习 | `rl_routes.py` | `/api/rl` | DRL 模型、训练状态 |
| 采样 | `sampling_routes.py` | `/api/sampling` | 采样池配置 |
| 信号 | `signal_routes.py` | `/api/signals` | 技术信号查询 |
| 智能信号 | `smart_signal_routes.py` | `/api/smart-signals` | AI 增强信号 |
| AI 信号集成 | `ai_signal_prompt_integration_routes.py` | `/api/ai-signal` | 信号-Prompt 联动 |
| 策略模板 | `strategy_template_routes.py` | `/api/strategy-templates` | 内置策略模板库 |
| 系统控制 | `system_control_routes.py` | `/api/system` | 服务启停控制 |
| 系统日志 | `system_log_routes.py` | `/api/system-logs` | 后台日志查询 |
| 系统监控 | `system_monitor_routes.py` | `/api/system-monitor` | CPU/内存/磁盘监控 |
| 用户 | `user_routes.py` | `/api/users` | 用户 CRUD |
| 可视化策略 | `visual_strategy_routes.py` | `/api/atas/v2/strategies` | 拖拽式策略设计器 |
| WebSocket | `ws.py` | `/ws` | 实时推送（价格/持仓/订单/日志） |

---

### 4.4 Services 服务层

服务层是整个平台的核心，共 170+ 文件，按功能分为以下模块组：

#### 🏗️ 基础设施类

| 文件 | 职责 |
|------|------|
| `startup.py` | 启动编排：调度器、全自动会话恢复、市场数据流、监控任务注册 |
| `scheduler.py` | APScheduler 封装，提供 `add_interval_task` 等接口 |
| `event_bus.py` | 异步事件总线，解耦服务间通信 |
| `ws_broadcast.py` | WebSocket 广播：将后端事件推送给所有前端连接 |
| `system_logger.py` | 系统日志收集器，日志写入数据库 |
| `price_cache.py` | 内存价格缓存，TTL 过期清理 |

#### 📊 市场数据类

| 文件 | 职责 |
|------|------|
| `market_price_service.py` | 统一取价 + symbol 同步（取代 market_stream） |
| `market_data_hub.py` | WS 行情总线，L2/funding/OI 热缓存 |
| `market_events.py` | 价格更新事件订阅/发布 |
| `market_data.py` | 市场数据聚合 |
| `market_data_analyzer.py` | 数据分析辅助 |
| `market_flow_collector.py` | 成交流、订单簿、OI、资金费率采集（15 秒聚合） |
| `market_flow_indicators.py` | 市场流指标计算 |
| `market_regime.py` | 市场状态分类（牛/熊/震荡） |
| `market_regime_detector.py` | 市场状态检测算法 |
| `market_regime_service.py` | 市场状态服务层 |
| `market_scanner.py` | 全市场扫描 |
| `market_fingerprint.py` | 市场指纹特征提取 |

#### 📈 K 线数据类

| 文件 | 职责 |
|------|------|
| `kline_realtime_collector.py` | 实时 K 线采集（异步，WebSocket） |
| `kline_collectors.py` | 历史 K 线批量拉取 |
| `kline_backfill_manager.py` | K 线历史补全任务管理器 |
| `kline_history_sync.py` | K 线历史同步 |
| `kline_data_service.py` | K 线数据查询服务 |
| `kline_ai_analysis_service.py` | AI 驱动的 K 线分析 |

#### 🤖 AI 决策类

| 文件 | 职责 |
|------|------|
| `trading_decision_interface.py` | AI 决策统一入口，汇聚 DRL/Kelly/PortfolioRisk 各层决策 |
| `trading_analysts.py` | AI 分析员（多模型并发分析） |
| `smart_prompt_generator.py` | 动态 Prompt 生成（多维度市场上下文注入） |
| `prompt_initializer.py` | Prompt 模板种子数据初始化 |
| `prompt_training_system.py` | Prompt 进化训练系统 |
| `rag_knowledge_service.py` | RAG 知识库检索（ChromaDB） |
| `intelligence_signal_engine.py` | 情报信号引擎（新闻+技术+链上数据综合） |
| `news_intelligence_service.py` | 新闻情报采集与分析 |
| `news_feed.py` | 新闻源管理 |
| `social_sentiment_collector.py` | 社交媒体情绪采集 |
| `sentiment_composite_service.py` | 情绪综合评分 |
| `onchain_data_collector.py` | 链上数据采集 |
| `whale_tracker_service.py` | 鲸鱼地址追踪 |

#### 🧠 策略引擎类

| 文件 | 职责 |
|------|------|
| `trading_strategy.py` | 策略管理器，统一调度各策略实例 |
| `strategy_library.py` | 策略算法库（EMA/MACD/BB/RSI 等） |
| `strategy_template_routes.py` | 内置模板（短线趋势/动量/区间、中线波段/均值回归、长线趋势/突破） |
| `strategy_coordinator.py` | 策略协调器 |
| `strategy_generator.py` | 自动策略生成 |
| `strategy_evolver.py` | 策略进化（遗传算法） |
| `strategy_optimizer_service.py` | 策略参数优化 |
| `strategy_health_service.py` | 策略健康度评估 |
| `strategy_learning_service.py` | 策略自学习复盘（每日）  |
| `strategy_genome.py` | 策略基因组编码（遗传算法核心） |
| `strategy_hypothesis_engine.py` | 策略假设验证引擎 |
| `strategy_hypothesis_generator.py` | 策略假设生成 |
| `strategy_intelligence_engine.py` | 策略情报引擎 |
| `strategy_validator.py` | 策略有效性验证 |
| `strategy_params_registry.py` | 策略参数注册中心 |
| `meta_strategy_selector.py` | 元策略选择器 |
| `multi_timeframe_orchestrator.py` | 多周期策略编排 |
| `visual_strategy_compiler.py` | 可视化策略节点图 → Python 代码编译器 |
| `visual_strategy_executor.py` | 可视化策略执行引擎 |

#### ⚖️ 风险管理类

| 文件 | 职责 |
|------|------|
| `risk_control_service.py` | 风险控制主服务 |
| `risk_model.py` | 风险模型（VaR、最大回撤等） |
| `risk_band_resolver.py` | 风险带宽解析器 |
| `position_sizer.py` | 仓位大小计算 |
| `rl_position_sizer.py` | 强化学习仓位管理（Kelly 公式） |
| `profit_protection_manager.py` | 利润保护管理（追踪止盈） |
| `profit_drawdown_guard.py` | 最大回撤保护 |
| `fee_guard.py` | 手续费保护（防止频繁小单） |
| `liquidity_filter.py` | 流动性过滤器 |
| `master_close_guard.py` | 全局强制平仓守卫 |
| `reentry_cooldown.py` | 重入冷却期管理 |
| `decision_consistency_gate.py` | 决策一致性门控 |
| `liquidation_monitor.py` | 爆仓预警监控（30 秒扫描） |

#### 💹 交易执行类

| 文件 | 职责 |
|------|------|
| `trading_commands.py` | AI/随机下单命令，`AI_TRADING_SYMBOLS` 符号列表 |
| `order_executor.py` | 订单执行器（市价/限价） |
| `order_matching.py` | 模拟撮合引擎 |
| `order_monitor.py` | 订单状态监控 |
| `order_scheduler.py` | 订单调度器 |
| `paper_trading_engine.py` | 模拟交易引擎 |
| `full_auto_trading_service.py` | 全自动交易会话（崩溃恢复） |
| `sub_position_manager.py` | 子仓位管理（分批建仓） |
| `tier_parallel_executor.py` | 分层并发执行器 |
| `long_tier_staged_tp.py` | 长线分阶段止盈 |

#### 🔗 交易所对接类

| 文件 | 职责 |
|------|------|
| `hyperliquid_trading_client.py` | Hyperliquid 交易所完整客户端（下单/撤单/持仓/历史） |
| `hyperliquid_market_data.py` | Hyperliquid 行情数据 |
| `hyperliquid_symbol_service.py` | 交易对列表管理（每 2 小时刷新） |
| `hyperliquid_environment.py` | 主网/测试网环境切换 |
| `hyperliquid_snapshot_service.py` | 持仓快照采集 |
| `hyperliquid_cache.py` | Hyperliquid 数据缓存 |
| `exchange_config.py` | 交易所配置管理 |

#### 🧬 强化学习类（`services/rl/` 子包）

| 模块 | 职责 |
|------|------|
| `system_coordinator.py` | SystemCoordinator：协调 DRL/Kelly/PortfolioRisk 三层决策 |
| `drl_agent.py` | 深度强化学习 Agent |
| `kelly_sizer.py` | Kelly 仓位管理器 |
| `portfolio_risk_manager.py` | 投资组合风险管理 |

#### 📡 通知告警类

| 文件 | 职责 |
|------|------|
| `dingtalk/` (子包) | 钉钉 Webhook 通知、波动率监控、后台任务 |
| `openclaw_notify.py` | 外部通知 |

#### 🔬 分析工具类

| 文件 | 职责 |
|------|------|
| `technical_indicators.py` | 技术指标计算（EMA/MACD/RSI/BB/ATR 等） |
| `signal_detection_service.py` | 信号检测 |
| `signal_confirmation_engine.py` | 信号确认引擎（多重确认） |
| `smart_signal_generator.py` | AI 增强信号生成 |
| `signal_analysis_service.py` | 信号分析统计 |
| `signal_backtest_service.py` | 信号回测 |
| `signal_feedback_tracker.py` | 信号反馈追踪（胜率统计） |
| `pattern_recognition_service.py` | K 线形态识别 |
| `pattern_extractor.py` | 形态特征提取 |
| `trend_classifier.py` | 趋势分类器 |
| `derivatives_analytics_service.py` | 衍生品分析（OI、资金费率） |
| `backtest_engine/` (子包) | 回测引擎核心 |
| `backtest_reporting/` (子包) | 回测报告生成 |
| `live_pipeline_backtest_engine.py` | 实盘管道回测引擎 |
| `genetic_optimizer.py` | 遗传算法优化器 |

#### 📚 学习进化类

| 文件 | 职责 |
|------|------|
| `learning_loop_service.py` | 学习循环服务 |
| `learning_bus.py` | 学习事件总线 |
| `unified_learning_service.py` | 统一学习服务，绩效矩阵衰减（每 12 小时） |
| `strategy_learning_service.py` | 策略学习复盘（每日） |
| `evolution_scheduler.py` | 回测进化自动调度 |
| `wisdom_tracker.py` | 智慧积累追踪器 |
| `trade_memory_miner.py` | 交易记忆挖掘 |
| `hypothesis_generator.py` | 假设生成器 |

---

### 4.5 配置与版本

#### `backend/config/settings.py`

```python
DEFAULT_TRADING_CONFIGS = {
    "BTC": TradingConfigData(
        market="BTC",
        min_commission=...,
        commission_rate=0.001,
        exchange_rate=...,
        min_order_quantity=0.001,
        lot_size=0.001,
    ),
    ...
}

ENABLE_COORDINATOR = True  # 启用 SystemCoordinator（DRL/Kelly/PortfolioRisk）
```

#### `backend/version.py`

```python
__version__ = "0.5.0"
```

---

## 5. 前端详解

### 5.1 入口 main.tsx

**路径**：`frontend/app/main.tsx`（1115 行）

**主要职责**：
1. **WebSocket 单例管理**：模块级 `__WS_SINGLETON__` 防止 React StrictMode 重复连接
2. **全局状态聚合**：`accounts`、`positions`、`orders`、`prices`、`aiDecisions` 等核心状态通过 `useState` 管理，由 WebSocket 增量更新
3. **Delta 合并**：`mergePositions()` 和 `mergeOrders()` 实现持仓/订单的增量更新，避免全量刷新
4. **懒加载路由**：各页面组件通过 `React.lazy()` 按需加载，减小初始 bundle
5. **Win95 主题外壳**：渲染 `Win95Window`、`Win95MenuBar`、`Win95Toolbar`、`Win95Taskbar`、`Win95Ticker`

#### 关键工具函数

| 函数 | 说明 |
|------|------|
| `mergePositions(current, changes)` | 增量合并持仓列表（支持 `_removed` 标记删除） |
| `mergeOrders(current, newItems, removedIds)` | 增量合并订单列表 |
| `resolveWsUrl()` | 解析 WebSocket URL（支持 `VITE_WS_URL` 环境变量） |

#### 懒加载页面组件

| 组件 | 对应页面 |
|------|---------|
| `PromptManager` | Prompt 管理 |
| `SignalManager` | 信号管理 |
| `AttributionAnalysis` | 归因分析 |
| `AnalyticsPage` | 分析统计 |
| `TraderManagement` | AI 交易员管理 |
| `KlinesView` | K 线图表 |
| `StrategyPage` | 策略配置 |
| `RiskPage` | 风险管理 |
| `SettingsPage` | 系统设置 |
| `MarketScannerPage` | 市场扫描 |

---

### 5.2 组件体系

#### Win95 主题外壳（`components/win95/`）

| 组件 | 说明 |
|------|------|
| `Win95Window.tsx` | 主窗口容器，标题栏、最小化/关闭按钮 |
| `Win95MenuBar.tsx` | 菜单栏（File/View/Trade 等菜单） |
| `Win95Toolbar.tsx` | 工具栏，快速功能按钮 |
| `Win95Taskbar.tsx` | 任务栏，显示打开的"窗口"标签 |
| `Win95Ticker.tsx` | 底部行情滚动条 |

#### 核心页面组件

| 组件 | 路径 | 说明 |
|------|------|------|
| `ComprehensiveView` | `portfolio/ComprehensiveView.tsx` | 主仪表盘（资产总览） |
| `ModernTradingDashboard` | `portfolio/ModernTradingDashboard.tsx` | 现代交易仪表盘 |
| `UnifiedDashboardView` | `portfolio/UnifiedDashboardView.tsx` | 统一仪表盘（多账户聚合） |
| `TradingConsole` | `trading-console/TradingConsole.tsx` | AI 交易控制台 |
| `AIDecisionPanel` | `trading-console/AIDecisionPanel.tsx` | AI 决策详情面板 |
| `SignalPanel` | `trading-console/SignalPanel.tsx` | 信号实时面板 |
| `PositionPanel` | `trading-console/PositionPanel.tsx` | 持仓面板 |
| `DecisionLog` | `trading-console/DecisionLog.tsx` | AI 决策日志 |
| `TraderManagement` | `trader/TraderManagement.tsx` | AI 交易员管理（添加/删除/配置） |
| `UnifiedWalletConfigPanel` | `trader/UnifiedWalletConfigPanel.tsx` | 钱包配置面板 |
| `LLMConfigManager` | `settings/LLMConfigManager.tsx` | LLM 配置管理器 |
| `StrategyPage` | `strategy/StrategyPage.tsx` | 策略配置页 |
| `DRLPanel` | `strategy/DRLPanel.tsx` | 深度强化学习面板 |
| `PromptManager` | `prompt/PromptManager.tsx` | Prompt 模板管理 |
| `SignalManager` | `signal/SignalManager.tsx` | 信号管理 |
| `ModernSignalManager` | `signal/ModernSignalManager.tsx` | 现代化信号管理 |
| `TradingViewChart` | `klines/TradingViewChart.tsx` | K 线图表（lightweight-charts） |
| `RiskPage` | `risk/RiskPage.tsx` | 风险管理页 |
| `SettingsPage` | `settings-page/SettingsPage.tsx` | 系统设置页 |
| `MarketScannerPage` | `market-scanner/MarketScannerPage.tsx` | 市场扫描页 |
| `HyperliquidSummary` | `portfolio/HyperliquidSummary.tsx` | Hyperliquid 账户摘要 |
| `BinanceDashboard` | `portfolio/BinanceDashboard.tsx` | Binance 账户面板 |
| `AssetCurveWithData` | `portfolio/AssetCurveWithData.tsx` | 资产曲线图 |

#### UI 基础组件（shadcn/ui + 自定义）

位于 `components/ui/`，包含：`Button`、`Card`、`Dialog`、`Input`、`Select`、`Switch`、`Tooltip`、`Badge`、`Avatar`、`DropdownMenu`、`Label`、`Progress`、`ScrollArea`、`Separator`、`Table`、`Tabs`、`Textarea`、`Drawer`、`AnimatedNumber`、`MetricCard`、`PacmanLoader`、`PremiumRequiredModal`

---

### 5.3 状态管理与 Contexts

前端状态管理采用 React Context + useState 模式（无 Redux/Zustand），核心状态在 `main.tsx` 中管理并通过 props 或 Context 向下传递。

**WebSocket 推送的数据类型**：

| 消息类型 | 说明 |
|----------|------|
| `positions_update` | 持仓增量更新 |
| `orders_update` | 订单增量更新 |
| `price_update` | 实时价格 |
| `ai_decision` | AI 决策通知 |
| `system_log` | 系统日志 |
| `asset_curve` | 资产曲线数据 |

---

### 5.4 前端依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| `react` / `react-dom` | 18.2.0 | 核心框架 |
| `vite` | 4.4.0 | 构建工具 |
| `tailwindcss` | 3.3.3 | CSS 框架 |
| `@radix-ui/*` | 最新 | 无障碍 UI 原语（shadcn 底层） |
| `lucide-react` | 0.536.0 | 图标库 |
| `lightweight-charts` | 5.0.9 | K 线图表 |
| `recharts` | 2.13.3 | 统计图表 |
| `chart.js` + `react-chartjs-2` | 4.5.1 | 图表库 |
| `@xyflow/react` | 12.10.0 | 可视化策略设计器（节点流图） |
| `@assistant-ui/react` | 0.11.45 | AI 对话 UI 组件 |
| `i18next` + `react-i18next` | 25.7.3 | 国际化 |
| `react-hot-toast` | 2.6.0 | Toast 通知 |
| `react-markdown` | 10.1.0 | Markdown 渲染 |
| `js-cookie` | 3.0.5 | Cookie 管理 |

---

## 6. WebSocket 实时通信

**端点**：`ws://localhost:8000/ws`（开发时通过 Vite 代理转发）

**文件**：`backend/api/ws.py`（端点定义） + `backend/services/ws_broadcast.py`（广播逻辑）

**通信模式**：

```
后端服务 → ws_broadcast.py → 所有活跃 WebSocket 连接 → 前端 main.tsx
```

**前端 WS 管理**：
- 模块级单例 `__WS_SINGLETON__`，防止 React StrictMode 双重挂载时建立两个连接
- 自动重连机制
- 断线期间缓冲消息

---

## 7. 数据库模型速查

### 主要关联关系

```
User (1) ─── (N) Account
Account (1) ─── (0:1) PaperBalance
Account (1) ─── (N) Position
Account (1) ─── (N) Order
Account (N) ─── (0:1) LLMConfiguration
Account (1) ─── (0:1) AccountPromptBinding ─── PromptTemplate
Account (1) ─── (0:1) TraderPersonality

CryptoKline: (exchange, symbol, market, period, timestamp, environment) 联合唯一
AIStrategy: 绑定 account_id，多交易对/多周期分析
AIDecisionLog: 绑定 account_id，记录每次 AI 决策完整上下文
```

### `Account` 关键字段说明

```python
account_type      # "AI" 或 "MANUAL"
trading_mode      # "live"（真实）或 "paper"（模拟）
hyperliquid_enabled / hyperliquid_environment  # 主网/测试网
binance_enabled / binance_market_type          # 期货/现货
llm_config_id     # 关联 LLMConfiguration（优先于旧 model/api_key 字段）
initial_capital / current_cash / frozen_cash   # 资金管理
```

### `CryptoKline` 关键字段说明

```python
exchange    # "hyperliquid" | "binance"
symbol      # "BTC" | "ETH" 等
market      # "perp" | "spot"
period      # "1m" | "5m" | "15m" | "1h" | "4h" | "1d"
timestamp   # Unix ms
open/high/low/close/volume
environment # "mainnet" | "testnet"
```

---

## 8. 关键业务流程

### 8.1 AI 自动交易完整链路

```
定时器/手动触发
    │
    ▼
trading_commands.py::place_ai_driven_crypto_order()
    │
    ├─ 获取市场数据（market_data.py）
    ├─ 获取 K 线数据（kline_data_service.py）
    ├─ 计算技术指标（technical_indicators.py）
    ├─ 市场状态判断（market_regime_service.py）
    │
    ▼
trading_decision_interface.py（TDI）
    ├─ 基础规则决策（rule_based_decision_engine.py）
    ├─ DRL Agent（rl/drl_agent.py）
    ├─ Kelly 仓位（rl/kelly_sizer.py）
    └─ 投资组合风险（rl/portfolio_risk_manager.py）
    │
    ▼
trading_analysts.py（LLM 分析，多模型并发）
    │ Prompt 由 smart_prompt_generator.py 动态生成
    │ 包含：K线、技术指标、市场状态、持仓、历史绩效等上下文
    │
    ▼
风险检查（risk_control_service.py）
    ├─ 仓位大小（position_sizer.py）
    ├─ 手续费保护（fee_guard.py）
    ├─ 流动性过滤（liquidity_filter.py）
    └─ 爆仓预警（liquidation_monitor.py）
    │
    ▼
order_executor.py → Hyperliquid/Binance API
    │
    ▼
ws_broadcast.py → 前端实时更新
ai_decision_log → 数据库持久化
```

### 8.2 策略回测流程

```
前端请求 /api/backtest
    │
    ▼
backtest_routes.py
    │
    ▼
services/backtest_engine/（回测引擎）
    ├─ 读取历史 K 线（kline_data_service.py）
    ├─ 运行策略逻辑（strategy_library.py 算法）
    ├─ 模拟撮合（order_matching.py）
    ├─ 计算绩效指标（夏普率、最大回撤、胜率等）
    └─ 生成报告（backtest_reporting/）
    │
    ▼
返回回测结果 JSON
（可触发 evolution_scheduler.py 遗传优化）
```

### 8.3 全自动交易会话

```
用户创建全自动会话 → full_auto_routes.py
    │
    ▼
full_auto_trading_service.py::FullAutoTradingService
    ├─ 启动时通过 restore_running_sessions() 恢复崩溃前会话
    ├─ 每个会话独立循环：分析 → 决策 → 下单 → 监控
    ├─ multi_timeframe_orchestrator.py（短/中/长周期协调）
    └─ tier_parallel_executor.py（分层并发执行）
```

---

## 9. 定时任务总览

所有定时任务通过 `scheduler.py` 的 `task_scheduler.add_interval_task()` 注册，在 `startup.py::initialize_sync_services()` 中统一启动。

| 任务 ID | 间隔 | 功能 |
|---------|------|------|
| `price_cache_cleanup` | 2 分钟 | 清理过期价格缓存 |
| `symbol-refresh` (线程) | 2 小时 | 刷新 Hyperliquid 交易对列表 |
| `market_flow_data_cleanup` | 6 小时 | 清理 30 天前市场流数据 |
| `trigger_frequency_monitoring` | 1 小时 | 触发频率异常监控 |
| `news_intelligence_fetch` | 5 分钟 | 新闻情报采集与 AI 分析 |
| `whale_tracker_fetch` | 2 分钟 | 鲸鱼地址追踪 |
| `ai_daily_journal` | 24 小时 | AI 交易日复盘 |
| `daily_strategy_learning` | 24 小时 | 策略学习复盘（7 天数据） |
| `regime_score_decay` | 12 小时 | 绩效矩阵分数衰减（×0.98） |
| `evolution` (进化调度) | 配置化 | 回测进化自动调度 |
| `db_maintenance` | 6 小时 | 数据库维护（清理过期 K 线/日志） |
| `asset_curve_broadcast` | 60 秒 | 资产曲线数据广播 |
| `paper_trading_monitor` | 10 秒 | 模拟交易持仓监控（TP/SL） |
| `tpsl_monitor` | 60 秒 | TP/SL 保护监控 |
| `liquidation_monitor` | 30 秒 | 爆仓预警扫描 |

---

## 10. 依赖关系图

### 后端主要依赖

```
FastAPI
  ├── SQLAlchemy (ORM)  →  SQLite (开发) / PostgreSQL (生产)
  ├── Alembic (迁移)
  ├── Pydantic v2 (数据验证)
  ├── uvicorn (ASGI 服务器)
  ├── httpx / aiohttp (HTTP 客户端)
  ├── websockets + python-socketio (WebSocket)
  ├── openai (OpenAI API 客户端)
  ├── anthropic (Claude API 客户端)
  ├── hyperliquid-python-sdk (Hyperliquid)
  ├── ccxt (多交易所支持)
  ├── pandas + numpy (数据处理)
  ├── pandas-ta (技术指标)
  ├── chromadb + sentence-transformers (RAG 向量库)
  ├── eth-account + eth-utils (以太坊钱包)
  ├── cryptography + python-jose + passlib (安全)
  ├── python-dotenv (环境变量)
  └── APScheduler / 自研 scheduler.py (定时任务)
```

### 前端主要依赖

```
React 18
  ├── Vite 4 (构建工具)
  ├── TypeScript
  ├── Tailwind CSS 3
  ├── shadcn/ui (Radix UI + class-variance-authority)
  ├── lucide-react (图标)
  ├── lightweight-charts (K线图)
  ├── recharts + chart.js (统计图)
  ├── @xyflow/react (可视化节点图)
  ├── @assistant-ui/react (AI 对话)
  ├── i18next (国际化)
  ├── react-hot-toast (通知)
  └── react-markdown + remark-gfm + rehype-raw (Markdown)
```

---

## 11. 项目运行方式

### 方式一：开发模式（推荐日常开发）

**Windows（推荐）**：

```bat
# 启动（开启两个进程：uvicorn:8000 + vite:5173）
双击 dev-start.bat

# 访问
浏览器打开 http://localhost:5173

# 停止
双击 dev-stop.bat

# 查看状态
双击 dev-status.bat
```

**macOS/Linux**（手动启动）：

```bash
# 终端1：启动后端
cd Hyper-Alpha-Arena/backend
uv sync
uv run uvicorn main:app --reload --port 8000 --host 0.0.0.0

# 终端2：启动前端
cd Hyper-Alpha-Arena/frontend
pnpm install
pnpm dev
```

### 方式二：桌面 App 模式

```bat
# Windows
双击 启动AlphaArena.bat
# 自动构建前端 + 弹出 pywebview 桌面窗口
```

### pnpm monorepo 命令

```bash
# 安装所有依赖
pnpm run install:all

# 同时启动前后端（开发）
pnpm run dev

# 构建前端
pnpm run build:frontend

# 单独启动后端开发服务
pnpm run dev:backend

# 单独启动前端开发服务
pnpm run dev:frontend
```

---

## 12. 环境变量参考

在 `Hyper-Alpha-Arena/backend/` 目录创建 `.env` 文件：

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `DATABASE_URL` | 数据库连接字符串 | `sqlite:///./data/alpha_arena.db` |
| `LOCAL_LLM_BASE_URL` | 内网本地 LLM API 地址 | `http://10.29.193.24:8888/v1` |
| `LOCAL_LLM_API_KEY` | 内网 LLM API Key | `sk-unsloth-...` |
| `LOCAL_LLM_MODEL` | 内网模型名称 | `local-model` |
| `LOCAL_LLM_NAME` | 内网模型显示名 | `内网本地模型 (Unsloth)` |
| `VITE_WS_URL` | 前端 WebSocket URL（可选） | 自动推断 |

---

## 13. 测试体系

**配置文件**：`Hyper-Alpha-Arena/pytest.ini`

```ini
[pytest]
testpaths = tests
addopts = -v --tb=short --strict-markers
markers =
    unit: 单元测试（快速，无 DB/网络）
    integration: 集成测试（可能需要 DB）
    e2e: 端到端测试（全栈）
    slow: 慢速测试（> 10s）
```

**测试目录**：`Hyper-Alpha-Arena/tests/`

**前端测试**：

```bash
cd frontend
pnpm test          # 单次运行 vitest
pnpm test:watch    # 监听模式
```

**测试报告**：  
`COMPREHENSIVE_TEST_REPORT_2026-04-10.md`（根目录，包含全量测试结果快照）

---

## 附录：版本历史关键变更

| 版本 | 主要变更 |
|------|---------|
| P4 | 因子落地 + Prompt 进化修复（见 `decisions_p4.md`） |
| v0.5.0 | SystemCoordinator 注入 TDI，DRL/Kelly/PortfolioRisk 三层激活 |
| v0.7.0 (前端) | ATAS V2 可视化策略中心，28 种节点类型 |
| - | 数据库从 PostgreSQL 迁移至 SQLite（本地开发） |
| - | 删除 Binance 路由（Phase 1 迁移，Hyperliquid 为主力） |
| - | 全自动交易会话崩溃恢复机制 |

---

*文档由 Code Wiki 生成器根据仓库快照自动分析生成，如有遗漏请结合源码补充。*
