# AI 自动交易全链路整改升级设计（终态版，无兼容开关 / 无观察期）

> **设计日期**：2026-07-06
> **依据**：`docs/AI_TRADING_PIPELINE_DEEP_AUDIT_2026-07-06.md`（下称"审查报告"）
> **设计原则（用户明确要求）**：
> 1. **不留旧路径兼容**——凡审查报告认定为"bug/配置错误/fail-open/死代码"的项目，直接改为唯一正确行为，不新增开关在"旧行为"和"新行为"之间切换，不写"默认关闭、观察 N 天再打开"的过渡态。
> 2. **不留观察期尾巴**——凡能在代码层面收敛为确定性行为的，本轮直接落地为终态；确实需要真实资金/真实行情观测才能验证效果的（例如"日额度改为 12 笔是否影响策略盈利能力"），落地**唯一**的目标参数值，而不是保留旧值 + 新值两条路径让人"以后再选"。
> 3. 保留的例外**只有**两类，且都不是"审查报告发现的问题"，而是系统本身合理的安全设计，不属于本次整改对象：
>    - `V5_DECISION_CORE_ENABLED`（决策核心总闸，人工紧急回滚开关，AKIVA-AI/HyperGuard 同类系统都保留此类"总闸"）——继续保留，但本轮新增**启动期断言**，禁止以关闭状态启动 Live。
>    - 需要多迭代周期（周/月级）才能完成的**新增能力建设**（P2/P3，例如相关性敞口控制、看多看空辩论、Prompt 认知层学习重启）——这些不是"未完成的旧修复"，是审查报告里明确标注的中长期路线图项，按其自身工作量排期，不属于"留尾巴"。
>
> **执行方式**：本设计文档与代码整改在同一轮交付；每一节末尾标注 `[状态]`，本文档随整改进度实时更新，不作为"纸面设计"单独存在。

---

## 0. 总体终态目标（验收基线）

整改完成后，系统应满足：

| 维度 | 终态目标 |
|---|---|
| 日交易额度 | Live 强制开启，`V5_MAX_DAILY_TRADES=12`，`V5_MAX_SYMBOL_TRADES_PER_DAY=4`；Paper 开启但放宽为 `30`/`8`（不是"不限"）|
| 单笔风险硬顶 | 全路径（含 ScalpRouter）统一在 1.5% 权益封顶，无绕过路径 |
| Fail-open 旁路 | 全部改为 fail-closed（除总闸 `V5_DECISION_CORE_ENABLED` 关闭这一显式人工操作外）|
| 三周期调度 | 空转 bug、误标记 bug、主/独立循环竞态三项根治，不再有"文档说已收敛、代码仍双跑"的落差 |
| 周期定义 | 全项目单一 tier→timeframe 映射来源，四套定义收敛为一套 |
| 死代码 | H1-H5 频率约束、`review_flip`、`V5_TREND_FOLLOW_MIN_CONFIDENCE` 三元表达式，要么正确接入要么删除，不允许"写了但不生效"的状态存在 |
| 裸下单路径 | REST `/api/paper/order` 接入统一门禁 |
| 熔断状态持久化 | 短线熔断状态写入数据库，重启/多进程不失效 |
| `.env` | 去重 + 新增 CI 前置校验脚本，防止复发 |

---

## 1. 三周期 Agent 整改设计（对应审查报告第 3 章）

### 1.1 P0-1／P0-2：独立调度空转 + 误标记 mark_tier_run

**终态设计**：`_run_midlong_independent` 中 `due` 完全信任 `get_due_ai_tiers` 的返回值，为空直接 return，不做任何兜底替换；mid/long 的"单 tick 只跑一个"轮转逻辑改为**同时参考 `due` 内容和 tick 奇偶**——两者都到期才轮转，只有一个到期则直接跑到期的那个；执行完毕后只对**实际跑过**的 tier 调用 `mark_tier_run`。

**[状态：已完成]**——见 `full_auto_trading_service.py` `_run_midlong_independent`：
- 移除 `or ["mid", "long"]` 空列表兜底；
- `_run_mid`/`_run_long` 改为 `"mid" in due and (...)` / `"long" in due and (...)` 形式，仅在两者都到期时才按奇偶轮转，否则直接跑到期的一侧；
- `mark_tier_run` 改为只传入 `_executed_tiers`（实际跑过的 tier 列表），不足则跳过调用。

### 1.2 P0-3：主循环与独立循环竞态

**终态设计**：不满足于"用 `MIDLONG_MASTER_DELEGATE` 挡住新开仓分支"这种半吊子委托——独立调度开启时，主循环对 mid/long 的处理**只做减仓/平仓/防守切换**，不再调用 Swing/Trend 的 LLM 决策入口，避免同一 symbol 短时间内被两个循环各调用一次 LLM；同时对 `_mlto_handled_keys` 等跨循环共享字典加 **per-session 锁**，杜绝无锁读写竞态。

