# 数据中心深度审计报告（2026-08-15）

> 审计日期：2026-08-15（系统运行中实测 + 代码静态核查）
> 审计对象：独立数据中心进程（`backend/workers/market_data_center.py`，:9100）+ 行情数据全链路 + 因子/训练数据管道
> 方法：PostgreSQL 实测（alpha_market，账户 laobao）+ 代码路径核查 + 运行日志交叉验证 + 5 路只读子代理并行探查
> 状态：**体检完成；P0 修复与数据补齐按《数据中心深度检查与数据补齐设计方案》执行中**

---

## 一、健康项（实测数字）

| 数据 | 实测状态（2026-08-15 15:1x） |
|------|------------------------------|
| K 线 crypto_klines | 66,402,752 行；5 所（asterdex/binance/bybit/okx/hyperliquid）10 周期；asterdex 1m 新鲜到 15:12；binance 1d 历史 3274 天（最早 2017-08-17） |
| 四所同步门禁 | gate.ok=true；asterdex catalog 507 币、binance 774、bybit 664、okx 404、hyperliquid 230；P2 深度回填各周期全 ok |
| 资金费率 perp_funding | 5 所新鲜（15:12–15:13）；**历史深度仅 14 天（HL 138 天）** |
| OI/mark market_asset_metrics | asterdex 265,435 行（11 天）、hyperliquid 608,844 行（29 天），新鲜 15:11–15:13 |
| 成交聚合 market_trades_aggregated | asterdex 157,253 行（11 天）、hyperliquid 458,415 行（29 天），新鲜 15:13 |
| 盘口快照 market_orderbook_snapshots | asterdex 481,985 行（11 天）、hyperliquid 183,535 行（16 天），新鲜 15:13 |
| 辅助时序 symbol_aux_timeseries | 108,315 行 / 140 币，新鲜 14:57；fear_greed/whale_tx/news_sentiment/social_score/tvl/btc_dominance/discussion_volume 全列有值（108K 全量，部分列 67–72K） |
| 原始事件 raw_market_events | 970,896 行，全部 data_type='kline'，新鲜 15:15（仅 K 线 shadow，无成交/清算事件） |
| 数据中心进程 | :9100 /health ok，uptime ~47h；7 组件 up |

## 二、关键问题（按严重度）

### P0-1 新闻/鲸鱼任务写错库（已核实，致命）

- `NewsEvent`/`WhaleActivity` 是 **MarketBase** 模型（`database/models.py:2701/2721`），表只存在于 alpha_market。
- 定时任务却用核心库 `SessionLocal`（`services/startup.py:575/600`）→ commit 静默失败、被 `except: rollback` 吞掉。
- 铁证：`news_events` / `whale_activities` 实测 **0 行**；日志（backend.log 2026-08-15 15:12）却每 2 分钟报「[WhaleTracker] 记录了 3 条鲸鱼异动」。
- 影响：新闻情绪、鲸鱼信号全部空转，选币器/AI 决策读到空数据。

### P0-2 AI 信号 K 线工具必崩（代码核实）

- `_tool_get_kline_context`（`ai_signal_generation_service.py:641-699`）引用未定义变量 `min_ts`/`max_ts`，DC_ONLY 分支（:668）与旧 HL 直连分支（:693）双双 NameError。

### P0-3 决策「实时价」实为 1m 收盘

- `strategy_coordinator._get_realtime_price_robust` → `market_data.get_last_price`；非 hyperliquid 所直接 `kline_service.get_klines_from_db(symbol,"1m",1)` 取最新 1m close（`market_data.py:169-173`），未走 `data_center.get_price_with_ts` 秒级链路。
- `data_center._db_1m_price_with_ts`（`data_center.py:440-460`）兜底无 stale 门。
- 影响：决策价与秒级成交价口径漂移（最大 ~60s+）。

### P0-4 DC_ONLY 残留直连

- `paper_trading_engine.py:590-616`：守卫 raise 被 `except: pass` 吞掉后落入无守卫 ccxt 直连。
- `rebate_arb/rebate_paper_market.py:62-84`：ccxt fetch_ticker 完全无守卫。
- `market_price_service.py:257-277`：LegacyRestPoller 无 DC_ONLY 守卫。

### P0-5 K 线两条写路径语义相反

- `kline_data_service._insert_kline_data_sync` 逐行 `insert_on_conflict_do_nothing`（首写者胜，:190-199）vs `repositories/kline_repo.py:85-87` `do_update`（后写者胜）。
- 写前无 NaN 清洗、无乱序检测；写失败静默丢数据、无死信计数。

### P1-1 订单流/盘口/OI/资金费历史太浅（DB 实测）

trades_agg 11–29 天、asset_metrics 11–29 天、orderbook 11–16 天、perp_funding 多所仅 14 天。与《短线因子K线研究阶段报告_2026-08-11》「订单流历史仅 7–25 天」结论一致——因子训练数据不足。

### P1-2 清算只算不存

DerivativesSnapshot 内存计算（Coinalyze/Coinglass），无清算历史表；期权 IV 为伪实现（`options_data_collector.py:167-173` 恒返回 1.0）。

### P1-3 鲸鱼/大单采集器无后台调度

`aggregate_whale/market/orderbook` 三个聚合采集器只在 API 手动触发（`market_intelligence_routes.py:536-539`）；`market_aggregation.collect()` 在 DC_ONLY 下被跳过；Hyperliquid 大单专用 WS 缺失。

### P1-4 秒级 ticker 不落库

`crypto_price_ticks` / `price_samples` / `crypto_prices` 全 0 行；ticker 只存在于 DC 进程内存 + 跨进程 REST（:9100/ticker/{base}，1s TTL 缓存 + 1.5s 超时）。

### P1-5 无宏观经济日历与事件时间轴

无 CPI/FOMC/NFP 等宏观事件采集器、无事件时间轴表；新闻管道因 P0-1 空转；`raw_market_events` 只有 kline 类型。

### P1-6 因子离线训练只装配 OHLCV

- `evolution/factor_evolution_loop._load_data` 仅从 `data_center.get_klines` 取数；表达式 DSL 声明的 funding/oi/basis/liquidation 列从不注入。
- 资金费/OI/CVD/taker 数据只注入实时决策路径（factor_bridge），不进训练管道。
- 活跃因子分类目录（technical/behavioral/composite/fundamental/macro/onchain/sentiment）均为空壳；真代码在 `_ai_gen_archive`(57) 与 `_ai_gen_quarantine`(1122) 且不加载。
- `alpha_training_pipeline.py` 为 0 行空文件。

