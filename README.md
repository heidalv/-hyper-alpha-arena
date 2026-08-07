# Heidalv-Alpha-Arena

Heidalv-Alpha-Arena 是一个加密货币永续合约 AI 全自动交易平台，包含 FastAPI 后端、React 前端、AI 决策日志、全自动策略循环、模拟交易、Hyperliquid/Binance 市场数据和风控模块。

## VIP 共用 AI 选币

VIP 专属特色：平台共用看板（短线 / 长线）+ 管理员 LLM 深度分析；VIP 手动采纳进自己的会话。  
**会话内「自动选币」开关同样仅 VIP/管理员可开**（非 VIP 会看到「需 VIP」）。  
**固定交易对（`symbols`）与 AI 选币（`auto_coin_symbols`）是两张独立表**：固定币不占 AI 选币槽位（槽位 5–10 只约束 AI 池）。  
运行中会话可在「AI策略 → 会话管理」直接改风险/回撤/并发/交易所/AI槽位并保存生效。  
**K 线图表**：支持多交易所切换、向左拖动补历史、MA/EMA/BOLL/RSI/MACD、约 2 秒刷新价格。  
说明见 [`docs/VIP共用AI选币说明.md`](docs/VIP共用AI选币说明.md)；页面：`frontend-next` → `/coin-select`。  
全面升级设计（共用 RankEngine / 闭环反馈 / 门控）：[`docs/AI选币全面升级设计_2026-08.md`](docs/AI选币全面升级设计_2026-08.md)。

## Windows 快速启动（2026-06-10 从 Mac 迁移后）

日常使用推荐：

| 文件 | 作用 |
|------|------|
| `DESKTOP.bat` | **推荐** 后端 :8000 + Electron 桌面窗（登录页，不依赖浏览器） |
| `dev-start.bat` | **推荐** 数据中心 + 后端 + **frontend-next :5273** |
| `QUICK.bat` | 后端 + frontend-next :5273，并打开浏览器（旧 Vite 已冻结） |
| `STOP.bat` | 双击停止所有服务 |
| `scripts\start-data-center.bat` | **独立数据中心**（K线/Ticker 采集，主服务重启也不停） |

**推荐运维拆分**：`dev-start.bat` 已默认「先数据中心后 API」。也可单独开 `scripts\start-data-center.bat`。说明见 [`docs/独立数据中心模块.md`](docs/独立数据中心模块.md)。健康检查：http://127.0.0.1:9100/health

桌面端细节见 [`QUICK_START.md`](QUICK_START.md) 与 [`frontend-next/README.md`](frontend-next/README.md)。

访问地址：

- 桌面端：双击 `DESKTOP.bat`（登录页）
- **正式前端**：http://127.0.0.1:5273/login（`frontend-next`）
- ~~旧 Vite 前端 :5173~~：**已冻结**（见 [`frontend/FROZEN.md`](frontend/FROZEN.md)，禁止再启动）
- 后端 API 文档：http://localhost:8000/docs
- 四所 K 线同步门禁：http://localhost:8000/api/monitor/kline-sync/gate
- 同步状态（catalog/心跳）：http://localhost:8000/api/monitor/kline-sync/status

运维说明：[`../docs/ops/四所K线同步运维手册.md`](../docs/ops/四所K线同步运维手册.md)（若从仓库根目录看则为 `001Alpha/docs/ops/`）。

## 数据中心唯一数据源（2026-08-04 统一收口）

### 目标
**项目内所有行情数据请求的唯一来源是数据中心（落库数据）**，禁止业务代码直连交易所 API。数据中心由独立进程采集（K线 P0/P1/P2、Ticker、市场流、衍生品），落库后业务层统一从 DB 读取。

### 开关
- `MARKET_DATA_DC_ONLY=true`（默认开）：所有行情读取只走数据中心落库数据，禁止兜底直连交易所。
- 关闭（`false`）用于应急排障，保留旧兜底逻辑。

### 本轮收口（DC_ONLY 守卫）
| 模块 | 收口点 |
|------|--------|
| `market_data.py` | `get_last_price` / `get_kline_data` / `get_ticker_data` / `get_market_status` / `get_all_symbols` 全部改为读 DB |
| `market_data_adapters/registry.py` | `get_klines` DC_ONLY 走 `data_center.get_klines` |
| `api/market_data_routes.py` | `/prices`、`/exchange-quotes`、`_fetch_exchange_rows` 全部改为 DB 聚合 |
| `api/market_intelligence_routes.py` | `/orderbook/{symbol}` DC_ONLY 走 DB 仓库 |
| `market_aggregation/*` | `collect()` DC_ONLY 跳过直连，改读数据中心仓库 |
| `market_price_service.py` | `LegacyRestPoller` / `get_price` DC_ONLY 走 `data_center.get_price` |
| `market_data_hub.py` | `_poll_stale_symbols` DC_ONLY 走 `data_center.get_price` |
| `strategy_coordinator.py` | `_get_realtime_price_robust` DC_ONLY 禁止 ccxt 兜底 |
| `paper_trading_engine.py` | `_get_current_price` DC_ONLY 走 `data_center.get_price` |
| `full_auto/data_health.py` | 价格缺失兜底 DC_ONLY 走 `data_center.get_price` |
| `full_auto/market_summary_helpers.py` | HL bulk ticker 兜底 DC_ONLY 禁止直连 |
| `auto_coin_selector.py` | `_fetch_market_snapshot` / `_build_hl_snapshot_cache` DC_ONLY 禁止直连 |
| `alpha/universe_manager.py` | `_step2_liquidity_gate` DC_ONLY 禁止 HL 直连 |
| `market_scanner.py` | 交易对列表 DC_ONLY 读 `symbol_catalog` |
| `strategic_analyst/new_coin_scanner.py` | HL 兜底 DC_ONLY 读 `symbol_catalog` |
| `ai_signal_generation_service.py` | `_tool_get_kline_context` DC_ONLY 读 DB |
| `backtest_performance_service.py` | `_get_klines` DC_ONLY 读 DB |
| `repositories/kline_repo.py` | 补齐缺失 K线 DC_ONLY 禁止直连 |
| `derivatives_analytics_service.py` | `_build_snapshot` DC_ONLY 只走本地落库 |

**非行情数据例外**（账户授权 `maxBuilderFee`、钱包交易等，数据中心不提供，保留直连）：
`account_routes.py` / `hyperliquid_routes.py` 中的 `maxBuilderFee` 查询。

### Asterdex API Key 说明
`.env` 支持 `ASTERDEX_API_KEY` / `ASTERDEX_API_SECRET`（当前为注释占位）。
**诚实说明**：Asterdex 官方限速基于 IP（约 2400 weight/min），**添加 API Key 不会提升公开 K 线抓取配额/速度**；API Key 启用的是私有数据流能力（用户数据 WS、账户查询等）。抓取提速靠的是回填深度与限速参数（见下）。

### 数据补齐（主流币 + 山寨币全周期）
根因与修复：
1. **symbol 上限 200 → 600**（`KLINE_DEPTH_BACKFILL_SYMBOL_LIMIT`）：asterdex 全 catalog（533 币）纳入深度回填。
2. **冷所跳过短周期 → 浅回填**（`kline_history_sync._cold_days`）：bybit/okx/hyperliquid 短周期（1m/3m/5m=15 天、15m/30m=45 天）补齐「主流币周期缺失」。
3. **1w 周期缺失**：`_depth_targets()` 补上 `1w: 520` 天。
4. **回填 ↔ 删除冲突**：`db_maintenance.py` 保留期改为配置化并对齐回填深度（`KLINE_RETENTION_DAYS_*`）。
5. **采集加速**：`ASTERDEX_BACKFILL_MAX_REQ_PER_MIN=300`、`KLINE_BACKFILL_REQUEST_INTERVAL_SEC=0.6`、P1 批次/并发提升、冷所并行回填。

**验证（2026-08-04）**：
- 主流币（BTC/ETH/SOL）：asterdex 1d 1798 天 / binance 1d 3274 天、4h 89 天、1h 210 天、15m/30m 30 天齐全。
- 山寨币：asterdex 521+ 币全周期（1h 210 天、1d 1798 天、4h 89.8 天）；binance 639 币（1d 3193 天）。
- 冷所短周期仍在回填中（心跳显示 1h 已完成，30m/15m/5m/3m/1m 按周期顺序排队）。

### Windows 迁移注意事项

