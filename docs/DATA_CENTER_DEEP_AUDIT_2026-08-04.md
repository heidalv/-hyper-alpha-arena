# 数据中心深度调查报告

> 调查日期：2026-08-04
> 调查对象：独立数据中心进程（`backend/workers/market_data_center.py`）+ 行情数据全链路
> 状态：**调查完成；A/B（asterdex 采集器）已修复验证 ✅，C/D 待修复 ⚠️，E/F/G 评估中；429 限流已根治（9735→6 条/15min，P0 成功率 89~100%）**

---

## 一、架构总览

### 1.1 双进程架构

```
┌─────────────────────────────────────────────────────────────────┐
│ 独立数据中心进程 market_data_center (PID 11828, 健康端口 9100)      │
│  ─ 启动 7 个采集组件（见 §二）                                      │
│  ─ K线落库 alpha_market.crypto_klines                              │
└─────────────────────────────────────────────────────────────────┘
            ↑ 主服务 DATA_CENTER_MODE=standalone → 跳过内嵌采集
┌─────────────────────────────────────────────────────────────────┐
│ 主 FastAPI 进程 (PID 32900/36660, :8000)                          │
│  ─ 旧 market_flow_collector（Hyperliquid WS）★ 实际供数者         │
│  ─ MarketDataV2Scheduler + ingest_queue（K线 shadow）★ SSL EOF重灾 │
│  ─ derivatives_analytics_service（L1本地+L2 HL+L3 Binance+L4 Coin) │
│  ─ llm_config_service（DeepSeek 直连）✅ 已修复                    │
└─────────────────────────────────────────────────────────────────┘
```

**关键结论**：数据中心已与主服务解耦（主服务重启不中断 K 线采集）。注：`.venv\Scripts\python.exe` 启动时会在 Windows 上派生出 `.runtime\Python312\python.exe` 真实进程（父子同构，仅一个采集实例），排查进程时不要误判为双实例。

### 1.2 数据落库

- 全部落 **PostgreSQL `alpha_market`**（`MARKET_DATABASE_URL=postgresql+psycopg://db_admin:YOUR_DB_PASSWORD@localhost:5432/alpha_market`），不经 Redis
- 核心表：`crypto_klines`（K线）、`market_trades_aggregated`（成交/CVD）、`market_orderbook_snapshots`（盘口）、`market_asset_metrics`（OI/费率/mark）、`perp_funding`（资金费）、`raw_market_events`（shadow 原始事件）

---

## 二、组件清单与运行状态

| # | 组件 | 职责 | 调度 | 数据源 | 运行状态 |
|---|------|------|------|--------|---------|
| 1 | `kline_realtime_collector` | P0 热路径每分钟 + P1 全市场补全 + P2 回填 | 独立线程 + asyncio，P0 每分/P1 每120s | ccxt（默认 asterdex） | ✅ **正常**（P0: 333ok/0err，成功率 89~100%） |
| 2 | `asterdex_ticker_poller` | Asterdex 秒级价格 + 24h 统计 | 每10s价格/每30s统计 | urllib + 代理 | ✅ 正常 |
| 3 | `live_kline_engine` | 内存 forming K线 | 10s 刷新 | 订阅 market_events | ✅ 正常 |
| 4 | `depth_backfill_runner` | 深度回填 | 每6h | ccxt | ✅ 正常 |
| 5 | `kline_freshness_inspector` | 只读巡检 + 飞书告警 | 每5min | 只读DB | ⚠️ 符号范围收窄（见 §四-F） |
| 6 | `market_flow` (asterdex_collector) | trades/orderbook/asset_metrics 三通道 | REST 轮询 30s/30s/120s | ccxt | ✅ **已修复**（原 100% 失效，见 §四-A/B） |
| 7 | `multi_venue_funding_collector` | 多所资金费率 | 每300s | ccxt + urllib | ✅ 正常（perp_funding 230万+行） |

---

## 三、数据流全链路