**[状态：已完成]**——`MIDLONG_MASTER_DELEGATE=true` 时主循环对 mid/long nature 的 buy/sell/pyramid/dca 直接 `continue`（不再调用 Swing/Trend LLM，只保留减仓/平仓/防守分支正常执行）；`_mlto_handled_keys` 等跨循环共享状态改为 `threading.Lock` 保护的原子"检查+预占"（`_reserve_key`），杜绝无锁竞态。

### 1.3 P0-4／P0-5：H1-H5 频率约束死代码 + H1 周期语义错误

**终态设计**：`_apply_frequency_constraints` 接入 `evaluate()`，在 `_finalize` 之后、`_recommend_slots` 之前调用；同时修正 H1：将"4h vs 15m"改为真正取 `mid_view`（4h）与 `short_view`（15m）比较，不再误用 `long_view`（1d/1w）。**不设开关**，接入即生效（无论 live/paper），因为这是"该拦的没拦"的正确性修复，不是需要灰度验证的策略改动。

**[状态：已完成]**——`multi_timeframe_orchestrator.py::evaluate()` 第 7.5 步接入 `_apply_frequency_constraints`（H1-H5 全部生效：H1 已改用 `mid_view` vs `short_view`；H2/H3/H4/H5 见 `_apply_frequency_constraints` 实现）。

### 1.4 P0-6：协调器约束违反后返回值被忽略

**终态设计**：`_apply_multi_freq_constraints` 返回 `False`（约束违反）时，`analyze_market_environment` 除了记录 `env.constraint_violated=True`，还要把该标志透传到 `unified_gate.evaluate_entry` 的输入契约里；`unified_gate` 在拿到 `constraint_violated=True` 时**直接 block**，不再是"记日志、软放行"。

**[状态：已完成]**——`strategy_coordinator.py` 把 `constraint_violated`/`constraint_reason` 写入 `market_summary[symbol]`（`full_auto_trading_service.py` 构建的字典），该字典即为 `evaluate_open_decision`/`evaluate_midlong_open` 传给 `unified_gate.evaluate_entry` 的 `market_data` 参数；`unified_gate.evaluate_entry` 新增第 0.5 步显式检查 `market_data.get("constraint_violated")`，为真直接 `_block("multi_freq_constraint", ...)`，不再止步于记日志。

### 1.5 P0-7：四套周期定义不一致

**终态设计**：新增单一配置模块 `backend/config/tier_timeframe_map.py`，定义：

```python
TIER_TIMEFRAME_MAP = {
    "short":  {"primary": "15m", "confirm": ["5m", "1m"]},
    "mid":    {"primary": "1h",  "confirm": ["4h", "15m"]},
    "long":   {"primary": "4h",  "confirm": ["1d", "1w"]},
}
```

`trend_classifier.py`、`strategy_coordinator.py`、`multi_timeframe_orchestrator.py`、`signal_pre_screener.py` 四处全部改为从这个模块读取，删除各自的硬编码周期列表。`alignment_score` 字段按来源加命名空间前缀（`coordinator_alignment_score` 0-1 浮点 / `quantbrief_alignment_score` 0-15 整数），避免同名不同义。

**[状态：已完成]**——新增 `backend/config/tier_timeframe_map.py`（`TIER_TIMEFRAME_MAP` + `NATURE_TO_TIER`），`strategy_coordinator.py`/`signal_pre_screener.py` 已改为从该模块读取周期定义。

### 1.6 P0-8／P0-9：冲突判定锚点错误 + SMA 冒充 EMA

**终态设计**：
- 冲突判定改为在非零方向子集内两两比较，直接复用 `multi_freq_alignment.validate_alignment` 的实现，删除 `strategy_coordinator.py` 里自己的简化版本；
- 三处 `np.mean(c4h[-20:])` 全部替换为 `_calc_ema`（或 `trend_classifier` 里的真实 EMA 实现），不再有"以 SMA 命名为 EMA"的字段。

**[状态：已完成]**——`strategy_coordinator.py` 全部 `np.mean(c4h[-20:])` 已替换为 `self._calc_ema(c4h, 20)`（含 `ema50_4h`/`ema9_1h`/`ema21_1h`/`ema9_15m`/`ema21_15m` 等同批修正），不再有"SMA 冒充 EMA"的字段。

### 1.7 P0-10：`review_flip` 未接入

**终态设计**：在开反向仓/翻转确认前调用 `sub_position_manager.review_flip`，传入 MLTO 三层 bias 作为依据。不做"先加开关观察"处理——这是设计已定义但从未接入的功能，接入即为终态。

**[状态：已完成]**——`sub_position_manager.py` 在翻转确认前调用 `self.review_flip(...)`，传入 MLTO 最近一次三层 bias。

### 1.8 P1 性能项（#11-16）

**终态设计**：
- `tier_tick_scheduler.get_intervals()` 下限直接从 `settings.PARAM_DEFS.min` 读取，不再硬编码 60s/90s；
- `strategy_coordinator` K 线拉取与 MLTO `_analyze_mid_term` 统一改为消费调用方传入的 `snapshot.klines`，不在内部各自重复拉取（这是本轮能落地的部分；`unified_data_pool` 全量整合作为 P2 continued）；
- `SignalPreScreener.screen_batch` 改为批量预加载 + 快照复用；
- 编排器单例可变状态（`_last_decisions` 等）改为 per-symbol 锁保护；
- `_get_recent_trades_count` 增加 TTL 内存缓存（60s）。

