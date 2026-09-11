# 运行时配置事实清单（RUNTIME_CONFIG FACTS）

> 单一事实来源：本表登记「关键配置开关的声明意图与期望值」。
> 校验脚本：`scripts/check_config_drift.py`（有漂移时退出码 1）。
> 变更纪律：改 `.env` 必须同步本表，同一 commit 提交；提交前跑校验脚本确认 0 漂移。
> 首次生成：2026-08-13，期望值以当时 `.env` 实况为准；备注中 `⚠ 待确认` 表示
> 实况与历史 README/文档声明不一致，需维护者确认「以哪边为准」后消除该标记。

| 配置键 | 声明意图 | 期望值 | 备注 |
| --- | --- | --- | --- |
| MARKET_DATA_DC_ONLY | 行情唯一来源=数据中心落库，禁直连交易所 | true | README §数据中心唯一数据源 |
| MARKET_DATA_VERIFIER_ENABLED | 行情数据校验器 | true | 安全关键 flag |
| V5_DECISION_CORE_ENABLED | V5 决策核心总闸；Live 下为 false 启动即抛错 | true | README §V5 |
| V5_DAILY_TRADE_CAP_ENABLED | 日交易笔数硬约束 | true | 安全关键 flag |
| V5_MAX_DAILY_TRADES_LIVE | 实盘日开仓上限 | 12 | README §V5 频率治理 |
| V5_MAX_DAILY_TRADES_PAPER | 模拟盘日开仓上限 | 10 | 2026-09-03 v3-P0 止血：60→10。原意图「高配额攒样本」在负期望下等于加速亏损，先降频再谈样本量 |
| V5_MIN_RISK_REWARD | 中长线一体盈亏比硬约束 | 1.8 | 同上 |
| V5_SCALP_MIN_RR | 短线 Live 盈亏比下限 | 2.0 | ★ 2026-09-02 P2.2：1.4→2.0。实测近14天 short tier 812 笔均盈 0.631/均亏 0.606，盈亏比仅 1.042、胜率 41.4%，该胜率下需 1.417 才打平——低 RR 是必亏的直接原因。回放定标（做多+pwin≥0.55，2680条，SL1.1%）：RR1.3=+25.4bp→RR2.3=+44.0bp。未取更高：RR≥3 时止盈命中率跌破 13%、持仓顶满 max_hold，收益来源变成持有 beta 而非止盈兑现 |
| V5_TREND_MIN_RR | 中长线 Live 盈亏比下限 | 1.8 | 同上 |
| ENABLE_KELLY_POSITION | Kelly 仓位上限夹紧 | true | ARCHITECTURE §5 |
| ENABLE_PORTFOLIO_RISK | 组合风险聚合（PortfolioRiskAggregator） | true | 2026-08-13 用户确认实况即意图（架构文档 v3 旧描述待同步） |
| ENABLE_COORDINATOR | SystemCoordinator 自动触发进化/重训仲裁 | true | ARCHITECTURE §5 |
| ENABLE_DRL_INTEGRATION | DRL 进入主循环（已下线，恒 false） | false | README §学习进化系统 |
| DRL_SHADOW_MODE | DRL 影子预测记录（无执行权） | false | 2026-09-02 改为跟随实况 false：DRL 主集成已下线（ENABLE_DRL_INTEGRATION=false），影子记录随之关闭才自洽；该项无执行权，关闭不影响任何交易行为。（08-13 曾登记 true） |
| DRL_RETRAIN_AUTO | DRL 自动重训 | true | 2026-08-13 用户确认：保持实况 true |
| PROMPT_EVOLUTION_ENABLED | Prompt 自动进化 | true | 2026-08-13 用户确认：有意开启（README 旧文案「默认 false」已修正） |
| PROMPT_TRAINING_AB_ENABLED | Prompt 训练 A/B | false | |
| MIDLONG_EXEC_AUTHORITY | 中长线执行权归属（trend/mlto） | mlto | 2026-08-13 用户确认：实况 mlto 即意图（README 旧文案「默认 trend」已修正） |
| MIDLONG_MLTO_CONTROLS_EXEC | MLTO 控制中长线执行 | true | 与上一条一致 |
| MIDLONG_POSITION_MGMT_ENABLED | Phase 5 持仓发展分析（模式 B） | true | README §Phase 5 |
| MIDLONG_DIRECTION_CONSISTENCY_ENABLED | 中长线方向一致性门 | false | |
| MULTI_VENUE_FUNDING_COLLECTOR_ENABLED | 多场所资金费采集（Binance/Bybit/OKX/Gate/Asterdex） | true | 2026-08-13 用户确认：实况 true 即意图（README 旧文案「默认 false」已修正） |
| CYCLE_PROB_GATE_ENABLED | 周期方向概率门禁（校准达标才硬拦截） | false | README §周期方向概率引擎 |
| ARBITRAGE_ENABLED | V3 统计套利总开关 | false | README §套利开关语义 |
| RISK_ENGINE_ENABLED | 风险引擎主开关 | true | 安全关键 flag |
| LIVE_SCALP_VETO_FAIL_OPEN | Live 短线否决层 fail-open（禁止） | false | 三周期整改：Live 下必须 fail-closed |
| LIVE_ORCHESTRATOR_HARD_GATE | Live 编排器硬门禁 | true | |
| LIVE_DIRECTION_COHERENCE_MODE | Live 方向一致性模式 | enforce | |
| LEGACY_RISK_HARD_ROLLBACK | 旧风控硬回滚 | false | |
| CONSECUTIVE_LOSS_PROTECTION_ENABLED | 连续亏损保护 | false | |
| SCALP_DAILY_OPEN_CAP | 短线日开仓配额 | 20 | 2026-09-03 v3-P0 止血：150→20。历史：08-23 由 60 上调到 150，其间 `--fix-doc` 曾误把文档回写成 60；实盘另有 LIVE_SCALP_DAILY_OPEN_CAP=30 与 V5_MAX_DAILY_TRADES_LIVE=12 两道更紧的闸 |
| TREND_DAILY_OPEN_CAP | 中长线日开仓配额 | 6 | 2026-09-03 v3-P0 止血：15→6，与 E1 趋势引擎的核心币数量匹配 |
| SCALP_EV_GATE_ENABLED | 短线 EV 门禁 | true | ⚠ 已回写实况（原期望 false，请人工确认意图） |
| SCALP_EV_FAIL_CLOSED_LIVE | 短线 EV Live fail-closed | true | |
| SCALP_MTF_RESONANCE_ENABLED | 短线 MTF 共振 | false | |
| DATA_CENTER_MODE | 数据中心运行模式 | standalone | README §独立数据中心模块 |
| KLINE_DEPTH_BACKFILL_ENABLED | K 线深度回填 | true | README §数据补齐 |
| KLINE_QUALITY_REPAIR_ENABLED | K 线质量修复 | true | |
| QAA_V3_ENABLED | QAA V3 调度框架 | false | ⚠ 已回写实况（原期望 true，请人工确认意图） |
| QAA_SCHEDULER_ENABLED | QAA 调度器 | true | |
| QAA_FULLAUTO_SCHEDULE_ENABLED | QAA 全自动调度 | false | ⚠ 已回写实况（原期望 true，请人工确认意图） |
| QAA_REBATE_SCHEDULE_ENABLED | QAA 套利调度 | false | 2026-09-05 中长线 LLM 主脑改造：不跑积分套利空转 |
| LLM_ANALYSIS_FORCE_STREAM | LLM 分析强制流式 | true | |
| OPENCODE_ENABLED | OpenCode 侧车 | false | ⚠ 已回写实况（原期望 true，请人工确认意图） |
| ONCHAIN_DATA_ENABLED | 链上数据采集 | false | |
| HERMES_L2_AB_ENABLED | Hermes L2 A/B | false | |
| PAIR_BINDING_LANE_ENABLED | 交易对绑定车道 | false | |
| AI_AB_FRAMEWORK_ENABLED | AI A/B 框架 | false | |
| TIER_DAILY_LOSS_BUDGET_PCT_SHORT | P0-E 短线周期独立日亏预算（权益%，0=禁用） | 2.0 | 只冻本周期新开仓，绝不跨周期 |
| TIER_DAILY_LOSS_BUDGET_PCT_MID | P0-E 中线周期独立日亏预算（权益%，0=禁用） | 2.0 | 只冻本周期新开仓，绝不跨周期 |
| TIER_DAILY_LOSS_BUDGET_PCT_LONG | P0-E 长线周期独立日亏预算（权益%，0=禁用） | 3.0 | 只冻本周期新开仓，绝不跨周期 |