1. **数据库使用本机 PostgreSQL 15**。Mac 的完整数据已于 2026-06-10 从 `postgres_backup/` 恢复（数据截至当晚 23:00）。连接账号 `db_admin / YOUR_DB_PASSWORD`，4 个库：alpha_arena / alpha_market / alpha_analytics / alpha_snapshots。`data/` 目录下的 SQLite 文件是 5/27 之前的旧数据，仅作备份保留。
2. **全自动交易会话已被手动暂停**（迁移时为安全起见）。如需恢复自动交易，在前端界面的全自动交易页面手动点击"恢复/Resume"。
3. 首次安装依赖：根目录运行 `pnpm install`，再 `cd frontend-next && npm install`，以及 `python -m uv sync --directory backend`（后端）。

## 快速启动

开发模式建议用根目录命令：

```bash
pnpm run dev
```

只启动后端：

```bash
pnpm run dev:backend
```

只启动前端（**frontend-next :5273**）：

```bash
pnpm run dev:frontend
```

后端默认端口是 `8000`，正式前端是 **`5273`**（Next.js）。旧 `frontend` Vite `:5173` 已冻结。

## V5 决策核心（2026-06-10 经济学深度重构）

针对「盈亏比倒挂 + 过度交易 + 手续费侵蚀」的根因，新增 `backend/services/decision_core/` 包，
成为所有开仓决策的统一门控入口（QAA 调度框架保留不动，decision_core 是它调用的决策大脑）。

### 核心改动

| 模块 | 改动 | 文件 |
|------|------|------|
| 统一门控 | 合并 7 处散落门控：日额度/盈亏比/费用/市场状态/scalp 门槛，日志标记 `[V5Gate]` | `decision_core/unified_gate.py` |
| 市场状态 | 趋势/震荡/极端三态：震荡收紧门槛（旧逻辑反向放宽）、极端禁开仓 | `decision_core/regime_agent.py` |
| 费用感知 | 往返成本/当日手续费/剩余额度注入所有 LLM 提示词（Master+Direction+Risk 共用） | `decision_core/fee_context.py` |
| 盈亏比硬约束 | **按 nature 拆分**：短线 Live RR≥1.4 / min_tp≥0.6%；中长线一体 RR≥1.8 / min_tp≥1.2%；MR 另有专用下限 | `config/settings.py` + `unified_gate.py` |
| 动态杠杆 | SizingAgent 重写：波动率+置信度连续映射 2-20x，AI 建议只能 ±30% 修正（消灭恒定 15x） | `position_sizing_agent.py` |
| 单笔风险硬顶 | 最大亏损 ≤ 权益 1.5%（最后一道闸） | `position_sizing_agent.py` |
| 频率治理 | **短线 / 中长线配额解耦**（`scalp_daily_cap` / `trend_daily_cap`，热改 `data/runtime_tuning.json`）；模拟盘刻意高配额攒样本（短线默认可达 150+/日）；中长线一体走 `trend_follow`，无独立 swing 日配额；单币 Paper 默认 12 | `runtime_tuning` + `.env` |
| 短线去重 | ExecutionGate regime 交 V5；MTF 默认只缩仓；short_tier 跳过二次置信度，Paper 同向冷却 30min | `scalp_*` / `short_tier_entry_gate.py` |
| QAA 旁路封禁 | 移除「信号全中性时硬造方向」的规则化捷径，全中性=不交易 | `full_auto_trading_service.py` |
| 反馈闭环 | 每日净扣费归因自动调门槛（写 `data/v5_runtime_gates.json`，60s 生效） | `decision_feedback_service.py` |
| 净值扣费看板 | 前端「分析 → 净值扣费」标签页 + `GET /api/analytics/net-performance` | `NetPerformancePanel.tsx` |
| LLM 算力重分配 | whale_tracker 15 分钟节流+大额预筛；coin_selector AI 审核 4h 一次；方向判定 max_tokens 翻倍 | `whale_tracker_service.py` 等 |
| DeepSeek 思考分层 | 短线关思考 / 中长线与主控 `high` / 架构演进等重任务 `max`；可用 `DEEPSEEK_THINKING_MODE` 覆盖 | `deepseek_thinking.py` |

### 一键回滚

`.env` 中改 `V5_DECISION_CORE_ENABLED=false` 并重启后端，Paper 模式所有 V5 门控直接放行，回到旧行为。
**2026-07-06 整改**：Live 模式下若该值为 `false`，启动时会直接抛出 `RuntimeError` 拒绝运行——总闸关闭
等价于对真实资金关闭全部风控，不允许静默发生，必须显式确认后果才能继续（详见
`docs/REMEDIATION_DESIGN_AND_EXECUTION_2026-07-06.md` §2.3）。
反馈闭环单独回滚：创建空文件 `data/v5_gates_rollback.flag`（保险丝，自动清空运行时门槛覆盖）。

### 验收检查（M5）

```bash
backend\.venv\Scripts\python.exe scripts\v5_acceptance_check.py --days 14
```

输出旧管线 vs V5 频率治理的离线回放对比 + 验收线（fee/gross ≤10%、均亏 ≤ 均盈、
日交易对照 **runtime tier 配额**（短线+中长线之和，模拟盘高配额不算 FAIL）、
最大单笔亏损 ≤1.5% 权益）。**验收要求固定配置跑 2 周不调参后质量项 PASS；笔数项按当前配额解读。**

V5 上线前历史基线（近 14 天）：日交易最多 19 笔、最大单笔亏损 5.86% 权益、均亏>均盈 — 质量项 FAIL，
正是 V5 要根治的问题；上线后用同一脚本复查。

> **2026-08-02**：模拟盘日开仓数量为样本刻意放大，勿再把「日交易 ≤10」当作硬失败标准；
> 生效配额键仅为 `scalp_daily_cap` / `trend_daily_cap`（旧键 `daily_cap_scalp` 等无效）。

### 中长线架构升级设计（2026-08-02）

中长线「分析有、成交少 / 总控空转」问题的完整调研与升级方案见：

[`docs/MIDLONG_V2_ARCHITECTURE_DESIGN_2026-08-02.md`](docs/MIDLONG_V2_ARCHITECTURE_DESIGN_2026-08-02.md)

**Phase 0–4 已落地（默认 `MIDLONG_EXEC_AUTHORITY=trend`）**：Single Writer + Hub/Regime + Master 减负 + LLM 分桶；Phase4 概念信念闭环（失败 Intent → `midlong_beliefs.json` → Trend/MLTO prompt + 有界 OWM/门槛）。详见设计文档。改配置后需**重启后端**生效。

**Phase 5 持仓管理模式已落地（2026-08-04）**：开仓后分析大脑自动从「入场分析」切换到「持仓发展分析」（模式 B），围绕已持仓交易对做六维发展分析——① 方向延续（`trend_agent.review_position`）② 滚仓（`evaluate_pyramid` + 5 层门控）③ TP/SL 调整 ④ DCA（默认禁止）⑤ 分批止盈（`long_tier_staged_tp`）⑥ 反转/无进展离场（`evaluate_midlong_exit` / `evaluate_no_progress_exit`）。执行复用 `paper_engine`，不新建平仓/加仓路径；同时解除 `master_execution` 中长线滚仓/补仓委托跳过断点（受 `trend_pyramid_gate` 5 层门控保护），`TIER_PYRAMID_PARAMS["long"]` 已启用。实现模块：`backend/services/full_auto/midlong_position_manager.py`；核心配置 `MIDLONG_POSITION_MGMT_ENABLED` / `MIDLONG_POSITION_MGMT_INTERVAL_SEC` / `MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC` / `MIDLONG_POSITION_MGMT_PYRAMID_ONLY_PROFIT`。审计与设计见 [`docs/MIDLONG_V2_AUDIT_REPORT_2026-08-04.md`](docs/MIDLONG_V2_AUDIT_REPORT_2026-08-04.md)。

**第二轮全链路/数据链路修复已落地（2026-08-04）**：对中长线完整链路 + 分析体系 + 数据链路做二次深调后修复 5 项问题：

1. **regime 恒判震荡（P1，高）**：`midlong_loop` 的 `{**market_summary, **fresh}` 整体替换丢失 `price_change_1h/24h_pct`、`volatility_pct` → 改为逐 symbol 深合并；`inject_midlong_indicators` 从 1h K 线补算 `price_change_*` 与 `volatility_pct`（含 `indicators_1d.atr` 增补）。**影响**：长线 regime 判定恢复真实，不再被误禁。
2. **24h 涨跌幅恒 0（P2，高）**：`unified_data_pool` 兼容读取 ticker 的 `percentage24h` 字段（原写错 `price_change_24h_pct`）。
3. **总控 5 处 NameError（P3，高）**：`master_execution` 模块级函数内 `getattr(self,...)` → 改读 `host`（新增 `MasterExecutionHost.last_unified_snapshot` / `last_orch_decisions_ts` / `scalp_traded_this_tick` / `training_allowed_symbols`）。**影响**：`STRICT_DATA_GATE` 数据就绪门恢复 fail-closed、orch 快照时间戳不再恒 0。
4. **LLM 失效被误读为做空（P4）**：`quant_layer` 对「未评估 thesis」（`review_count<=0` 且 `conviction==0`）映射中性 0.5，不再把 conviction=0 当极度看空。
5. **波动率量纲误判 extreme（P5）**：`regime_agent` 极端态判定必须由 `price_change` 佐证，年化波动率（0.6~2.0）不再误伤禁开。