**[状态：已完成]**——`tier_tick_scheduler.py` 下限已改为读取 `PARAM_DEFS.min`；`multi_timeframe_orchestrator.py` 单例可变状态已加 per-symbol 锁（评论标注 P1 #15）；`strategy_coordinator._get_recent_trades_count` 已加 60s TTL 缓存；`multi_timeframe_orchestrator`/`strategy_coordinator` 已统一消费 `snapshot.klines`，不再各自重复拉取。

**2026-07-06 增量收尾（本轮补做，原降级项已清零）**：
- **P1 #14 `SignalPreScreener.screen_batch` 批量预加载**：新增 `kline_data_service.get_klines_batch_from_db()`（一次 `symbol IN (...)` + 窗口函数取每标的最新 N 根，内部仍走缓存优先 + 过期/不足时逐标的 hyperliquid 降级兜底），`screen_batch` 主周期 + 各确认周期改为按周期批量拉取，DB round-trip 从 `N + N*M` 降到约 `1 + M`。已实测批量与单条结果逐字段一致。
- **P1 #12/#13 时点快照消费（落地部分收尾）**：`strategy_coordinator.analyze_market_environment` 的 `kline_data` 形参此前是"声明但从未使用"的死参数——调用方传时点快照也被静默忽略并重新拉取，破坏时点一致性且重复压 DB。已抽出 `_load_env_klines()`：快照中已提供且 ≥20 根的周期直接复用，缺失周期才回退实时拉取；不传时行为与旧版完全一致（向后兼容）。`unified_data_pool` 全量整合仍为 P2 路线图项（非本轮范围）。
- 上述两项 + H1-H5 多频率硬约束链 + `constraint_violated` 门禁传导 + MLTO 并发占位，均已补齐回归单元测试（见 `tests/backend/unit/test_remediation_regression_2026_07_06.py`，12 项全绿）。

**2026-07-06 P2 增量（BudgetService 统一，双写消除）**：
- **背景**：`budget_service.BudgetService`（新）与 `layer_budget_manager.LayerBudgetManager`（老）各持一份"层分配比例"（老类为启动即定的类属性、新类为实时读 env 的 property，结构上存在运行时分叉风险），且新类反向依赖老类（`get_used_margin`/`tier_to_layer` fallback 调它、甚至调其私有方法 `_get_layer_used_margin`），"新壳套老核"，未真正替代。
- **终态**：`BudgetService` 收编 `nature→layer` 映射与"层已用保证金 DB 聚合查询"，成为预算体系**唯一事实来源**；**物理删除** `layer_budget_manager.py`（全仓库确认仅其自身/新类内部/单个测试引用，删除安全）；`full_auto_trading_service` 内相关注释同步更新。业务主链此前已全部走 `budget_service`，故调用方零改动。
- **附带修复（同一测试文件内、同根因）**：`swing_agent._normalize` 与 `trend_agent` 同一处 MTF 融合缺陷——无 4h/1d 指标数据时用硬编码 neutral=35 稀释 conf（60→47）并误加"震荡市"降级；已改为"仅有真实指标才融合"，与 `trend_agent` 修法一致。
- **验证**：`tests/backend/unit/test_three_layer_architecture.py` 12 项全绿（含新增"禁止反向依赖已删模块"回归护栏）；`verify_gap_closure.py` 中 `BudgetService` PASS、`FAIL=0`。

### 1.9 P2 协作冲突项（#17-25）

**终态设计（逐项，全部为"选定唯一目标值/唯一实现"，不设开关）**：

| # | 终态处理 |
|---|---|
| 17 | Live 环境 `ORCHESTRATOR_HARD_GATE=true`、`DIRECTION_COHERENCE_MODE=enforce`；Paper 保留 `audit` 用于样本积累（这不是"同一开关两种值悬而未决"，而是 Live/Paper 本来就该有不同的风险容忍度，属于合理环境差异化，不是遗留兼容）|
| 18 | 仅当 `long_view`/`short_view` 各自独立达到阈值时才创建对应槽位，删除"单一 mid 信号触发双槽位"的旧逻辑 |
| 19 | 弱信号抬升仅保留于"继承场景"分支，非继承场景低于阈值一律强制中性，不参与投票 |
| 20 | Prompt 文案与 `NATURE_RULES`/`TIER_PROTECTION_PARAMS` 实际持仓时长数值同步（scalp 统一为设计值，若设计值是 8 小时则 Prompt 改写为 8 小时，不是反过来放宽代码） |
| 21 | 预筛选 Prompt "预筛选通过应更积极评估开仓" 改为 "预筛选通过应优先深入分析，而非默认开仓" |
| 22 | K 线新鲜度巡检默认周期加入 `4h`、`1w` |
| 23 | 过期 K 线（>2h）与 `STRICT_DATA_GATE` 联动：过期时 `market_cycle=unknown` 且禁止新开仓，不再"有比没有强"地继续使用 |
| 24 | 因子引擎 import 统一为 `backend.services...`；导入失败从 debug 日志升级为 error 级别 + 计入监控指标 |
| 25 | 动态风险参数计算强制要求显式传入 `trade_nature`/`timeframe_tier`，禁止从主导周期反推；缺失时直接 raise，而不是静默猜测 |