## 因子系统与 pwin 仲裁（2026-09-02 F17 登记）

> 这批开关直接决定「因子能否拿到权重」「信号能否变成订单」「实盘结果能否回流学习」，
> 却长期不在本清单内 —— 松紧被改动后既无留痕、漂移检查也查不到。以下期望值即
> 登记当日的 `.env` 实况；带 ★ 的是本次因子闭环修复中变更过的项，改动依据写在
> `.env` 同名键上方的注释里。

| 配置键 | 声明意图 | 期望值 | 备注 |
| --- | --- | --- | --- |
| FUSION_PWIN_ABSOLUTE_MIN | pwin 绝对下限（低于此值一律不开仓） | 0.45 | 与 FUSION_PWIN_TIERED 分档共同构成 pwin 硬门 |
| FUSION_PWIN_TIERED | pwin 分档门槛（按周期/环境差异化） | true | 关闭则退回单一绝对门槛 |
| FUSION_PROBE_MIN_PWIN | 保底探针门槛 | 0.55 | ★ C7：0.45→0.55。12.66 万条已结算信号回溯显示 pwin<0.55 各档净收益均为负，0.55 是正期望起点 |
| FUSION_PROBE_DAILY_QUOTA | 保底探针日配额 | 0 | ★ G18：3→0。模拟盘 2947 笔复盘后关闭：探针本就是「地板下放行」的已知负期望单，而样本量已足够训练，无需再靠它攒数据 |
| FUSION_SHADOW_PROBE_MIN_PWIN | 影子探针门槛 | 0.55 | ★ C8：此前未显式设置，继承出 0.40 的更松值；现与主探针对齐 |
| FUSION_PWIN_UNUSABLE_MODE | meta 模型不可用时的处置（全局回落） | hold | ★ C9：explore_quota→hold。原语义是「模型不可用时每天仍放 5 笔无 pwin 把关的单」 |
| FUSION_PWIN_EXPLORE_DAILY_QUOTA | 上一项为 explore_quota 时的日配额（全局回落） | 5 | UNUSABLE_MODE=hold 期间惰性不生效；保留以便回滚 |
| FUSION_PWIN_UNUSABLE_MODE_LIVE | 实盘：模型不可用时的处置 | hold | ★ 实盘不拿真金白银换样本，沿用 C9 封堵 |
| FUSION_PWIN_UNUSABLE_MODE_PAPER | 模拟盘：模型不可用时的处置 | explore_quota | ★ C9 把 hold 一并施加到模拟盘 → 09-04 全天短线 0 单（09-03 尚有 31 笔），形成「模型 unusable → 不开仓 → 攒不到成交样本 → 永远 unusable」死锁。模拟盘不花真钱，恢复采样 |
| FUSION_PWIN_EXPLORE_DAILY_QUOTA_LIVE | 实盘探索配额 | 5 | 沿用旧上限 |
| FUSION_PWIN_EXPLORE_DAILY_QUOTA_PAPER | 模拟盘探索配额 | 120 | ★ 按「够重训一批」给量，对齐历史日均开仓（30~100 笔） |
| FUSION_PROBE_MIN_PWIN_PAPER | 模拟盘探针门槛 | 0.42 | ★ 刻意低于分档最低档 0.55——探针只在 pwin<地板 时进入，不低于地板则数学上不可命中 |
| FUSION_PROBE_DAILY_QUOTA_PAPER | 模拟盘探针日配额 | 60 | ★ G18 关闭探针的立论「样本已足够、无需靠它攒数据」只对信号样本成立，成交样本（滑点/实际成交价/真实 PnL/退出行为）只能靠真实下单产生 |
| FUSION_PROBE_DAILY_QUOTA_LIVE | 实盘探针日配额 | 0 | 沿用 G18 关闭 |
| SCALP_META_GATE_AUC | meta 模型 usable 判定的 AUC 门槛 | 0.60 | 低于此值模型判不可用 → 走上面的 UNUSABLE_MODE |
| SCALP_META_LOAD_WINDOW_DAYS | meta 训练样本分批读取窗口（天） | 5 | ★ F15 新增。60 天切 12 批短事务，避免 35 万行单事务撞 DB_IDLE_IN_TXN_TIMEOUT_MS |
| SCALP_SHORT_LIVE_STRICT | 短线实盘严格模式 | false | |
| FACTOR_COMBO_MODE | 因子组合权重模式（icir/equal） | icir | ★ F17 显式化，值=原代码默认。equal 会抹掉 ICIR 差异回到等权 |
| FACTOR_IC_WEIGHT_MODE | IC 权重口径（ic_ev/winrate） | ic_ev | ★ F17 显式化，值=原代码默认。winrate 是旧口径，会让负 IC 因子凭胜率拿满权 |
| FACTOR_MIN_NET_IC | 因子净 IC 最低门槛 | 0.02 | 扣除换手成本后的 IC 下限 |
| FACTOR_OVERSIGHT_MAX_PBO | 自动化复核允许的最大 PBO | 0.30 | ★ E14：默认由 0.5 恢复 0.30。该复核只管 SMALL_LIVE/ACTIVE（进实盘），放宽到 0.5 等于与基础门齐平、复核形同虚设 |
| FACTOR_HELDOUT_ENABLED | held-out 判决段总开关 | true | 关闭则训练段打分直接决定晋升 |
| FEATURE_WFO_GATE_ENABLED | WFO 样本外滚动验证门禁 | true | ★ 2026-09-03 重开。此前"多币误杀"的根因是门禁代码本身坏了：策略级 WFO 读了不存在的 report.consistency/n_periods（恒 0 → 恒失败），IC-WFO 窗口只有 4h 口径（5m/15m 数据凑不出窗口 → insufficient_windows:0），逐窗重算因子造成预热 NaN 吃掉短测试窗。四处已修（factor_wfo.py），策略级降为咨询、IC-WFO 为绑定门；多币规则改为 2/3 通过（FACTOR_EVO_WFO_REQUIRE_ALL 默认 false）。test_factor_wfo_gate_repair_20260903.py 锁定 |
| FACTOR_EVO_WFO_REQUIRE_ALL | WFO 多币是否要求全部通过 | false | ★ 2026-09-03 显式登记代码默认。true 时任一币失败即拒，与"池化评估"矛盾；false 走 FACTOR_EVO_WFO_MIN_SYMBOL_RATIO（默认 0.66，即 2/3）多数通过 |
| FACTOR_EVO_WFO_STRATEGY_GATE | 策略级 WFO（固定 z 分数规则）是否作为硬门 | false | ★ 2026-09-03 新增。固定 naive 交易规则的 PBO 衡量的是"规则"而非"因子"，pbo=0.74 误杀过 IC-WFO 通过的真因子；默认只记报告不拦截，IC-WFO 才是绑定门 |
| FACTOR_GP_OBJECTIVE | GP/MCTS 挖矿适应度目标 | icir_net | ★ 2026-09-03 icir→icir_net（路线图 3.2）。纯 IC/ICIR 只看排序相关，高换手因子能过挖矿却在扣费后为负；icir_net = 分段 ICIR + 含成本净收益（成本与 FACTOR_SCORER_COST 同源），GP-CPU/GP-GPU/MCTS 三条路径统一走 fitness_objective.blend_objective |
| PAPER_FACTOR_LIVE_EXCLUDE | role=paper 影子因子在实盘会话是否清零 | true | ★ 2026-09-03 新增。held-out 判决未过的因子此前以 role=paper、权重上限 0.5 参与实盘融合；现实盘会话权重=0（pipeline/V3/中线路由/策略注入 4 处同一入口 paper_factor_policy），模拟会话仍按 PAPER_FACTOR_WEIGHT_CAP 上限继续学。false 回到旧行为 |
| FACTOR_SCORER_LAG1_ENABLED | 晋升回测是否并行跑"延迟一根成交"对照 | true | ★ 2026-09-03 新增。signal@t → entry@t+1，产出 lag1_net_return/lag1_sharpe/lag1_retention/lag1_fragile 落库，用于识别依赖零延迟的脆弱边际 |
| FACTOR_SCORER_LAG1_GATE | lag1 脆弱是否把 A/B 级降为 C | false | ★ 2026-09-03 新增，默认观察不拦截。需先看 3～5 天 lag1_retention 分布再决定开门（见 _实施登记 §5） |
| FACTOR_SCORER_LAG1_MIN_RETENTION | lag1 净收益留存率下限（低于判 fragile） | 0.3 | ★ 2026-09-03 新增。lag1_net/lag0_net < 此值或 lag1_net ≤ 0 → fragile |
| FACTOR_NEUTRALIZE_BETA_TRAIN_RATIO | 中性化 OLS β 的拟合窗占比（时间轴前段） | 0.7 | ★ 2026-09-03 新增。此前 β 用全窗（含验证段）拟合，验证标签参与了拟合；现前 70% 拟合（尾部 purge fwd 根）、全窗套用。1.0 回到旧全窗行为 |
| LIVE_LEARNING_HOOKS_ENABLED | 实盘开仓因子快照 + 平仓盈亏回填 | true | ★ D10 新增。关闭即切断「实盘成交 → 因子 IC」闭环，实盘样本对因子权重的贡献恒为 0 |

