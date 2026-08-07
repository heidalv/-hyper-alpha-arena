# 幻方量化计划 v5 ↔ 当前代码 差异核验报告（2026-08-05）

> 对齐对象：`C:\Users\heida\Desktop\幻方对比与量化增强执行计划_v5.md`（2026-07-31 迁移前设计）
> 核验对象：`D:\001Alpha\Hyper-Alpha-Arena` 当前代码（2026-08-05 实测）
> 结论：方向正确，但约 40% 的"现状诊断"已落后于实机——多模块在 7/31-8/4 期间已落地或改造。本报告逐章核验，标注"已落地/部分落地/仍缺失"，并修正所有过时引用。

---

## 总览：计划诊断 vs 实机状态

| 计划章节 | 计划诊断 | 实机状态（2026-08-05） | 结论 |
|---|---|---|---|
| 第二章 项目现状映射 | 评级 C/B-/D 等 | 数据中心唯一数据源已改造完成、QAA 包已建 | **部分过时**，需更新 |
| 第四章 执行算法层 | "无任何拆单/执行算法" | `execution/algo.py` 已实现 TWAP/POV/FundingIS/SOR，**但未接线到下单链** | **方向正确**，从"建"改"接线" |
| 第五章 因子系统闭环 | GP 挖掘器/Codegen LLM 无、评估器无分层回测 | 因子级 WFO 门禁已存在、OOS 回测评分器已存在；GP/LLM 挖掘仍缺 | **半对**：评估环有进展，挖掘环仍缺 |
| 第六章 AI 说了算 | 投票制稀释、规则覆盖 LLM | llm_qual 已 0.30、方向已 LLM 主导（极端值）、`ai_first` 已实现；`ai_governed` 开关、组合预算仍缺 | **部分落地**，核心改造仍有空间 |
| 第七章 AI 选币 | "无反馈闭环（致命）" | `coin_rank/feedback.py` 反馈闭环**已完成**（命中率+衰减） | **已落地**，计划需改口径 |
| 第八章 学习进化 | Hermes 一闭一缺、假进化 | 诊断准确：净费归因缺、排序仍 id DESC、假进化默认开启 | **诊断有效**，变量名需修正 |
| 第十章 本地算力 | 2×2080Ti 22GB | 硬件盘点为文档级，代码无涉 | 不变化 |
| 第十一章 分阶段计划 | 阶段 1-4 | 若干项已被提前完成 | 需重排 |

---

## 第四章 执行算法层：核心修正

**计划说**："唯一下单入口链 live_executor.place_order，无任何拆单/执行算法；需新建 `backend/services/execution_algo/` 9 个文件。"

**实机**：
- `backend/services/execution/algo.py` **已存在**，包含：
  - `twap()`（L41）、`pov()`（L56）、`funding_is()`（L86）、`sor_route()`（L118）
  - `contracts/types.py` 有 `OrderAlgo` 枚举（L46-51）
- **但 grep 未发现生产代码 import `execution.algo`** → 算法层是"写了没用"的状态。

**修正方向**：计划从"新建 execution_algo/ 目录 9 文件"改为"**接线现有 `execution/algo.py` 到下单链**"——接入点不变（live_executor.place_order / paper_trading_engine.place_order），但算法实现已在，省 4-6 人天。

---

## 第五章 因子系统闭环：半对

### 5.1 挖掘环

| 计划诊断 | 实机 | 结论 |
|---|---|---|
| `_mine_candidates`（:249）= 12 手写模板 + AlphaMiner 随机搜索 | `factor_evolution_loop.py:258` 仍手写模板 + `mine_random` 随机搜索（2000 次） | **一致，未改** |
| `alpha_miner.py mine_random`（:207）纯随机 AST | `alpha_miner.py:207` 仍纯随机 | **一致** |
| `CodegenCritic`（:287）占位，`_has_llm=False` | `alpha_miner.py:287` 仍占位，`_has_llm=False`（L296） | **一致**（LLM 因子生成从未接通） |
| GP/RL 文档承诺、代码不存在 | 无 `gp_miner.py` | **一致** |
| `genetic_optimizer.py` 是策略参数 GA 非因子 | 确认是 NSGA-II（策略参数） | **一致** |

