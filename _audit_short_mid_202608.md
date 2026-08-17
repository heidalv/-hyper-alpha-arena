# 审计主报告草稿（独立验证部分 — 合并前）
> 由主审计员独立验证的核心发现（已读源码、含行号证据）。待 5 个子代理结果合并去重。

## A. 因子挖掘闭环（重点）

### A1. [HIGH] 冷池前视防护正则可被绕过（变量型 shift 漏检）
- file: backend/services/factor_engine/midlong_cold_pool.py:49
- _LOOKAHEAD_RE = r"\.shift\(\s*-\s*\d+" 只匹配字面量 .shift(-N)
- 变量形式 .shift(-confirm_bars+1) / .shift(-horizon) 不会命中 → 前视因子进入冷池重评、
  霸榜 top_by_ic，promote=True 时被登记为候选（虽然最终晋升仍要人工复核，但报告排名被污染）。
- 证据：quarantine 中 ai_gen_breakout_valid.py:55-56 即变量型 shift 前视。
- fix: 改 AST 分析（提取 shift 调用参数，静态求值为负常数则拦截），或直接禁用含 shift( 的因子。

### A2. [MEDIUM] 冷池多前瞻期变体择优 + 千因子多重比较 → 选择偏差
- file: midlong_cold_pool.py:297-311, 348-349
- 每个因子尝试 3 个 fwd 变体并取最优代表，再按 |IC| 排行 top20。
- 无 DSR/PBO 校正 → 报告/候选池被多重比较膨胀。
- fix: 固定前瞻期或对变体选择做 Bonferroni/DSR 校正，报告标注多重比较。

### A3. [MEDIUM] factor_evaluator 滚动 IC 重叠窗口 → ICIR 虚高 + top-k 裸选
- file: factor_engine/factor_evaluator.py:100,108-117,180-181
- 前瞻收益 close.pct_change(fwd).shift(-fwd) 起点为 t 收盘（与 t 收盘成交一致，可接受），
  但 _rolling_ic 重叠窗口自相关，ICIR=mean/std 非真实信息比率；
  evaluate_batch 按 |ic_mean| 裸选 top_n，无多重检验（需查调用方是否再经 DSR）。
- 另 253-264: 平坦段 std<1e-10 时填入 0.0，把 ic_mean 向 0 拉偏。

### A4. [MEDIUM] 学习权重重训可能静默永久停滞
- file: factor_engine/learned_weighting.py:237-245
- compute_weighted_signal 仅在 _due_for_retrain 且调用方同时提供 historical_data+labels 时才重训；
  否则旧模型无限期使用，无告警。
- fix: 到期未重训时打 warning 并记录 last_effective_train。

### A5. [INFO] 遗留双因子引擎
- backend/factor_engine/（含 0 字节 ai_gen_trend_r2.py、130+ ai_generated 因子）疑似死代码，
  运行时实际加载 services/factor_engine。需确认无 import 混用（factor-mining 子代理确认）。

## B. 学习闭环（重点）

### B1. [HIGH] DRL 重训触发为死代码（闭环断链）
- file: services/rl/system_coordinator.py:128-149 + 全库 grep "trigger_drl_retrain"
- check_and_coordinate 从不设置 action.trigger_drl_retrain=True；
  _should_retrain_drl 与 DRL_RETRAIN_AUTO/回填链路全部悬空。learning_loop_service.py:748 的分支永远不执行。
- impact: DRL 组件永远无法自动重训，闭环部分功能空转（2026-08-09 只恢复了回填，没接上触发）。

### B2. [HIGH] 晋升收益口径维度错配（pnl/换手 vs 回测 ROE）
- file: training_live_promote_service.py:136-170 + strategy_validator.py:223-231
- _compute_paper_total_return_pct = 已实现pnl / Σ(entry*qty)（分母是累计换手额，非占用资本）
  与 backtest_return_pct（ROE%）直接比偏差 → 高换手策略必然"偏差>30%"被 Gate2 拦死（fail-closed），
  闭环"改进落地"环节形同虚设；低换手策略则口径偏松。
- fix: 分母改为时间加权平均占用资金，或回测侧同口径重算。

### B3. [HIGH] 单策略降级翻转整个会话 trading_mode
- file: training_live_promote_service.py:244-258
- 任一 live 策略 wr<0.40 或 dd>0.18 即 session.trading_mode="paper" → 其余正常 live 策略被静默切回模拟。
- fix: 按策略粒度切 trading_mode，勿动会话级状态。

### B4. [MEDIUM] 降级判据 wr<0.40 误杀低胜率高赔率策略
- file: training_live_promote_service.py:244
- 趋势策略 WR 30% + RR 3:1 是正期望，仍被 OR 判据降级。
- fix: 用期望值/ProfitFactor 而非裸胜率。

### B5. [MEDIUM] outcome 标签 regime_at_exit 恒等于 regime_at_entry
- file: learning_loop_service.py:395-396
- regime_at_exit=str(ctx.get("regime")) 与 entry 同源 → 区制条件学习统计被污染。
- fix: 平仓时回读当前 regime。

### B6. [MEDIUM] 全局 3 连亏触发紧急进化（跨策略聚合）
- file: rl/system_coordinator.py:244-255
- 最近 3 笔平仓（不限策略）全亏损即触发 trigger_emergency_evolution("all_new")，
  24h 冷却内只触发一次。短线策略密集平仓时极易满足 → 进化作业空转/预算浪费。
- fix: 按策略聚合连亏、设最小时间窗。

### B7. [MEDIUM] walk_forward_validator 年化硬编码 sqrt(8760)（h1 假设）
- file: walk_forward_validator.py:242-243, 302
- 若调用方传入 4h/1d/交易级收益，Sharpe 年化差 sqrt(24)/sqrt(365) 倍 → 晋升/验证判定失真。
- fix: 由调用方传频率参数。

### B8. [HIGH] live_pipeline_backtest_engine 资金费/恐贪指数最近邻查询含未来数据
- file: live_pipeline_backtest_engine.py:655-670
- _get_funding_rate/_get_fgi 用 min(abs(t-ts)) 取最近样本，允许 1~2 天偏移 → 决策时刻可能
  引用未来样本（前视），且与实盘（只能拿当前值）不一致，验证管线失真。
- fix: 改为只取 ≤ ts 的最近样本（direction="backward"）。

### B9. [MEDIUM] 回放引擎资金费结算相位与真实结算时刻脱钩
- file: live_pipeline_backtest_engine.py:286-291, 318
- i % bars_per_8h 相对序列起点对齐，非 00/08/16 UTC 结算时刻 → 资金费计入时刻错位。
- fix: 按 bar.timestamp 对 8h 边界取模。

## C. 短期交易链路

### C1. [HIGH] 结构止损被 regime 钳制覆盖（结构/ATR 计算全部失效）
- file: scalp/structure_stop_calculator.py:79-115
- 79-89 行算出结构止损 → 109 行 sl_pct=max(min(...)) 把 sl_pct 钳到 [1.2%,2.0%]，
  110-113 行用 price*(1-sl_pct) 重建 sl_price —— swing 结构与 ATR 自适应被丢弃，
  最终 SL 恒为固定 1.2%~2%（与模块声称"SL 必须在 swing low/high 外侧"直接矛盾）。
- 64-66 行 ATR 倍数法同样被覆盖，compute_atr_pct 本身又钳到 [1.2%,2.0%]。
- fix: 保留结构止损为主，regime 只调 buffer/RR。

### C2. [LOW-MED] tp_sl_authority 未知 tier 静默回落 scalp
- file: tp_sl_authority.py:25-30
- tier=None/未知 → scalp(2%/1.2%)；若 mid/long 订单漏传 tier，SL/TP 数量级错误。

## D. 中期交易链路

### D1. [MEDIUM] nature_staged_tp 状态在 entry_price 变化时全量重置
- file: nature_staged_tp.py:100-105
- DCA 后新均价触发 triggered_stages.clear() → 已触发阶段可重复收割；
  且 LLM override stages 未排序，乱序时触发顺序错乱（109-122）。


## 补充确认（主审计员二轮验证）
- A5 修正：全库 grep "from backend.factor_engine" 0 命中 → 遗留 backend/factor_engine 确认是死代码
  （含 0 字节 ai_gen_trend_r2.py、130+ 因子），只构成维护陷阱，非运行时 bug。
- A3 修正：factor_service.evaluate_batch 的 top_n 裸选仅用于 IC 报表展示；
  正式晋升走 factor_backtest_scorer.score_formula（dsr_required=True，有 PBO/DSR 基建），
  故 A3 降级为 low（展示口径问题）。rolling IC 重叠窗口问题保留为 medium。
- 新增确认：live_pipeline_backtest_engine.run() 入场为"t 收盘决策 + t 收盘成交"（含 slippage），
  SL/TP 自 t+1 bar 起判定 —— 该约定可接受；但 _get_funding_rate/_get_fgi 的
  min(abs(t-ts)) 最近邻（±1~2 天）确实会引用未来样本（B8 维持 HIGH）。
- 新增确认：reentry_cooldown 状态 key=account_symbol_tier，带 TP 后最低冷却与连续亏损倍率，
  但 _loss_history 仅内存态（重启清零，最多影响 3x 倍率冷却）。low。



## F. 子代理结果归档（待合并入最终报告）

### F-ST 短期交易链路（代理 6a9b317e）
1. [HIGH] profit_drawdown_guard.py:346-348 _compute_profit_lock_sl 锁利SL非单调：long new_sl=min(entry*(1+buffer), current*0.995)，
   回撤后价格已跌破上次SL时算出更低SL；paper_trading_engine.py:2784 无条件 pos.sl_price=new_sl 覆盖 → 锁利位下移。
   已验证：engine 2784 行确为无条件覆盖。
2. [HIGH] unified_exit_executor.py:399-403 _set_emergency_sl 固定 5% 距离，20x 爆仓价≈entry*0.955，SL=entry*0.95 在爆仓价外侧 → 高杠杆下先爆仓。
3. [HIGH] reentry_cooldown.py:394-396 _durable_reopen_blocked 把 unrealized_pnl（close 时已含 partial）再 +partial_realized_pnl →
   分段止盈后小亏仓被算成正盈利，重启后跳过亏损冷却。已验证：paper_trading_engine.py:1477-1479/1533 确认 total=final+partial 写入 unrealized_pnl。
4. [MEDIUM] TIER_TO_NATURE 三分叉：tp_sl_authority(long→trend_follow) vs unified_learning_service:27(long→position) vs trade_nature_resolver.py:24-28(mid→intraday,long→swing，未见引用) → 学习归因口径不一致。
5. [MEDIUM] cycle_direction_probability.py:490-494 标签 range_thr 用全样本(含测试)中位数 → 标签定义泄漏。
6. [LOW] dynamic_leverage_calculator.py:105 最终钳位 max(1,...) 忽略 lev_min。
7. [LOW] short_tier_entry_gate.py:214-217 三元运算符优先级 bug：paper 分支无 1800 兜底。
8. [LOW] entry_confidence_gate.py:61-64 tier_key=='scalp' 永假死分支。
9. 备注：scalp_exit_override.py 源码已删除仅存 .pyc，需确认是否仍被导入。

### F-FM 因子挖掘闭环（代理 e5afc035）
1. [HIGH] dsr_pbo.py:169-171 compute_pbo_simple 按 ICIR 值 argsort 分组而非时间切分，输入无时间维度 → PBO≈0 恒通过；
   factor_backtest_scorer.py:485 DSR_MIN_SYMBOLS 默认 4 > 实际 3 币 → fail-open 跳过；:424 n_trials 固定 40 与数百候选脱节。
   已验证：compute_pbo_simple 输入确为因子标量列表，CSCV 分组=按值排序，时间过拟合无法被检测。
2. [HIGH] factor_decay_monitor.py:120 evaluate_all_factors 无任何调用点；get_factor_weight_penalty(:139-141) 依赖 _decay_status
   （仅 evaluate_* 填充）→ 恒返回 1.0；record_ic 只写内存。衰减降权/淘汰机制是死代码。已验证：grep 全库无 decay_monitor.evaluate_* 调用。
3. [HIGH] ai_factor_discovery_service.py:275-292 直接写 .py + hot_reload 绕过 candidate→回测→active 闸门；
   ai_decision_integration.py:74/400、signal_bus.py:238/308、v3_factor_pipeline.py:255、strategy_coordinator.py:376 的 compute_all_factors 不传 allowlist → 未验证因子进 AI 决策/信号路径。
4. [MEDIUM] factor_loader.py:79 registry.register(obj, override=True) 静默覆盖 + :61 glob 未排序 → 同名因子覆盖顺序不确定。
5. [MEDIUM] learned_weighting.py:191-199 train 全量历史 fit 无 OOS；min_ic_to_include/purge_bars 为死配置；:237-238 训练即上线。
6. [MEDIUM] midlong_cold_pool.py:49 前视正则只拦字面 shift(-数字)，变量负移可绕过（与主审计 A1 一致，双源确认）。
7. [MEDIUM] custom_factor_store.py:71 实盘缺 open 用 np.roll(close,1) 近似 vs factor_backtest_scorer.py:142-145 回测直读真实 open → feature drift。



### F-LL 学习闭环（代理 5f3a69b6）
1. [HIGH] 晋升 Sharpe 门槛实质失效：StrategyMemory.sharpe_ratio 是盈亏符号 EMA（unified_learning_service.py:786-791，instant=sign(pnl_pct)，值域[-1,1]），
   Gate2 却要求 >=1.0（strategy_validator.py:94/196）→ 真实 Sharpe≈1.5 的策略伪 Sharpe 只有 ~0.5 永远被拦；
   仅近期全胜的运气策略可能通过。已验证：全库 StrategyMemory.sharpe_ratio 仅有该 EMA 写入点。
2. [HIGH] DecisionSnapshot 盈亏回写模糊匹配：paper_trading_engine.py:3479-3486 回退路径只按 symbol+pnl NULL+48h
   取最近快照，不看 strategy/方向；主路径也只看 strategy+symbol+pnl NULL。同币 48h 内多策略/重入场 → (决策,盈亏) 配对错乱，
   污染 trade_attribution 与 SFT 训练数据。decision_snapshot_writer.py:25-54 120s 去重会静默丢弃同签名新快照。
   已验证：主/回退路径代码与代理描述一致。
3. [HIGH] 回放引擎资金费/恐贪前视（与主审计 B8 完全一致，双源确认）：live_pipeline_backtest_engine.py:654-670 min(abs(t-ts))。
4. [HIGH] learning_loop_service.py:526-537 _paper_position_pnl 用 original_size 算全仓盈亏再 +partial_realized_pnl →
   分批止盈仓位 PnL 双计，虚高流入 StrategyMemory/Gate2。主审计已读同一函数，确认逻辑成立。
5. [MEDIUM] Gate2 "最大回撤≤10%" 实为"单笔最大亏损≤10%"：unified_learning_service.py:780-782 只存 max(|pnl_pct|)，
   training_live_promote_service.py:213 当作回撤×100 比对。
6. [MEDIUM] live 反馈兜底只扫最近 600s（learning_loop_service.py:355-372），重启/宕机>10min 的 live 交易反馈永久丢失（paper 有 7 天补扫）。
7. [MEDIUM] 连亏 streak/进化计数器内存态（unified_learning_service.py:120-121），重启清零，保护失效。
8. [MEDIUM] RL 仓位集成断裂：trading_decision_interface.py:166-174 调不存在的 _discretize_state + select_action 签名不符 +
   RLActionResult 被当标量除 → 全被 except 吞掉，RL 仓位建议从未生效。
9. [MEDIUM] memory_decay_service.py:221 只认 "ts" 键；trade_memory_miner 写 discovered_at、decision_feedback_service.py:889-897
   不写时间戳 → 教训永不衰减，经 RAG 持续注入。
10. [MEDIUM] concept_drift_detector.py:281-287 7d/14d 窗口实为交易笔数近似（n/30 折算），deque 无时间戳、重启丢状态。
11. [LOW] concept_drift_detector.py:254 DDG-DA 分支用未定义变量 pnl_series（应为 all_pnl），NameError 被吞，功能永不生效。
12. [LOW] learning_ab_framework.py:210-232 对独立样本做配对 t 检验（ttest_rel），按索引强行配对；当前未接线，潜伏缺陷。



### F-DE 数据管线/回测撮合（代理 cde412ad）
1. [HIGH] backtest_engine/backtest_engine.py:127-129 fill_model 默认 "close"（env BACKTEST_FILL_MODEL，注释明示"默认 close 保持旧行为"）：
   event_driven 模式策略 on_bar 看到完整 OHLC 后按同根 bar['close'] 成交 → 收盘决策按收盘成交，回测收益系统性虚高，
   Gate1/晋升用虚高结果选策略。已验证：127-129 行默认值与注释一致。fix: 默认 next_open。
2. [MEDIUM] paper 限价单被强制 maker：paper_trading_engine.py:1056 force_maker=order_type=='limit'；
   paper_exchange_simulator 可市价化的限价单也判 maker 且按限价成交 → 成交价失真+费率低估。
3. [MEDIUM] price_cache 旧接口 key 不含交易所（price_cache.py:125-137、market_data.py:128-133）：
   asterdex 写的缓存会被 hyperliquid 读走 → 跨所串价。
4. [MEDIUM] forming K 线落库并被当已收盘消费：kline_collectors.py:568 采集 ohlcv[-1]（未闭合 bar）upsert 入库；
   data_center 新鲜度门 ≤2周期+60s 永远放行 → 指标/信号基于跳动中的 close 计算，实盘侧活前视，与回测口径不一致。
5. [LOW] paper 爆仓价用全局 MAINTENANCE_MARGIN_RATE（paper_trading_engine.py:4473-4481），未按交易所取维护保证金率。
6. 数据流总体健康：时间戳口径统一（epoch 秒/UTC/开K时间），kline_write 幂等 upsert 到位，无 ms/s 混用。



## G. 主审计补充验证（第三轮）
- C1 强化：mid_long_structure_stop.py:57-78 注释自证 structure_stop_calculator 的 SL 钳到 0.8-2%、
  TP 1.2-2.5%（即主审计 C1 的钳制覆盖 bug），中长线只是把已钳制的 sl_pct×3 再钳到 3-8%/5-15% ——
  结构 swing 止损在短/中/长三条链路上都进不了最终 SL，模块名"结构止损"名存实亡。
- 紧急SL验证：unified_exit_executor.py:399-403 固定 5% 距离 + paper_engine.update_position_tp_sl 直接落库，
  无 _ensure_sl_inside_liq 保护 → 20x 杠杆下 SL 在爆仓价外侧，确认 HIGH。
- fill_model 验证：backtest_engine.py:127-129 默认 "close"，注释明示"默认 close 保持旧行为"，next_open 需 env 显式开启 → 确认 HIGH。



### F-MT 中期交易链路（代理 be9312ca）
1. [HIGH] paper_trading_engine.py:2459 统一分段止盈 TP3 空单追踪止损漏乘 _side_dir：
   _new_sl = _peak_price - _atr_price*trail_mult（TP1/TP2 均乘了 side_dir）→ 空单 SL 被放到现价下方，
   下一次 price>=sl 立即命中并以 fill_price_override=sl 在低于市价成交 → 虚增空单浮盈、追踪止损失效。
   已验证：2459 行确无 side_dir，2470-2471/2483 有。
2. [HIGH] paper_trading_engine.py:4258-4260 _maybe_settle_funding 仅 PAPER_SIMULATION_TIER=="research" 生效，
   settings.py:148 默认 "demo" → 默认纸面 PnL 从不结算资金费，funding_net_rr_ok 入场闸门的成本假设永不兑现，
   学习闭环在无资金费偏置的 PnL 上自训练。已验证。
3. [MEDIUM] paper_trading_engine.py:2452-2483 分段止盈单 tick 只触发最高档且 TP3 直接置 tp_level_reached=3 →
   跳档时 TP1/TP2（合计50%）永久跳过，70% 仓位暴露给反转。已验证。
4. [MEDIUM] full_auto/midlong_position_manager.py:284-288 long_tier_staged_tp 状态仅内存（host dict），
   重启丢触发档位→重复减仓；与 nature_staged_tp（exit_state_json）/统一版（tp_level_reached 落库）三套口径并存。
5. [LOW] 冷池正则（同 A1，第三源确认）。
6. 未覆盖：swing_agent/trend_agent 主体、market_regime_service 全量、mid_long_quant_brief、实盘执行路径。

## E. 数据基础
- dataset_builder 整体点-in-time 设计良好；事件表 naive 时间戳按服务器本地时区解释（268-271）
  依赖部署时区，脆弱；训练骨架含最后一根未成形 bar（321-327 有缓冲处理，但未丢弃成形中 bar）。