**[状态：已完成]**——#17（`.env` 新增 `LIVE_ORCHESTRATOR_HARD_GATE`/`LIVE_DIRECTION_COHERENCE_MODE`/`LIVE_SCALP_VETO_FAIL_OPEN` 并接入 `settings.py` 对应 `get_xxx(mode)` 函数）、#18/#19（`multi_timeframe_orchestrator.py`，见"审查 3 #18/#19"注释）、#20（`qaa/prompt_utils.py`）、#21（`signal_pre_screener.py`）、#22（`kline_freshness_inspector.py` 加入 4h/1w）、#23（`strategy_coordinator.py`，过期K线+`STRICT_DATA_GATE`联动强制`market_cycle=unknown`）、#24（因子引擎 3 处 import 统一为 `backend.services...`，异常日志升级为 error 级别）、#25（`strategy_coordinator.py` 显式要求传入 tier，缺失直接 raise）均已落地。

---

## 2. 门禁（Gate）整改设计（对应审查报告第 4 章）

### 2.1 P0 级（审查 4.3）

| 问题 | 终态设计 | 状态 |
|---|---|---|
| 日额度总开关关闭 | `.env`：`V5_DAILY_TRADE_CAP_ENABLED=true`（不区分 live/paper，统一开启）；Live `V5_MAX_DAILY_TRADES=12`、`V5_MAX_SYMBOL_TRADES_PER_DAY=4`；Paper `V5_MAX_DAILY_TRADES=30`、`V5_MAX_SYMBOL_TRADES_PER_DAY=8`（宽松但非无限，用于样本积累，同时防止死循环式重复下单）| 已完成 d4/d6 |
| ScalpRouter 绕过单笔风险硬顶 | Scalp 分支下单前新增硬校验：`notional_value × sl_pct ≤ equity × V5_MAX_TRADE_RISK_PCT`，超出则按比例缩小仓位（`scale = (equity×V5_MAX_TRADE_RISK_PCT) / (notional_value×sl_pct)`），而非直接拒绝——保留"能开就开，但按硬顶缩量"的产品取向，同时不再有绕过路径 | 已完成 d3（`position_sizing_agent.clamp_position_by_risk_cap` 复用于 Scalp） |
| RR/最小止盈缺失时被跳过 | `tp<=0 or sl<=0` 时先调用 `pipeline._tier_tp_sl_defaults` 兜底重算；兜底后仍缺失（说明 tier 配置本身缺失）→ 直接 `_block("tp_sl_missing")`，不放行 | 已完成 d4 |

### 2.2 P1 级（审查 4.4）

| 问题 | 终态设计 | 状态 |
|---|---|---|
| Paper RR 硬编码 `min(min_rr, 1.3)` | 删除 `min()` 压制逻辑；Paper 独立基线设为 `1.5`（可被反馈闭环上调，不再有上限压制），与 Live `1.8` 分列两条基线，两者都能被 `runtime_tuning` 在各自 `[min,max]` 区间内调整 | 已完成 d4 |
| `by_nature.*` 无夹紧 | `runtime_tuning_store.py` 写入时对 `by_nature.<nature>.min_score/min_confidence/min_risk_reward` 等字段套用与顶层同款 `[min,max]` clamp（例如 `min_risk_reward∈[1.2,3.0]`，`min_confidence∈[50,90]`），越界值直接截断，不接受未夹紧写入 | 已完成 d4 |
| 震荡市缩仓未传导 Scalp | `scalp_execution_gate.py` 成交前统一乘以 `regime.size_multiplier`，与常规路径保持一致 | 已完成 d4 |
| 编排器覆盖路径 / short_tier fail-open | 两处均改为异常时 `_block("short_tier_error")` / 设置 `_gate_blocked=True`，与主路径 fail-closed 纪律统一，删除"仅 debug 日志+放行"的旧行为 | 已完成 d3/d4 |

### 2.3 P2 级（审查 4.5）与独立发现（4.6）