```
┌─ 采集层 ─────────────────────────────────────────────────────────┐
│ K线:  ccxt 直连/代理 → crypto_klines (幂等 UPSERT)                 │
│ 价格: urllib → price_cache/market_events → live_kline_engine      │
│ 盘口/成交: hyperliquid WS (主进程旧采集器) → 三张市场表              │
│ 资金费: multi_venue_funding → perp_funding                        │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌─ 消费层 ─────────────────────────────────────────────────────────┐
│ data_center.get_klines → kline_data_service.get_klines_from_db    │
│   → unified_data_pool / scalp_loop / midlong_loop                 │
│ market_flow_indicators → CVD/因子/信号引擎                          │
│ derivatives_analytics → 决策因子 / 信号                            │
│ kline_enrichment_service → K线 CVD 富化 (★ 只认 hyperliquid)       │
└───────────────────────────────────────────────────────────────────┘
```

---

## 四、关键发现（按严重程度）

### 🔴 A. asterdex 市场流采集器 100% 失效（僵尸组件）— 致命

**现象**（三重铁证）：

1. **日志铁证**（`data-center.log`）：
   - `poll_trades`：**50930 条，100% 是 `[WARNING] ... 异常: binance GET https://api.asterdex.com/api/v3/exchangeInfo`**，零成功
   - `poll_orderbook`：**25640 条，100% 失败**，同样报 exchangeInfo
   - `poll_asset_metrics`：**8600 条失败**，报 `AttributeError: 'binance' object has no attribute 'fapiPrivateGetPremiumIndex'`
   - 含 "asterdex" 日志共 **101782 条**，绝大部分是刷屏失败

2. **数据库铁证**（直接查 alpha_market）：

| 表 | hyperliquid | asterdex | 其余 |
|---|---|---|---|
| `market_trades_aggregated` | 380,382 | **0** | - |
| `market_orderbook_snapshots` | 104,675 | **0** | okx 24 / binance 22 |
| `market_asset_metrics` | 529,982 | **0** | binance 25 / okx 25 |
| `perp_funding` | 2,294,400 | 10,665 | 各所约1万 |

   asterdex 三张市场表**从无到有均为 0 行**。

3. **网络实测**：
   - `api.asterdex.com` **DNS 无法解析**（`Resolve-DnsName` 直接报错）
   - `fapi.asterdex.com` 无响应
   - ccxt binance 实例**未配置代理**（`aiohttp_trust_env=False`）→ 直连必然 DNS 失败

**根因**（两个确定性 bug）：

1. `_create_ccxt_exchange`（asterdex_collector.py L51-74）：ccxt binance 实例把 `public` URL 指向 `https://api.asterdex.com/api/v3`（现货 exchangeInfo），该域名 **DNS 不可解析**。ccxt 首次 `fetch_trades`/`fetch_order_book` 会惰性 `load_markets()` 拉 exchangeInfo → 100% 失败。且 ccxt 把底层 DNS/SSL 细节折叠成 `ExchangeError("binance GET <url>")`，**根因被吞**，日志只见裸 URL。
2. `poll_asset_metrics`（L227）：`await self._exchange.fapiPrivateGetPremiumIndex(...)` —— 实测 ccxt binance **只有 `fapiPublicGetPremiumIndex`**，私有方法不存在 → 100% AttributeError。且即便修好，代码 L233 直接把 `openInterest` 写死 `None`，Asterdex OI 永远采不到。

**影响**：DC 模式下 `market_flow` 健康检查显示 `up:{'asterdex': True}`，但 asterdex 的 trades/orderbook/OI 数据**完全空白**，CVD/OI/深度因子全部缺 asterdex 源。当前靠主进程 hyperliquid 旧采集器兜底，系统主体未断，但 **asterdex 这个"数据中心主采集器"实际是空转刷屏的僵尸**，14+ 小时 8 万+ 条垃圾日志。