**新增事实**：`factor_engine/factors/ai_generated/` 下有 **40+ 个 AI 生成因子**（ai_gen_*.py），另有 `_ai_gen_quarantine/` 隔离区（20+）——说明存在另一条"LLM 因子生成"通道（非 CodegenCritic），计划未覆盖。需核实其生成入口（疑似 factor_sync_service / factor_jobs）。

### 5.1 评估/回测环

| 计划诊断 | 实机 | 结论 |
|---|---|---|
| evaluation.py 无分层回测、无 t 检验、无 turnover 惩罚 | `factor_engine/evaluation.py:159` 仍只有 IC/ICIR/单调性/换手/半衰期 | **一致**：5.3.3 五个新函数仍缺 |
| 训练 90 + 验证 30（VAL_BARS=30）窗口太短 | `factor_evolution_loop.py:41` `VAL_DAYS=30`、`TRAIN_DAYS=90`（L39-40），`DEFAULT_PERIOD` 已是 **4h**（L30，计划写 1h） | **半对**：窗口仍是 90/30，但周期已升级 4h |
| 无多随机种子 | 仍单次运行 | **一致** |
| 无分层回测/无测试集三层切分 | `_split_train_val`（:238）仍两段切分 | **一致** |

### 5.1 上线环 + 新增资产

| 计划诊断 | 实机 | 结论 |
|---|---|---|
| 上线环相对完整 | purge → lifecycle → shadow_judge → DSR/PBO → online_weights → drift_watcher 全部存在 | **一致** |
| 因子级 Walk-Forward 需新建（5.4.2） | **`evolution/factor_wfo.py` 已存在**（M5 因子级 WFO 门禁：pbo≤0.30 + overfitting≥0.5 + consistency≥0.6，fail-open 受 FEATURE_WFO_GATE_ENABLED 控制） | **已落地**，计划 5.4.2 改为"复用/增强" |
| 因子级回测器需新建（5.4.1） | **`factor_engine/factor_backtest_scorer.py` 已存在**（`_walk_forward_backtest` + `score_formula` OOS 评分 + `validate_and_promote`） | **已落地**，计划改为"扩展" |

---

## 第六章 AI 说了算：部分落地

### 现状 vs 计划诊断

| 计划诊断 | 实机（2026-08-05） | 结论 |
|---|---|---|
| decision_hub.fuse_signals 8 信号加权投票，llm_qual 名义 0.30 | `decision_hub.py:117` 仍加权投票，但 `WEIGHTS_MID/LONG` 中 `llm_qual=0.30`（was 0.03/0.04），`_LLM_WEIGHT_LONG/MID` 均可 env 调（`MLTO_LLM_WEIGHT_*`） | **半落地**：权重已提 10 倍，但结构仍是投票制 |
| consistency 惩罚压缩 AI | `decision_hub.py:145-163`：`consistency = max(0, 1−fw_std×2)`，分歧>0.4 才 ×0.8（阶段3a 已放宽阈值 0.3→0.4），震荡区 0.35-0.55 保护 ≥0.85 | **部分改善**，仍存在惩罚 |
| `_derive_direction`（:154-163）llm_qual≥0.6/≤0.4 AI 主导 | **实机在 `decision_hub.py:270-306`**：llm_qual≥0.6 强制 long、≤0.4 强制 short（LLM 优先于 orch_bias）；中性带 0.4-0.6 回退框架 | **已落地**（计划行号过时） |
| 规则覆盖 AI 决策：开仓 SL/TP 用 structure_stop 计算 | `orchestrator.py:447-464` 仍只调 `mid_long_structure_stop.compute`（不读 LLM）；`midlong_helpers.py:262-320` 用 `max(LLM sl, structure sl)` 规则覆盖 LLM；ATR 地板 `apply_structure_atr_floor`（midlong_trade_design.py:231，SL≥ATR×1.5） | **一致**：LLM exit_plan 未直通开仓 SL/TP |
| chop_regime 禁开长线 + funding RR 硬否决 | `mlto/open_gate.py:159-196`（注意：不是 decision_core/unified_gate.py）：7 条底线含 chop 禁开（L159-170）与 funding RR（L172-196，`MIDLONG_MIN_NET_RR=2.0`），`.env L264 MIDLONG_THESIS_OPEN_GATE=true` 全生效 | **一致，仍在** |
| ai_governed 模式需新建 | **无 `ai_governed` 关键字**。近似实现：`FULLAUTO_FLOW_MODE=ai_first`（settings:188 默认）+ `MIDLONG_AI_MANDATORY=true`（settings:203）+ `OPEN_THRESHOLDS_AI_FIRST`（decision_hub:54 松门槛） | **部分落地**：用 ai_first 近似，但无独立的 ai_governed 模式开关 |
| 组合级预算（portfolio_budget / 日 VaR / 3σ 熔断） | **均不存在**。仅有：`midlong_portfolio_risk.py`（净方向敞口≤30% / 相关簇同向≤2 / 全局≤4 仓 / 无进展超时离场）、`ENABLE_PORTFOLIO_RISK=false`（settings:1223，RL 侧 Kelly+相关性）、ATR 地板 | **仍缺失**（阶段1 第4项未做） |
| P4 修复（conviction=0 映射 neutral） | `quant_layer.py:50-64` 已实现（review_count≤0 且 conviction==0 → llm_v=0.5） | **已落地** |
| P1-3 修复（hub 阈值按 session mode） | `_use_paper_hub_thresholds(mode)`（decision_hub:68）已实现 | **已落地** |