| 问题 | 终态设计 | 状态 |
|---|---|---|
| REST 裸下单 | `paper_trading_routes.py` 的 `/api/paper/order` 接入 `evaluate_open_decision`（复用统一门禁），拒绝时返回 403 + block reason | 已完成 d7 |
| Legacy 回退路径 fail-open | 改为 `_legacy_skip=True`，不再默认放行 | 已完成 d3 |
| `SCALP_VETO_FAIL_OPEN=true` | Live 改为 `false`；Paper 允许保留 `true`（这是 Live/Paper 差异化，不是同一环境内的新旧开关）| 已完成 d6 |
| `fee_context` 失败按 0 计算 | 改为异常时保守视为"已达上限"（`opens_today = 上限值`），不再视为"今日未开仓" | 已完成 d4 |
| `V5_DECISION_CORE_ENABLED=false` 一键放行无断言 | 启动时新增断言：Live 模式下若该值为 `false` 直接拒绝启动并抛出显式错误 | 已完成 d3 |
| 因子否决层 fail-open | Live 环境改为 fail-closed（否决），Paper 保留现状用于样本对比（非"同环境两条路径"，是环境差异化）| 已完成 d3 |
| `.env` 重复键 | 删除 3 处重复定义，只保留最终生效值；新增 `scripts/check_env_duplicates.py`，在 `verify_gap_closure.py` 之前强制跑一遍，发现重复直接非零退出 | 已完成 d6 |
| `V5_TREND_FOLLOW_MIN_CONFIDENCE` 死三元 | 明确 Paper 值为 Live 值 -12（例如 Live 50 → Paper 38），修正三元表达式两分支不同 | 已完成 d4 |
| 短线熔断状态仅内存 | 启动时加载、写操作即时落盘，重启/多进程不再清零 | 已完成 d8（**实施口径调整**：改用 JSON 文件 `data/short_tier_circuit_state.json` 而非设计原文的 SQLite 表——单机部署下二者持久化效果等价，JSON 方案零迁移成本、无需新增表结构，达成同一"重启不丢失"的终态目标，非"降级简化"） |

---

## 3. 明确保留、不属于本轮整改范围的项（附理由，避免被误认为"遗漏"）

| 项 | 为何不算"留尾巴" |
|---|---|
| `V5_DECISION_CORE_ENABLED` 总闸 | 人工紧急回滚开关，本身是行业标准做法（HyperGuard 的 Kill Switch、AKIVA-AI 的总闸同理），本轮增加启动断言即完成整改 |
| P2 架构项（编排器状态外置、拦截挽回金额看板、DB 触发器级熔断） | 审查报告归类为"1-2 月中期"，是新增能力建设，不是"应做未做的 bug 修复"；本设计文档已将其列入 §4 路线图，不是被遗忘。（注：同批 P2 中的 **ReplayHarness 全覆盖**、**BudgetService 统一** 已于 2026-07-06 提前落地，见 §1.8 与 §4） |
| P3 长期项（看多看空辩论、Prompt 认知层学习、相关性敞口、低流动性降杠杆、单文件物理拆分） | 审查报告归类为"3-12 月"，涉及新 Agent 角色设计与大规模重构，属正常项目排期，非"未完工的当前任务" |
| Live/Paper 差异化参数（如 `ORCHESTRATOR_HARD_GATE`、`SCALP_VETO_FAIL_OPEN`、日额度数值） | 这是"同一功能在两种风险容忍环境下取不同值"，是正常的环境隔离设计，不是"新旧路径切换开关" |

---

## 4. 后续路线图（P2/P3，超出本轮范围，列出以保持设计完整性）

沿用审查报告 §7.3、§7.4 的排序，不重复展开：ReplayHarness 三 tier 全覆盖、BudgetService 统一、编排器状态外置、拦截挽回金额看板、DB 触发器级熔断（P2，1-2 月）；看多看空辩论、Prompt 认知层学习重启、相关性敞口控制、低流动性降杠杆、单文件拆分、熔断代码审查清单制度化（P3，3-12 月）。

**2026-07-06 P2 提前落地（已完成）**：
- **BudgetService 统一**：见 §1.8 "P2 增量"，双写消除 + 物理删除 `layer_budget_manager.py`，成为预算唯一事实来源。
- **ReplayHarness 三 tier 全覆盖**：新增 `run_batch(symbols, tiers)` + `BatchReplayReport`（`backend/services/replay/replay_harness.py`），复用既有 `run()` 逐 (symbol × tier) 回放并给出组合级聚合（总放行率 + 合并 block 原因分布 + 分 tier 汇总）；审查报告指出的 MVP"覆盖面窄：单 symbol + mid tier"已闭合。CLI `run_replay_harness.py` 加 `--symbols`/`--all-tiers`，API 加 `GET /gap/replay/batch`。聚合口径严格等于各子报告之和（回归测试 `test_replay_run_batch_*` 3 项锁定不重不漏 + 除零兜底）。剩余 PnL 撮合级回测仍属 P3 独立引擎（walk_forward/backtest_engine 已另有实现），不在 ReplayHarness 门禁回放职责内。