> ✅ **已修复（2026-08-04 修复轮）**：
> 1. `_create_ccxt_exchange`：`public`/`private` URL 全部改为 `https://fapi.asterdex.com/api/v3`（与 AsterdexAdapter 一致），显式注入 `proxies/aiohttp_proxy`（走 1080 代理，直连 TLS 会被 RST），`timeout` 提到 10000ms；
> 2. `fetchMarkets: {"types": ["linear"]}`：只加载永续合约市场，避免 spot/inverse 段打到真实 binance 被 418/429；
> 3. `fapiPrivateGetPremiumIndex` → `fapiPublicGetPremiumIndex`；
> 4. `binance_symbol` 计算修正（去掉重复 USDT → 正确 `BTCUSDT`）；
> 5. 补 `from decimal import Decimal`、预热 `load_markets`、清死代码；
> 6. 同步修复 `kline_collectors.py`（binanceusdm 同步驱动）与 `multi_venue_funding_collector.py` 的 asterdex URL/`fetchMarkets`。
> **验证**：重启后 market_flow 采集正常（仅剩少量 429 退避日志），P0 K 线落库完整（asterdex BTC 各周期齐全，1m 43215 根）。

---

### 🔴 B. `fapiPrivateGetPremiumIndex` 方法名 bug（致命，同属 A 组件）

- `asterdex_collector.py:227` 调用的 `fapiPrivateGetPremiumIndex` 在 ccxt binance 中**不存在**（只有 `dapiPublicGetPremiumIndex` / `fapiPublicGetPremiumIndex`）
- 已用 `dir(ccxt.binance)` 实测确认
- 导致 `poll_asset_metrics` 100% AttributeError，Asterdex OI/funding/mark 数据 0 行

---

### 🟠 C. 双命名空间双单例（高危，结构性）

**铁证**：`derivatives_analytics_service` 被以两种路径导入：

| 路径 | 使用处 |
|---|---|
| `from services.derivatives_analytics_service import derivatives_analytics` | `unified_data_pool.py:515`（**裸路径**） |
| `from backend.services.derivatives_analytics_service import derivatives_analytics` | 其余 **19 处** |

`backend/` 被 append 进 `sys.path` 后，`services.*` 与 `backend.services.*` 是两个不同模块名 → **实例化两份单例**：
- 双缓存、双后台刷新线程、双倍网络调用、缓存不一致
- `market_flow_routes.py:193-198` 注释已明确警告此模式（l2_orderbook_manager 曾踩过）

**影响**：热路径因子可能读到旧缓存；后台刷新线程翻倍消耗网络；同一数据两处消费行为不一致。

---

### 🟠 D. `kline_enrichment_service.FLOW_EXCHANGE="hyperliquid"` 硬编码错配（高危）

- `kline_enrichment_service.py:15` 硬编码 `FLOW_EXCHANGE = "hyperliquid"`
- 但数据中心独立模式**只启动 asterdex 采集器**，且 asterdex 已 100% 失效
- 结果：K线 CVD 富化只查 `exchange=='hyperliquid'` 的表，**即便 asterdex 修好也永远读不到**；而 hyperliquid 数据依赖主进程旧采集器，DC 模式下这条链路脆弱
- **修复后遗症**：asterdex 即使修复成功，富化层也不会消费

---

### 🟡 E. SSL EOF 影响面评估（中危，非致命）

`backend.error.log` 中 `UNEXPECTED_EOF_WHILE_READING` 共 **820 条**：

| 服务 | 条数 | 影响 |
|---|---|---|
| `market_data_ingest_queue`（urllib→代理） | 485 | v2 shadow 原始事件缺数据；60s 熔断；**非致命** |
| `derivatives_analytics_service`（两命名空间合计） | 264 | L2-L4 网络源间歇失败，回退 L1 本地；数据陈旧 |
| `backend.services.market_data`（requests→代理） | 48 | 行情查询偶发空返回（fail-open） |
| `llm_config_service` | 10 | 配置拉取偶败，非致命 |
| 其余（rebate_arb/macro/new_coin_scanner 等） | 13 | 偶发自愈 |