---

## 第七章 AI 选币：反馈闭环已落地（计划最大过时点）

| 计划诊断 | 实机（2026-08-05） | 结论 |
|---|---|---|
| **"致命：无反馈闭环——命中率不可算"** | **`backend/services/coin_rank/feedback.py` 已完整实现**：`write_price_feedback` 回写 price_after_24h/72h + hit_24h/72h；`_rebuild_from_db` 聚合 30 天样本算 hit_rate/avg_pnl_24h，按阈值生成衰减乘数（<0.35→0.75 / <0.45→0.88 / ≥0.60→1.05）；`CoinRankFeedbackScheduler` 后台 900s 周期 | **已落地**，计划 7.3 第 1 项从"做"改"核验 + 深化" |
| MIN_AI_CONFIDENCE 0.60→0.50 | `AUTO_COIN_MIN_AI_CONFIDENCE = 0.50`（settings.py:2113） | **已落地** |
| V3 三层渐进 AI 审核 | `ai_review`（auto_coin_selector.py:1382）+ `_llm_high=0.75/_llm_low=0.45` 硬/软门槛 | **已落地**（V3 交付） |
| 两级漏斗（Flash 粗筛 + Pro 复核） | **不存在**，ai_review 单级 deep（L1424） | **仍缺**（12.3 L2 第 4 项） |
| LLM 从审核员到选币决策者 | 仍是审核员 | **仍缺** |
| 组合视角选币（相关性聚类） | `midlong_portfolio_risk` 有相关簇逻辑（BTC/ETH/SOL 同向≤2），但非选币聚类 | **部分** |
| resolve_exchange 不读 account.selected_exchange | 未核验（7.2 遗留弊病），建议后续核验 | 待核验 |

---

## 第八章 学习进化系统：诊断有效，变量名需修正