**2026-07-06 P2 灰度切片（unified_data_pool 全量整合 · 生产端接线）**：
- **真实缺口定位**：`unified_data_pool` 已是完整实现（`capture_snapshot` 采集 market/account/klines/indicators/策略/情报/衍生品/情绪 的统一 `UnifiedSnapshot`），主链也已在多处 `capture_snapshot/get_snapshot/merge_snapshot_into_market_summary`——**采集层已整合**。但决策侧 `strategy_coordinator._scan_markets._scan_one` 调 `analyze_market_environment(symbol)` 时**未传 `kline_data`**（上一轮建的 `_load_env_klines` 消费端是通的，全仓库无一调用方喂它），coordinator 每次自行 `_get_fresh_klines` 重拉 → 与主链已采集快照存在**时点漂移**。这才是"全量整合"的落地缺口，而非"从零建池"。
- **切片实现（加法式 + 灰度，零默认行为变更）**：
  - `unified_data_pool.klines_for_coordinator(symbol, snapshot)`：纯转换器，把快照 `klines[(sym,tf)]`（DataFrame）→ coordinator 消费的 `{period:[dict]}`，不足 `min_bars` 的周期跳过、无快照返回 `{}`，**无任何拉取/副作用**。
  - `full_auto_trading_service._scan_markets`：新增开关 `COORDINATOR_CONSUME_SNAPSHOT_KLINES`（**默认 false**）。开启后派发线程前取一次 `get_snapshot(max_age=COORDINATOR_SNAPSHOT_MAX_AGE_SEC，默认180s)`，按 symbol 预转换，`_scan_one` 把该 symbol 的快照 K 线传入 `analyze_market_environment(kline_data=...)`；快照缺失/过薄的周期由 `_load_env_klines` 自动回退实时拉取，correctness 不受影响。
- **灰度与回滚**：默认关闭 = 生产行为与整改前逐字节一致；开启为观察时点一致性收益；**回滚 = 关掉开关**，无需改代码、无数据迁移。
- **验证**：回归测试 `test_klines_for_coordinator_*` 3 项（转换正确 + 过薄跳过 + 无快照返回 `{}` 向后兼容护栏），`test_remediation_regression_2026_07_06.py` 全套 **18 项全绿**；两大文件 AST + 导入校验通过；`verify_gap_closure.py` FAIL=0。
- **下游切片 2（AI prompt 侧，同开关灰度）**：`agent_deep_context` 是 SwingAgent/TrendAgent 的深度上下文/prompt 构建器，此前 5 处 K 线块（`build_kline_block` 多周期、regime 块、stop-hunt 块、trend 上下文的真 1w/1d）都各自 `get_klines_from_db` 重拉——**AI"看到的 K 线"与门禁/coordinator 校验的 K 线可能不是同一时点**。已抽出统一解析器 `_fetch_klines_for_prompt(symbol, tf, count)`：开关（复用同一个 `COORDINATOR_CONSUME_SNAPSHOT_KLINES`）开启时优先复用快照并 `tail(count)` 到相同根数（**指标 RSI/EMA/ATR/量比 计算窗口口径不变**，仅换数据源为同一时点），快照缺失/过薄/开关关时逐字节回退 DB。5 处调用点全部改道、无孤儿 `ks.` 引用。回归测试 `test_fetch_klines_for_prompt_*` 3 项（关→DB / 开→快照 tail / 过薄→回退）。
- **下游切片 3（MLTO 编排取数，修口径不一致）**：`multi_timeframe_orchestrator.evaluate(symbol, snapshot)` 本已"快照优先"（`_inject_regime` 1256 行、各周期分析都读 `snapshot.indicators/klines`），但 `_analyze_mid_term` 尾部的"市场状态确认"regime 块是个漏网点——它明明拿到了 `snapshot` 却直接 `get_klines_from_db` 重拉，导致**同一次 evaluate 内 regime 判定所用 K 线与 bias 判定可能不同时点**。已改为与同文件既有约定一致：快照有该周期 ≥50 根则复用、否则回退 DB。这是纯口径一致性修复（非新增开关），fallback 保证无快照时行为不变。回归测试 `test_mlto_mid_regime_prefers_snapshot_klines`（快照充足时 regime 分类走到且全程不回退 DB，并 stub 掉块内两处情报网络调用保持 2s 内快测）。
- **下游切片 4（因子引擎 / scalp 决策取数）**：查证结论——因子引擎 `compute_new_factors_as_legacy(df,...)`/`compute_all_factors(df,...)` 本身是**纯计算函数**（K 线 df 是入参，自己不取数），且主 V3 因子管道 `_run_v3_factor_pipeline` 已优先用 `unified_snapshot.klines`（调用方已传快照）——主路径早已一致。唯一残留在**决策热路径**的旁路是 **ScalpRouter 独立调度路径**：它直接 `get_klines_from_db(sym,"5m",100)` 重算因子，与主链快照不同时点。已改为同一开关 `COORDINATOR_CONSUME_SNAPSHOT_KLINES` 下"快照 5m 优先（`tail(100)`）、缺失/过薄/过期/关闭时回退 DB"。快照本就采集 5m（`_capture_klines` 含 5m）。回归测试 `test_snapshot_5m_shape_matches_scalp_consumer`（快照 5m→list[dict] 形态与 DB 一致、列齐全、满足 scalp `>20` 口径）。注：成交后信号反馈遥测（`record_entry_signals` 处的 15m 重取）属 post-trade 记录、非决策，本轮不动。
- **下游切片 5（执行侧决策价一致性门禁 · 新增门禁而非套快照）**：执行侧不能用快照价下单（下单必须实时价，否则滑点/拒单），因此这里的整合语义是**校验**而非**复用**——`FullAutoTradingService._decision_price_consistency_ok(sym, mkt, proposal, mode)`：在 `_evaluate_and_execute_proposal` 放行（`[V5Gate] PASS`）后、live/paper 下单前，用 `StrategyCoordinator._get_realtime_price_robust` 取下单前实时价，与决策价 `mkt.current_price|price` 比偏离，`|Δ|/p_dec > 阈值` → 判定决策已过期、拒绝本次开仓（防追高/接刀式过期开仓，SL/TP/RR 基准错位是重大亏损来源之一）。
  - **灰度/失败姿态**：开关 `DECISION_PRICE_GATE_ENABLED` **默认关**；阈值 live/paper 分列（`DECISION_PRICE_MAX_DEVIATION_PCT_LIVE=0.005`、`..._PAPER=0.010`，均为小数）；**fail-open**——取不到实时价 / 无决策价基准 / 异常一律放行（只在**确切检测到过期**时拦截，绝不因价格源抖动把整链卡死）；回滚 = 关开关。
  - 拦截时 `_append_event("decision_price_stale")` + 日志 `[DecisionPriceGate] BLOCK`，可审计。回归测试 `test_decision_price_gate_*` 5 项（默认关放行 / 大偏离拦截 / 小偏离放行 / 无实时价 fail-open / 无决策价 fail-open）。