另加固 `thesis_store.get_or_create` 并发原子性（RLock，防多入口重复建 thesis）。详见 [`docs/MIDLONG_V2_AUDIT_REPORT_2026-08-04.md`](docs/MIDLONG_V2_AUDIT_REPORT_2026-08-04.md) §九。

**P0-A LLM 空响应已根治（2026-08-04 重启后运行时验证）**：修复链 = ① `is_reasoning_model` 覆盖 `deepseek-v4`（flash 全系深度思考）→ 用 `max_completion_tokens` + 解析 `reasoning_content`；② LLM 调用不再继承行情代理（`.env` 的 Shadowsocks `127.0.0.1:1080` 对 DeepSeek SSE 长连接不稳定 → `SSL: UNEXPECTED_EOF_WHILE_READING`），默认**直连** `api.deepseek.com`（`LLM_HTTP_PROXY`/`LLM_HTTPS_PROXY` 显式配置才走代理，`httpx trust_env=False`）。运行时实测：TrendAgent 3/3 调用成功（`reasoning捞回 3357/16149/21502 chars`，`finish=stop`）、MasterController.synthesize 一次成功（116.6s）、中长线每 tick 产出真实 LLM 决策、safety cap 截断 0 次、LLM 侧连接中断 0 次。

**数据中心 asterdex 采集器已修复 + 429 限流已根治（2026-08-04）**：① **asterdex 市场流采集器 100% 失效修复**：URL 全部改为 `fapi.asterdex.com`（原 `api.asterdex.com` DNS 不可达）、显式注入 1080 代理（直连 TLS 被 RST）、`fetchMarkets` 只加载 linear 合约、`fapiPrivateGetPremiumIndex`→`fapiPublicGetPremiumIndex`、`binance_symbol` 计算修正，同步修复 `kline_collectors` 同步驱动与 `multi_venue_funding_collector` 的 asterdex URL。② **429 持续性限流根治**（曾 8 分钟 9735 条、P0 整轮归零、自激永不恢复）：新增进程级滑动窗口限速（`live` 桶 900 req/min + `backfill` 桶 150 req/min）+ **全局封禁开关**（任一组件命中 429 → 全链路 fail-fast 停手 90s，杜绝自激）+ `ExchangeRateLimitError` 异常上抛（P0/P1/P2/depth/market_flow 统一冷却）+ market_flow 接入全局封禁并降频（30s/30s/120s）+ P2 回填深度分级（1m/3m/5m 近 30 天、15m/30m 近 90 天、1h+ 400 天）。**实测**：15 分钟窗口 429 从 9735 降到 **6 条**，P0 成功率 89~100%，P0/P1 超时 0 次，asterdex K 线落库 358 万根（BTC 各周期齐全、最新到秒级）。详见 [`docs/DATA_CENTER_DEEP_AUDIT_2026-08-04.md`](docs/DATA_CENTER_DEEP_AUDIT_2026-08-04.md) §八。

## 三周期 Agent 与门禁全链路整改（2026-07-06）

对全链路（三周期 Agent 调度 + 统一门禁 `decision_core`）做的一次深度审查 + 终态整改，**不设兼容开关、不留观察期**——凡认定为 bug/fail-open/死代码的项目直接改为唯一正确行为。详见：

- 问题基线：`docs/AI_TRADING_PIPELINE_DEEP_AUDIT_2026-07-06.md`
- 整改设计与验收记录：`docs/REMEDIATION_DESIGN_AND_EXECUTION_2026-07-06.md`

### 本轮关键改动

| 模块 | 改动 |
|------|------|
| 三周期调度 | 修复独立调度空转/误标记 bug；主循环与独立循环共享状态（`_mlto_handled_keys` 等）改为加锁原子读写，消除竞态 |
| 频率硬约束 H1-H5 | `multi_timeframe_orchestrator.py` 接入 `_apply_frequency_constraints`（此前定义了却从未被调用），并修正 H1 语义错误（4h vs 15m，此前误用 1d/1w） |
| 约束→门禁闭环 | `strategy_coordinator` 判定的 `constraint_violated` 此前只写日志不拦截，现已接入 `unified_gate.evaluate_entry`，违反直接 block |
| 周期定义统一 | 新增 `backend/config/tier_timeframe_map.py` 作为全项目唯一 tier→timeframe 映射来源 |
| SMA 冒充 EMA | `strategy_coordinator.py` 多处 `np.mean(c4h[-20:])` 冒充 EMA 字段，已全部替换为真实 EMA 计算 |
| `review_flip` 接入 | 翻转确认前调用 `sub_position_manager.review_flip`（此前设计但从未接入） |
| Fail-open → Fail-closed | 编排器覆盖门控、Legacy 回退、因子否决层、short_tier、fee_context 等异常路径统一改为 Live 环境下 fail-closed |
| 单笔风险硬顶 | Scalp 分支此前绕过 1.5% 权益硬顶，现已接入统一的 `clamp_position_by_risk_cap` |
| 裸下单接入门禁 | `POST /api/paper/order` 现在也经过 `unified_gate`，不再是无门禁的旁路 |
| 短线熔断持久化 | 状态写入 `data/short_tier_circuit_state.json`，重启/多进程不再清零 |
| `V5_DECISION_CORE_ENABLED` 总闸 | Live 模式下若设为 `false`，启动即抛错拒绝运行，不再"一键放行却无感知" |

### 验收结果

`scripts/verify_three_cycle_strategy.py`（PASS=25/FAIL=0）、`scripts/verify_gap_closure.py`（FAIL=0）、`scripts/check_env_duplicates.py`（无重复键）均通过；相关单元测试全部通过。

## 周期方向概率引擎（2026-07-06 新增）

在三周期 Agent 之上补齐一块**可校准的方向概率**能力：用真实历史 K 线量化"每种技术状态下未来涨/跌/震荡的概率"，并自评校准质量。完整调研与实证见 [docs/CYCLE_DIRECTION_RESEARCH_2026-07-06.md](docs/CYCLE_DIRECTION_RESEARCH_2026-07-06.md)。

### 用途与文件

| 用途 | 文件 | 说明 |
|------|------|------|
| 参数敏感度实证 | `scripts/analyze_cycle_direction_sensitivity.py` | 从 `crypto_klines` 算 参数×周期 敏感度矩阵（IC/互信息/命中率lift），输出 `data/cycle_sensitivity/` |
| 概率引擎 | `backend/services/cycle_direction_probability.py` | 加权朴素贝叶斯：训练+推理+Brier/reliability 校准，模型落 `data/cycle_prob/prob_model_<tier>.json` |
| 证据/降幻觉 | `backend/services/agent_evidence_builder.py` | 注入 `cycle_prob_*` fact 作为 LLM 方向先验（含校准质量透明化） |
| 门禁 | `backend/services/decision_core/unified_gate.py` | 步骤 3.5 概率门禁（`CYCLE_PROB_GATE_ENABLED`，默认关，校准达标才硬拦截，Live 严/Paper 软/fail-open） |
| 冲突仲裁 | `multi_timeframe_orchestrator.py` + `strategy_coordinator.py` | 编排器三视图冲突投票 `_cycle_prob_arbitration` + 协调器 `conflicting` 分支 `_cycle_prob_tier_lean` 加深缩仓；H1-H5/constraint_violated 仍为硬上限 |
| 自适应 | `sync_calibration_to_governor()` | 校准质量作 RuntimeGovernor 最低优先级 source，调 `scalp_min_confidence` |

### 关键实证结论

- **短周期（1m/15m）是均值回归、长周期（4h/1d）才是趋势跟随**——短线顺势规则方向大概率是反的。
- **波动率 atr_pct 是跨周期最稳信号**（应作风险/状态门）；**ADX、量比对方向几乎无预测力**。
- 当前模型校准良好但边际优势弱（三态准确率约 40% vs 随机 33%），故所有下游默认"校准感知、谦虚保守"。