| 计划诊断 | 实机（2026-08-05） | 结论 |
|---|---|---|
| learning_loop 全 logger.debug 吞异常 | `learning_loop.py` 异常路径 50/64/74/81/103/124/129/152/155/201/215 行**全为 logger.debug** | **一致**（止血项仍有效） |
| trigger_job 两个 job | `learning_loop_service.py:226` 定义，`learning_loop.py:191-192` 调 `paper_outcome_backfill`/`outcome_batch`；共 5 个 job（+kelly_portfolio/coordinator/heartbeat） | **基本一致** |
| Hermes 文件位置 `hermes/` 子目录 | **实际平铺在 `backend/services/`**（hermes_agent_wisdom_engine.py / hermes_proposal_wisdom_engine.py / hermes_orchestrator.py） | **路径修正** |
| `HERMES_ENABLED=false` 已冻结 | **`HERMES_ENABLED` 变量不存在**！实际开关：`HERMES_L2_AB_ENABLED=false`（.env:235）、`HERMES_L3_AUTO_ACCEPT_PAPER=true`（settings:1261，**默认开启=假进化默认在跑**） | **变量名修正**（重要） |
| `extract_wisdom_from_outcome` 净费归因缺失 | `hermes_agent_wisdom_engine.py:24` 存在，直接用 `outcome.pnl`，**无净扣费归因**（全文件无 net/fee/手续费） | **一致**（仍需补） |
| `build_agent_wisdom_context` ORDER BY id DESC"最近 10 条" | `hermes_agent_wisdom_engine.py:115` 确为 `ORDER BY id DESC` | **一致**（仍需改造） |
| proposal 样板（ABS(pnl_impact) DESC + decay） | `hermes_proposal_wisdom_engine.py:225` 确为 `ORDER BY ABS(pnl_impact) DESC, confidence DESC`；`_should_accept_wisdom`（L345）三级门；`_compute_decay` 存在 | **一致**（样板可复用） |
| 假进化 `reconcile_implemented_paper` 需停用 | `hermes_orchestrator.py:76` 调用，定义在 `hermes_architecture_evolution_engine.py:251`（accepted→implemented 无验证）；`auto_accept_pending_paper`（L291）批量 accept；由 `HERMES_L3_AUTO_ACCEPT_PAPER=true` 驱动 | **一致且默认开启**（停用项仍有效） |
| NSGA-II → v5_runtime_gates（60s 生效） | `genetic_optimizer.py:350` NSGA-II 在；**但 `v5_runtime_gates.json` 文件已删除**，通道收敛到 `RuntimeGovernor → runtime_tuning.json`（evolution_scheduler.py:281 `_sync_champion_to_v5_gates` 提交 runtime_governor.submit_intent） | **通道变更**（计划引用需更新） |

---

## 第十二章 LLM tier：调用点行号修正

| 计划诊断 | 实机（2026-08-05） | 结论 |
|---|---|---|
| KlineAnalyst（trading_analysts.py:1000）deep | `trading_analysts.py:825` 类、`:1000` config、`:956` 固定 tier="deep" | **一致**（可切 quick 项仍有效） |
| AutoCoin AI 审核（auto_coin_selector.py:904）deep | **实际在 `:1424`**（ai_review L1382，deep 配置 L1424）| **行号修正**，tier 一致 |
| AutoCoin 到期评估（:1619）deep | **实际在 `:2601`**（_ai_expiry_removal_review L2594）| **行号修正**，tier 一致 |
| MLTO qual（trend_agent.py:922）deep | `qual_layer._call_llm`（L444）→ trend_agent.py:979 / swing_agent.py:739，经 get_llm_config_for_analysis（deep）| **一致** |
| MasterController（trading_analysts.py）deep | `trading_analysts.py:1764` 类、`:3452` _call_llm、`:3467` deep | **一致** |
| scalp_flash_veto / scalp_agent quick | `scalp/scalp_flash_veto.py:63` quick、`scalp/scalp_agent.py:24` quick | **一致** |
| 因子生成 CodegenCritic 接通时用 quick | **目前 CodegenCritic 完全不调 LLM**（占位）| **事实修正**：接通时用 quick 的方向不变 |
| 双 tier 机制 | `llm_config_service.py:143-154` 存在；`is_reasoning_model` 含 deepseek-v4（:471）| **一致**（P0-A 修复后） |
| LLM 代理 | `LLM_HTTP_PROXY`/`LLM_HTTPS_PROXY`（settings:272-273，默认直连）、trust_env=False | **已落地**（新增，计划未覆盖） |
| LLM_SEMANTIC_CACHE_ENABLED 默认关 | settings 未定义，`llm_config_service.py:1379` 读 env 默认 false；但 `framework_rollout.py:32` 激进注入 true | **半落地**（实际可能 true） |