## 短线交易频次与方向门槛（2026-09-02 G18 登记）

> 模拟盘主账户（id=14，本金 500 → 权益 211）2947 笔已平仓复盘定位到两个结构性失血点。
> 其一是频次：09-01 起门槛放松后日开仓量由 3 笔暴涨至 105 笔，胜率同步从 42% 跌到 25%，
> 累计手续费 107.90，占总亏损 37%、占本金 21.6%；已平仓中 64% 属「开仓后价格几乎不动
> 就被超时平掉」的磨损单。其二是方向：做空 868 笔亏 104.55，做多 843 笔仅亏 5.81，
> 做空占短线亏损的 95%。以下期望值为回调后的实况，整体对齐代码默认的保守档。

| 配置键 | 声明意图 | 期望值 | 备注 |
| --- | --- | --- | --- |
| SCALP_FACTOR_SCAN_INTERVAL_SEC | 短线因子扫描间隔（秒） | 45 | ★ G18：15→45 回到代码默认。15 秒扫描配合每轮 2 单是日均 105 笔的主因 |
| SCALP_OPEN_COOLDOWN_SEC | 短线开仓冷却（秒） | 300 | ★ G18：120→300。与扫描间隔同属频次闸，只调一个无效 |
| SCALP_MAX_OPENS_PER_TICK | 每轮最多开仓数 | 1 | ★ G18：2→1 恢复代码默认 |
| SCALP_FACTOR_CONFIRM_THRESHOLD | 因子确认门槛 | 35 | ★ G18：25→35。低门槛放进来的多是无方向信号，只贡献手续费 |
| SCALP_FACTOR_EXECUTE_THRESHOLD | 因子执行门槛 | 45 | ★ G18：35→45，与 CONFIRM 成对回调 |
| SCALP_DIRECT_THRESHOLD | ExecutionGate 直通分 | 45 | ★ G18：30→45。必须不低于 VETO_BAND_LOW，否则分层判定颠倒 |
| SCALP_VETO_BAND_LOW | ExecutionGate 最低分 | 35 | ★ G18：25→35。低于此分直接拒绝，35~45 区间走 Flash Veto 复审 |
| FUSION_SHORT_PWIN_EXTRA | 空头 pwin 地板附加值 | 0.05 | ★ G18：0→0.05。恢复 08-31 已写入代码却被 .env 置 0 的空头保护。12.8 万条回溯：pwin>=0.55 时做多胜率 66.3%、做空仅 49.0% |
| SCALP_PWIN_FAIL_CLOSED | pwin 仲裁异常时是否拒开 | true | ★ G18 新增，未在 .env 显式设置故走代码默认。原两处仲裁异常均 fail-open，且主轴那处是 debug 级静默日志，等于唯一有效的质量闸存在一条无声旁路 |

## 币种杠杆：一币一档（2026-09-04 登记）

> 交易所的杠杆是**按币种**的账户级设置（`set_leverage(BTC, 5)`），同币同方向的仓位
> 在交易所会合并成一个净头寸。因此「短线 10x / 中线 6x / 长线 3x」这类按周期分配的
> 杠杆在交易所根本落不了地——本地按多套倍数记账，等于记假账，实盘会按交易所的那个
> 唯一值成交。改为一币一档后，杠杆只决定占用多少保证金，风险敞口全部交由名义价值
> 控制（`PC_MAX_WEIGHT_PER_SYMBOL` / `PC_RISK_PER_TRADE_PCT` / `PC_CLUSTER_CAP` /
> `PC_GROSS_CAP` 四道帽）。
>
> 档位依据爆仓距离 ≈ 1/杠杆：5x→约 20%、4x→25%、3x→33%；长线 Chandelier 止损实测
> 5.7%~8%、极端 15%，故最低档 3x 仍能保证止损先于爆仓触发。

| 配置键 | 声明意图 | 期望值 | 备注 |
| --- | --- | --- | --- |
| SYMBOL_LEVERAGE_ENABLED | 币种杠杆总开关 | true | 关闭则回落到旧的「上游自算杠杆」行为，仅作应急回滚 |
| SYMBOL_LEVERAGE_MAP | 币种杠杆档位 | BTC:5,ETH:5,SOL:4,BNB:4,XRP:4,DOGE:4,LINK:4,AVAX:4,ADA:4 | 主流币流动性好、波动小给 5x；二线 4x；未列入的小币走 DEFAULT |
| SYMBOL_LEVERAGE_DEFAULT | 未列入币种的档位 | 3 | 小币/新币波动大，爆仓距离 33% |
| PC_MAX_LEVERAGE | 杠杆兜底上限 | 10 | 2026-09-04：3→10。降级为「防 SYMBOL_LEVERAGE_MAP 写错」的护栏，不再是主约束；原 3x 会把 BTC 的 5x 档压回 3x，并会改掉从交易所 adopt 来的存量仓杠杆 |
| LIVE_LEVERAGE_FAIL_CLOSE | 实盘杠杆对齐失败即拒绝开仓 | true | 原实现失败只告警不阻塞、交给事后对账；期间交易所仍按残留倍数成交（XPL 事故：交易所 75x，5.5U 的意图变成 935U 敞口）。减仓/平仓不受此闸影响，避免卡住止损 |
| MIDLONG_SYMBOL_EXPOSURE_CAP_PCT | 中长线同币合计名义上限 | 0.35 | 2026-09-04：0.6→0.35，与 `PC_MAX_WEIGHT_PER_SYMBOL` 同口径。此前更宽，中长线闸缩到 60% 后仍会在 `place_order` 里被 PC 拦成 blocked，白跑一趟且拒绝日志分散在两处 |

**杠杆的唯一来源**：`leverage_authority.resolve_leverage(symbol=...)`。上游一律不得自算。
2026-09-04 修掉的三处遗漏（都因为不传 `symbol` 而回落到 10x 兜底）：

| 位置 | 症状 | 后果 |
| --- | --- | --- |
| `position_memory_manager.build_position_plan` | `TIER_LEVERAGE` 三档实际全是 10x，且 `_calc_leverage` 有 `[5,20]` 全局硬下限 | **仓位规模的源头**。名义 = 保证金×杠杆，VIRTUAL 开口就提议 `margin=$754 → notional=$7543`（174% 权益），必被敞口闸拒 → 中线长期零成交 |
| `midlong_helpers` 组合风控预估 | 调 `resolve_leverage` 未传 `symbol` | `est_notional` 按 10x 估算，比实际大 3 倍多，组合闸误拦 |
| `midlong_portfolio_risk.estimate_open_notional` | 默认参数 `leverage=10.0` | 调用方漏传时静默按 10x 估算 |

> `TIER_LEVERAGE` 与 `_calc_leverage` 已停用（保留定义但无调用方），新代码不得再读。

### 因子权重与证据强度对齐（G19）