### 关键配置（`.env`，默认全关/安全）

```
CYCLE_PROB_GATE_ENABLED=false            # 概率门禁总开关
CYCLE_PROB_GATE_MIN_CALIBRATION=0.15     # 校准质量达标线，低于则只观察不拦截
CYCLE_PROB_GATE_MARGIN=0.08              # 反向概率-意图概率 ≥ 此值才判"明显反向"
CYCLE_PROB_GATE_PAPER_SIZE_MULT=0.5      # Paper 命中冲突时的缩仓系数
```

### 复现与验收

```bash
python scripts\analyze_cycle_direction_sensitivity.py --top-symbols 20   # 敏感度矩阵
python -m backend.services.cycle_direction_probability                    # 训练三周期模型
python scripts\verify_cycle_probability.py                                # 验收（PASS=26/FAIL=0）
python -m pytest tests/backend/unit/test_cycle_direction_probability.py -q # 单测（14 passed）
```

### 短期因子策略（Scalp）深度分析（2026-07-06）

对 `scalp_factor_router.py` + `factor_engine` 全链路与 2026 年主流量化因子实践做了逐维度比对，
详见 [docs/SCALP_FACTOR_STRATEGY_ANALYSIS_2026-07-06.md](docs/SCALP_FACTOR_STRATEGY_ANALYSIS_2026-07-06.md)。
结论：核心方法论（IC 加权线性聚合、多周期共振、分层风控、LLM 仅裁决边缘信号）与主流一致，
最大短板是**缺独立的 walk-forward 回测/成本建模基础设施**，任何因子/阈值调整目前只能靠实盘试错验证。

已按报告优先级落地两项改进：
1. **因子同类去重**：`factor_signal_generator.py` 新增 `FACTOR_CATEGORY_MAX_SHARE`（默认40%）上限，
   防止 top-15 聚合被单一类别（如动量类 RSI+MACD+Momentum+ROC）重复计权虚增置信度。
2. **`scripts/walk_forward_validate_scalp_factors.py`**：Scalp 因子滚动样本外验证脚本（含成本模型）。
   首次真实运行发现：momentum/ema_align/di_diff 等"趋势延续类"因子在 5m 数据上与未来30-60分钟收益
   呈**稳定负相关**，与本项目 `CYCLE_DIRECTION_RESEARCH` 报告"短周期偏均值回归"的结论一致，但因当前
   仅约7天历史、5个币种，样本量不足以支撑改动生产因子方向映射，已记录为待观察项（详见报告第4节）。

### 生产核验：中线/长线决策审计落库修复（2026-07-06）

**背景**：概率引擎/证据链上线后，抽查线上真实决策发现一个**独立于概率引擎本身**的既有问题——
`ai_decision_logs`（决策审计表）里 mid/long 的记录长期停留在"调度桩"占位文案
（如 `[中长线AI强制→SwingAgent LLM]`），看起来像是"中线/长线分析没有真正跑"。

**排查结论**：SwingAgent/TrendAgent 的真实 LLM 分析**其实每 tick 都在正常执行**
（应用日志可见真实的 deepseek 调用、~3000 字推理链、置信度/评分阈值判断），但独立调度循环
只在**决定开仓**时才把决策写入审计表；占绝大多数的"hold（无信号观望）"结果只发到会话事件流
（临时、不进审计表），导致审计表被另一条无关的调度占位逻辑的旧文案占据，**无法验证证据链
（含 cited_fact_ids / evidence_checklist / cycle_prob_\*）是否真正生效、也无法审计幻觉**。

**修复**：`full_auto_trading_service.py` 新增 `_persist_independent_scan_log`，
不论是否开仓，都把 SwingAgent/TrendAgent 每次真实分析（reasoning、cited_fact_ids、
evidence_checklist、fact_guard 结果）落库到 `ai_decision_logs`。纯旁路审计写入，
不参与任何交易/风控判断，失败不影响主流程。

**修复后核验**（`scripts/audit_midlong_evidence.py`，重启后实测样本）：

| 指标 | swing（中线） | trend_follow（长线） |
|------|------|------|
| agent_source / cited_fact_ids 覆盖率 | 100% | 100% |
| evidence_checklist 平均可用率 | ~88-90% | ~71-75% |
| cycle_prob_* 概率引擎事实可用率 | 100% | 100% |
| FactGuard 违规 | 1 例 `FG_MISSING_DATA`（shadow 模式仅记录未拦截） | 0 |

结论：证据链与概率引擎在生产环境**确实生效**；未发现系统性幻觉（仅个别引用了当时不可用的
指标，FactGuard 已按设计捕获）；trend_follow 证据可用率略低于 swing，主要是较高周期（4h/1d）
指标偶发滞后，非数据缺失问题，可作为后续优化方向持续观察。

```bash
python scripts\audit_midlong_evidence.py --hours 3   # 审计最近 N 小时中线/长线决策质量
```

## 学习进化系统（2026-06-11 修复升级）

针对「复盘零写入、进化停摆、假开关、死代码」四大断点做的一次收敛式升级：
**砍掉断裂和无效的分支，把所有学习产出收敛到一条已验证的下发通道（v5_runtime_gates）**。

### 升级后的闭环架构

```
平仓 → DecisionRetrospective 复盘落库 → decision_feedback_service
     → ① v5_runtime_gates.json（动态门槛，决策核心 60s 生效）
     → ② 提示词硬约束注入（净扣费教训 + 复盘教训）
离线进化（每3天，NSGA-II 多目标）→ 冠军参数 → v5_runtime_gates.json
交易结果 → Kelly 仓位聚合（30min）→ 仓位上限夹紧（保留不动）
```

### 修复的断点（2026-06-11）

| 断点 | 根因 | 修复 |
|------|------|------|
| 复盘 0 条 | Mac 时期 analytics 库无 `decision_retrospectives` 表，写入静默失败 | Windows 迁移后 `create_all` 自动建表；写入路径已冒烟验证 + 修会话泄漏 |
| GA 进化 5/21 停摆 | ① 3天 interval 任务首次触发要等进程跑满3天，频繁重启永远等不到 ② `weekly_evolution` 误用 Analytics 会话查主库表（PG 双库下 UndefinedTable） | ① 启动 60s 后检查超期自动补跑 ② 改用主库会话 |
| learning_bus 重进化失效 | 用自增 `id` 匹配字符串 `strategy_id` | 改 `AIStrategy.strategy_id` |
| `/learning/*` 接口报错 | 5 个接口引用未定义变量 `query` | 改内存排序 |
| `learning_enabled` 假字段 | 前端关闭学习不生效 | `process_outcome` 入口真实检查（60s 缓存） |

### 本次取舍（用户已确认）

- **DRL 已下线**：无训练模型、1722 条影子预测 `is_correct` 从未回填（只写不读）。
  关闭 `DRL_SHADOW_MODE` / `ENABLE_DRL_INTEGRATION`，协调器不再触发重训，前端面板移除。
  历史数据保留在 `drl_performance` 表。**回滚方式**：`.env` 中两个开关改回 `true` 重启。
- **Prompt 自动进化已禁用**：历史 36/36 次 LLM 改写提示词全部失败。
  新增总开关 `PROMPT_EVOLUTION_ENABLED`（默认 false）。教训提取/参数自适应/因子权重不受影响。
  **回滚方式**：`.env` 加 `PROMPT_EVOLUTION_ENABLED=true` 重启。
- **`ENABLE_EVOLUTION_FEEDBACK` 假开关废弃**：消费端 `adapt_params` 从未被主循环调用（死链路）。
  进化反哺统一改走 `v5_runtime_gates.json` 通道（`_sync_champion_to_v5_gates`，
  只派生盈亏比门槛且夹紧在 [V5_MIN_RISK_REWARD, 3.0]，不能放松 V5 硬约束）。

### 进化算法升级

`weekly_evolution`（每3天）从单目标 GA 切换为 **NSGA-II 多目标**，目标对齐 V5 经济学：
净利润因子（最大化）/ 最大回撤（最小化）/ 夏普（最大化）。
冠军取 Pareto 前沿折中解，落库晋升 + 同步 v5 gates。

### 闭环健康监测

- 后端：`GET /api/learning/health` — 返回 6 条闭环（复盘/进化/gates/Kelly/协调器/策略记忆）
  的最后活动时间与 ok/warn/dead 状态
- 前端：AI 学习中心「总览」顶部「学习闭环健康」卡片，60s 轮询，断了的闭环标红

## 套利中心（2026-06-11 修复升级 + S8 Stage 6）