- **根因**：`BINANCE_HTTPS_PROXY/HTTP_PROXY/HTTPS_PROXY=127.0.0.1:1080`（Shadowsocks），1080 端口在监听但**上游链路不稳定**，TLS 读取阶段被掐断
- **性质**：可重试的偶发网络抖动，全部消费方 fail-open + 下个 tick 重试，**不致命**，但静默拉低 Binance/Coinalyze 数据新鲜度
- **与 LLM 无关**：LLM 已改为直连（上一轮 P0-A 修复），行情数据仍走 1080 代理，二者彻底隔离 ✅

---

### 🟡 F. 其余中危问题

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| F1 | 新鲜度巡检符号范围静默收窄 | `kline_freshness_inspector._symbols()` L55-60 | `MARKET_DATA_V2_SYMBOLS=account_selected` 时**不回退解析真实交易标的**，硬编码 `BTC,ETH,SOL,BNB,ASTER,JTO` 6 币 → 其他实际交易币永不巡检（历史 JTO 缺行情即此类） |
| F2 | P0 全量超时丢弃已采集数据 | `kline_realtime_collector._collect_current_minute` | `asyncio.wait_for(gather(...))` 整体超时即取消所有任务并记 err，虽 executor 可能已入库，但 ok/err 统计失真、热点币最后几根可能丢 |
| F3 | `kline_data_service` 逐行 insert | `_insert_kline_data_sync` L135 | 每行 execute + 每500行 commit，深度回填万行级性能差；与 write_batcher 的 executemany 不一致 |
| F4 | `kline_sync_meta` PG 方言 SQL | `_ensure_tables` | 用 `id SERIAL`/`COUNT(*) FILTER` 等 PG 专用语法，若 `MARKET_DATABASE_URL` 指向 SQLite 则建表静默失败（被 warning 吞） |
| F5 | `_best_exchange_for` 带 NameError 死代码 | `kline_history_sync.py` L152 | 从未被调用，且函数体引用未定义变量 `expected`，一旦调用必崩 |
| F6 | 每根 K 线落库都查活跃交易所 | `_collect_symbol_kline` L1034 | 每根成功 K 线 `get_active_exchange()` 开新 DB session，P0 每分钟上千次查询浪费 |

---

### 🟢 G. 低危/遗留问题

| # | 问题 | 说明 |
|---|------|------|
| G1 | `DepthBackfillRunner` 硬编码 asterdex | `kline_history_sync.py` L738 固定 `exchange="asterdex"`，切所后深度回填仍在补 asterdex |
| G2 | `asterdex_ticker_poller` 吞错 | `_fan_out` 逐 symbol `except Exception` 只打 debug；无 metrics 暴露 |
| G3 | forming bar 覆盖不全 | `_refresh_forming_bars` 只对 1m/5m/15m 回查，30m+ 长周期 forming 靠 P0 校准，广播窗口内有陈旧风险 |
| G4 | `get_trading_exchanges` fail-open | DB 查询失败回落到 `get_quote_exchanges()`（覆盖更多所），DB 故障时巡检告警爆炸 |
| G5 | 死代码/注释不符 | ① `_async_main` L119 `if False else` 残留调试；② 注释声称 ccxt.pro watchTrades 实为 REST 轮询；③ `_test_exchange_connection` docstring 乱码；④ `AsterKlineCollector` 遗留别名不可达 |
| G6 | 双回填入口并存 | `quick_sync_symbol`/`_run_sync_queue`（采集器内部）与 `kline_backfill_manager`（API 触发）职责边界模糊 |

---

## 五、与上一轮修复的关系

| 项目 | 状态 |
|---|---|
| LLM 直连（P0-A） | ✅ 已修复，LLM 侧 SSL EOF = 0 |
| 行情数据代理（1080） | ⚠️ **未改**，SSL EOF 仍存在（历史问题，非致命） |
| 数据中心供数 | 🔴 asterdex 100% 失效，靠主进程 hyperliquid 兜底 |