---

## 数据中心 / 数据工程（计划 v5 完全未覆盖，实机已重大改造）

**计划 v5 写于 7/31 迁移前，未涉及数据中心。实机在 8/1-8/4 已完成以下改造：**

| 改造 | 实机状态 |
|---|---|
| `MARKET_DATA_DC_ONLY=true`（.env:28）数据中心唯一数据源 | `data_center.py` 的 get_klines/get_price/get_all_market_tickers 全部读 DB（crypto_klines + symbol_catalog），禁止直连交易所 |
| 独立数据中心进程 | `backend/workers/market_data_center.py`：K线 P0/P1/P2 深回填 + ticker + orderbook/trades/OI + funding + asset_metrics，健康检查 9100 |
| 限流体系 | `_AsterdexRateLimiter`（双桶 live/backfill + 全局 ban 冷却）+ `_ColdExchangeRateLimiter`（每冷所独立） |
| 深度回填 | `kline_history_sync.py DepthBackfillRunner`：1m~1w 全周期，1w 目标 520 天，冷所短周期浅回填（1m/3m/5m=15 天、15m/30m=45 天），全 catalog 600 币 |
| 保留期对齐 | `db_maintenance.py` RETENTION：1m/3m/5m=30 天、15m/30m=90 天、1h/4h=400 天（与回填目标对齐，修复"删→补"死循环） |
| 数据健康 | K线链路有 `kline_freshness_inspector`（分级告警+飞书）；**行情/链上链路无统一监控**；链上链路完全无告警 |

**计划 2.3"数据环节补强"结论修正**：
- 缺口 A（数据源健康监控）：K线链路已有，行情/链上缺 → 计划从"全建"改"补齐行情/链上两条链路 + 统一看板"
- 缺口 B（DataProvider 抽象层）：仍不存在，Coinglass 仅占位（derivatives_collector.py:41 返回 None），实际用 Coinalyze 免费 API → 计划仍有效

---

## crypto 独有 7 因子落地核验

| 因子 | 实机 | 结论 |
|---|---|---|
| funding_momentum | ✅ `perp_factors.py:56` + `orderflow_crypto_factors.py:141` | 已落地 |
| cvd_divergence | ✅ `orderflow_crypto_factors.py:60,88`（Tick Rule 代理算法，无 taker 列时降级）| 已落地（代理版） |
| ofi | ✅ `orderflow_crypto_factors.py:102`（代理版本，无 L2 深度时）| 已落地（代理版） |
| liquidation_heatmap | ❌ 仅 `perp_factors.py:62` 有近似 `liquidation_rank` | **缺** |
| onchain_netflow | ❌ 无 | **缺** |
| wick_protection | ❌ 无 | **缺** |
| stablecoin_mint_burn | ❌ 无 | **缺** |

> 关键约束：`orderflow_crypto_factors.py:4-21` 自述 K线管道只落库 OHLCV，**无 taker 买卖量拆分列、无 L2 盘口深度列**，CVD/OFI 用 Tick Rule 代理，已预留"出现 taker_buy_volume 列自动切换真实数据"钩子。→ 计划第 7 项 L2 重建层（阶段2）是真实数据源的前提，仍有效。

---

## 第十一章 分阶段执行计划：重排建议

**计划认为"要做"但实机已完成的（从阶段清单移除或改"核验"）：**

| 原计划项 | 实机 | 处理 |
|---|---|---|
| 阶段1 第8项 选币反馈闭环（7.3 第1项） | `coin_rank/feedback.py` 已完成 | 改为"核验 + 历史基线回填 + 前端展示" |
| 阶段2 第6项 因子级回测器 + Walk-Forward（5.6 步骤 5-6） | `factor_wfo.py` + `factor_backtest_scorer.py` 已存在 | 改为"复用扩展 + 报告卡落库" |
| 阶段3 执行算法 v0.5→v3（4.6） | `execution/algo.py` 已实现未接线 | 改为"接线 + 灰度 + TCA 基线" |
| 阶段2 第15项 数据健康（2.3 缺口A） | K线链路已有 | 改为"补齐行情/链上链路" |
| 数据中心全部项 | 已改造完成 | 新增"数据完整性验收"一节 |