一次针对引擎级逻辑错误、S1-S8 策略可行性、S8 积分规则过时、前端交互的整体升级。
核心代码在 `backend/services/rebate_arb/`，前端在 `frontend/app/components/arbitrage-hub/`。

### 架构

```
QAA tick / Paper 会话(90s) → ExecutionAuthority → RebateArbitrageEngine
  ├─ 扫描: tick_context(统一激励 schema + campaigns) → S2/S3/S4/S6/S7/S8.evaluate()
  ├─ 执行: 风控(risk_gate R1-R11) → 刷量规避 → 宏观过滤(V5 RegimeAgent, fail-closed)
  │        → 执行计划(pre_steps → side_a → hold → close_plan → post_steps)
  ├─ Paper: rebate_paper_simulator(真实费率/滑点) + arbitrage_paper_account(分账/流水)
  └─ 平仓: 绩效落库(RebatePerformanceLog/RebateTradeOutcome)
           → 学习闭环(S8/S3 → StrategyTrade(rebate_S8/S3) + learning_bus)
健康检查: GET /api/learning/health 新增 rebate_arb 环路（最近 tick / 最近平仓 / 异常计数）
```

### 本次修复的引擎级错误（M1）

- 双腿策略 `side_b_size` 从未赋值 → 对冲腿不平仓、敞口统计失效（已修）
- S8 在 AI 中性信号时默认做多 → 改为默认 skip（可配 `neutral_direction_action: half`）
- QAA 路径 `auto_execute` 硬编码 True 绕过配置 → 统一从配置读取
- R5/R6 敞口单位不一致 → 统一按名义价值计量
- 宏观过滤异常时静默放行 → fail-closed（异常 skip 本轮）
- Live 开仓积分快照固定写 0 → 真实调用 `get_points_snapshot()`
- 时区错位（risk_gate / wash_trade_avoider / engine 统计）→ 全部 UTC aware

### S1-S8 处置结论（M4，已与用户确认）

| 策略 | 结论 | 处置 |
|------|------|------|
| S1 Maker返佣对冲 | 负 EV（月返佣 <$1 vs 成本 $40） | **已下线**（deprecated，工厂/注册表/前端移除） |
| S2 OKX VIP冲刺 | 需 1 万U 以上 | 保持关闭，数据管线已修通（volume_30d） |
| S3 HL积分挖矿 | 正 EV 但兑换无承诺 | **保留**：Season 3 费率 + HYPE 动态价 + 投机性折扣 0.5 |
| S4 交易竞赛 | campaigns 管线曾恒为空 | 保持关闭，管线已接通 |
| S5 费率+积分 | 数据结构 bug + 与 V3 重复 | **已下线** |
| S6 跨所费率差 | 负 EV 被 `>-5` 伪门槛掩盖 | **关闭** + is_viable 收紧为必须正 EV |
| S7 Binance Alpha | 规则已变、API 不可用 | 保持 monitor_only |
| S8 Asterdex | 主力 | **升级为 Stage 6 专用积分策略**（见下） |

资金子池：S1/S5/S6 配额清零，倾斜给 S8（主力）+ S3（次级）。

### S8 Stage 6 积分模型（M2/M3）

旧代码按「Taker 2x × 持仓 2x × USDF 20x = 80x」估分，已过时。现按官方 Stage 6 Convergence：

> 总积分 = (交易积分 + 持仓积分 + 资产积分 + 清算积分 + 盈亏积分) × 团队加成 + 推荐积分

- 新默认模式 `stage6_optimal`（配置 `rh_optimization_mode`，旧 safe/quick 仍可切回）：
  - **Maker 限价优先**（0% 费率 + Maker 流动性积分），90s 未成交回退 Taker（0.04%）
  - **动态持仓 2-8h**（持仓积分无上限；资金费为成本时缩短、为收益时延长）
  - **USDF 全仓**（pre_steps: `mint_usdf` + `ensure_cross_margin`，资产积分要求 cross）
  - **方向由 AI + V5 RegimeAgent 决定**（extreme 跳过 / ranging 降置信度），不做对冲刷分（官方惩罚）
  - symbol boost 从写死改为规则同步任务动态刷新（`symbol_boost_store`）
- `estimate_round_metrics()` 重写为净 EV 模型：积分估值（按类别、含投机性折扣）−
  Taker 费 − 滑点 − 资金费，输出 `net_ev_usd` + `stage6` 类别拆分
- 费率单一来源：YAML `fee_schedule`（USDT 永续 0% maker / 0.04% taker），Paper 模拟器同步

### 前端（M6）

- P0：事件列表增量合并不再清空闪烁；启动按钮强制「启动前检查」通过；两个积分 Tab
  汇总按策略组过滤（后端 `/points/summary` 新增 `by_strategy`）；顶栏状态灯拆为 V3|Rebate|Paper
- 信息架构：总览新增 Paper 摘要卡（权益/策略/tick/S8 进度直达）；删除未挂载死代码
  （DashboardTab/HistoryTab/ConfigTab）；主页面拆出 `RuleSyncPanel.tsx` + `useArbitrageHubData.ts`
  + `KpiTile.tsx`；消除会话双重轮询；无账户加「去创建」CTA；fetch 失败 toast
- S8 面板升级 Stage 6：积分类别拆分、单轮净 EV、Maker/Taker 占比、动态持仓说明

### 验收与回滚

- 端到端验收脚本：`backend\.venv\Scripts\python.exe scripts/verify_stage6_e2e.py`
  （开仓→持仓→平仓→绩效落库→学习闭环，16 项断言）
- 单测：`python -m pytest tests/backend/unit -k "rebate or arbitrage or s8 or macro"`
- **回滚 Stage 6 模式**：`backend/config/rebate_arb_config.yaml` 中 S8 的
  `rh_optimization_mode` 改回 `safe`（Taker 市价 + 65min 固定持仓）重启即可
- **恢复 S1/S5/S6**：YAML 对应策略 `enabled: true` 并去掉 `deprecated`，同时在
  `strategies/__init__.py` 的 `DEPRECATED_STRATEGIES` 中移除、`strategy_runtime_registry.py` 补回条目

## 套利中心深度升级（2026-07-06，Paper 优先 + 真实数据验证）

针对"方法和逻辑不通、看着活着实则空转"的深度重构。经运行时日志实锤了 6 个病灶并逐一修复，
把套利中心重构为"现代化 delta-neutral 资金费/刷积分系统"。全程 Paper（模拟盘）+ 真实数据，
不接真金白银、不下真实订单。

### 修复的病灶（前 5 个已被 `backend_restart.log` 运行时实锤）

- **病灶A（P0 引擎级）**：`ALL_STRATEGIES` 全库从未定义、`strategies/` 无 `__init__.py`，
  导致 `engine.py` 顶层 import 崩溃、每个 tick 被静默吞掉 → Rebate 实际完全空转。
  **修复**：新增 `strategies/__init__.py` 显式构建 `ALL_STRATEGIES` 注册表（S1/S5 下线不注册）。
- **病灶B（P0 业务级）**：主力 S8 刷的 Asterdex Stage 6 **已于 2026-03-29 结束**。
  **修复**：新增离线 `program_registry.py` 登记各项目生命周期；引擎扫描自检跳过非 active 项目；
  YAML 中 S8 配额清零并停用，配额转给活跃的 S3 + 新 SDN。
- **病灶C（P0 数据级）**：`IncentiveAggregator` 无 adapter（本环境无交易所客户端/密钥）→ 数据全空。
  **修复**：无 adapter 时降级从 `program_registry` 读离线费率/积分/程序状态兜底，不再全空。
- **病灶D（P1 数据级）**：`RuleChangeDetector` 网络到不了交易所、规则同步瘫痪。
  **架构决策**：以离线 `program_registry` 作**权威数据源**，实时抓取仅作"有网络时的可选刷新"。
- **病灶E（P1 自污染环）**：`proposal_auto_applier` 拿 `n=0` 空样本反复给死策略调参。
  **修复**：加最小样本门槛 `MIN_SAMPLE_N`（默认 10），空样本不调参；超最长观察期无成交则作废。
- **病灶F（P1 方法级）**：资金费套利单腿裸方向、非 delta-neutral、非原子。
  **修复**：新增 delta-neutral 策略 + Paper 双腿准原子执行器（见下）。

### 新增模块与能力

- `program_registry.py`：积分项目生命周期 + 费率 + 积分规则的**离线权威注册表**
  （HL Season2=active、Aster Stage6=ended、Backpack/Paradex/Lighter/Pacifica/Extended=active…）。