**结论**：用户上一轮指出的"行情数据走数据中心"属实——行情数据确实经数据中心 + 1080 代理链路。但 LLM 直连修复**没有影响行情链路**（代码层面已隔离，见 §四-E）。

---

## 六、修复建议（按优先级）

### P0（立即，致命）
1. **修复 asterdex_collector**：
   - `_create_ccxt_exchange`：`public`/`private` URL 改为 `https://fapi.asterdex.com/api/v3`（与 `asterdex_adapter.py:80` 一致），或改用 `asterdex_adapter` 的路径
   - ccxt 实例补 `proxies={"http": proxy, "https": proxy}`（走 1080 代理，与 AsterdexAdapter 一致）
   - `fapiPrivateGetPremiumIndex` → `fapiPublicGetPremiumIndex`
   - 若 `api.asterdex.com`/`fapi.asterdex.com` 均不可达（DNS 铁证），考虑**直接禁用 asterdex 市场流**，DC 模式改启 hyperliquid 采集器，避免僵尸空转
2. **决策**：DC 模式 `ACTIVE_MARKET_FLOW_EXCHANGES` 从 `asterdex` 改为 `hyperliquid`（与主进程旧采集器对齐），或统一只保留一套

### P1（高危）
3. **修复双单例**：`unified_data_pool.py:515` 的 `from services.derivatives_analytics_service` 改为 `from backend.services.derivatives_analytics_service`
4. **`kline_enrichment_service.FLOW_EXCHANGE` 改为读配置**：`os.getenv("KLINE_ENRICHMENT_FLOW_EXCHANGE", "hyperliquid")`，并默认与 DC 活跃采集所对齐

### P2（中危，择机）
5. 新鲜度巡检符号解析复用 `resolve_configured_symbols`（而非硬编码 6 币）
6. `_best_exchange_for` 死代码删除或补全
7. `kline_sync_meta` 建表语句兼容 SQLite 或用异常显式告警
8. `_collect_symbol_kline` 复用 P0 传入的 exchange_id，减少无谓 DB 查询

### P3（观察期后）
9. SSL EOF 行情代理问题：如 1080 持续不稳定，可仿照 LLM 方案给行情侧增加独立代理配置或直连开关（需评估交易所可达性）

---

## 七、结论一句话

**数据中心主体（K线/价格/资金费）健康，但市场流层存在一个 100% 失效的 asterdex 僵尸采集器（DNS 不可达 + 方法名 bug，8 万+ 垃圾日志，三张表 0 数据），叠加双命名空间双单例、CVD 富化只认 hyperliquid 的配置错配、以及经 1080 代理传播的偶发 SSL EOF（非致命），构成当前行情数据链路的主要风险面；LLM 直连修复与行情链路完全隔离，未受影响。**

---

## 八、asterdex 429 限流根治记录（2026-08-04 下午）

### 8.1 症状

asterdex 采集器修复上线后，又出现**持续性 429**：

- `429 Too Many Requests {"code":-1003,"msg":"Too many requests; current limit of IP(126.227.100.196) is 2400 requests per minute"}`，8 分钟累计 **9735 条**（97% 来自 `kline_collectors`）
- P0 整轮 `0ok/333err`、P1 超时归零、ticker/depth 全 429
- 停手 5 分钟仍不恢复 → **自激循环**：窗口被持续请求占满，永不回收

### 8.2 根因（四条叠加）

| # | 根因 | 位置 |
|---|------|------|
| 1 | **429 被吞**：`fetch_current_kline`/`fetch_historical_klines` 的 `except` 只打日志不传播，P0/P1/P2 的冷却机制永远不触发 | `kline_collectors.py` |
| 2 | **冷却各自为政**：depth_backfill（`kline_history_sync`）与 `kline_data_service` 命中 429 各自吞掉，不通知 P0 → P0 下一轮仍发 333 请求全撞墙 | 多采集器无统一开关 |
| 3 | **market_flow 不受控**：唯一不过 K 线限速器的大流量组件（30 symbol × 3 通道持续轮询，约 240/min），其 120s backoff 恢复后又打，窗口再被占 | `asterdex_collector.py` |
| 4 | **P2 深历史回填超载**：短周期 1m 回填 400 天 = 576 批/币，多币叠加 + P0 突破窗口 | `kline_realtime_collector._initial_backfill` |