| 键 | 作用 | 值 | 依据 |
| --- | --- | --- | --- |
| FACTOR_UNVERIFIED_WEIGHT | 样本不足（n<30）因子的权重 | 0.1 | ★ G19 新增，未在 .env 显式设置故走代码默认。原为初始值 1.0 满权 |
| FACTOR_IC_SHRINK_K | IC 置信收缩强度，权重用 ic×n/(n+K) | 100 | ★ G19 新增，未在 .env 显式设置故走代码默认。0 可关闭收缩回到裸 IC |

修复的两处失配（实测 data/factor_runtime_weights.json，97 个因子）：

1. **没有证据 = 满权信任**。`weight` 初始值为 1.0，仅当 `n >= MIN_SAMPLES(30)`
   才进入 IC 定权分支；而 `_rank_ic` 对 n<30 直接返回 None，导致该分支里那句
   "IC=null（样本不足）→ 中性 0.1"的注释从未生效。实测 50 个样本 3~28 条的因子
   全部保持满权，合计占全池权重 75.2%，而 11 个 n>=500 的可信因子仅占 4.8%。
2. **裸 IC 定权不含可信度**。`w = 0.5 + 4×IC` 使 ai_gen_sl_break 以 n=37 的
   IC=0.2759（t=1.66，不显著）顶到上限 1.5，而 obv 以 n=757 的 IC=0.0939
   （t=2.58，显著）只拿 0.876 —— 权重与证据强度倒挂。

修复后同一批数据的话语权分布：

| 分组 | 修复前 | 修复后 |
| --- | --- | --- |
| n<30（未验证） | 75.2% | 26.9% |
| n>=500（可信） | 4.8% | 16.5% |
| 统计显著 t>1.96 | 7.6% | 20.1% |

参与合成的因子数 71→71 不变：本次只重新分配话语权，不淘汰因子。低权重不影响
样本积累（样本来自因子计算与 signal_trade_feedback，与权重无关），因子攒够证据
后会自动升权。

### 为什么 pwin 是质量闸而 factor_score 不是

12.8 万条已结算信号（scalp_signal_log）分档回溯，同一批样本、同一结算口径：

| 分档依据 | 低档表现 | 高档表现 | 结论 |
| --- | --- | --- | --- |
| factor_score | 25-35 档净收益 -0.000842 | >=65 档净收益 -0.002305 | 各档全负，且分数越高越差，无预测力 |
| meta pwin | <0.40 档净收益 -0.000818 | 0.60-0.65 档净收益 +0.003292 | 0.55 为分水岭，以下全负、以上全正 |

meta 模型（v3，样本外 AUC 0.634、45 特征、3.65 万去重样本）报告口径同样印证：
不过滤时胜率 36.4%、净收益 -0.19%；取模型评分前 30% 胜率 50.2%、净收益转正；
取前 15% 胜率 55.7%、净收益 +0.19%。因此入场质量应由 pwin 主导，factor_score
仅作并列参考 —— 这也是 scalp_loop 中 pwin 仲裁必须 fail-closed 的直接依据。

## 双模型交叉验证：契约与本地票（2026-09-04 登记）

> 交叉验证此前**从未真正产出过结论**。三处独立缺陷叠加，任何一处都足以让它全程空转：
> 契约本身是错的、本地兜底票被云端配额误杀、补位会挑中配额已耗尽的传输。

### 1. 输出契约：未注册任务的兜底模板会教模型犯错

`schemas.output_contract` 对未注册任务的兜底原本是 `{k: "..." for k in COMMON_REQUIRED}`，
把 `direction` / `strength` / `confidence` / `key_factors` 一律渲染成字符串 `"..."` 塞进
system prompt 当契约。模型越照抄，类型错得越彻底 —— 实测 DeepSeek 与本地 qwen3 同时
四个字段全错，`consensus` 恒为 `skipped`。

而 `signal_review` / `anomaly` 两个 agent 恰好**从未注册**过：

| 调用点 | task 名 | 修前状态 |
| --- | --- | --- |
| `agents/signal_review.py:357` | `signal_review` | 未注册 → 走错误兜底 → 100% schema 失败 |
| `agents/anomaly_agent.py:511` | `anomaly` | 未注册 → 同上 |
| `agents/timing_agent.py:353` | `timing` | 已注册 ✓ |
| `api/analysis_routes.py:88` | `gateway_test` | 已注册 ✓ |

修法：兜底模板改为类型正确的 `COMMON_TEMPLATE`；补注册上述两个 task。
回归测试 `test_analysis_schemas_20260904.py` 锁死三件事 —— 源码里 `dual_call` 用到的
task 名必须已注册、每个 task 的 template 与 required 类型自洽、兜底模板同样自洽。

#### 1b. 光注册还不够：契约根本没发给模型

补完注册后实测仍然 100% 失败，且这次是**六个必填字段一个不剩**。原因是
`output_contract` 要**调用方自己拼进 system**，而三个 agent 全都漏了：

| 调用点 | 是否拼接契约 |
| --- | --- |
| `api/analysis_routes.py:85`（gateway_test） | ✓ |
| `analysis/tasks.py:132`（日报等） | ✓ |
| `model_gateway.py`（仲裁票） | ✓ |
| `agents/signal_review.py`、`agents/anomaly_agent.py`、`agents/timing_agent.py` | ✗ 全漏 |

模型没收到字段要求就只能自由发挥 —— qwen3 直接按信号源名分组返回
`{"e5_5_news_hedge": {...}, "e5_3_liq_cascade": {...}}`，语义上完全合理，但与 schema 无关。

这类遗漏靠"约定调用方记得拼"防不住，故改为在 `ModelGateway.call()` 内统一兜底注入；
已自行拼接的调用方按标志串 `schemas.CONTRACT_MARK` 识别，不重复注入。两条路径都有
回归测试覆盖。

### 2. 本地推理传输免配额

| 配置键 | 声明意图 | 期望值 | 备注 |
| --- | --- | --- | --- |
| ANALYSIS_LOCAL_TRANSPORTS | 免配额的本地传输名单 | ollama,ollama2 | 本地不走供应商 Key，无 5h 窗 / 周配额 / 峰时倍率 |
| ANALYSIS_FALLBACK_TRANSPORTS | 主票缺席时的顶替候选 | deepseek,ollama,ollama2 | 按序补位，缺几条补几条 |
| ANALYSIS_OLLAMA_MODEL | 本地票模型 | qwen3:14b | |
| ANALYSIS_OLLAMA2_MODEL | 第二条本地票模型 | qwen2.5:7b-instruct-q4_K_M | 必须与上一条不同模型，否则退化为单票 |

> 这四项此前只写在本文档、靠代码默认值运行，漂移检查一直报 MISSING（且文档记的
> 值已过时：只写了 `ollama`，实际早已是两条）。2026-09-04 已显式写入 `.env`。

QuotaGuard 原本对所有传输一视同仁（`record` 的理由是"供应商侧同样扣减"，这对本地
模型不成立）。后果是云端配额一耗尽，免费的本地兜底票会被一起拦掉 —— 恰恰在最需要
兜底的时候失效。豁免只针对**次数**类配额；上下文 / 输出的 token 上限仍然生效，
那是防止塞爆模型窗口的保护，与配额无关。

注意豁免要做两处：`record` 不写窗口计数，`_load` 回读流水时也要跳过本地传输，
否则回读会把 record 里的豁免悄悄抵消（本地流水仍写 `llm_quota_usage`，保留可观测性）。

### 3. 补位时预检配额

补位原本只看 `configured`，会把配额早已耗尽的传输选为主票（实测 DeepSeek 5h 窗
37/30 仍被选中，调用必然 degrade），白占一个席位、且让本可顶上的传输没了机会。
现于补位环节追加配额预检。配额是动态的，预检失败不代表实际调用一定失败，因此
只用于排序取舍，真正的拦截仍在 `call()` 内部。

### 4. 两条本地票：必须不同模型，且必须串行

`ollama2` 走 `prefer_db=False`，模型名只认 `ANALYSIS_OLLAMA2_MODEL`。若它像 `ollama`
那样查 DB，会命中同一条 `provider=ollama` 绑定 → 两票落到同一个模型。**同模型的一致
毫无信息量，却会算出很高的共识分并写进账本 —— 假的交叉验证比没有更危险**，因此
`dual_call` 里另有一道保护：两条主票解析到相同模型时直接退化为单票。