### P2-1 监控盲区

- 心跳按 (exchange,period,pool) 不分币种；`/health` 只看 kline 采集器 up。
- 新鲜度巡检硬编码 6 币（`kline_freshness_inspector.py:55-60` 的 `BTC,ETH,SOL,BNB,ASTER,JTO`）。
- stale 阈值多套口径并存（5s/300s/按周期/period*2+60）。
- 执行价一致性门默认关且 fail-open（`execution_gates.py:54-82`）。

### P2-2 K 线管道残留

1d/1w 无留存桶永不清理；1M 深度目标「60 根」被当「60 天」；`_period_to_seconds` 缺 3m/1w/1M；`_best_exchange_for` 死代码（引用未定义变量）；双回填入口边界模糊（quick_sync/_run_sync_queue vs kline_backfill_manager）。

## 三、历史修复痕迹（验证有效）

| 事项 | 状态 |
|------|------|
| 2026-08-04 asterdex 市场流采集器 100% 失效（DNS+方法名 bug） | ✅ 已修复：trades/orderbook/OI 现均有 asterdex 数据且新鲜 |
| 429 限流自激（9735 条/8min） | ✅ 已根治：双桶限速 + 全局封禁（90s），P0 成功率 89~100% |
| DC_ONLY 统一收口（18 文件） | ✅ 大部分有效；P0-4 三处残留待封 |
| 回填↔删除冲突（曾误删 163 万行） | ✅ 留存期已对齐回填深度；1d/1w 无桶问题遗留 |

## 四、修复与补齐执行计划

见《数据中心深度检查与数据补齐设计方案》（本会话批准稿），分四阶段：

1. **阶段 1**：P0 修复（新闻/鲸鱼写库、AI 工具、DC_ONLY 残留、决策秒级取价、K 线写路径、心跳健康）。
2. **阶段 2**：数据补齐（funding 历史回填、清算落库、鲸鱼大单定时调度、ticker 落库、宏观日历、新闻增强、统一事件时间轴）。
3. **阶段 3**：因子训练管道（多源对齐装配器、数据可用性门、泄漏测试、长线装配）。
4. **阶段 4**：监控看板扩展 + 量化验收。

## 五、执行进度与验收记录（2026-08-15 16:10 更新）

### 阶段 1（全部完成 ✅）
| 修复 | 文件 | 验收结果 |
|------|------|---------|
| 新闻/鲸鱼写库 | startup.py / news_intelligence_service.py / whale_tracker_service.py / intelligence_routes.py | **在线验证**：news_events 从 0 行 → 40+ 行/小时，whale_activities 从 0 行 → 持续新增；日志与 DB 增量一致 |
| AI 信号 K 线工具 NameError | ai_signal_generation_service.py | min_ts/max_ts 现由 timestamps 计算，DC_ONLY 走 active_exchange |
| DC_ONLY 残留封堵 | paper_trading_engine.py / rebate_paper_market.py / market_price_service.py / cross_exchange_ws_feed.py | 3 处直连兜底移除；套利 WS 默认禁（ARB_DIRECT_MARKET_FEED 显式放行） |
| 决策秒级取价 | strategy_coordinator.py / market_data.py / data_center.py | `_get_realtime_price_robust` 首选 `get_price_with_ts`（5s 校验）；1m 兜底加 stale 门（3 周期+30s） |
| K 线写路径统一 | kline_write.py（新）/ kline_data_service.py / kline_repo.py | 单一 upsert（后写者胜）+ NaN/非法时间戳清洗 + 批量写入；单测通过 |
| 心跳与健康 | workers/market_data_center.py / kline_freshness_inspector.py / data_center_gate.py | `/health` 新增 component_health（每组件 last_success_ts/age/stale）；巡检符号走 resolve_configured_symbols；gate 增 P0 stale 判定 |

### 阶段 2（全部完成 ✅，DB 实测）
| 项 | 验收结果 |
|----|---------|
| D5 秒级 ticker 落库 | ticker_snapshots 7,772 行/4 分钟（14 天保留） |
| D1 资金费率历史回填 | binance/bybit/okx/gateio 从 14 天 → **90 天**；asterdex 无历史接口（诚实前向积累） |
| D3 清算落库 | liquidation_events 49 行（Coinalyze 小时聚合，90 天保留） |
| D4 鲸鱼/大单 | DC 进程每 45s 聚合三所大单：aggregate_whale 25 行/轮（BTC buy $1.03M 等真实大单）；market_trades_aggregated 新增 largest_trade_usd/side |
| D2 订单流归档 | flow_archive_5m 表 + 30 天滚动归档任务 |
| D6 宏观 | macro_events/macro_series 表 + FRED/官方日历采集器；**待办：注册免费 FRED_API_KEY 填入 .env 后 FRED 序列自动生效** |
| D7 新闻快通道 | CryptoPanic important 90s 通道已启动（日志验证） |
| D8 事件时间轴 | `GET /api/market/data-center/events` 上线（news/macro/whale/liquidation 四源合并） |

### 阶段 3（全部完成 ✅）
- `factor_engine/dataset_builder.py`（新）：K 线骨架 + funding/OI/CVD/aux/清算/事件 merge_asof 对齐；覆盖率门槛（<80% 整列丢弃不造假）；事件特征点-in-time 防泄漏；时区统一（naive 本地 → UTC）。
- 实测装配 BTC 1h×200：funding 覆盖 1.0、CVD 0.865、aux 0.995；OI/清算/事件历史尚浅被诚实丢弃（随前向积累自动解锁）。
- `alpha_training_pipeline.py`：0 行空文件 → 门面（指向真实两条训练路径）。
- 泄漏/清洗单测 9 项全过（事件收盘边界、窗口语义、permutation 对齐敏感、NaN/毫秒/未来时间戳拒绝）。

### 阶段 4（全部完成 ✅）
- 质量看板 `GET /api/market/data-center/quality` 上线：11 源新鲜度/行数（pg_class 估算，避免大表 COUNT 超时）/stale 聚合。实测 2026-08-15 16:45：全部 fresh（ticker 5s / kline 16s / trades 16s / orderbook 16s / whale 34s / news 296s / funding 224s / oi 61s / aux 21s / 清算 1.7h 按小时聚合属正常）。
- 排障记录：后端重启时曾被「残留 DB 会话持有 paper_positions AccessShare 锁」阻塞旧迁移 ALTER，形成 watchdog 杀-启死循环；已 pg_terminate_backend 清理残留会话恢复（此类残留会话来自被强杀的半启动后端进程，建议后续在 watchdog stop 时加 PG 空闲事务清理）。