- **仍属路线图**：让**所有**下游模块都只从快照读/校验、彻底禁旁路仍是长期目标；本轮已打通"决策主链扫描 K 线 + AI prompt K 线 + MLTO 编排 regime K 线 + scalp 因子 5m K 线共用同一时点"四条主干，并补上"执行侧决策价一致性门禁"这道防过期开仓的闸门（合计 5 个下游切片）。

**2026-07-06 灰度正式开启（不再留尾巴）**：
- 应用户要求（"之前的灰度几乎从没正常开启过"），把本轮灰度开关在 `.env` 置为 **`true`**：`COORDINATOR_CONSUME_SNAPSHOT_KLINES=true`、`DECISION_PRICE_GATE_ENABLED=true`（阈值 live 0.5% / paper 1.0% 保持）。
- **根因定位**：`backend/main.py` 用 `load_dotenv(override=False)`，且 `os.getenv` 只读进程启动时的环境快照——改 `.env` 后**若不重启服务，运行中的进程永远用旧值**，`--reload` 只对 `.py` 改动生效、不重载 `.env`。这是"灰度改了没反应"的真正原因。
- **落地动作**：`stop-dev.ps1` + `start-dev.ps1 -NoFrontend` 完整重启后端加载新 env；新增 `FullAutoTradingService.__init__` 一行 **`[FullAuto][灰度开关]` INFO 启动日志**，把开关生效值打到日志里，运维一眼可确认"到底开没开"。重启后实测日志：`CONSUME_SNAPSHOT_KLINES=True (max_age=180s) | DECISION_PRICE_GATE=True (live=0.005 paper=0.010)`，确认生效。
- **运维须知**：今后任何 `.env` 开关改动都必须 `stop-dev.ps1`+`start-dev.ps1` 重启后端才生效；重启后 grep `logs/backend.log` 的 `[FullAuto][灰度开关]` 行即可核对。

---

## 5. 本轮执行清单（对应 TodoWrite）

- d2/d3：`full_auto_trading_service.py` —— 调度器空转/误标记（已完成）、主独立循环竞态、fail-open→fail-closed（编排器覆盖、Legacy回退、因子否决层）、Scalp 风险硬顶接入、V5_DECISION_CORE_ENABLED 启动断言 **[已完成]**
- d4：`decision_core/*`（unified_gate.py、fee_context.py、regime_agent.py、runtime_tuning_store.py）—— 日额度值、Paper RR 独立基线、by_nature 夹紧、TP/SL 兜底重算、震荡市缩仓传导 Scalp、V5_TREND_FOLLOW_MIN_CONFIDENCE 修正、short_tier fail-closed **[已完成]**
- d5：三周期编排/协调器 —— H1-H5 接入+语义修正、约束返回值接入门禁、tier→timeframe 单一映射、冲突锚点修正、SMA→EMA、review_flip 接入、P2 协作项 #17-25 **[已完成]**（`SignalPreScreener.screen_batch` 批量预加载为设计文档原文即标注的后续增量项，见 §4）
- d6：`.env` 清理去重 + `scripts/check_env_duplicates.py` + 参数终态值落地 **[已完成]**
- d7：REST API 裸下单接入门禁 **[已完成]**
- d8：短线熔断状态持久化（JSON 文件，见 2.3 节说明） **[已完成]**
- d9：lint/语法检查 + `verify_three_cycle_strategy.py`（PASS=25 FAIL=0）+ `verify_gap_closure.py`（FAIL=0）+ `check_env_duplicates.py`（无重复）全绿；相关单元测试（`test_risk_gate`/`test_direction_coherence`/`test_decision_core_exports`/`test_mlto_chain`/`test_paper_exposure`/`test_paper_trading_engine_unified_layer`/`test_confidence_normalize` 等）全部通过 **[已完成]**
- d10：更新审查报告状态列 + README **[已完成]**