- `funding_rate_matrix.py`：**多场所资金费率矩阵扫描**，为每个 symbol 选最优 delta-neutral
  多空腿组合（资金费最低处做多、最高处做空），产出毛资金费 APR / 手续费拖累 / 保本天数 /
  指定持有期净 APR，并优先让长腿落在 active 积分场所。
- `points_valuation.py`：**诚实积分估值**（FDV × 空投占比 / 总积分 × 折现率，三档；缺数据即
  "不可估"拒绝拍脑袋）与扣成本净 EV 合成。
- `strategies/s_delta_neutral_points.py`（策略 **SDN**，2026 主流刷分范式）：active 积分 DEX 开多 +
  深场所开等额空，赚资金费价差 + 白拿积分、方向中性。无资金费数据时自判 not viable（不臆造机会）。
- `paper_delta_neutral_executor.py`：**Paper 双腿准原子执行**——长腿成→空腿成；空腿失败**回滚**
  长腿（不留裸敞口）；成交后算 **delta 漂移**（超 2% 告警）与**完整成本模型**（开+平两腿手续费+滑点+资金费）。
- `arb_switches.py`：**统一套利开关语义**（单一事实来源），厘清 V3 统计套利（`FUNDING_ARB_ENABLED`
  且会话 `arb_enabled`）与 Rebate/delta-neutral（Paper 恒可扫描、`auto_execute` 才自动开仓），
  根治"开了套利但 V3 不动"的困惑。

### 新增接口 / 前端

- `GET /api/rebate/programs`：返回各积分项目生命周期与状态（前端置灰 status≠active 的死项目）。
- `GET /api/rebate/arb-switches`：返回两条套利链路的开关状态与语义说明。
- 前端 `ArbitrageProgramsPanel.tsx`：在"套利积分"Tab 展示项目状态卡，死项目自动置灰。

### 验证与验收

- **历史回放**：`python scripts/replay_funding_arb_validate.py`——用 `perp_funding` 真实历史资金费
  回放验证资金费捕获腿扣成本后是否正 EV。**诚实结论**：本环境 `perp_funding` 仅有 Hyperliquid 单场所
  数据，故只能验证"资金费腿"（约平均毛 6.8% APR，扣两腿 taker 费后单场所大多为负，仅少数 symbol 为正）；
  真正的**跨所价差是额外 upside**，需先补第二场所资金费历史才能完整回放（Phase 5 数据接入）。
- **验收脚本**：`python scripts/verify_arbitrage_center.py`（19 项 PASS/FAIL 端到端自检）。
- **单测**：`python -m pytest tests/backend/unit/test_arbitrage_center_upgrade.py`（17 项，覆盖注册表/
  生命周期/资金费矩阵/积分估值/双腿回滚/开关）。

### 分阶段状态

Phase 0（止血：引擎可加载 + 停刷死项目 + 掐断自污染）→ Phase 1（多场所资金费矩阵 + 诚实积分估值）
→ Phase 2（delta-neutral 刷积分策略 SDN）→ Phase 3（Paper 双腿准原子执行 + 回滚 + 统一开关）
→ Phase 4（历史回放 + 前端展示 + 验收/单测）**均已完成**；Phase 5（实盘预备）仅留桩、默认硬关闭、本次不实施。

### 端到端打通（2026-07-06 继续完善）

上一轮把各模块建好后，本轮把 delta-neutral 刷分**真正端到端接通**（此前 SDN 在生产路径拿不到数据、
双腿失败会留裸腿、积分价值恒为 0）：

- **真实资金费数据源** `funding_rate_provider.py`：从 `perp_funding` 表读**每场所每 symbol 的最新
  资金费快照**，产出 `{exchange:{symbol:rate}}`（SDN/矩阵真正需要的形状）。离线可用、随新场所数据入库
  自动扩展。当前仅 Hyperliquid 单场所 → `has_multi_venue_coverage=False`，SDN 据此诚实判 not viable。
- **扫描注入** `engine.scan_all_strategies` → `_inject_funding_matrix`：把资金费矩阵注入 `incentive_data
  ['funding_rates']`。传入已是嵌套 `{exchange:{symbol:rate}}` 则直通；否则（空/扁平）回落到上面的真实
  provider。浅拷贝、不污染调用方，异常绝不中断扫描。**修复"SDN 在生产恒拿不到资金费数据"**。
- **双腿准原子回滚**（真实引擎路径）`engine._paper_execute`：长腿已成交、对冲腿因无行情失败时，
  **立即反向平仓回滚长腿**并把回滚手续费计入 `paper_cost_summary`，返回 `rolled_back=True`。
  **修复"Paper 双腿套利可能留下单向裸敷口且成本不诚实"**（对所有两腿计划生效，不止 SDN）。
- **积分入 EV 的诚实闭环**：`PointsProgram` 新增可选估值参数（`expected_fdv_usd` / `total_points_estimate`
  / `airdrop_supply_pct` / `points_per_1k_usd_per_day`，默认全 None）；`points_valuation.value_points_for_program`
  按 `名义 × 持有天数 × 累积速率` 估我方积分数，再 FDV 折现估值，SDN 净 EV 据此计入积分价值。
  **未填齐参数即诚实降级为"不可估、积分价值 0"**——坚持宁可低估、绝不臆造。真实项目当前默认不填数字，
  待维护者据公开信息补齐后自动生效。
- **单测**：`python -m pytest tests/backend/unit/test_arbitrage_center_polish.py`（14 项，覆盖 provider/
  矩阵注入/双腿回滚/积分入 EV/资金费累计/combo 透传/平仓含资金费），叠加原 17 项共 **31 项全绿**；验收脚本仍 19/19。

### 执行侧打通（同轮补齐）

让 SDN 从"能评估"到"执行/结算也对"：

- **真实组合透传到执行计划** `engine.execute_strategy`：SDN 分支把 `evaluate` 选定的最优 `combo`
  （真实 symbol + 长/空场所）传入 `build_execution_plan`，**修掉"执行退回占位场所(长 hyperliquid/
  空 binance)、下到错误腿"** 的隐患。`build_execution_plan` 同时把 `funding_meta`
  （net/long/short 每日资金费）写进计划、随 `position.metadata` 落库。
- **持仓期资金费盈亏累计**（delta-neutral 的经济核心）`engine._paper_close_execute`：delta-neutral
  两腿价格波动相互抵消，原平仓 PnL 只含手续费/返佣，**漏了资金费价差这一真正收益来源**。现在按
  `funding_rate_provider.hold_funding_pnl(net_funding_per_day, 名义, 持仓秒数)` 累计并计入 `current_pnl`
  与 `paper_close_summary.funding_pnl`。单测验证：持仓 7 天 × 0.1%/日 × $10000 名义 ≈ **+$70** 资金费收益
  被正确计入（否则 SDN 纸面永远看似只亏手续费）。

### 第二场所资金费采集管道（Phase 5 数据接入·选 A）

补齐 delta-neutral 的**第二条腿数据源**——此前 `perp_funding` 只有 hyperliquid 单场所，SDN 永远凑不齐双腿。

- **新增采集器** `backend/services/multi_venue_funding_collector.py`：用**公共只读** ccxt 客户端
  （`fetch_funding_rates`，无需 API key）轮询 Binance/Bybit/OKX/Gate.io/Asterdex 的资金费，归一为与
  hyperliquid 一致的**基础符号**（如 "BTC"）后写入 `perp_funding`，天然可与 hyperliquid 数据配对。
  - **Windows 关键修复**：ccxt/aiohttp 依赖 aiodns，在 Windows 默认 ProactorEventLoop 下会抛
    `aiodns needs a SelectorEventLoop`——采集器在独立线程用 `SelectorEventLoop` 运行抓取，规避该问题、
    有网即生效。
  - **诚实原则**：无外网/被墙时各场所返回 {} → **优雅空转、绝不写虚构费率**；默认关闭
    （`MULTI_VENUE_FUNDING_COLLECTOR_ENABLED=false`），需运维在有网环境显式开启。
  - **手动采集**：`python -m backend.services.multi_venue_funding_collector --once --symbols BTC,ETH`
- **调度接入**：`scheduler.start_multi_venue_funding_collector()`（默认每 300s，开关关时只打印提示不跑），
  已在 `startup.py` 挂载。
- **实测已跑通**：本环境实际拉到 **Gate.io 真实资金费**并写入 `perp_funding`，BTC/ETH 遂具备
  **hyperliquid + gateio 双场所**覆盖。SDN 据此产出**真实 delta-neutral 组合**
  （如 long gateio / short hyperliquid ETH，毛资金费 APR ≈ 8%），并**诚实判定 7 天持有期 not viable**
  （保本需 8.56 天，一次性手续费未摊平）——真实数据、真实经济、零臆造。