## 六、消费端总验收（2026-08-15 17:50，4 路并行审计 + 修复）

> 目标：数据层补齐后，核实所有用数据的 agent（策略/因子/选币/信号/回测）拿到的是真实数据、占位是否清理、设计缺陷是否修正。审计由 4 个只读子代理完成，修复项如下。

### 已修复（本轮）
| # | 缺陷 | 修复 |
|---|------|------|
| C1 | `v3_factor_pipeline` 链上导入缺 `backend.` 前缀 → 链上/宏观注入从未生效 | 改 `backend.services.onchain_data_collector` |
| C2 | `auto_coin_selector._fetch_dc_snapshot` funding_rate/OI 恒 None → fund_score 恒吃默认值 | 接 `data_center.get_derivatives`（perp_funding/market_asset_metrics 落库真实值） |
| C3 | `_assess_trend` 非 HL 所恒返回 0.5 占位 | 新增 `_assess_dc_trend`：用数据中心 1h K 线计算真实 MA 趋势分（与 HL 同公式） |
| C4 | `factor_bridge`/`v3` 的 `oi=1.0` 编码注释误导、`total_notional=abs(cvd)*10` 合成、`sell_notional=1.0` 伪分解 | 新增 `fetch_real_oi_pair`/`fetch_real_flow`：OI 绝对值对与真实吃单买卖额（market_asset_metrics / market_trades_aggregated）；合成值仅作历史不足回退；两条管道统一走 `inject_orderflow_for_factors` |
| C5 | funding 来源不一致 + 静默 0：coordinator 靠字符串闸门 + 进程内缓存；scalp 靠 derivatives 缓存 | 统一 `data_center.get_derivatives` 落库直读（缓存无关恒可用）；coordinator 移除字符串闸门 |
| C6 | midlong 净 RR 闸门：funding=None 按 0 → 成本 0 绕过 | `funding_net_rr_ok`：缺失时用保守估计费率（`MIDLONG_FUNDING_UNKNOWN_RATE` 默认 0.01%/8h）并在原因标注「估计口径」 |
| C7 | `WhaleSignal` 无 available 字段（docstring 声称有）；`get_aggregate_sentiment` 无新闻返回 0 无标记；BTC 价格全失效时伪造 70000 | 补 available 语义（无数据 available=False）；70000 兜底删除（价格不可用→本轮跳过采集） |
| C8 | 排名应急兜底伪造 volume=1.0 | 改 volume=0 + `emergency_fallback` 来源标记（诚实反映无行情） |
| C9 | `intelligence_signal_engine` 取数失败的 sentinel 默认值冒充真实中性 | 新增 `sources_available` 逐源可用性标记，`to_prompt_text` 列出 data_unavailable |
| C10 | `replay_harness` mid/long 注入 RSI=50 占位指标，扭曲回放 allow_rate | 真实 Wilder RSI 计算；4h/1d/1w 如实标 unavailable（单粒度回放无该数据） |
| C11 | Gate2 偏差检查：backtest_return_pct=0 静默跳过；分母用无单位 max_position_size | 无基准 → fail-closed 显式拦截；分母改真实成交名义额（return_on_traded_capital 口径） |
| C12 | 因子进化 `_load_data` 纯 OHLCV，DSL 字段 funding/oi/liquidation 离线空转 | 接入 `build_enriched_dataset`（env `FACTOR_EVO_ENRICH` 默认开，覆盖率门槛保证低覆盖列整列丢弃）。实测 BTC 1h：funding/链上/社交/情绪列已进入训练数据 |
| C13 | new_coin_scanner 类别硬编码波动率冒充真实 | 有 K 线历史用实测波动率（1h 收益 std 年化）；无历史才用类别假设并标注 `volatility_is_estimate` |
| C14 | LLM 标注管道结构性失效：`resolve_llm` 守卫把「无租户+按用途（news_intel）」的后台调用直接拒绝 → 新闻/鲸鱼全部走启发式（实测 news 120 条 conf 全 0.30、方向 108/120 为 0） | 守卫放行「仅用途绑定」解析（usage_scope 平台级路由，无全库默认回退）；启发式结果 conf 0.3 保留为诚实标记。**待办：llm_configurations 当前 0 行，需在设置→LLM 配置里添加用途=news_intel 的配置后 LLM 标注才真正生效** |
| C15 | 数据集装配器低覆盖列仅标记不删除（残留 NaN/零值） | 低覆盖源整列从 DataFrame 删除 + 新增 DSL `liquidation` 总列（仅清算源未丢弃时） |
| C16 | `base_factors` oi_delta required_keys 注释误导 | 注释修正（真实键 oi/prev_oi；编码语义说明） |

### 核实为「已修/有意设计」（无需改动）
- strategy_coordinator:367（import 前缀）与 :1354（方法名）两处历史缺陷：07-10/07-06 已修，仅注释为修复记录。
- scalp_factor_exclude 白名单恒空：08-14 已修（AnalyticsSessionLocal）。
- training_live_promote Gate2 恒 0：07-18 已修（真实 pnl 汇总），本轮补分母口径。
- `unified_data_pool` 衍生品降级：已诚实标注（`⚠️数据不可用` + degraded 标志）。
- `analyst_report_builder`：占位 sentinel（funding=0/fgi=50/whale=0/atr=0）转 "N/A" 的诚实模式已存在。
- `mlto/qual_layer`「占位文本」= 显式「无视图/数据不足」说明，非伪装数据。
- 快照新闻注入：修复后已实际生效（实测 17:38 快照含 5 新闻）。

### 记录为架构性遗留
- parity_score 成交/滑点两维用常数基准（仅 core 4 维可比，代码已用 core_score + 连续 2 周缓解）。
- portfolio/construction notional 当 qty、data/derivatives_collector Velo/Kingfisher 占位、walk_forward CMA-ES 回退、dataset_cache 无消费者、real_factor_backtest 占位 DB 密码——均为死代码/未激活路径，清理属专项。
- `_ai_gen_archive`(57)/`_ai_gen_quarantine`(1122) 不加载的因子代码保持隔离；精选池收敛策略（SCALP_USE_VETTED_FACTORS_ONLY）继续生效。
- `factor_active_set` 当前 0 ACTIVE 行（进化晋升到没人读的表，base_factors 桥接读 ACTIVE）——因子晋升→实盘消费的完整闭环属后续专项课题。