**仍缺失、计划必须保留的（核心待办）：**
1. GP 挖掘器 + Codegen LLM 接通（5.3.1/5.3.2）——完全未动
2. 评估器 5 新函数（5.3.3：quantile_backtest/ic_significance/rolling_decay/parsimony/admission_gate）——未动
3. 三层切分 + 周期分档（5.4.3）——未动
4. 因子报告卡落库 + 衰减下线自动化（5.5）——未动
5. ai_governed 模式 + LLM 止损直通（6.3）——未动
6. 组合日 VaR / 3σ 熔断 / portfolio_budget（阶段1 第4项）——未动
7. L2 重建层 + 4 个缺失 crypto 因子（阶段2 第7/8项）——未动
8. 数据源抽象层 DataProvider（2.3 缺口B）——未动
9. Hermes wisdom 闭环 + 假进化停用 + 净费归因（8.3）——未动
10. 执行算法接线 + TCA（阶段3）——未动
11. LLM tier 优化（KlineAnalyst 切 quick / AutoCoin 两级漏斗，12.3 L1/L2）——未动
12. 学习止血（learning_loop 静默→告警，8.3 阶段1）——未动

---

## 引用修正清单（微调计划文档时必须改）

| 原引用 | 修正为 |
|---|---|
| `backend/services/evolution/gp_miner.py`（新建） | 不变（仍新建），但 5.3.1 说明"现有 DSL 复用"已可用 |
| `factor_evolution_loop.py:249` `_mine_candidates` | 实机 `:258` |
| `alpha_miner.py:207` | 实机 `:207`（不变）|
| `alpha_miner.py:287` CodegenCritic | 实机 `:287`（不变）|
| `evaluation.py` | 实机 `factor_engine/evaluation.py:159` |
| `_split_train_val`（factor_evolution_loop.py:229）| 实机 `:238` |
| `decision_hub.fuse_signals`（:95-102）| 实机 `:117` |
| `_derive_direction`（:154-163）| 实机 `decision_hub.py:270-306` |
| `orchestrator.py:404-422` structure_stop | 实机 `:447-464` |
| `open_gate.py:159-196` | 实机 `mlto/open_gate.py:159-196`（注意与 `decision_core/unified_gate.py` 是两个模块）|
| `trading_analysts.py:1000` KlineAnalyst | 实机 `:825` 类、`:956` tier |
| `auto_coin_selector.py:904`（AI 审核）| 实机 `:1424` |
| `auto_coin_selector.py:1619`（到期评估）| 实机 `:2601` |
| `trend_agent.py:922` | 实机 `:979`（经 qual_layer._call_llm）|
| `scalp_flash_veto.py:63` | 实机 `:63`（不变）|
| `HERMES_ENABLED=false` | 修正为 `HERMES_L2_AB_ENABLED=false` + `HERMES_L3_AUTO_ACCEPT_PAPER=true`（需停用）|
| `v5_runtime_gates.json` | 已删除，修正为 `RuntimeGovernor → runtime_tuning.json` |
| Hermes 文件 `backend/services/hermes/` | 实机平铺在 `backend/services/` |
| `execution_algo/` 新建 9 文件 | 实机已有 `execution/algo.py`（4 算法），改为"接线" |

---

## 结论

1. **计划方向完全正确**，但约 40% 的"现状诊断"已落后于实机（7/31-8/4 数据中心改造 + 选币反馈闭环 + 因子 WFO/OOS 评分器 + AI first 灰度已落地）。
2. **核心待办不变**：因子挖掘闭环（GP/LLM）、评估器升级、组合风险预算、AI 说了算模式、学习进化止血——这些仍是本项目最大短板。
3. **建议**：按本报告微调 v5 计划为 v6，将"已落地"项转为"核验/深化"，将"新建"项中已存在实现的（execution/algo.py、factor_wfo.py、factor_backtest_scorer.py、coin_rank/feedback.py）改为"复用/接线"。