- **单测**：`test_arbitrage_center_polish.py` 现 **19 项**（新增符号归一/过滤/写入幂等/离线不造数/有数写入），
  叠加原 17 项共 **36 项全绿**；验收脚本仍 19/19。

### 采集扩场所 + SDN 自适应持有期 + 前端矩阵（2026-07-06 继续完善·A/B/C）

在上面管道基础上，本轮把「更多场所可配对 / 边界组合可行 / 结果可视」三件事补齐：

- **A) 逐 symbol 兜底 + 并发抓取，扩大成功场所**
  - `ccxt_base_adapter.get_funding_rate(symbol)`：对**单个统一符号**（如 "BTC/USDT:USDT"）显式取 linear
    永续资金费。**修复 OKX 不支持批量 `fetchFundingRates`、Asterdex 批量踩 binance `dapiPublic` 端点歧义**
    ——批量失败即逐 symbol 兜底，稳定拿到 linear 永续费率。
  - `multi_venue_funding_collector`：各场所**并发**抓取且**每场所独立超时**（`VENUE_TIMEOUT=30s`，
    内部 `BULK 10s + 4×PER_SYMBOL 4s`），**慢/被墙场所不再拖垮 Gate.io 等正常场所**（此前串行会整体超时、
    丢掉全部结果）。时间预算按 `BULK + N×PER_SYMBOL < VENUE` 设计，使场所协程自然跑完、`finally` 干净关闭
    客户端，**消除 OKX "Unclosed client session" 告警**。
- **B) SDN 按保本天数自适应持有期**（`s_delta_neutral_points._adaptive_horizon`）
  - 正 carry 但 `breakeven_days` 超过默认 7 天窗口时，**把持有期延长到 `breakeven × 1.5`（封顶
    `MAX_HORIZON_DAYS=21`）**，摊平一次性手续费、抬高净 EV；负 carry 或保本 < 默认窗口则维持 7 天。
  - 采用的持有期写回 `combo.effective_horizon_days`，`build_execution_plan` 的 `hold_phase` 随之延长；
    `evaluate` 的 `details` 暴露 `horizon_days / default_horizon_days / horizon_adaptive / breakeven_days`。
  - 仍受 `MIN_NET_APR=5%` 闸门约束：如 8% 毛年化、8.56 天保本的组合自适应到 12.84 天后净 EV 转正但约
    2.7%，**低于阈值仍诚实判 not viable**——延长只在数学上真正划算时才让组合过关。
- **C) 实时资金费矩阵 + 净 EV 前端面板**
  - 后端 `GET /api/rebate/funding-matrix?horizon_days=&use_taker=&min_net_apr=`：基于 `funding_rate_provider`
    实时快照产出 `venues / matrix（每 symbol×场所费率）/ combos（每 symbol 最优 delta-neutral 组合，按净年化降序）`；
    **无≥2 场所覆盖时 `multi_venue=false`、combos 为空，绝不臆造**。
  - 前端 `FundingMatrixPanel.tsx`（挂在「套利积分」Tab）：净 EV 机会表（长→空腿、毛/净年化、保本天数、
    手续费拖累、积分标记）+ 资金费矩阵表（symbol×场所），支持 7/14/21 天持有期切换与刷新；数据不足时
    明确提示「不足 2 场所、无法凑双腿」。
- **实测**：接口对当前 `perp_funding`（hyperliquid + gateio）返回 8 symbol、2 组合（含 ETH long gateio /
  short hyperliquid，毛 APR≈8.1%、保本 8.56 天）；网络受限时采集器 ~14–20s 内**有界完成、优雅空转**。
- **单测**：`test_arbitrage_center_polish.py` 现 **21 项**（新增自适应持有期纯函数 + evaluate 延长并抬高净 EV），
  叠加原 17 项共 **38 项全绿**；验收脚本仍 19/19。

### 可观测性与前端补强（同轮·续）

- **采集器逐场所结构化摘要**：`collect_once` 返回 `venue_report`，每个场所一条
  `{status, count, elapsed_ms, via}`：`status ∈ ok/empty/error/cancelled/timeout/unknown`、
  `via ∈ bulk/per_symbol/mixed`。日志单行输出如 `gateio=ok(2,10784ms,per_symbol) okx=cancelled(...)`，
  运维在有网环境一眼看清**哪些场所通了、走批量还是逐 symbol、耗时多少、被墙/超时**。实测已捕到
  `gateio=ok via=per_symbol`——**验证 A 的逐 symbol 兜底在网络可达时真实生效**。
- **/funding-matrix 叠加 SDN 可行性**：每个 combo 追加 `sdn_horizon_days / sdn_horizon_adaptive /
  sdn_net_apr / sdn_viable / sdn_min_net_apr`——即按 SDN 自适应持有期（保本期>默认窗口则延长、封顶）
  重算净年化并给出是否达可行阈值。前端表格新增「SDN持有/净APR」「SDN可行」两列（`*`=已延长、
  绿标=可行）。诚实呈现：如 ETH 保本 10 天→SDN 延到 15 天、净年化转正 +2.3% 但未达 5% 阈值 → 标「不可行」。
- **前端自动刷新**：`FundingMatrixPanel` 每 30s 自动轮询（页面隐藏暂停、重新可见立即补拉），带手动开关与刷新按钮。
- **采集健康度透出（状态接口 + 前端）**：`GET /api/rebate/funding-collector/status` 透出采集器运行配置
  （`enabled / interval_seconds / alert_threshold`）与**最近一轮内存快照**（`venue_report / rows_written /
  symbols_covered / as_of_iso / consecutive_failures / alerted_venues`）；从未采集过则 `has_report=false`、
  **绝不臆造状态**。`FundingMatrixPanel` 顶部新增「采集器」健康条：每场所一枚状态胶囊（绿=正常并显示 symbol 数、
  灰=无匹配、红/黄=错误/超时/取消），hover 显示 `状态/symbol数/耗时/方式/错误/连续失败轮数`，告警态场所前带 ⚠️。
- **连续失败告警**：某场所连续 `MULTI_VENUE_FUNDING_ALERT_THRESHOLD`（默认 3、0=关闭）轮采集失败
  （`error/cancelled/timeout`；`empty`=连通无匹配 symbol **不计为故障**）即经 `FeishuNotifier` 飞书告警**一次**
  （避免每轮刷屏），恢复后自动复位并发「已恢复」通知。未配置通知渠道时静默降级、**绝不阻塞采集主流程**。
- **单测**：`test_arbitrage_center_polish.py` 现 **26 项**（新增连续失败告警阈值/复位、empty 不计故障、状态接口形状）。

### 顺带修复：S8 paper_mode 未透传导致持仓时长误判（同轮）

- **Bug**：`S8.build_execution_plan(paper_mode=True)` 明确要求按 Paper 处理，但 `_normalize_mode` /
  `_learning_gate` 忽略该入参、改查**全局** `capital_coordinator.is_paper_mode()`。当全局非 paper 态时，
  显式 paper 计划会走**实盘学习门禁**、因 `recovery_mode` 被降级为 `paper_experiment`，持仓时长从资金费
  方向应给的 2h/8h 变成实验档 900s——与代码注释「Paper 保持 stage6_optimal」的设计意图相悖。
- **修复**：新增 `_effective_paper(paper_mode)`（显式入参优先、未传回退全局），`_normalize_mode`/
  `_learning_gate` 接受并透传该入参，`_build_optimizer_decision` / `build_execution_plan` 传入本次
  `paper_mode`。**实盘行为不变**（不传时仍按全局协调器、保留负 EV 降级）。
- **测试对齐**：`test_s8_stage6_dynamic_hold_follows_funding_direction` 恢复正确（成本→2h、收益→8h、
  中性→4h）；并把过期的 `test_s8_close_dispatches_learning_outcome` 更新为「L2 收敛」后语义（校验
  `unified_learning.process_outcome` 被调用，不再期待引擎直接 `learning_bus.dispatch`）。相关 S8/宏观
  单测 **全绿**。

## AI 全自动策略运行逻辑

全自动策略的核心后台循环在 `backend/services/full_auto_trading_service.py`。

主要流程：

1. 启动后恢复数据库中 `running`、`defensive`、非手动暂停的全自动会话。
2. 注册 `fullauto_unified_<session_id>` 统一循环，默认每 90 秒调度一次。
3. 每个 tick 会做持仓保护、市场分析、AI 决策、止盈止损调整、策略健康检查。
4. AI 决策会写入 `ai_decision_logs`，前端的“AI 决策日志”从这里读取。

### AI 策略五层流水线（2026-06 重构）