### 8.3 修复（五层防护）

**① 429 异常上抛**（`kline_collectors.py`）

- 新增 `ExchangeRateLimitError` + `_is_rate_limited_error()`（识别 429/418/-1003/banned）
- `fetch_current_kline` / `fetch_historical_klines` 命中限流时 `raise ExchangeRateLimitError`，不再吞错

**② 进程级滑动窗口限速（双桶）**（`kline_collectors._AsterdexRateLimiter`）

- `live` 桶：P0/P1 实时采集，上限 `ASTERDEX_MAX_REQ_PER_MIN=900`
- `backfill` 桶：P2 深历史回填，上限 `ASTERDEX_BACKFILL_MAX_REQ_PER_MIN=150`，请求间隔 `KLINE_BACKFILL_REQUEST_INTERVAL_SEC=1.2`
- 两桶互不抢配额，总速率 ~1050/min，远低于交易所 2400/min

**③ 全局封禁（任一组件触发 → 全链路同步停手）**

- `note_banned()`：任一 Asterdex 请求命中 429 → 置 `_banned_until`（`ASTERDEX_RATE_BACKOFF_SEC=90`）
- `wait()`：冷却期内所有 bucket 直接 `raise ExchangeRateLimitError`（**fail-fast，不发真实请求**）
- P0/P1/P2/depth/kline_data_service 统一经 `_sync_fetch_ohlcv`，自动纳入

**④ market_flow 接入全局封禁 + 降频**（`asterdex_collector.py`）

- 每轮轮询前 `_wait_global_ban()` 检查全局冷却，冷却期内等待不发包
- 命中 429 时 `note_banned()` 同步全局
- 轮询间隔降频：trades 15s→30s、orderbook 20s→30s、asset_metrics 60s→120s

**⑤ P2 回填深度分级**（`kline_realtime_collector`）

- 1m/3m/5m：近 30 天（P0 每分钟实时积累）
- 15m/30m：近 90 天（规则引擎近端够用）
- 1h/4h/1d/1w：`KLINE_BACKFILL_DAYS=400` 全深度（规则引擎需 1h ≥2190 根）
- `KLINE_P0_TIMEOUT_S` 55→70，配合 live 桶 900 保证长周期轮不超时

### 8.4 验证结果（重启后实测）

| 指标 | 修复前 | 修复后（15 分钟窗口） |
|------|--------|----------------------|
| 429 累计 | 9735 条 / 8 分钟 | **6 条** |
| P0 轮次 | 持续 0ok/333err | 12 轮中 10 轮全 ok，成功率 89~100% |
| P0/P1 超时 | 每轮超时 | **0 次** |
| 冷却恢复 | 永不恢复（自激） | 触发后 90s 自动恢复，最多损失 1~2 轮 |
| K 线落库 | 断供 | asterdex 358 万根，BTC 各周期齐全（最新到秒级） |

### 8.5 新增配置清单

```
ASTERDEX_MAX_REQ_PER_MIN=900          # live 桶（P0/P1）
ASTERDEX_BACKFILL_MAX_REQ_PER_MIN=150 # backfill 桶（P2 深历史）
KLINE_BACKFILL_REQUEST_INTERVAL_SEC=1.2
ASTERDEX_RATE_BACKOFF_SEC=90          # 全链冷却时长
KLINE_P0_TIMEOUT_S=70
KLINE_REQUEST_INTERVAL_SEC=0.3        # ccxt 实时采集请求间隔
KLINE_MIN_1H_CANDLES=2190             # 1h 最低数据量（90 天）
KLINE_MIN_1D_CANDLES=180              # 1d 最低数据量（半年）
```