### 验证
- 回归测试 35/36（唯一失败为审计前既有 fail-closed 测试不一致，已记录）。
- 在线验证：`fetch_real_oi_pair('BTC')=(43589.75, 43589.62)`、真实吃单流注入、进化取数含富化列、事件轴大单流（ETH $1.36M）、后端重启健康 200。

### 追加修复（2026-08-15 18:10，回测/训练报告 C 节残尾收口）- C-3 `scalp_factor_exclude` 白名单第一源（scalp_active_factor_set）`except: pass` → 显式 warning（第二源 factor_active_set 仍兜底）。
- C-4 `key_utils.normalize_engine_key` 仅剥 `evo_` 前缀 → 扩展 `ai_` 前缀与 `t{tenant_id}:factor_id` 键形归一化（公式因子/自定义存储键不再被白名单误排除）。
- C-7 `walk_forward` CMA-ES 回退 print → logger.warning（结构化告警）。
- C-8 replay 波动率 0.015 硬编码 → 真实收益波动率（历史不足标注 volatility_is_estimate）；orchestrator 中性加 `replay_mvp_no_orchestrator` 标注。
- C-12 `real_factor_backtest.py` 硬编码占位密码 → 读 MARKET_DATABASE_URL/PG* 环境变量，缺失时明确报错（惰性解析，不影响 --help）。
- 单测 22/22；后端已重启（health 200）。

### 追加修复（2026-08-15 18:20，因子/信号报告 C-10 收口）
- `expr/parser._parse_delta_time` 自称「占位实现」且缺 1w/1M → 补齐周/月单位（1w=604800s、1M=2592000s，M 与 m 大小写区分），修正注释；实测全单位解析正确、非法输入安全回 0。
- 该报告 C1–C7（oi=1.0/total_notional 合成/buy-sell 伪分解/v3 链上导入缺前缀/注释误导/情报信号中性默认不标）均在前两轮修复并在线验证；C8 诚实文本核实无需改；C9/C11 死代码与隔离目录记录在案。

### 追加修复（2026-08-15 18:30，AI 选币报告 B 节收口）
- B-6.10 `intelligence_signal_engine._get_price_change_1h` 失败返回 0.0 → 被 OI 四象限误判「震荡整理」：改为返回 None，`_classify_oi_regime` 显式 quadrant=unavailable（不再把取数失败当价格无变化）；`sources_available.oi` 同步识别 unavailable 象限。
- B-2 注入硬门 catalog 门 fail-open（目录刷新后为空即放行）→ 改为 fail-closed：目录为空或查询异常一律拒绝注入。
- 该报告其余项此前已闭环：funding/OI 恒缺（接 data_center.get_derivatives）、trend 恒 0.5（真实 MA 趋势）、WhaleSignal available 语义、BTC=70000 删除、volume=1.0 兜底、新币波动率实测、情报信号 sources_available、whale DB 回退、新闻 available 语义。
- 记录在案：score.py「无成交量偏好序流动性分」带 explain 标注（设计偏好序，非冒充真实成交量）；new_coin hype_score 为公开评分规则（类别/交易所信誉权重）；HL 趋势 <20 根回退 0.5 为诚实中性。
- 单测 22/22；后端已重启（health 200）。

### 追加修复（2026-08-15 18:45，全自动决策链报告「冒充侧」收口）
- **Market DB 衍生品快照硬编码 0**（unified_data_pool._fetch_one）：funding_rate/oi_total/liquidation 原写死 0.0 且标 data_quality="market_db"（假 0 冒充真实）→ funding 改读 data_center 落库、OI 改读 market_asset_metrics 真实值、清算与缺失字段显式 None。
- **degraded 不穿透**：get_intelligence_summary 原把 degraded 当 "neutral" 注入 AI → 改显式 "⚠️数据不可用"；merge_snapshot 的 oi_total/long_short_ratio/liquidation 在 degraded 时改 None（不再写 0/1.0 占位）。
- **coordinator compute_all_factors 空返回无日志** → 补 warning（因子不可用显式可见）。
- **v3 常数列注入风险**：`_kdf.assign` 原把 oi/cvd/funding 等订单流键作为常数序列注入 DataFrame（时间序列因子 delta(oi,5) 会读到恒 0）→ 仅保留快照标量上下文键（fear_greed/btc_dominance/社交/期权），订单流键只经 market_data dict 供 base_factors。
- **ai_decisions 取价失败 price=0 直送仓位规划** → fail-closed：价格缺失/非法即跳过该币决策。
- 已核实并记录（不修）：master_execution vol_value=0.015 为带日志的保守风控默认（仓位规划必须有波动率值，1.5% 保守假设 + 数据可靠时用真实 ATR）；MarketEnvironment 默认中性字段由 analyst_report_builder 的 N/A 标记兜底；ai_decisions:114 portfolio 回退属账户上下文兜底。
- 单测 22/22；后端已重启（health 200）。

## 七、用户待办（需用户操作）
1. 注册免费 FRED_API_KEY → `.env`（宏观日频序列即刻生效）。
2. 设置 → LLM 配置：添加一条用途含 `news_intel` 的 LLM 配置（否则新闻/鲸鱼/宏观的 LLM 标注持续走启发式，conf=0.3 标记）。
3. 可选：Coinglass 免费层（清算热力图增强）；Hyperliquid 大单专用 WS；`backend/factor_engine` 遗留目录清理评审。
4. 已知记录项：Coinglass 稳定币净铸造端点上游未开放（外部限制）；`test_final_test_confirm_fail_open_without_test` 既有测试与 fail-closed 行为不一致（审计前即存在）。

### 追加修复（2026-08-15 16:50，数据真实性专项）
- 期权 IV 期限结构伪实现（恒返回 1.0）→ 改为真实解析 Deribit 到期日并计算近月/远月 IV 比率，解析失败返回 None（`options_data_collector.py`，含到期日解析单测级验证：'BTC-28JUN25-65000-C'→2025-06-28 ✓）。
- 社交情绪 `sentiment_change_24h` 写死 0.0 → 改为从 symbol_aux_timeseries 计算最新 vs ≥20h 前快照的真实差值（`social_sentiment_collector.py`；实测 BTC/SOL 当前源数据平直为 50，差值为 0 属源数据特性）。
- `news_feed.py`（CoinJournal 纯文本注入 prompt）与 `news_intelligence_service`（落库+LLM）两套并存为有意设计：前者是轻量 prompt 上下文，后者是结构化事件管道；保留现状。