选 qwen2.5 而非同系列的 qwen3，是因为交叉验证要的是「独立犯错」，跨代际模型的失败
模式差异更大。7b 约占 4.4GB，可与 14b（9.0GB）同时常驻（2080 Ti 22.5GB）。

本地票之间强制串行。本机 GPU 推理本来就要排队，并发拿不到加速，却会在模型冷启动时
踩踏 —— 三次对照实测：

| 场景 | 结果 |
| --- | --- |
| 串行 + 冷启动 | 成功（8.1s） |
| 并发 + 热启动 | 成功（1.8s） |
| 并发 + 冷启动 | **失败（8.0s 后空响应）** |

Ollama 默认 `keep_alive` 仅 5 分钟，系统闲置后的第一轮分析必然撞上冷启动，
不串行的话这一轮就白跑。

### 验证（重启后线上实测）

```
deepseek: ok=false  quota=degrade  error="5 小时窗已用 37/30"
ollama:   ok=true   quota=allow    latency=1242ms   schema_errors=[]
```

DeepSeek 配额耗尽被拦的同时，本地票正常放行并产出零 schema 错误的合规 JSON ——
上述修复缺任何一项，此刻都会是"一个可用模型都没有"。

补齐两条本地票后（先手工卸载模型制造冷启动）：

```
status=ok  consensus=0.8  accepted=True
  ollama   qwen3:14b                    ok=True  2623ms  dir=neutral
  ollama2  qwen2.5:7b-instruct-q4_K_M   ok=True  2983ms  dir=neutral
```

`accepted=True` —— 交叉验证首次产出可入 signal_ledger 的共识结论。
在此之前，这条链路自上线起从未产出过任何结果。

> **热重载默认关闭**：`run_uvicorn_dev.py` 受 `NO_RELOAD` 控制，当前部署为 OFF。
> 改动 Python 代码后必须手动重启后端，否则线上仍跑旧模块（本次 `gateway/status`
> 长时间看不到 `ollama` 即此原因，而非代码未生效）。
>
> 判断"是否已重启"不能看进程数：`NO_RELOAD=true` 时同样是父子两个进程
> （venv 启动器 + `.runtime\Python312` 实际解释器），这与热重载的 reloader/worker
> 结构长得一样。可靠做法是看日志时间戳或直接验证新行为。
> 重启用 `scripts/start-backend-noreload.cmd`（内含 `BACKEND_HOST=0.0.0.0`，
> 手工拼命令容易漏掉而退回 `127.0.0.1`，导致远程访问中断）。

## 信号样本饿死：三条 E5 + 中线被 no_progress 误杀（2026-09-04 登记）

从"信号太少"查到四条独立问题，一并修。

### e5_3 清算级联：小时源仍用 336 桶门槛（结构性 bug）

`min_history_buckets=336` 按 **30min × 7 天** 标定；回退到 `liquidation_events`
小时桶时仍用 336 → 实际要 **14 天稠密小时**。DOGE 等填充率约 82% 的币
在默认 24h 扫描里经常不够数，连已经入账的事件都扫不出来。

修法：按墙钟缩放（30min→336，60min→168），稀疏序列改看时间跨度；
影子 lookback `E5_E5_3_LIQ_CASCADE_LOOKBACK_H=72`。
验证：24h 窗检出 0→3；72h 窗检出 6。

### e5_2 资金费率：无 OI 直接丢弃

`position_structure` 与 funding 重叠仅 ~26 币，约 39% 的 z 命中死在「无 OI」。
改为：**有 OI 但未达阈值 → 仍丢弃**；**取不到 OI → 降置信入账**
（`oi_confirmed=false`，置信度封顶 0.55）。OI 门槛 3%→2%。
验证：168h 窗检出 2（OP/INJ）。

### e5_5 新闻：见上一节（重复 88% + 标注从未走 LLM）

回填近 7 天 10 条关键词新闻为 LLM 标注后，ETF 流入类已能打到强度 4 /
方向 +0.7，具备触发条件。阈值暂不放宽（旧量纲回测全负）；等 LLM
样本攒够再 `backtest()` 重标定。

### 中线：`no_progress` 18h 误杀浮盈单

近 7 天 mid 平仓 23 笔，均亏 43bp，几乎全是 `no_progress`；多笔平仓时
`cur_R>0`（方向对了，只是还没走到 0.5R）。no_progress 本意是收回死钱，
不应砍掉仍在浮盈的单。

修法：`MIDLONG_NO_PROGRESS_HOURS_MID` 18→36；**cur_R≥0 不触发**。
当前 open 仅 2 笔 long（BTC/ETH），无 mid 在仓——与"中线被砍光"一致。

### AI 分析频率（配额放宽后）

| 项 | 旧 | 新 |
| --- | --- | --- |
| ANALYSIS_EVENT_BATCH_LIMIT | 3 | 8 |
| ANALYSIS_EVENT_SCAN_SEC | 1800（硬编码） | 900 |
| AGENT_ANOMALY_INTERVAL_SEC | 1800 | 900 |

日简报 / 周复盘 / 择时仍非峰时 cron（GLM 峰时 3× 消耗），未强行加密。

## 新闻策略查不出 edge：根子在标注从未用过 LLM（2026-09-04 登记）

从"信号太少"查起，最后落到一条完整因果链上。

### 症状

`e5_5_news_hedge` 影子样本长期只有 7 条，而结论门槛是 20 条 —— 策略无法被检验。
影子扫描每 15 分钟一轮、日志恒为 `检出=0 入账=0`。

### 一、新闻表 88% 是重复行

去重只做在进程内存里：

```python
h = hashlib.md5(item["title"].encode()).hexdigest()
if h not in self._seen_hashes: ...
if len(self._seen_hashes) > 5000:
    self._seen_hashes = set(list(self._seen_hashes)[-2500:])   # set 无序！
```

两个缺陷叠加：**进程一重启集合就清空**，而 RSS 源里还是那批新闻 → 整批重新入库；
裁剪又写成 `list(set)[-2500:]`，`set` 无序，取到的是任意 2500 个而非最近的。
落库侧没有任何唯一约束，于是同一条新闻最多被插了 **79 次**
（`theblock` 的几条 08-14 新闻，3 天内每小时一次，与采集间隔完全吻合）。

全表 5271 行 → 去重后 **632 行**，重复率 88%，一切基于 `news_events` 的统计
此前都被放大约 8 倍。已清理并建唯一索引 `ux_news_events_url` /
`ux_news_events_title`（部分索引：URL 非空按 URL，否则按标题）。
去重改为内存 + 数据库两级，**数据库那级是权威**，不随进程重启失效。

### 二、标注从来没经过 LLM

```python
config = get_llm_config()      # ← 未传 tenant_id
if not config:
    return self._heuristic_analyze(item)
```

`get_llm_config()` 明确拒绝无租户的调用（`[LLM] 拒绝公用默认配置`），
而新闻服务是**系统级采集器**，本就没有租户上下文 —— 于是每条新闻都拿到 `None`，
静默回落关键词词表。库里 3790/3809 条标注 `confidence` 恒 0.3、分类一律 `general`。

同一批样本上的实测对比：

| 新闻 | 关键词 | LLM |
| --- | --- | --- |
| 交易所被盗 2 亿、暂停提现 | 强度 5、general | 强度 4、**exchange** |
| SBI 2.7 亿收购 Ajaib | 方向 **0.00**、强度 1 | 方向 +0.1，"常规并购，**本交易不直接涉及加密资产**" |
| ETF 单日净流入 7.31 亿 | general | **macro**，"机构资金回流，需观察持续性" |

### 三、于是策略必然查不出 edge

用策略自带 `backtest()` 在**去重后**数据上扫阈值（45 天、成本线 14bp）：

| 强度 | 方向 | 样本 | 4h 超额 bp | 命中率 | 过成本线 |
| --- | --- | --- | --- | --- | --- |
| ≥4 | ≥0.5（线上） | 7 | −76.7 | 0.29 | ✗ |
| ≥4 | ≥0.3 | 18 | −25.3 | 0.39 | ✗ |
| ≥3 | ≥0.3 | 67 | −6.3 | 0.49 | ✗ |
| ≥2 | ≥0.3 | 85 | −2.4 | 0.50 | ✗ |