> **教训**：交易所限流是整 IP 共享的，多采集器必须共享一个"限流总开关"——任何组件命中限流都应让所有组件同步停手，否则各自为政的退避会让窗口永不满、429 自激。

## 九、DC_ONLY 统一收口 + 数据补齐验证（2026-08-04 晚）

### 9.1 统一数据源目标
用户需求：**数据中心是项目唯一数据来源**，业务代码需要数据时只能从数据中心读，禁止直连交易所。

实现：`MARKET_DATA_DC_ONLY=true`（默认开），全局守卫函数 `_dc_only_enabled()`，业务层所有行情读取统一走 DB（`crypto_klines` / `symbol_catalog` / `market_asset_metrics` / `market_orderbook_snapshots` / `whale_activities`）。

### 9.2 本轮收口的旁路（18 个文件）
详见 `README.md`「数据中心唯一数据源」章节的收口点表格。涵盖：
- 核心价格/K线门面：`market_data.py` 全部函数、`registry.get_klines`、`data_center.get_price`
- API 路由：`market_data_routes` / `market_intelligence_routes`（`/orderbook/{symbol}` DC_ONLY 改读 DB）
- 策略/执行：`strategy_coordinator` / `paper_trading_engine` / `data_health` / `market_summary_helpers`
- 选币/宇宙：`auto_coin_selector` / `universe_manager` / `market_scanner`（读 `symbol_catalog`）/ `new_coin_scanner`
- AI/回测：`ai_signal_generation_service` / `backtest_performance_service`
- 采集聚合：`market_aggregation.collect()` DC_ONLY 跳过直连

**保留直连（非行情数据）**：账户 `maxBuilderFee` 授权查询（`account_routes` / `hyperliquid_routes`）、交易客户端、数据中心采集器本身。

### 9.3 Asterdex API Key 的诚实结论
添加 `ASTERDEX_API_KEY`/`ASTERDEX_API_SECRET` **不会提升公开 K 线抓取配额/速度**（限速基于 IP）。API Key 仅启用私有数据流（用户 WS、账户查询）。抓取提速已通过回填深度与限速参数实现。

### 9.4 数据补齐验证（DB 实测 2026-08-04 18:00–21:00）

**主流币（BTC/ETH/SOL）**：
| 交易所 | 1h | 4h | 1d | 1w | 15m/30m |
|--------|-----|-----|-----|-----|---------|
| asterdex | 210d ✓ | 89.8d ✓ | 1798d ✓ | 399d ✓ | 30d ✓ |
| binance | 210d ✓ | 89.7d ✓ | 3274d ✓ | 3269d ✓ | 30d ✓ |
| hyperliquid | 208d ✓ | 89.7d ✓ | 1108d ✓ | 2527d ✓ | 30d ✓ |

**山寨币（全 catalog）**：
| 交易所 | catalog | 1h | 4h | 1d | 1m |
|--------|---------|-----|-----|-----|-----|
| asterdex | 533 | 526币/210d ✓ | 521币/89.8d ✓ | 521币/1798d ✓ | 521币/7d（回填中） |
| binance | 803 | 639币/210d ✓ | 628币/89.7d ✓ | 627币/3193d ✓ | 625币/7d（回填中） |

**冷所短周期（bybit/okx）正在回填中**：心跳显示 1h 已完成（60 币），30m/15m/5m/3m/1m 按 `ordered_periods`（1h→4h→1d→1w→30m→15m→5m→3m→1m）排队推进。1m 目标 15 天（冷所浅回填）。

**心跳进度**（`kline_sync_heartbeat.pool='p2_depth'`）：
```
asterdex 全周期完成（1h/4h/1d/30m/15m/5m/3m/1m）✓
冷所 1h 完成（okx/bybit/binance/hyperliquid 各 60 币）→ 4h/1d/1w/短周期排队中
```