### 追加修复（2026-08-15 16:55，实时链路报告 C 节收尾）
- 限流冷却口径统一：`kline_realtime_collector._rate_backoff_sec` 默认 120s → **90s**，与 `_AsterdexRateLimiter._ban_backoff` 对齐（消除「本地跳过」与「全局封禁」恢复时刻错开的交替撞墙窗口）。
- `kline_repo.py:248/272` 两处乱码注释（mojibake）→ 修复为正常中文 docstring/注释。

### 追加修复（2026-08-15 17:00，决策链路报告 R 项收尾）
- R3 决策价一致性门：`execution_gates.decision_price_consistency_ok` 默认 **false→true**；异常路径 **fail-open→fail-closed**（取不到实时价/校验异常均阻断，仅「无决策价字段输入」放行并记 debug）。与 R2 决策价秒级化配套，防口径漂移。
- R4 多所聚合新鲜度门：`kline_data_service.get_aggregated_klines` 新增基准所最新 bar 新鲜度校验（`period_sec*2+60` 同 data_center is_fresh 口径），过期返回空（fail-closed），缓存命中同样校验；开关 `KLINE_AGG_FRESHNESS_GATE_ENABLED`（默认 true）。单元测试新增「过期拒绝/新鲜放行」两项。
- R8 注入价语义：`auto_coin_selector._resolve_inject_price` 第三步 purpose research→**trade**（过期返回空 + active_exchange 同源），杜绝陈旧 1m 收盘进入进场参考价。
- R6/R7（price_cache 跨所串扰、多套 freshness 阈值口径）记录为架构性遗留：涉及 legacy 调用面广，收敛需专项重构，不在本轮范围。
- 相关单测 22/22 通过。

### 追加修复（2026-08-15 17:10，因子训练报告 E 项收尾）
- E1（回看窗口不足→三段切分退化）：核实已由 2026-08-08 修复（`_load_data` 用 `_lookback_for_period(p)` 并记录 need/got），无残留。
- E2（expr_ast=NULL 隔离死数据污染统计）：写侧拒绝已于 08-06 落地；实测 `factor_active_set` 已无 NULL 表达式行，无残留。
- E3（未知因子类别 rev50/seed_bootstrap 告警）：核实为 08-14 有意设计（WARNING 提示 + 进化源落 PATTERN，白名单语义不受影响），保持现状。
- E5（`data_center` 声明未实现的三个方法）：补齐 `get_derivatives()`（funding/OI/mark/oracle/mid/premium/24h 量，30s 缓存）、`get_orderbook()`（best/spread/深度/挂单数/raw_levels，5s 缓存）、`get_snapshot()`（价格+衍生品+盘口+1d K 线聚合）。实测 asterdex BTC：funding=6.85e-05、mark=62932.5、OI=None（asterdex 无 OI，诚实返回 None）、盘口 fresh。三方法均读数据中心落库表，不直连交易所。
- 更新 `extreme_scenario.py` 两处过时「空表」注释（symbol_aux_timeseries 108K 行、liquidation_events 已落库、raw_market_events 有 kline 影子）。
- 单测 22/22 通过；后端已重启加载（health 200）。

### 追加修复（2026-08-15 17:20，K 线管道报告 C 节收尾）
- R1/R2 留存重构：`db_maintenance` 从分组桶改为**按周期独立桶**，每个周期的 `KLINE_RETENTION_DAYS_<PERIOD>` env 键全部生效（原 4h/30m/3m/5m 键被忽略）；**修复 5m 留存 30 天 < 回填目标 50 天的隐性删除死循环**；补 1d/1w 10 年长桶（保 binance 2017 起历史，同时有明确边界）。实测长周期数据完好：1d 562K 行 / 1w 86.7K / 1M 7.1K。
- R3 月线语义：`_depth_targets` 的 "1M":60 现正确折算为 ~1826 天回填窗口（原被当 60 天只回填 ~2 根月线）。
- R4 `_period_to_seconds` 缺 3m/1w/1M：前轮已修复。
- R5 缺口步长：`detect_missing_ranges` 按周期取步长（原硬编码 1 分钟，复用其它周期会误判）。
- R6 死代码：删除两个同名 `_backfill_asterdex_period`（一个兼容包装被后者覆盖、后者无内部空洞检测；实际路径只有 `_backfill_exchange_period`）。
- R7 Hyperliquid 限流：HL 纳入冷所 429 **封禁冷却**（60s fail-fast，防深回填自激撞墙），但不加 180/min 速率桶（HL 为主动所时 P0 需 ~360 req/min，加桶会压死 P0）。
- R8（gateio 注册但无调度）/R9（P1 深度默认关、键名易混淆）/R10（trade 读不触发回填，设计意图）记录为已知设计现状。
- 单测 35/36（唯一失败为审计前既有 fail-closed 行为不一致，已记录）；DC 与后端均已重启加载（gate 全绿、DC 健康）。

---

*审计方法说明：所有 DB 数字均为 2026-08-15 实测查询结果；代码引用为审计当日 file:line。*

---

## 八、追加修复（2026-08-16，挖矿停摆三卡点 + 看门狗死亡循环）

### 卡点 1：15m/5m 进化永远 depth_insufficient（need_days≥190）
- **根因**：`mining_boost` 预设残留旧三层切分窗口 `FACTOR_EVO_TRAIN/VAL/TEST_DAYS=120/40/30`（=190 天），
  且 `_split_days_for_period` 的 env 覆盖压过所有周期分档 → 15m 需 190×96+50=18,290 根（实际 8,411）、
  5m 需 54,770 根（实际 8,648，即使回填到 55 天目标也只有 15,840 根）→ 短周期档**永远**深度失败。
  该三键同时被持久化进 `backend/config/compute_overrides.env`，重启后 lazy 注入 os.environ，即使关掉自动加强也照样生效。
- **修复**：`compute_config.py` 的 `mining_boost` 预设移除三个窗口键（加强=只加大搜索力度：种群/代数/种子/MCTS/codegen；
  窗口回到 `_PERIOD_SPLIT_DAYS` 分档，4h 档本身 270 天更深）；清理 `compute_overrides.env` 残留三键。
- **验证**：新进程实测 `15m=(30,10,10)→4,850 根`（现库 8,411 ✓）、`5m=14,450`（回填到 55 天=15,840 后 ✓）、`4h=1,670`（现 2,400 ✓）、`1d=320`（现 1,126 ✓）。
- **注**：`FACTOR_MINING_BOOST_AUTO=1` 保留，自动加强继续生效（仅搜索力度）。

### 卡点 2：中线打分截面 1d 永远 ✗（BTC 1126/需2400）
- **根因**：`FACTOR_SCORER_MIDLONG_LOOKBACK=2400` 对 4h（=400 天，刚好够）与 1d（=6.6 年，asterdex 仅 3.1 年）
  共用同一条 lookback，1d 永远不够 → 预检恒 ✗。