**7 组阈值 × 4 个时间窗全为负，无一过成本线。** 且越放宽越趋近随机
（命中率 → 0.50、超额 → 0）—— 这是"信号本身没有 edge"的形状，
不是阈值没调好。**放宽阈值只会把噪声灌进账本**，故未放宽。

修法是补上语义理解这一环：标注改走系统级 `ModelGateway`
（顺序 `minimax → glm_opencode → ollama`，按实测耗时定：MiniMax 4.5s、
GLM 17~26s，质量相当；本地票兜底免费不限量），任务类归为 `light`
（单条标题，约 21 条/天，远低于日上限 120）。
`news_annotate` 契约**不复用** `COMMON_REQUIRED` —— 新闻的 direction 是
−1~+1 连续值、strength 是 1~5，与通用契约的 enum direction / 0~10 strength
量纲不同，混用会让标注对不上库表。

> **阈值必须重标定**：现有 632 条历史标注仍是词表打的，上表结论只在旧量纲下成立。
> LLM 标注积累一段时间后要重跑 `backtest()`，不可沿用当前阈值。

## 配额：全局一个数按最弱的定，外加单测污染生产账本（2026-09-04 登记）

排查"DeepSeek 5h 窗 54/30 超支"时挖出两个独立问题，**一个是假象，一个是真限制**。

### 1. 单元测试把假流水写进了生产账本

`llm_quota_usage` 里 3 小时内出现 429 条 `ollama` + 35 条 `deepseek` 的
`gateway_test` 记录。识别特征：`latency_ms=1`（真实调用 1500~8000ms）、
`model='m'`、`run_id` 为空、成批出现在跑测试的时间点。

根因是测试里这行：

```python
monkeypatch.setattr(QuotaGuard, "_persist", lambda *a, **k: None, raising=False)
```

当时 `QuotaGuard` **没有** `_persist` 方法（落库内联在 `record` 里），
`raising=False` 让这个无效 patch 静默通过 —— 每跑一次单测就往生产库写 65 条假流水，
直接顶掉 DeepSeek 的 5h 窗与 light 日配额，把真实调用误判为 degrade。

修法两条，缺一不可：
1. 落库抽成真实存在的 `_persist()`，测试才能屏蔽；
2. 测试里去掉 `raising=False` —— 方法名写错时必须当场报错。

另外 `_load()` 会从生产库回读近 7 天用量，单测同样要屏蔽，
否则断言拿到的是线上真实计数。已清理 455 条污染记录。

> DeepSeek 的真实用量是 29 次，不是 54。**超支是假象**。

### 2. 全局单一上限 = 按最弱的那家供应商定

各家套餐差着数量级，而 `ANALYSIS_5H_CALLS_PER_MODEL` 是**所有传输共用**的：

| 传输 | 供应商侧真实额度 | 旧全局上限 | 实际用到 |
| --- | --- | --- | --- |
| glm_opencode | Coding Plan **Max**，每 5h 约 2400 次 | 30 | **约 1%** |
| minimax | Token Plan **最高档**，实测 8 次调用耗 5h 窗 4% | 30 | 约 4% |
| deepseek | 按量付费，超额直接产生账单 | 30 | — |

买来的高档额度被本地限制锁在门外，模型却频繁被判 degrade，
交叉验证因此频繁退化成单票。

改为按传输配置（未列入 MAP 的传输回落全局默认，行为不变）：

| 配置键 | 含义 | 值 | 依据 |
| --- | --- | --- | --- |
| ANALYSIS_5H_CALLS_MAP | 按传输的 5h 窗上限 | glm_opencode:400,minimax:100,deepseek:50 | GLM 取 2400 的 1/6；MiniMax 按 token 计费，"次数"只是本地近似，取满额约一半 |
| ANALYSIS_WEEKLY_CALLS_MAP | 按传输的周上限 | glm_opencode:2000,minimax:400,deepseek:250 | 同上比例 |
| ANALYSIS_DAILY_CALLS_MAP | 单传输每日总调用上限 | deepseek:80 | 与分类上限取**较小者**；只对按量付费的 DeepSeek 设 |

分类上限同步放宽 —— 原值（6/20/40）是在两家云端都没接通、只能省着用的前提下拍的。
保留该维度作为**我们自己的成本与节奏控制**，与供应商侧上限是两回事：

| 配置键 | 含义 | 值 | 备注 |
| --- | --- | --- | --- |
| ANALYSIS_DEEP_DAILY_PER_MODEL | 深度任务每模型每日上限 | 20 | 日报 / 周复盘 / 择时 / 参数寻优 |
| ANALYSIS_EVENT_DAILY_PER_MODEL | 事件评估每模型每日上限 | 60 | |
| ANALYSIS_LIGHT_DAILY_PER_MODEL | 轻量 / 测试调用每日上限 | 120 | 含 gateway_test 联调 |

> **DeepSeek 不随套餐放宽**：它是按量付费，超额即账单，故仅小幅抬到 50。

重定档后：

```
minimax        5h=  8/100   week=  8/400
glm_opencode   5h= 28/400   week= 32/2000
deepseek       5h= 21/50    week= 29/250
ollama/ollama2 5h=  0/-1    week=  0/-1     (local=true，-1 = 不限量)
```

顺带修了看板两处：本地传输此前**根本不在配额看板里**（硬编码只列三家云端），
而它们的耗时/成功率本就需要与云端横向对比；纳入后其上限报 `-1` 而非全局默认，
避免显示成 `0/30` 让人误以为受限。

## 基准标的的超额恒为 0：信号好坏永远判不出来（2026-09-04 登记）

`ledgers._score_direction` 原本对**所有**方向性信号扣减 BTC 基准：

```python
ret_bp    = direction * ret          # 方向调整后收益
excess_bp = ret_bp - direction * btc_ret_bp
```

标的是 BTC 时，`ret = ret_btc`，于是 `excess_bp` **恒等于 0** —— 拿 BTC 跟自己比。

这不是"少一个指标"那么简单，因为 `excess_bp` 是两条关键链路的**唯一判据**：

| 消费方 | 用途 | excess 恒 0 的后果 |
| --- | --- | --- |
| `agents/signal_review._verdict` | 信号源停用 / 加权 | 三个分支（`ex_hi<0` / `ex_lo>0` / `ex_mean<-5`）全不成立 → 永远 `keep` |
| `strategies/event/base.kpi` | **影子转实盘的晋升门** | `excess_ci_bp[0] > COST_BP` 永不成立 → 好策略也永远晋升不了 |

外加 `signal_review` 的 IC、半衰期曲线、regime 分层同样基于 excess，一并失真。

即**差策略停不掉、好策略上不来**，两个方向同时失效。

实测 `e5_5_news_hedge`（7 笔里 6 笔标的为 BTC）：

| 指标 | 修前 | 修后 |
| --- | --- | --- |
| 平均超额 | **-2.53bp**（看着几乎无影响） | **-108.47bp** |
| 超额 95% 区间 | [-7.49, 2.43] | [-234.48, 17.55] |
| 命中率 / 平均收益 | 0.2857 / -115.08bp（这两项一直是对的） | 同左 |

差 43 倍。命中率与 `ret_bp` 一直如实记录着"这个信号很差"，唯独作为判据的
`excess_bp` 被稀释成了无害的 -2.53。

修法：`_is_benchmark_symbol()` 识别标的是否即基准（含 `BTCUSDT` / `BTC-USDT` /
`XBT` 等写法），是则 `excess_bp = ret_bp`，不做扣减。非基准标的口径不变
（`SOL` 涨 2% 而 `BTC` 涨 1% → 超额 +100bp）。历史 6 条已回填并备份至 `_ptmp/`。
回归测试见 `test_signal_ledger_excess_20260904.py`。

> **`ret_bp` 是方向调整后的收益，不是原始涨跌**：`direction=-1` 的信号价格下跌时
> `ret_bp` 为**正**。核对逐笔数据时若按原始涨跌去理解，会误判成"命中判定反了"。
> 用 `entry_price` / `exit_price` 复算过 13 笔，命中判定全部正确。

## 云端两票的接入陷阱：都不是 Key 的问题（2026-09-04 登记）

MiniMax 与 GLM 的 Key 一度双双报错，排查后**两个 Key 本身都是好的**，
错的是端点。这类故障的共同特征是：报错信息指向"Key 无效/没钱"，会把人带偏。