标准链路已冻结为：

`Direction → PositionSizingAgent → RiskGate → Execution → DecisionRetrospective → PromptFeedback`

| 模块 | 文件 | 职责 |
|------|------|------|
| 入口审计 | `ai_agent_entrypoint_map.py` | 梳理所有决策入口，检测 sizing 旁路 |
| 分层提示词 | `ai_prompt_layers.py` | 方向/持仓/风控/仓位四层 prompt + 证据评分 |
| 仓位规划 | `position_sizing_agent.py` | **唯一** sizing 源（杠杆、仓位比例） |
| 职责契约 | `agent_pipeline_contracts.py` | 各 Agent 允许/禁止行为 |
| 盈亏归因 | `trade_performance_analyzer.py` | 按 close_reason/tier/nature/symbol 统计 |
| 反馈闭环 | `decision_feedback_service.py` | 复盘→下轮 prompt 硬约束 + 每日日报 |

完整审查报告：`docs/AI_AGENT_SYSTEM_REFACTOR_REPORT.md`

**Phase 3–5 已落地：**
- TradePlanner sizing 逻辑已并入 `position_sizing_agent.py`
- `short_tier_entry_gate.py` 对 short/scalp 提高门槛 + 连续同向冷却
- 复盘写入修复：`DecisionRetrospective` → `alpha_analytics.db`
- 每日日报 + 离线回放：`decision_feedback_service` / `strategy_offline_replay.py`
- DualAgent 默认 `primary`（模拟盘直接走 DirectionAgent + TradeRiskAgent，无需 shadow 对比）

手动运行盈亏归因：

```bash
cd backend && PYTHONPATH=.. python3 -m services.trade_performance_analyzer ../data/alpha_arena.db
```

离线回放对比：

```bash
cd backend && PYTHONPATH=.. python3 -m services.strategy_offline_replay ../data/alpha_arena.db
```

## 本地开发注意事项

后端使用 `uvicorn --reload` 时，只应该监听后端 Python 文件。不要让后端运行时自动构建前端，因为前端构建会写入 `backend/static`，可能触发后端反复 reload，导致 AI 策略循环无法稳定执行。

当前默认行为：

- `backend/main.py` 默认关闭前端文件 watcher。
- 如确实需要后端自动构建前端，可手动设置 `FRONTEND_AUTO_BUILD=true`。
- `pnpm run dev:backend` 已排除 `backend/static`、`backend/data`、`logs`、`*.log`、`*.lock`。

## AI 策略卡死排查

如果前端一直只看到旧的 AI 决策日志，或日志里显示 `24h AI 决策日志: 0 条`，优先检查：

1. `logs/backend.log` 是否每几秒反复出现 `Application startup complete`、`全自动交易服务初始化完成`。
2. 是否有多个后端启动方式同时运行，例如 `pnpm run dev`、启动器、手动 `uvicorn`。
3. `logs/backend.error.log` 是否出现 SQLAlchemy 并发错误，例如 `concurrent operations are not permitted`。
4. 是否出现重复注册 `fullauto_unified_<session_id>`。

本次修复已增加两层保护：

- 只有拿到调度器文件锁的 worker 会恢复全自动后台任务。
- 同一个全自动 session 的统一循环增加跨进程文件锁，多个进程同时存在时也只允许一个 AI tick 执行。

## Mid/Long Agent 升级（2026-06-27，Phase 0–4 已落地）

针对 SwingAgent（中线）与 TrendAgent（长线）的系统性升级：修 P0 bug、证据链防幻觉、
Hermes 自进化闭环、Prompt 外迁、绩效归因看板。设计文档见
`docs/MID_LONG_AGENT_UPGRADE_DESIGN_2026-06-27.md`。

### 架构一览

```
市场数据 → agent_evidence_builder（L1 证据清单）
         → PromptRegistry task_swing / task_trend（L2 推理 prompt）
         → LLM → cited_fact_ids + agent_fact_guard（L3 校验，shadow/enforce）
         → 现有门控链（DCP / V5 / crypto_alpha）
开仓 → TrendPredictionRecord 落库（scenario A/B/C）
平仓 → agent_decision_wisdom（Hermes）+ unified_learning
看板 → GET /api/analytics/by-agent
进化 → Hermes L2 A/B（task_swing_agent / task_trend_agent_direction）
```

### 关键配置（`.env`）

| 变量 | 默认 | 说明 |
|------|------|------|
| `AGENT_FACT_GUARD_MODE` | `shadow` | `off` / `shadow` / `enforce`；建议 shadow 满 7 天再切 enforce |

### 前端入口

| 位置 | 内容 |
|------|------|
| **Analytics → Mid/Long Agent** | Swing/Trend 净盈亏、PF、平均持仓、Scenario 命中率 |
| **Analytics → 净值扣费** | 全系统 net-performance（含 by_nature） |
| **OpenCode → Hermes 进化 → L2** | Agent Prompt A/B 与版本列表 |

### API

```http
GET /api/analytics/by-agent?days=30&nature=swing|trend_follow
```

### 一键验收

```bash
backend\.venv\Scripts\python.exe scripts/mid_long_agent_acceptance_check.py --days 30
```

检查：模块导入、Prompt Registry、Hermes 表/基线、by-agent 通路、盈利线（swing PF≥1.5、trend PF≥2.0，样本足才判 FAIL）。

### 相关单测

```bash
python -m pytest tests/backend/unit/test_prompt_registry_agent_tasks.py ^
  tests/backend/unit/test_agent_analytics_service.py ^
  tests/backend/unit/test_hermes_l2_agent_tasks.py ^
  tests/backend/unit/test_agent_fact_guard.py ^
  tests/backend/unit/test_trend_prediction_service.py ^
  tests/backend/unit/test_hermes_agent_wisdom.py ^
  tests/backend/unit/test_trade_memory_nature_filter.py ^
  tests/backend/unit/test_trend_agent.py -q
```

### 验收目标（设计文档第 10 章）

| 类别 | 目标 |
|------|------|
| LLM 调用 | 每 symbol 每 tick，swing/trend 各 ≤1 次 |
| 盈利 | 30 日 swing PF ≥ 1.5；trend_follow PF ≥ 2.0 |
| 审计 | shadow 模式下决策含 cited_facts |
| 进化 | 7 日内 Hermes ≥20 条 swing/trend wisdom；L2 至少 1 次 Agent A/B |

## 关键日志

- `logs/backend.log`：后端主日志、启动流程、全自动策略循环。
- `logs/backend.error.log`：告警和错误日志。
- `logs/frontend_dev.log`：前端开发服务日志。

## 常用验证

检查后端语法：

```bash
cd backend
uv run python -m py_compile main.py services/startup.py services/full_auto_trading_service.py start_server.py
```

检查最近 AI 决策：

```bash
rg "AI决策|AI 决策|fullauto_unified|24h AI 决策日志|concurrent operations" logs
```

## FullAuto monolith 拆分进度

- **monolith 行数**：`backend/services/full_auto_trading_service.py` ≈ **3753**（非空行；Host + thin shim；公开 API 与少量收尾辅助仍留在 monolith，尚未 100% 拆完）
- **`backend/services/full_auto/` 已拆出 47 个模块**（含 `__init__.py`/`state.py`/`orchestrator.py` 等公共件）。本轮新增（在此前 `paper_session_helpers` / `orch_background` / `decision_sizing` / `tp_sl_gates` / `live_trading` / `midlong_helpers` / `data_health` 基础上继续拆分）：
  - `analyst_system_cycle.py` / `qaa_v3_tick_cycle.py` / `qaa_legacy_cycle.py`
  - `proposal_execution.py` / `mlto_cycle.py` / `quick_orchestrator_eval.py`
  - `hold_timeout_trend_review.py` / `light_trading_cycle.py` / `v3_factor_pipeline.py`
  - `strategy_lifecycle.py` / `refresh_positions.py` / `strategy_creation.py` / `symbol_risk.py`
- **验收**：

```bash
python -m py_compile backend/services/full_auto_trading_service.py backend/services/full_auto/*.py
python -m pytest tests/backend/unit/test_full_auto_batch_extract.py tests/backend/unit/test_create_risk_refresh_extract.py tests/backend/unit/test_light_v3_lifecycle_extract.py tests/backend/unit/test_mlto_quick_hold_extract.py tests/backend/unit/test_full_auto_loop_c2_golden.py -q
```

> 全量回归见下方「全量测试」章节；已知与本次拆分无关的历史失败：`test_opencode_layer.py::test_minor_routes_through_review_not_direct_apply`、`test_scalp_factor_router.py::test_short_signal`。