- **修复**：新增 `FACTOR_SCORER_MIDLONG_LOOKBACK_1D=1000`（.env + settings + env_registry）；
  共享助手 `factor_backtest_scorer.midlong_lookback_for(timeframe)`，四个调用点统一（
  `midlong_registry_factors` / `midlong_cold_pool` / `midlong_active_factor_set` / `validate_and_promote`）；
  `ops_routes.midlong-factors` 预检改为分周期口径（need_bars 按 tf 分别给出）。
- **验证**：BTC 4h=2,400/2,400 ✓，1d=1,000/1,000 ✓。

### 卡点 3：registry 因子 F「有效样本不足」（6 个）——三类真实原因
1. **ema_trend / sma_cross 的 F 是过期成绩**（08-15 滚动重算上线前扫描的旧结果）。现路径实测：
   `ema_trend@4h=C（ic=-0.089）`、`sma_cross@4h=**B（ic=-0.083，sharpe=0.55）**`——重扫即恢复。
2. **supertrend 算法退化（真 bug）**：旧实现用「当前 bar 中点 ± 3×ATR」判 close 突破，而 ATR 含当前 bar
   自身区间，数学上 close 几乎永远在带内 → BTC 4h 400 天实测 0/386 次触发 → 序列恒 0 → F。
   - **修复**：新增 `factor_engine/supertrend.py` 标准跟踪带算法（Wilder ATR + 前一根带 + 方向记忆），
     `base_factors.compute_supertrend` 与 `LegacySupertrendFactor` 两处共用。
   - **验证**：BTC 4h 滚动重算后 +1=205/-1=182，评分 `C（ic=0.027）`。
3. **oi_delta / taker_ratio / cvd_ratio 缺历史数据源**：legacy 标量实现读 `params['_market_data']`，
   评分路径从不注入 → 恒 0 → F。
   - **修复**：`midlong_registry_factors._enrich_flow_history` 从 Market 库
     `market_asset_metrics`（OI）/`market_trades_aggregated`（吃单买卖额）聚合**真实 per-bar** 列注入评分 DF
     （时间戳秒→毫秒换算已修；行落 bar[t,t+tf) 内、bar 收盘已知，防未来函数）；
     `_flow_series` 直接由富化列构造序列（oi pct_change / ln(buy/sell) / net/gross）；
     `_LegacyBase` 增加 df 列回退取 md 兜底。
   - **验证**：BTC 4h 三个流式因子现在分别 `C（ic=0.019/20 笔）`、`C（ic=-0.063/16 笔）`、`C（16 笔）`。
   - **诚实边界**：trades_agg/asset_metrics 原始表只保留 30 天 → 4h 仅 ~174 根有流式值；1d 需 ~180 天历史
     （当前 ~29 天 → 1d 流式因子仍会「有效样本不足」，属真实数据深度限制，随采集时间自然补齐）。
     4h 流式因子样本仍薄（16~20 笔），晋升受 IC/DSR/PBO 闸门约束，不会滥竽充数。

### 附带：backend-watchdog 死亡循环（重启→启动风暴→误判僵尸→再重启）
- **现象**：每次重启后 FullAuto 各循环 + 批标注（11.6s）+ 因子闸门 job + LLM 预热同时爆发，GIL 饱和让
  极轻的 `/api/health` 偶发 >8s 超时；旧阈值 8s×3 次≈60s 短于 3~4 分钟风暴期 → 每次重启必被误杀 → 无限循环
  （日志实锤：21:06:34 healthy → 21:07:03/33/21:08:02 zombie → 21:08:13 误杀 → 21:09:30 再 healthy）。
- **修复**：`backend-watchdog.ps1` 僵尸阈值 3→10 次、探针超时 8→12s、**重启后 300s 冷却期**（冷却期内 zombie
  超时不计数，down 仍计数）。真僵尸永不恢复，晚几分钟重启无害；健康但繁忙的后端不再被误杀。看门狗已换新参数重启生效。
- 教训：`/api/health` 已被设计成一次 GIL 时间片内完成（main.py 注释自述同一循环），但「忙」与「死」在
  探针视角不可分，只能靠冷却期 + 更耐心的阈值区分。

### 回归
- 相关单测 172/174：唯一新增失败 `test_factor_registration_count`（期望 `l2_depth_imbalance` 注册，base_factors
  从未注册该键，测试漂移，与本次改动无关）；`test_final_test_confirm_fail_open_without_test` 为审计前既有失败。
- 后端已重启加载全部修复（health 200）；数据中心未改动、无需重启；5m/1m 深度回填继续后台推进。

### 追加修复（2026-08-16 晚间，无限重启根因——用户现场复现「点预检/刷新后端就挂」）
- **根因**：`GET /api/compute/evolution/preflight` 与 `POST /api/compute/evolution/repromote-quarantine`
  是 `async def`，却在事件循环线程里**直跑分钟级同步重活**（多币 × 数千根 K 线富化装配 / 逐因子回测）→
  整个事件循环被堵死 → `/api/health` 超时 → 看门狗判「僵尸」→ 杀进程重启 → 重启风暴又撞上同一预检 →
  无限重启。看门狗阈值调整只是止血，此才是病根。
- **修复 1（去阻塞）**：两个接口改同步 `def` → FastAPI 线程池执行，事件循环永不占用。
  **实测**：15m 预检重活运行期间，并发健康探针 **29/29 全部 200**（此前必超时）。
- **修复 2（预检提速）**：`_load_data` 新增 `use_enrich=False` 参数，深度预检跳过富化装配
  （只取根数，不需要 funding/事件列）。预检从 **>5 分钟降到 0.9~12 秒**。
- **修复 3（预检谎报）**：原实现把「≥100 根的币」当 ok，从不与 need_bars 比较 → 5m 实际 30 天/8.7k 根
  却显示 ready=9/9。现复用进化循环的 `_check_split_depth`（同一事实源），返回 need_bars/need_days/by_symbol。
  实测真相：**15m=9/9 ready（需4,850/50天，实有 90 天）**、**4h=9/9 ready（需1,670/270天，实有 400 天）**、
  **5m=0/9（需14,450/50天，实有 30 天，回填到 50 天后自动转 ready）**。
- 5m 档在回填达标前仍会报 depth_insufficient（诚实、可观测、nudge 继续推回填，非静默停摆）。
- 看门狗防线保留：僵尸阈值 10 次 + 重启后 300s 冷却，作为「未来再有人写阻塞 handler」的兜底，不再作为主修复。