### MiniMax：订阅 Key 分区域，国际站与中国站互不认

| 端点 | 结果 |
| --- | --- |
| `https://api.minimax.io/anthropic` | **401 invalid api key**（`x-api-key` 与 `Bearer` 两种认证头都一样） |
| `https://api.minimax.cn/anthropic` | ✅ 200 正常出文，配额接口也能读到余量 |

同一个 `sk-cp-` 订阅 Key，换个区域就从"无效"变"可用"。连
`/v1/token_plan/remains` 配额接口也在国际站返回 `2049 invalid api key` ——
**报错完全不提示这是区域问题**，容易误判成 Key 写错或过期。

> 认证头不是问题：官方文档里 Claude Code 用 `ANTHROPIC_AUTH_TOKEN`（→ `Bearer`），
> 而 anthropic SDK 的 `api_key=`（→ `x-api-key`）同样被接受，实测两者行为一致。

### GLM Coding Plan：必须走 coding 专用端点

| 端点 | 结果 |
| --- | --- |
| `https://api.z.ai/api/paas/v4` | **429 code 1113「余额不足或无可用资源包，请充值」** |
| `https://api.z.ai/api/coding/paas/v4` | ✅ 200 正常出文 |

订阅制的 Coding Plan 不走按量付费端点。打到通用端点时**认证是过的**，
失败在计费环节，于是报成"没钱"——但账户其实有有效的订阅套餐。
项目里这条链路经 OpenCode sidecar 的 `zai-coding-plan` provider，端点由
sidecar 侧处理，只需保证 Key 正确。

> **sidecar 是独立进程，改 `.env` 不会生效**：`start_opencode_sidecar.ps1`
> 在启动时把 `.env` 逐行读进自己的环境变量。换 Key 后必须重启 sidecar，
> 否则它仍拿旧 Key 去认证（表现为 401 后进 30 分钟冷却）。

### 接入后实测

```
consensus  MiniMax-M3 + zai-coding-plan/glm-5.3   score=0.833  accepted=True
  minimax       direction=neutral strength=2.0   20.1s
  glm_opencode  direction=neutral strength=1.0   74.9s
```

方向一致、强度接近 → 无需仲裁直接达成共识。这是该链路首次产出
`accepted=True` 的可入账本结论。注意 GLM 经 sidecar 耗时约 75s，
明显慢于直连的 MiniMax（20s），排查超时问题时需按此基准判断。

## 本地算力槽位：别把免费算力让给付费配额（2026-09-04 登记）

`_call_ollama_generate` 有两道信号量：全局 `OLLAMA_MAX_CONCURRENT`，以及针对
"重负载批量调用方"（KlineAnalyst / MasterController）的单调用方槽。抢不到槽即
**降级云端**。原档位是全局 2、单调用方硬编码 1，依据是 35B MoE 时代的判断
"Ollama 单模型推理串行，并发排队会挤爆线程池"。

该前提对现在的 qwen3:14b 已不成立。2080 Ti 22.5G、14b + 7b 双模型常驻下实测：

| 场景 | 墙钟 | 成功率 | 最慢单请求 |
| --- | --- | --- | --- |
| 串行 ×4 | 10758ms | 4/4 | 4192ms |
| 并发 ×4 | **6011ms** | 4/4 | 6008ms |
| 串行 ×8 | 17526ms | 8/8 | 7504ms |
| 并发 ×8 | 11106ms | 8/8 | **11100ms** |

并发不崩且更快，但超过 4 路后 P95 延迟翻倍 —— 真并行度约 4，故全局定档 4。

旧档位的实际代价：KlineAnalyst 一批扫 3~6 个币，只有 1 个能走本地，其余 3s 抢不到
槽就降级云端。日志实测 **53 次云端降级里 47 次来自它**（41 次抢不到槽 + 6 次超时），
而同期 **GPU 利用率是 0%** —— 本地免费算力闲置，钱花在云端 API 上。

| 配置键 | 含义 | 值 | 备注 |
| --- | --- | --- | --- |
| OLLAMA_MAX_CONCURRENT | 全局并发槽 | 4 | 实测真并行度；再高只排队 |
| OLLAMA_HEAVY_CALLER_SLOTS | 单个重调用方上限 | 2 | = 全局的一半，始终给其他调用方留一半 |
| OLLAMA_SLOT_WAIT_SEC | 抢槽等待 | 3 | 短线链路对延迟敏感，等久了不如直接降级 |

重定档后实测（同为 9 分钟窗口）：

| 指标 | 改前速率 | 改后速率 | 变化 |
| --- | --- | --- | --- |
| 抢槽被拒 | 1.55 次/分 | 0.44 次/分 | **-72%** |
| 云端降级 | 2.10 次/分 | 1.42 次/分 | -32% |

> "不让单个调用方独占"的原始意图仍然正确，因此保留单调用方槽机制，只重定档位；
> 靠放宽并发提高本地命中率，而不是靠拉长等待硬撑。

### 剩余的云端降级是合理的，不要再往本地拉

改后剩余降级仍以 KlineAnalyst 为主，但原因已从"抢不到槽"变为"本地 8s 超时"，
而这一档**不该再放宽**：

- `KlineAnalyst:deep_analysis` 用 `max_tokens=1500`。qwen3:14b 约 40~70 tok/s，
  跑满要 20~30s，8s 超时对它本来就不可能够。
- 云端 DeepSeek 同样长度实测 9.7~10.4s（4288 字符）—— **比本地快**。
- 该调用在短线链路上，延迟敏感。

即"长输出 + 延迟敏感"的调用降级云端是正确取舍，只有短输出调用才值得等本地。

> **两条 LLM 路径是分开记账的**，不要混为一谈：
> `llm_config_service` 的降级云端**不经过** `QuotaGuard`，`llm_quota_usage` 账本里
> 只有 `ModelGateway` 的调用。所以 KlineAnalyst 烧的是 API 账单，
> **不会**挤占深度分析的配额。曾出现的 DeepSeek 5h 窗 37/30 超支，
> 是 ModelGateway 自身调用打满的（其中 25 次是 `gateway_test` 联调所致）。

## 三道"保护真金白银"的闸门叠加，把模拟盘锁成了 0 单（2026-09-04 登记）

症状：09-04 全天短线 0 单、中线 0 单（09-03 尚有 31 / 1 笔），长线仅 2 笔在仓。

三道闸门各自都有正当理由，但**都是按实盘口径设计，又被无差别施加到模拟盘**。
叠加后形成三个自锁：亏损 → 封禁/降权 → 开不了单 → 攒不到翻案样本 → 永不解锁。

### 闸门一：亏损币惩罚状态机（`symbol_penalty`）

由 paper 日报驱动。09-04 17:40 实测九个固定币**全部中招**：
BTC/ETH/SOL/VIRTUAL 进观察名单被 `continue` 直接禁开；
XRP/BNB/UNI/XPL/ASTER 被 `×0.5` 打折 → 当日最高分 69 腰斩成 **34.5**。
而元模型不可用期的探索门槛是 **45** → 数学上永不可达。

惩罚的立论是"止损保护"，只对真金白银成立；模拟盘的亏损**本身就是要采集的样本**。

### 闸门二：`FUSION_PWIN_UNUSABLE_MODE=hold`

元模型 unusable 期直接不开仓。但元模型重训需要真实成交样本，
而样本只能靠开仓产生 —— hold 让它永远等不到重训数据。

### 闸门三：短线影子模式（`SCALP_SHADOW_MODE`）

`scalp_shadow_enabled(trade_mode)` **收了 `trade_mode` 参数却从未使用**，
注释明写"paper/live 一律受控"。影子的全部立论是：

> 短线 08-26→09-02 净 -$1,193，其中**手续费 $1,420**；单笔目标 ≈ 4bp 而往返成本 ≈ 8bp

**手续费只在实盘真实发生，模拟盘的手续费是虚拟记账。** 为省一笔不存在的费用，
代价是拿不到滑点 / 实际成交价 / 真实 PnL / 退出行为 —— 而恢复真单的晋升门
要求"影子样本 N ≥ 300 且净收益 95% 下界 > 0"，这些统计量恰恰只能由真实成交产生。

### 中线另有两处