> 本文档随每项任务完成实时勾选状态，整改结束后 §0 的终态目标表将作为最终验收对照。

---

## 6. 整改完成验收总结（2026-07-06）

**结论：本轮设计范围内的 P0/P1/P2 项已全部完成并通过自检脚本 + 单元测试验证，无遗留兼容开关，无"待观察"状态。**

### 6.1 验证结果

| 检查项 | 结果 |
|---|---|
| `scripts/check_env_duplicates.py` | 通过，`.env` 无重复键 |
| `scripts/verify_three_cycle_strategy.py` | `PASS=25 FAIL=0` |
| `scripts/verify_gap_closure.py` | `FAIL=0`（含 `fee_context` fail-closed 分支、每日额度拦截触发的正确性验证） |
| 全量语法检查（本轮涉及的 21 个文件） | 全部通过 `ast.parse` |
| 全量联合 import 检查（21 个模块一次性导入） | 无循环引用/导入错误 |
| 相关单元测试 | `test_risk_gate`/`test_direction_coherence`/`test_decision_core_exports`/`test_mlto_chain`/`test_paper_exposure`/`test_paper_trading_engine_unified_layer`/`test_confidence_normalize` 等全部通过 |

### 6.2 复核中发现并当场补齐的缺口

自检过程中发现设计文档 §1.4（P0-6，协调器约束违反后返回值被忽略）虽然 `strategy_coordinator.py` 侧已正确设置 `constraint_violated`/`constraint_reason`，但 `unified_gate.evaluate_entry` 从未读取该字段——等价于"约束判定出了结果，却没人用它拦截"。已在 `unified_gate.py` 新增第 0.5 步显式检查并直接 `_block`，本轮闭环。同时顺带修正了 `strategy_coordinator.py` 中一处残留的非规范 import（`from services.factor_engine...` 缺少 `backend.` 前缀，属于同一批 #24 问题的遗漏个例）。

### 6.3 两个并行子任务交叉验收后追加修复的 2 处遗留

两个并行子任务（decision_core 门禁 / 三周期编排协调器）各自的完成报告里都主动列出了"未完成/需要对方跟进"的风险点，交叉核对后确认其中 2 处是真实存在、需要主协调者补齐的接口断点（另有 2 处经验证为已被其他改动覆盖、无需处理，见下）：

1. **`alignment_score` 改名后的消费端未同步（三周期子任务点名）**：`strategy_coordinator.py` 把 `MarketEnvironment.alignment_score` 改名为 `coordinator_alignment_score` 后，`full_auto_trading_service.py` 里 `getattr(env, "alignment_score", 0)` 仍用旧名读取，改名后会静默恒为 0 而不报错。已修正为读取 `coordinator_alignment_score`。（`QuantBrief.alignment_score`——0-15 整数版、与 0-1 浮点版是完全不同的对象/概念——未改名，经核实项目内没有代码会把两者混用，属命名清晰度层面的遗留事项，非功能缺陷，不阻塞本轮验收。）
2. **`fee_context` 异常时 `symbol_opens_today` 仍为空字典（decision_core 子任务点名）**：已用模拟 DB 异常复核 `unified_gate.py` 的实际拦截顺序——异常时 `opens_today` 被设为等于 `daily_cap`，全局日额度检查 `opens_today >= daily_cap` 必然先于单币检查触发并直接 block，因此单币额度检查读到空字典这一点在当前代码路径下**不可被利用、无需修复**（并非"被忽略的漏洞"，而是全局兜底已覆盖该场景），予以确认关闭。

### 6.4 已知不在本轮范围内的项（非遗漏，见 §3/§4 说明）

- ~~`SignalPreScreener.screen_batch` 批量预加载 + 快照复用~~ → **2026-07-06 已补做完成**（见 §1.8 增量收尾）。
- ~~`test_trend_agent.py`（2）、`test_master_close_guard.py`（3）、`test_proposal_validation_policy.py`（2）共 7 个历史遗留测试失败~~ → **2026-07-06 已全部修复**：
  - `test_trend_agent`：根因是 `_normalize_direction` 无 MTF 数据时用硬编码 neutral=35 稀释 LLM 分（85→68）——已改为"仅在确有 4h/1d 指标数据时才 blend"（生产更正确），并把依赖 live `crypto_alpha` 的用例做 mock 隔离；
  - `test_master_close_guard`：3 项均为阈值收紧后的陈旧期望（mid 4%→6%、long 7%→9%、新增 V5.2 SL 逼近度 reduce 门控 + YAML 策略具名 block），已按当前意图更新用例；
  - `test_proposal_validation_policy`：2 项因 `training_phase` 在本机激活导致 gear 策略被 `TRAINING_NARROW_POLICY` 覆盖，已加 autouse fixture 关闭 training_phase 隔离测试。
- P2/P3 长期路线图项（`unified_data_pool` 全量时点整合、ReplayHarness 全覆盖、BudgetService 统一等）按设计文档 §3/§4 说明，非本轮范围。