### 追加修复（2026-08-16 深夜，「回填好几次为什么还缺这么多」根因）
- **实锤否定外部限制**：直接问 asterdex API——60 天前的 5m（1,440 根/5 天窗口）、120 天前的
  15m 全部可取。**交易所不缺历史，是我们自己的回填管线没把深历史落库。**
- **根因 1（主因）：轮次调度饿死核心币短周期**
  - 一轮回填 4-6 小时（catalog 模式 507 币 × 5 所 × 10 周期），周期顺序 `1M→1d→…→5m→3m→1m`
    把短周期排最后；
  - 数据中心当天被重启 3 次（17:08 旧目标轮、20:10 换新目标、22:46 换新排序），
    **每次重启整轮从头重排** → 队尾的短周期永远轮不到；
  - 叠加旧代码目标本身就浅（1m 目标原 30 天）→ 「回填好几次」补的都是 30 天内的洞。
- **根因 2：单次大 INSERT 慢**——35k 行 upsert 一次事务要 2-4 分钟（期间占事务与行锁）。
- **修复**：`kline_history_sync` 核心币前置（BTC/ETH/SOL/ASTER/BNB/VIRTUAL/XPL/UNI/XRP
  提到列表最前）+ 周期顺序改短周期优先（1m→5m→15m→30m→1h→4h→1d→1w→1M→3m）+
  `_depth_symbols` 核心币插入头部（冷所同受益）。
- **验证（新轮次 22:48 起）**：BTC 1m 一次补齐 **26.6 天 → 55 天（38,317→74,156 根）**；
  BTC 5m 55 天（15,852 根）。此后任何重启都在轮次开头几分钟内先补齐核心币短周期。
- **另发现（待办）**：后端 ORM 会话泄漏——LeakGuard 日志 948 次强制终止 idle-in-transaction
  会话（ai_strategies/paper_positions/full_auto_sessions 查询、factor_exposure_snapshots INSERT），
  这些事务被回滚、写入丢失。与回填无关但是真实数据完整性问题，需按 thread-dump 逐个定位开事务不关的代码。
- 看门狗：backend-watchdog 已按用户要求关闭（后端再挂不会自动拉起，需手动
  `scripts\backend-watchdog.ps1`）；data-center-watchdog 保留（仅在 DC 进程真死亡时重启）。

### 追加修复（2026-08-16 深夜2，ORM 会话泄漏治理第一刀）
- **实锤写入泄漏**：`factor_engine/exposure_service.snapshot()` 原实现先开会话再在循环里
  逐币 `_compute()`（分钟级因子计算）→ 事务 idle-in-transaction >120s 被 LeakGuard 强杀 →
  `factor_exposure_snapshots` 整批 INSERT 回滚（日志实锤该 INSERT 被反复 kill）。
  **修复**：改为「先算后写」——全部计算完收集 payload，再一次性短事务插入提交。
- **读泄漏（热路径）**：scalp/midlong 循环把 ORM 会话跨 LLM/因子计算/冻结链持有
  （portfolio_budget.evaluate_open 栈实锤）。被强杀只回滚空读、无数据丢失，但消耗连接池。
  热路径重构风险高（短线刚恢复交易），**记录为后续专项**，本轮先降杀伤面：
  `DB_LEAK_GUARD_KILL_SECONDS` 120→90s、告警 20→15s（只杀 idle-in-transaction，
  正在执行的大 INSERT 不受影响）。
- 后端已手动重启加载（watchdog 已关，走 `start-dev.ps1 -NoFrontend -NoWatchdog -NoReload`，
  35s 健康）；DC 未被 stop-dev 误伤、回填持续（BTC/ETH/SOL 1m 均达 55 天=216,679 根）。

### 追加修复（2026-08-16 深夜3，短线恢复 + 真·硬预算）
- **隔离复评晋升**：`repromote_quarantine_factors(period=4h)` 复评 12 个隔离因子，
  **4 个回 PAPER**（5a0c7a/180ee6/seed_rev5/seed_rev10，net_ic 0.037~0.042 ≥0.02 阈值），
  8 个净 IC 为负继续隔离 → 精选白名单 **3 → 7 个 PAPER**（短线分数来源加倍）。
- **真·硬预算**：首版预算只截断可选长尾（阶段7），必选门禁路径（挖掘→WFO→测试集）
  实测仍可跑 80min+（15m 档 9 币全量 WFO 太重）。修正为**门禁边界 fail-closed 截断**：
  预算剩余 <300s 时跳过 WFO/测试集且**清空 promoted**——跳过门禁即不晋升，
  预算只损失吞吐、绝不降低门禁质量；报告新增 `budget_truncated` 字段。
  另：加强档参数 500/30/16 → 300/20/4（+MCTS 500/5 不变），新参数已实测生效。
- 权重自愈：复评回 PAPER 的 4 个因子暂无权重，由每小时 `factor_online_weight_hourly`
  任务与进化阶段8 在 ≤1h 内赋 1h 权重；此后短线分数有望越过连亏币 38 门槛。
- 桌面客户端 0.2.32 已打包发布（更新源 latest.yml 验证 200、广播已发）；打包器
  `npmRebuild: false` 修复 pnpm 悬空符号链接致 electron-builder 必败。
- GPU 批量求值器设计+原型入侧分支 `feat/gpu-batch-factor-eval`（提交 aef0444），
  M1 实测结论：机制可行；执行布局须换栈式（朴素全树布局比 numpy 慢 27×）；
  滚动方差须 float64（float32 灾难性消去）；500 种群下 CPU 已够快，GPU 价值在 2000+ 种群。

### 追加修复（2026-08-16 深夜4，分时段批式回填 + 交易链路收口）
- **分时段批式回填（用户要求「不一次填满，慢慢弄」）**：
  - 新增 `KLINE_DEPTH_BACKFILL_ROUND_MAX_SEC=900`（单轮工作预算）+ `KLINE_DEPTH_BACKFILL_IDLE_SEC=1800`
    （轮间休息）→ 占空比 1/3，恒为实时采集让路；
  - `_run_once` 周期循环 / `_job` / `_process_symbol` / 窗口循环四级预算检查，到点截断；
  - 断点续传 = 每币 DB 内 min/max(timestamp)，已填窗口为空秒跳过，无需额外状态。
  - 回填范围同步收紧：全 catalog 模式→热币模式（60 上限、冷所 20）——此前 507 币×10 周期×5 所
    塞满共享 2400 req/min + 本地代理，P1-Watch 87% 失败、XPL/VIRTUAL 15m 断更 47 分钟。