- **AI 候选污染扫描宇宙**：每 tick 只扫 `MIDLONG_SCAN_BATCH` 个币，AI 选出的
  AVAX/LINK 占着名额却在 active 所取不到 K 线（0 根），白耗轮次。
  已持仓的 AI 币仍并入 `_ai_mid_hold` 续管，不会没人管仓。
- **入场阈值 0.35 过高**：实测全 universe `|score|` 中位 0.128，只有 UNI 过门
  且被逆资金流一致性闸拦下 → 全天零开仓。

### 处置：实盘从严，模拟盘采样

| key | 含义 | 值 | 依据 |
| --- | --- | --- | --- |
| `PAPER_SYMBOL_PENALTY_ENABLED` | 模拟盘是否套用亏损币状态机 | false | 模拟盘无真金白银可保护，亏损即样本 |
| `SCALP_SHADOW_MODE_PAPER` | 模拟盘是否走影子层 | false | 手续费为虚拟记账，影子挡住晋升门所需真实样本 |
| `SCALP_SHADOW_MODE` | 实盘影子层（原状） | true | 未过 edge_ledger 晋升门不得恢复真单 |
| `FUSION_PWIN_UNUSABLE_MODE_PAPER` | 元模型不可用期 paper 处置 | explore_quota | 继续采样以攒重训数据 |
| `FUSION_PWIN_UNUSABLE_MODE_LIVE` | 元模型不可用期 live 处置 | hold | 实盘不得在 pwin 噪声期盲开 |
| `FUSION_PWIN_EXPLORE_DAILY_QUOTA_PAPER` | paper 探索日配额 | 120 | 覆盖九个固定币的日内轮次 |
| `FUSION_PWIN_EXPLORE_DAILY_QUOTA_LIVE` | live 探索日配额 | 5 | 保持原保守值 |
| `FUSION_PROBE_MIN_PWIN_PAPER` | paper 探针最低 pwin | 0.42 | 低于最低分档，专收噪声带样本 |
| `FUSION_PROBE_DAILY_QUOTA_PAPER` | paper 探针日配额 | 60 | 采样需要 |
| `FUSION_PROBE_DAILY_QUOTA_LIVE` | live 探针日配额 | 0 | 负 EV 带实盘不下探针 |
| `FACTOR_ROUTE_ENTRY_THRESHOLD_PAPER` | 中线 paper 入场阈值 | 0.22 | 实测中位 0.128，0.35 全天零过门 |
| `FACTOR_ROUTE_ENTRY_THRESHOLD_LIVE` | 中线 live 入场阈值 | 0.35 | 实盘不放松 |
| `MIDLONG_MID_AI_CANDIDATES_ENABLED` | AI 候选并入中线扫描宇宙 | false | AI 币在 active 所无 K 线，占名额不产出 |

回归测试：`backend/tests/unit/test_paper_sampling_not_blocked_20260904.py`（14 例），
逐条锁死上述分治，防止再被"统一口径"合并回去。

## 中长线 LLM 主脑（2026-09-05）

> 没有新鲜深度论题就不许新开。因子 / V2 / E1 / 图审只当证据和否决闸。
> 回滚：`MIDLONG_NO_THESIS_NO_OPEN=false` 或 `MIDLONG_BRAIN_MODE` 切走 = 紧急全禁新开，不静默把方向盘还给因子。

| 配置键 | 声明意图 | 期望值 | 备注 |
| --- | --- | --- | --- |
| MIDLONG_BRAIN_MODE | 中长线唯一主脑模式 | llm | 唯一合法值 llm；去掉 hybrid 口子，避免半接入 |
| MIDLONG_THESIS_TTL_MID_S | 中线论题最长沉默（秒） | 14400 | 成功票才吃满；失败票见 FAIL_BACKOFF |
| MIDLONG_THESIS_TTL_LONG_S | 长线论题最长沉默（秒） | 28800 | 成功票才吃满；失败票见 FAIL_BACKOFF |
| MIDLONG_THESIS_FAIL_BACKOFF_MID_S | 中线失败票短退避 | 1200 | 低分票不得锁死 4h |
| MIDLONG_THESIS_FAIL_BACKOFF_LONG_S | 长线失败票短退避 | 2400 | 低分票不得锁死 8h |
| MIDLONG_WATCH_SHOCK_PCT_MID | 中线现价冲击阈值 | 0.012 | 同根 4h K 内涨跌超 1.2% 重问 |
| MIDLONG_WATCH_SHOCK_PCT_LONG | 长线现价冲击阈值 | 0.025 | 同日涨跌超 2.5% 重问 |
| MIDLONG_WATCH_CHASE_PCT_MID | 中线追高阈值 | 0.008 | 论题后已涨 0.8% 先重问，不拿旧票追 |
| MIDLONG_WATCH_CHASE_PCT_LONG | 长线追高阈值 | 0.015 | 论题后已涨 1.5% 先重问 |
| MIDLONG_WATCH_NEAR_INV_PCT | 失效价靠近 | 0.004 | 离失效价 0.4% 立刻重问 |
| MIDLONG_WATCH_MIN_REFRESH_S | 冲击类最小间隔 | 1800 | 收盘/失效/事件不受此限 |
| MIDLONG_NO_THESIS_NO_OPEN | 无新鲜 accepted 论题禁止新开；false=紧急全禁 | true | false 不是放开因子，是连论题也不开 |
| MIDLONG_CHART_REQUIRED | 无新鲜图审禁新开 | false | 图是附加证据；缺图 fail-open。true 才紧急收紧 |
| ANALYSIS_UNIVERSE | 图审/分析宇宙 | BTC,ETH,SOL,BNB,XRP,DOGE,ADA,AVAX,UNI,ASTER,VIRTUAL,XPL,LINK | 覆盖中长线白名单，有图才做否决 |
| ANALYSIS_TREND_CHART_SEC | 图审扫描间隔 | 28800 | 8 小时扫一轮；不跟文字论题绑死 |
| ANALYSIS_TREND_CHART_FRESH_H | 图审新鲜窗（小时） | 8 | 对齐图审间隔；窗内已有共识则跳过 |
| ANALYSIS_TREND_CHART_MAX_PER_CYCLE | 单轮最多审几个币 | 4 | 缺图/过期优先，避免一轮串行堵死 |
| MIDLONG_CHART_GATE_LOOKBACK_H | 开仓闸认图审的最长小时 | 16 | 约两轮图审；更老的图不当否决 |
| MIDLONG_THESIS_MAX_REFRESH_PER_CYCLE | 闲扫每轮最多刷新 | 3 | 纯 TTL 闲扫配额 |
| MIDLONG_THESIS_WATCH_REFRESH_CAP | 变盘池每轮最多刷新 | 5 | watch/失败退避优先 |
| ANALYSIS_WEEKLY_ENABLED | 周复盘定时注册 | false | 研究报告不控仓，默认停 |
| ANALYSIS_TIMING_ENABLED | 择时定时注册 | false | 同上 |
| MIDLONG_MID_VIA_FACTOR_ROUTE | 中线因子路由自己开仓 | false | 因子只产证据快照 |
| LONG_TREND_V2 | 长线 V2 规则自己开仓 | 0 | 脑模式下 V2 降为证据 |
| THESIS_SHADOW_ENABLED | 本地 14B 影子论题 | false | 主脑上线后下线 |
| COMMITTEE_SHADOW_ENABLED | 影子委员会 | false | 主脑上线后下线 |
| TREND_E1_ENABLED | E1 日任务真下单 | false | 可留算目标+漂移，不许 place_order |
| TREND_E1_LONG_LANE_EXCLUSIVE | E1 独占长车道 | false | 否则 LLM 长线新开会被交易所层拒掉 |
| SCALP_OPEN_DISABLED | 短线新开总闸（纸盘+实盘） | true | 已有短线仓只许平、不许加 |
| PAIR_SELECTOR_WATCHER_ENABLED | 短线 AI 选币扫描 | false | 选币已关仍 5 分钟扫的漏开源 |
| E5_SHADOW_ENABLED | E5 事件影子车道 | false | 停空转 |
| AGENT_PARAM_SEARCH_ENABLED | 周日 ParamSearch 重任务 | false | 停 |
| ALLOCATOR_APPLY | 分配器写入 runtime_tuning | false | false 时不再注册每日空转 |