- **交易链路收口（订单停摆全链条修复）**：
  - 真相：纸面交易写入 `trade_facts` 事件流（paper_positions/orders 已退役、空表正常）；
    最后交易 = 06:39 SOL 平仓，之后停摆原因为 因子池薄(3→7 修复) + CPU 饿死(修复) +
    XPL/VIRTUAL 15m 断更(巡检名单补全 15 个扫描币，实测 ≤18 分钟新鲜、闸门 31 分钟)。
  - opencode 残留：独立服务器进程（123.7 CPU 小时）已杀；后端 register_opencode_jobs
    两处注册点加 OPENCODE_JOBS_ENABLED=0 默认停用。
  - 遗留观察项：`paper_orders` 每 ~30 分钟出现一次瞬时 UndefinedTable（decision_feedback
    归因任务读已退役表；表实际存在，瞬时原因未明，自愈、非致命）。

### 追加修复（2026-08-17，Agent 全量审计 + 死代码清除 + 仲裁 Gate）
- **三层审计**（总控/角色/辅助，24h 日志 + 接线双证据）后执行：
  - **删除 15 个死模块**（引用全部外科拆除，重启 12s 健康零导入错误）：
    light_trading_cycle / quick_orchestrator_eval / qaa_v3_tick_cycle / meta_strategy_selector /
    direction_agent / trade_risk_agent / trading_narrative_engine / prompt_training_system(+routes) /
    midlong_health_report / social_sentiment_collector(CryptoPanic token 明文泄露) /
    drift_monitor(线程失效) / opencode_routes / opencode_sidecar / opencode_scheduler。
    暂缓：pattern_recognition_service（3 处模块级 import 需先拆信号链）。
    保留：strategy_runtime_report（活跃 opencode 引擎按需使用，删的是其 tick job）。
  - **修复 3 真 bug**：decision_feedback 改读 trade_facts（原 PaperOrder 退役空表、
    归因永远空）；hermes_db 连接复用 + DDL 首启一次（原 500+/文件重复初始化）；
    trading_commands 的 coordinator 单例 ImportError（regime 富化静默失效）。
  - **QAA 收尾**：.env `QAA_MODE=ai_first / QAA_V3_ENABLED=false / QAA_FULLAUTO_SCHEDULE_ENABLED=false`
    ——full_auto 域 QAA tick 是饿死侧车（不建 market_summary，0 symbols 全 hold），
    ai_first 统一循环才是三周期真正使用的决策路径。
  - **新增 Agent #1：决策一致性仲裁 Gate**（`full_auto/decision_arbitration.py`）：
    多源方向冲突（master vs scalp 对同 symbol+tier 反向、双方置信度≥55）→ fail-closed
    拒绝开仓；已接线 master_execution 主链 + scalp 独立直下单两条路径；TTL 120s 内存
    视图表；env ARB_CONFIDENCE_GATE / ARB_VIEW_TTL_SEC 可调。
  - 剩余计划：专职退出 Agent（升级 position_exit_orchestrator）、因果回灌闭环
    （causal_analyzer/counterfactual → MasterController 决策约束）。

### 追加修复（2026-08-17 下午，三个新 Agent 全部上线 + smart-signal 集群清除）
- **Agent #2：专职退出 Agent**（`full_auto/exit_agent.py`）：跨 tier 退出协调 ——
  分档时间止损（short 2h / mid 48h / long 240h）+ 同向持仓≥6 叠加预警；
  默认**仅建议**（`EXIT_AGENT_EXECUTE=false`），已接线 `run_health_check` 末尾
  （用 paper_engine.get_positions(db, account_id, "open")，复用巡检 db session）。
- **Agent #3：因果回灌闭环**（`full_auto/causal_feedback.py`）：
  - `build_constraints`：trade_facts 近 24h 聚合 → tier 级（样本≥6、胜率<35%、净亏→
    开仓置信度要求 +10）+ 币种级（连亏 3 笔 → 冷却 1h），落盘 `data/causal_constraints.json`；
  - **每小时重建**：piggyback 在 `run_health_check` 末尾，文件 updated_at 节流 ≥3600s
    （不新增调度线程、不每个 tick 全表扫）；
  - **prompt 注入**：`MasterController`（trading_analysts.py）在 decision_feedback
    之后 append `constraints_text()`（≤8 条、6h 过期）到 feedback_constraints_text，
    直接进 `build_layered_master_prompt` 上下文。
  - 三个新 Agent 全部 fail-safe/可观测优先：Gate fail-closed 拒单、Exit 仅建议、
    Causal 仅文本注入——均可在 .env 关闭或收紧。
- **smart-signal 孤儿集群清除**（补上「暂缓」项，且一并拔掉整条依赖链）：
  - 证据：frontend 0 引用 + 24h 日志 0 命中（`/api/prompts/generate-*`、smart-signal 全部端点）；
  - 删除 6 文件：smart_signal_routes / ai_signal_prompt_integration_routes /
    smart_signal_generator / smart_prompt_generator / ai_signal_prompt_integration_service /
    **pattern_recognition_service**；
  - main.py 注销 2 个 router（4 行）；prompt_routes.py 拆掉 Smart Prompt 段
    （3 handler + 3 模型 + 模块级 import，保留 /adaptive-parameters 与 /strategy-styles
    这两个只依赖活跃 market_regime_service 的端点）；阻塞 IO 回归测试盘点表同步删条目。
  - 脚本留存：`scripts/purge_smart_signal_cluster.py`（可审计、可回滚参考）。
- **生产实测验证（16:48 维护轮，tick#12）**：
  - `[ExitAgent] stack_warn */short *: short 方向叠加 6 个持仓，建议检查风险预算` —— 真实触发；
  - `[CausalFeedback] 约束重建: 5 条` —— 每小时节流重建真实触发（约束文件 5 条：
    mid tier 24h 胜率17%净亏 → +10 置信度要求；ASTER/BNB/BTC/XRP 连亏3笔 → 1h 冷却）；
  - 维护轮完整结束（589s，active=75，无异常）；ArbGate 离线四用例全过
    （反向高置信 fail-closed / 同向放行 / 低置信放行 / hold 直通）。
  - 离线验证脚本：`scripts/verify_three_agents_offline.py`。
  - 已知边角：ExitAgent 的 account_id 取法已加固（不依赖 sub_mgr 分支），
    编译通过、待下次重启生效（当前进程 sub_mgr 正常，功能不受影响）。

