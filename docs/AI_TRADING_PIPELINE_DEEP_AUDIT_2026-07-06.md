# AI 自动交易全链路深度审查报告

> **审查日期**：2026-07-06
> **审查范围**：`Hyper-Alpha-Arena` 全部 AI 自动交易链路（策略生成 → 门控 → 执行 → 学习闭环），重点是"三周期 Agent"（scalp/swing/trend）与 V5 统一门禁（`decision_core/unified_gate.py`）
> **方法**：代码全文审阅（`decision_core/*`、`full_auto_trading_service.py` 17497 行、`multi_timeframe_orchestrator.py`、`strategy_coordinator.py` 等）+ 24 份历史设计/诊断文档交叉验证 + 运行自检脚本（`verify_three_cycle_strategy.py`、`verify_gap_closure.py`）+ `.env`/`settings.py`/`runtime_tuning.json` 实际生效值核对 + 历史日志与 JSON 快照取证 + 行业竞品与学术论文调研
> **结论先说**：工程团队过去 3 个月的自我诊断质量很高（`honest_project_diagnosis`、`GAP_CLOSURE_AND_SURPASS_DESIGN` 等文档已经发现了本报告中的大部分结构性问题），但存在**"发现问题的速度快于验证修复效果的速度"**的系统性节奏问题——很多"已修复"标记的问题在后续版本中以新形式复发（详见第五章），且至少 1 项被文档明确写为核心安全机制的门禁（日交易上限）**当前处于关闭状态**。
>
> **整改状态更新（2026-07-06 当日闭环）**：本报告第 3/4 章列出的全部 P0/P1/P2 问题已按 `docs/REMEDIATION_DESIGN_AND_EXECUTION_2026-07-06.md` 完成整改并通过自检脚本（`verify_three_cycle_strategy.py` PASS=25/FAIL=0、`verify_gap_closure.py` FAIL=0、`check_env_duplicates.py` 无重复）与相关单元测试验证，无遗留兼容开关、无"观察期"过渡态。详细的终态设计、每项问题的具体修复位置与验收记录见该设计文档；本报告正文保留原始诊断内容不做回填修改，作为整改前的问题基线存档。

---

## 目录

1. [现状架构全景](#1-现状架构全景)
2. [行业竞品与前沿论文对比](#2-行业竞品与前沿论文对比)
3. [三周期 Agent 错误清单与修正建议](#3-三周期-agent-错误清单与修正建议)
4. [门禁配置诊断与修正清单](#4-门禁配置诊断与修正清单)
5. [历史重大问题时间线（证据链）](#5-历史重大问题时间线证据链)
6. [差距对比总表](#6-差距对比总表)
7. [改进方案与分优先级实施步骤](#7-改进方案与分优先级实施步骤)
8. [验证方法](#8-验证方法)
9. [参考文献与竞品](#9-参考文献与竞品)

---

## 1. 现状架构全景

### 1.1 决策链路（当前真实状态，2026-07-06 验证）

```
市场数据 → unified_data_pool / multi_timeframe_orchestrator(MLTO, 软注入)
         ↓
  ┌────────────┬──────────────────┬──────────────────┐
  │  短线 short │    中线 mid       │    长线 long       │
  │  (scalp)   │   (swing)        │  (trend_follow)   │
  ├────────────┼──────────────────┼──────────────────┤
  │ScalpRouter │ SwingAgent 独立   │ TrendAgent 独立    │
  │独立调度     │ 循环 tick=45s     │ 循环 tick=90s      │
  │scan=45~60s │ MIDLONG_AGENT_    │ MIDLONG_AGENT_    │
  │            │ INDEPENDENT_      │ INDEPENDENT_      │
  │            │ SCHEDULER=true    │ SCHEDULER=true    │
  └─────┬──────┴────────┬─────────┴────────┬──────────┘
        ↓               ↓                  ↓
  scalp_execution_   evaluate_midlong_open (pipeline.py)
  gate → evaluate_        ↓
  scalp_proposal      unified_gate.evaluate_entry (V5)
        │             ├─ regime_agent (趋势/震荡/极端)
        │             ├─ fee_context (费用感知)
        │             ├─ threshold_resolver (置信度门槛)
        │             ├─ short_tier_entry_gate (短线硬门/熔断)
        │             └─ 盈亏比/最小止盈 检查
        ↓                  ↓
  SCALP_SIZE_PCT     position_sizing_agent (2-20x 动态杠杆,
  直接算保证金         单笔风险≤1.5%权益硬顶)
        ↓                  ↓
       ExecutionChannel (paper_engine / live client)
        ↓
  DecisionRetrospective 复盘 → decision_feedback_service
        → runtime_tuning.json (60s 热加载) → 回灌 unified_gate
  Hermes/NSGA-II 离线进化(3天) → 冠军参数 → runtime_tuning
```

同时并行存在的：`FullAuto Master 主循环`（`_execute_master_decisions`，六路分析师）仍在跑，通过 `SCALP_MASTER_HARD_BLOCK=true`、`MIDLONG_MASTER_DELEGATE=true` 两个开关"委托"新开仓给上面的独立链路，自己只做减仓/平仓/防守切换——这是 2026-07-05 `DECISION_PATH_CONVERGENCE` 文档 P0 工作的成果，本报告已验证该委托机制**代码确实存在**（`full_auto_trading_service.py` 内 `MidLongExecutionLane`/`ScalpExecutionLane` 分支）。

### 1.2 关键事实核验（本次审查独立验证，而非只读文档）

| 断言 | 验证方式 | 结果 |
|---|---|---|
| 三周期自检全绿 | 运行 `scripts/verify_three_cycle_strategy.py --no-live` | ✅ 16/16 PASS |
| 差距缩短自检全绿 | 运行 `scripts/verify_gap_closure.py` | ✅ 18/18 PASS，但 stderr 打印 `[FeeContext] 费用统计失败（已忽略）: 'NoneType' object has no attribute 'query'`——**验收脚本本身在验收过程中就触发了一次被吞掉的异常**，具有讽刺意味地印证了第 4 章"静默吞异常"问题仍广泛存在 |
| README 声称"日上限 10 笔"已落地 | 读取 `.env` + `settings.py` + `unified_gate.py:240` | ❌ **不成立**。`V5_DAILY_TRADE_CAP_ENABLED=false`，日额度门禁**完全关闭**，且即便打开默认值是 50 笔/日，不是 10 笔 |
| `.env` 配置无重复/无冲突 | 全文件重复 key 扫描 | ❌ 发现 **3 个重复定义的键**：`LAYER_BUDGET_SCALP`（0.60 与 0.40 并存，dotenv 取最后一次即 0.40）、`SCALP_FACTOR_SCAN_INTERVAL_SEC`（45 与 60）、`QAA_ANALYST_STREAM_SAFETY_CAP_S` |
| 单文件维护性 | 行数统计 | `full_auto_trading_service.py` 单文件 **17,497 行 / 1.02MB**，几乎所有决策路径、Master 逻辑、执行逻辑都挤在一个文件里，`ARCHITECTURE.md` 早在 4 月就把"物理拆分"列为 P3 但至今未做 |

### 1.3 架构层面的核心矛盾

这是理解后面所有具体 bug 的关键背景：**这个系统在过去半年里经历了至少 4 次架构范式转变**（v3 整改 → V4 多 Agent → V5 决策核心 → TCP 提案-裁决-执行），每次转变都以"新增开关 + 保留旧路径做兼容"的方式落地，而不是"替换旧路径"。结果是：

- 同一个语义（比如"三周期时间框架划分"）在 `trend_classifier.py`、`strategy_coordinator.py`、`multi_timeframe_orchestrator.py`、`signal_pre_screener.py` 四个文件里有**四套不同定义**（见 3.7）；
- 同一个安全机制（日交易上限）在 `unified_gate.py`、`risk_control_service.py`、`decision_feedback_service.py` 三处都有代码，但只要一个总开关关闭，全部失效，且没有人在最上层做"这个总开关是否符合当前风险偏好"的定期复核；
- 门禁数量做"减法"（GAP_CLOSURE 文档的"减门不加门"原则本身是对的），但减法过程中出现了本该保留的硬约束被顺手连带关闭的情况（3.4 节 H1-H5 频率约束死代码）。

---

## 2. 行业竞品与前沿论文对比

项目自己在 `docs/industry_quant_agent_comparison_report_2026-05-08.md` 已做过一轮对比，本次审查做了两件事：① 复核该文档结论是否仍成立；② 补充 2026 年最新的行业范例（尤其是"门禁/风控"设计），弥补该文档"风控对比"较薄弱的部分。

### 2.1 学术论文

| 论文 | 核心思想 | 与本项目的关系 |
|---|---|---|
| **TradingAgents**（Xiao et al., arXiv:2412.20138，Tauric Research）| 模拟真实交易公司分工：基本面/情绪/技术分析师 → 多空研究员辩论 → **专职风险管理团队**监督敞口 → 交易员综合决策。结构化输出 + 自然语言辩论并存 | 本项目 QAA/Master "六路分析师"理念相似，但**缺"看多/看空辩论"环节**——各分析师结论直接加权，没有互相质疑淘汰弱证据的机制；风险管理团队角色被拆散到 `unified_gate`+`regime_agent`+`position_sizing_agent` 三个模块，**没有统一的"风控代理人"对外发声** |
| **FinCon**（Yu et al., arXiv:2407.06567）| LLM 多 Agent + "概念化口头强化学习"：把交易结果的教训以自然语言形式沉淀，反哺决策 Agent 的信念 | 与本项目 `decision_feedback_service` + Hermes 学习闭环思路一致，但 FinCon 的反馈是**直接改写 Agent 的"信念文本"**，本项目反馈闭环目前只调整**数值门槛**（`runtime_tuning.json`），Prompt 自进化通道 36/36 次失败后已停用——即"数值层学习"在跑，"认知层学习"事实上是空的 |
| **FinMem**（Yu et al., arXiv:2311.13743）| 分层长期记忆（浅/中/深三层，按时效性衰减），可调"认知跨度" | 本项目 Hermes 记忆/wisdom 机制不做分层衰减，新旧教训权重相同，容易被最近几笔的噪声"教训"污染门槛（对照本报告 4.7 节 `by_nature` 无夹紧问题）|
| **FinAgent**（Zhu et al., arXiv:2402.18485）| 多模态基础 Agent：双层反思模块（低层快速适应 + 高层长期学习）+ 专家知识注入 | 本项目复盘（`DecisionRetrospective`）近似"低层反思"，但没有对应的"高层反思"定期把多次复盘归纳成策略级结论后再校验——目前更像"单笔复盘"而非"周期性策略反思" |

### 2.2 工程/产品竞品（2026 年最新样本，补充 05-08 报告未覆盖的风控设计）

| 项目 | 风控/门禁设计要点 | 本项目差距 |
|---|---|---|
| **HyperGuard**（cwklurks，Hyperliquid/Binance 中间件）| **14 层显式命名门禁**（Kill Switch、白名单、单笔限额、日亏上限、回撤熔断、保证金占用率、频率限流、时段限制、冷却、波动率熔断、相关性敞口、维护窗口、单币日限额、自定义规则），每层职责单一、可独立开关、可独立统计"拦截挽回金额" | 本项目 `unified_gate` 把 7+ 种门禁合并进 1 个函数确实降低了"重复造轮子"的问题，但代价是**可观测性和职责边界模糊**——审查中很难一眼看出"这一单是被哪一层拦的"（虽然有 `rule=` 字段，但没有 HyperGuard 那种"拦截挽回金额"统计看板） |
| **AKIVA-AI/enterprise-crypto**（10 Agent 层级制）| **"Fail-closed"是硬性设计公理**：任何安全检查异常 = 停止交易，绝不 fail-open；熔断器是 **Postgres 触发器**（应用层 bug 无法绕过）；Strategy Agent 只能"提案"不能"下单" | 本项目多处 `except Exception: 跳过/放行`（详见 4.4、4.5、3.6），即"提案者可以在特定异常路径下绕过裁决者直接影响执行"，与该项目"数据库级熔断，代码 bug 绕不过去"的设计哲学正好相反 |
| **Quant-Nanggroe-AI**（LangGraph 状态图编排）| **9 检查点宪法风控**，12 个硬编码常量，任何一项失败 = 一票否决（VETO）；Agent 输出走 Pydantic Schema 强约束，不许自由文本 | 本项目 2026-07-05 才引入 `ConstitutionalProfile`（Live 宪法层），比该项目晚，且当前只覆盖"日亏熔断/单币限额/总仓位/保证金"4 类，未覆盖"相关性敞口"（多个 symbol 同向高相关时的总敞口控制，本项目目前没有这层）|
| **Seneca / "Risk Shield"**（tomserres.com 文章描述的 AI 原生交易系统）| 四层风控：① 动态分批建仓（不一把梭）② "Trim Lock" 防守态自动分批减仓 ③ 结构化止盈止损（挂在价格结构而非杠杆倍数上）④ 低流动性时段自动降杠杆 | 本项目"结构止损"（`structure_stop_calculator`）已有雏形，但**没有"低流动性时段自动降杠杆"**（周末/重大宏观事件前后），也没有 Trim Lock 式的"渐进式减仓"，止损目前更多是"一次性硬止损"|
| **Freqtrade / NautilusTrader**（05-08 报告已提及）| 回测/实盘同一套撮合引擎语义，Walk-forward 是标配 | 本项目 `ReplayHarness` 2026-07-05 才 MVP 落地，覆盖面窄（单 symbol + mid tier），ATAS 独立因子链路仍未完全并入统一 evaluate（GAP_CLOSURE 域 3 状态：Phase B 待完成）|

### 2.3 一句话结论

> 本项目在"三周期分层预算 + 独立调度"这一点上**已经领先大多数开源竞品**（多数竞品是单周期或周期耦合），这一点值得保留和继续投入；但在"门禁的 fail-closed 纪律性""风控角色的独立发声权""回测-实盘同管道"三个维度上，落后于 2026 年新一代生产级项目（AKIVA-AI、Quant-Nanggroe-AI、HyperGuard）。**这与项目自己 GAP_CLOSURE 报告"综合评分 6.1 → 目标 8.4"的判断方向一致，本次审查用具体代码证据为该判断做了背书。**

---

## 3. 三周期 Agent 错误清单与修正建议

> 范围：`tier_tick_scheduler.py`、`multi_timeframe_orchestrator.py`、`strategy_coordinator.py`、`trend_classifier.py`、`multi_freq_alignment.py`、`sub_position_manager.py`、`signal_pre_screener.py`、`full_auto_trading_service.py` 中三周期相关代码段。按严重度分 P0（逻辑错误/竞态，可直接导致资金损失或信号失真）/ P1（性能瓶颈）/ P2（协作冲突、标准不一致、门禁绕过）。

### P0：逻辑错误 / 竞态条件

| # | 问题 | 位置 | 根因 | 实际影响 | 修正建议 |
|---|---|---|---|---|---|
| 1 | **独立 mid/long 循环绕过 tick 调度器空转** | `full_auto_trading_service.py` ~L12746 | `due = get_due_ai_tiers(session_id) or ["mid", "long"]` —— 调度器返回空列表（未到调度时间）时，Python 把 `[]` 当假值，被 `or` 强制替换成 `["mid","long"]` | 独立循环**每个 tick 都跑**，完全无视 `tier_tick_scheduler` 的 45s/90s 节流；与主循环叠加后 LLM 调用量翻倍、同 symbol 重复决策、数据库/交易所 API 压力暴增 | 改为 `if not due: return`，不要用 `or` 兜底替换空列表 |
| 2 | **`mark_tier_run` 误标记未执行的 tier** | `full_auto_trading_service.py` ~L12775-12806；`tier_tick_scheduler.py` L64-69 | 独立循环单次 tick 只跑 mid 或只跑 long（奇偶轮转），但收尾时把 `due` 里**全部** tier（包括没跑的那个）都标记为"已执行" | 长线 Agent 可能连续错过整轮调度（最长可漏 90-240s 一次），且与主循环共享的 `_last_ai_run` 互相"抢跑/漏跑" | 只标记实际执行的 tier：`mark_tier_run(session_id, ["mid"] if _run_mid else ["long"])` |
| 3 | **主循环与独立循环共享无锁状态，产生竞态** | `full_auto_trading_service.py` ~L12301-12333、L13698-13709、L4884-5037、L7074 | `MIDLONG_MASTER_DELEGATE=true` 只拦截了"新开"分支，主循环仍完整跑 `_run_trading_cycle`（含编排器/Master LLM）；独立线程同时读写 `_mlto_handled_keys` 等全局字典，无锁 | 同一 symbol 短时间内两次 LLM 决策；`_mlto_handled_keys` 被主线程清空后独立循环可能重复开单；子仓位 nature 重复性检查被绕过 | 独立调度开启时主循环 due_tiers 只做轻量维护，不再调用 Agent LLM；或对共享字典加 per-session 锁 |
| 4 | **多频率硬约束（H1-H5）已实现但从未被调用** | `multi_timeframe_orchestrator.py` L1940-2203（定义）/ `evaluate()` L201-273（未调用） | `_apply_frequency_constraints` 是死代码，文档/注释宣称"有约束"，运行时完全不生效 | 4h 与 15m 方向相反时仍可能激活 short+long 同向槽位，产生逆势短线开仓 | 在 `_finalize` 之后、`_recommend_slots` 之前接入调用；若确认不需要则删除以免误导审查者 |
| 5 | **即便接入，H1 约束的周期语义本身写错** | `multi_timeframe_orchestrator.py` L1973-1978 | 注释写"4h vs 15m"，但实现比较的是 `long_view`（1d/1w）与 `short_view`（5m/15m），根本没有比较 4h | 约束对错了周期组合，该拦的信号放行，该放的信号被拦 | 改为 `mid_view`（4h）与 `short_view` 比较，从 snapshot 取真实 4h/15m 方向 |
| 6 | **协调器多频率约束违反后返回值被忽略** | `strategy_coordinator.py` L643-646、L2060-2188 | `_apply_multi_freq_constraints` 违反时函数返回 `False`，但调用方 `analyze_market_environment` 只把它记到 `env.constraint_violated` 字段，不阻断分析、不向上抛出；下游门控默认软模式，读不到这个标志 | 1h 与 4h 明显冲突时**仍可正常开仓**，硬约束名存实亡 | `unified_gate`/`evaluate_entry` 显式读取 `constraint_violated` 并硬拦；或 coordinator 层直接返回"不可交易"标志给调用方 |
| 7 | **"三周期共振"在四个模块里有四套不同定义** | `trend_classifier.py` L262-357（1d/4h/1h）、`strategy_coordinator.py` L1150-1191（15m/1h/4h）、`multi_timeframe_orchestrator.py`（long=1d/1w, mid=4h/1h, short=5m/15m）、`signal_pre_screener.py` L117-126（short=5m+15m+1m, mid=15m+1h+5m, long=1h+4h+1d） | 历史上分批开发，没有建立单一的 tier→timeframe 映射表 | Swing/Trend 门控、MLTO bias、预筛选三方对同一个"周期共振"结论互相矛盾；AI prompt 依据的数据和执行层依据的数据可能不是同一组周期 | 建立单点配置的 tier→timeframe 映射表，所有模块从同一处读取；`alignment_score` 字段同名不同义（coordinator 是 0-1 浮点，QuantBrief 是 0-15 整数）需改名或加命名空间 |
| 8 | **多周期冲突判定逻辑以第一个非零方向为锚，遗漏真实冲突** | `strategy_coordinator.py` L1164-1170 | `elif any(d * dirs[0] < 0 for d in dirs[1:])`——若 15m=0、1h=+1、4h=-1，因为锚点是 dirs[0]=15m=0，不会判定为冲突，只判定为"分歧" | 真正的 1h/4h 对立方向被低估，仓位缩放比实际应有的更乐观 | 在非零方向的子集里两两比较，或直接复用 `multi_freq_alignment.validate_alignment` 的实现 |
| 9 | **用简单移动平均冒充指数移动平均** | `strategy_coordinator.py` L2091-2092、L2114-2115、L2143-2144 | `ema20_4h = np.mean(c4h[-20:])` 实际是 SMA，与 `_analyze_per_timeframe` 里真正的 EMA 实现不一致 | `freq_*_direction` 与 `m*_trend_dir` 可能给出相反结论，AI 拿到的上下文自相矛盾 | 统一复用 `_calc_ema` 或 `trend_classifier` 里的真实 EMA 实现 |
| 10 | **三周期"翻转审核"机制定义了但从未接入执行链** | `sub_position_manager.py` L474-508 | `review_flip`（`reduce_swing_intraday`/`full_flip`）全库搜索只有定义、没有调用点 | 短线方向反转时，中线/长线子仓不会按设计联动减仓，可能在同一标的上累积多空互斥的仓位 | 在开反向仓/翻转前调用 `review_flip`，并传入 MLTO 三层 bias 作为决策依据 |

### P1：性能瓶颈

| # | 问题 | 位置 | 影响 | 修正建议 |
|---|---|---|---|---|
| 11 | `tier_tick_scheduler.get_intervals()` 的强制下限（mid≥60s, long≥90s）覆盖了用户配置的 45s/90s，且与验收脚本期望（≤60/≤120）矛盾 | `tier_tick_scheduler.py` L42-49；`paper_fast_trial_controller.py` L67-84 | "快速试单"预设实际不生效，用户以为 45s 实际是 60s+ | 下限改为从 settings 读取 `TIER_*_MIN_SEC`，与 `PARAM_DEFS.min` 对齐 |
| 12 | `analyze_market_environment` 每次调用重复拉取 4 个周期 K 线 + 因子融合，`_factor_cache` 无 symbol 级锁 | `strategy_coordinator.py` L268-271、L658-704、L479-480 | 多 symbol 并行分析时数据库/交易所 API 压力暴增，单次分析可达数百毫秒到数秒 | 与 `unified_data_pool` 的时点快照复用；扩大 `_api_kline_cache`；按 session 批量分析 |
| 13 | MLTO `_analyze_mid_term` 在线程池内同步拉 K 线（1h/4h 各一次） | `multi_timeframe_orchestrator.py` L749-765 | `evaluate_portfolio` 最多 6 worker 并发时放大 I/O 压力，编排器批量评估容易超过 90s 超时后 fallback 成空决策 | 强制要求调用方传入 snapshot.klines，regime 结果写入 snapshot 缓存复用 |
| 14 | `SignalPreScreener.screen_batch` 每 symbol 最多 3 次数据库查询，无跨 symbol 批量缓存 | `signal_pre_screener.py` L156-191 | 20 个币种 × 3 周期 = 每 tick 最多 60 次数据库查询 | 批量预加载，与统一快照合并 |
| 15 | 编排器单例的可变状态（`_last_decisions`/`_freeze_until`/`_last_side`/`_pending_flip`/`_intel_cache`）在多线程 `evaluate_portfolio` 中无锁读写 | `multi_timeframe_orchestrator.py` L147-157、L263、L275-312 | 冻结状态错乱、flip 确认计数串台，60s 全局情报缓存跨 symbol 互相覆盖 | 改为 per-symbol 锁，或让 evaluate 无状态化，状态外置到 session 级存储 |
| 16 | `_get_recent_trades_count` 每次调用对 90 天 `StrategyTrade` 表做全表 count，无缓存 | `strategy_coordinator.py` L995-1007 | 高频 tick 下数据库压力持续累积 | 增加 Redis/内存 TTL 缓存 |

### P2：协作冲突 / 标准不一致 / 门禁绕过

| # | 问题 | 位置 | 影响 | 修正建议 |
|---|---|---|---|---|
| 17 | `ORCHESTRATOR_HARD_GATE=false` + `DIRECTION_COHERENCE_MODE=audit` 默认组合下，编排器与 DCP 冲突时只记日志不拦截 | `settings.py` L46-47；`test_orchestrator_soft_demotion.py` L105-107 | 三周期明显看空时，中线 AI 仍可能开多单——若这是有意为之的"探索期策略"，需要在 Live 部署文档里显著标注风险，而不是留在默认配置里 | Live 环境切换为 `enforce` + `ORCHESTRATOR_HARD_GATE=true`，或按 tier 差异化（long 硬拦、short 软拦）|
| 18 | 单一 mid 信号触发就自动创建 short+long 同向槽位 | `multi_timeframe_orchestrator.py` L1898-1909 | 长线 TrendAgent 在自己的 long bias 还未确认时就被"拉起"，与"长线独立高门槛"的设计初衷冲突 | 仅当 long_view/short_view 各自单独达到阈值时才创建对应槽位，mid 不应扩散激活其他槽位 |
| 19 | 弱方向信号（conf<0.18）被人为抬高到 ≥0.22 后参与多数投票 | `multi_timeframe_orchestrator.py` L1140-1143 | 本应被过滤的噪声信号混入共识计算，弱信号触发开仓 | 只对"继承场景"做抬升，或低于阈值时强制保持中性、不参与投票 |
| 20 | LLM Prompt 写"短线持仓<2小时"，代码实际配置 scalp 期望持仓 8 小时 | `qaa/prompt_utils.py` L93-97 vs `sub_position_manager.py` L85-101 | 模型按 2 小时的心智模型做决策，但被 8 小时+ 的复审/强平逻辑对待，决策行为被扭曲 | 同步 Prompt 与 `TIER_PROTECTION_PARAMS`/`NATURE_RULES` 的真实数值 |
| 21 | 预筛选模块 Prompt 文案主动鼓励"预筛选通过应更积极评估，否则应优先考虑开仓" | `signal_pre_screener.py` L594-597 | 与中线 QuantBrief 门控、Monte Carlo 缩仓的保守取向直接相反，人为抬高 LLM 开仓倾向 | 改为"优先考虑分析"而非"优先考虑开仓"，按 tier 差异化文案 |
| 22 | K 线新鲜度巡检默认周期列表缺少 4h（中线主周期） | `kline_freshness_inspector.py` L26、L62-64 | 4h 数据停滞时不会告警，中线/MLTO 的 mid_view 可能静默使用过期指标 | 默认周期列表加入 `4h`（以及 MLTO 使用的 `1w`）|
| 23 | API 拉取失败时，"有比没有强"的降级策略允许用超过 2 小时的过期 K 线继续交易 | `strategy_coordinator.py` L699-704、L371-378 | 陈旧行情下计算出的趋势方向/ATR 止损距离都会失真 | 与 `STRICT_DATA_GATE` 联动：数据过期时 `market_cycle=unknown` 且禁止新开仓 |
| 24 | 因子引擎 import 路径可疑（`from services.factor_engine...` 而非 `backend.services...`），异常被 debug 级别吞掉 | `strategy_coordinator.py` L312-313 | 因子融合字段在某些运行环境下恒为 0，相当于少一路输入却无人察觉 | 统一为 `backend.services` 包路径；导入失败应触发告警级别日志/指标，而非静默 |
| 25 | 动态风险参数计算在缺少显式 tier 时，用主导周期（15m/1h/4h）反推 tier，可能推断错误 | `strategy_coordinator.py` L1394-1400 | Swing 仓可能被误判为 short tier（止损上限收窄到 4%），或反过来长线止损被放得过宽 | 必须从 `trade_nature`/`timeframe_tier` 显式传入，禁止用主导周期反推 tier |

### 测试覆盖缺口

| 测试文件 | 已覆盖 | 未覆盖的盲区 |
|---|---|---|
| `test_orchestrator.py` | 参数加载、nature 推断 | `_coordinate`/`_finalize`/`_recommend_slots`、并行场景、约束链 |
| `test_trend_agent.py` | TrendAgent 归一化、回退逻辑 | 与 Swing/MLTO 的协作、调度器交互 |
| `test_p2_risk_refactor.py` | D10-D15 风控表、long immune | 三周期调度、子仓位竞态 |
| `test_orchestrator_soft_demotion.py` | 验证"软放行"是预期行为 | **不测试**上表 #3 的双循环竞态 |
| `verify_three_cycle_strategy.py` | 静态配置项 + live 指标 | 测不到 #1（L12746 运行时 bug）；tick 下限矛盾（#11）可能被误判为 PASS |

> **说明**：`test_orchestrator_soft_demotion` 明确验证了默认 `ORCHESTRATOR_HARD_GATE=false`、`DIRECTION_COHERENCE_MODE=audit`——这是**已知的设计取舍**（AI 方向判断优先于编排器建议），不是 bug，但建议在 Live 部署 checklist 里显式提示这一取舍的风险敞口。

---

## 4. 门禁配置诊断与修正清单

### 4.1 完整门禁参数现状表

| 层级 | 参数 | 当前生效值 | 来源 | 说明 |
|---|---|---|---|---|
| 总开关 | `V5_DECISION_CORE_ENABLED` | `true` | `.env` | 为 `false` 时 `evaluate_entry` **直接放行**，回滚开关 |
| **日额度** | `V5_DAILY_TRADE_CAP_ENABLED` | **`false`** | `.env`/`settings.py` | ⚠️ **核心发现**：README 宣称的"日上限 10 笔"门禁**未生效** |
| 日额度 | `V5_MAX_DAILY_TRADES` | 50（`.env`）；`runtime_tuning.json` 内为 12 | 两处不同 | 因总开关关闭，两个值都不会被检查 |
| 日额度 | `V5_MAX_SYMBOL_TRADES_PER_DAY` | 20 | `.env` | 同上，不生效 |
| 盈亏比 | `V5_MIN_RISK_REWARD` | 1.8（Live）/ **1.3 封顶**（Paper） | `unified_gate.py:207,314-317` | Paper 模式硬编码把任何 runtime 调高的值（含反馈闭环写入的 2.0）压回 1.3 |
| 最小止盈 | `V5_MIN_TP_PCT` | 1.2%（Live）/ 0.8%（Paper） | `unified_gate.py:208,334` | Paper 更宽松 |
| 短线置信度 | `V5_SCALP_MIN_CONFIDENCE` | 70（Live 基准）；Paper **硬编码 65**；`runtime_tuning.json` 内 manual 覆盖为 75 | `unified_gate.py:205,257` | 三个数字并存，且与 ScalpRouter 自己的因子分制（25-45 分制）不是同一标尺 |
| 长线置信度 | `V5_TREND_FOLLOW_MIN_CONFIDENCE` | 50 | `unified_gate.py:206` | **本次审查新发现**：paper/live 三元表达式两个分支值完全相同，等于该参数在 paper 模式下没有被放宽（见 4.6） |
| 单笔风险硬顶 | `V5_MAX_TRADE_RISK_PCT` | 1.5% 权益 | `position_sizing_agent.py` | 仅覆盖走 `position_sizing_agent` 的主路径，**ScalpRouter 不经过此模块**（见 4.3）|
| 短线硬门 | `SHORT_TIER_CONFIDENCE_EXTRA` | 8（但 `PAPER_FAST_TRIAL` 模式下为 0） | `short_tier_entry_gate.py` | fast trial 模式下短线额外门槛被清零 |
| 短线冷却 | `SHORT_TIER_SAME_DIR_COOLDOWN_S` | settings 默认 14400s(4h)；`paper_fast_trial.json` 覆盖为 3600s(1h) | 两套配置并存 | |
| 币种熔断 | `CIRCUIT_BREAKER_CONSEC_LOSSES` / `CIRCUIT_BREAKER_COOLDOWN_S` | 4 笔 / 6 小时 | `short_tier_entry_gate.py` | **状态仅存于进程内存字典，重启/多进程即失效**（见 4.6）|
| 市场状态 | `regime_agent` 极端判定 | \|24h\|≥12% 或 \|1h\|≥5% 或 vol≥5% | `regime_agent.py` | 极端态 block 新开，震荡态缩仓 50%（不 block）|
| 反馈闭环边界 | `_runtime_overrides()` 夹紧范围 | `max_daily_trades∈[3,20]`、`scalp_min_confidence∈[60,90]`、`min_risk_reward∈[1.5,2.5]` | `unified_gate.py:44-56` | **但 `by_nature.*` 系列参数完全没有夹紧**，可被写入任意极端值 |
| Live 宪法 | `LIVE_CONSTITUTIONAL_RISK_ENABLED` | `true` | `.env` | 2026-07-05 新落地，覆盖日亏熔断/单币限额/总仓位/保证金 |

### 4.2 路径覆盖矩阵：谁真正经过了统一门禁？

| 下单路径 | 是否过 `unified_gate` | 是否过 `position_sizing_agent`（1.5%硬顶） | 异常时行为 |
|---|---|---|---|
| FullAuto 主循环 buy/sell/pyramid/dca | ✅ | ✅ | fail-closed |
| 编排器覆盖开仓 | ✅（`evaluate_entry`） | 部分 | ⚠️ **fail-open**（`full_auto_trading_service.py` ~L9677）|
| ScalpRouter 独立路径 | ✅（`evaluate_scalp_proposal`） | ❌ 用固定 `SCALP_SIZE_PCT=0.30` 直接算保证金 | short_tier 异常时 **skip**（~L13325） |
| Legacy 分析师回退路径 | 尝试调用 | 视路径而定 | ⚠️ **fail-open**（~L7134） |
| `POST /api/paper/order`（REST API 直接下单） | ❌ | ❌ | **完全裸下单**，无任何门禁 |
| rebate_arb 套利引擎 | ❌（自有 R1-R11 风控） | ❌ | 独立体系，R11 规则同步异常时 skip |
| Live 实盘交易客户端 | 走 `_execute_live_trade` → 宪法风控 | ✅ | — |

### 4.3 P0 级问题：可直接导致风险敞口失控

**问题 1｜日交易额度门禁总开关处于关闭状态**

```1553:1556:001Alpha/Hyper-Alpha-Arena/backend/config/settings.py
V5_DAILY_TRADE_CAP_ENABLED: bool = os.getenv(
    "V5_DAILY_TRADE_CAP_ENABLED", "false"
).strip().lower() in ("true", "1", "yes")
```

代码注释写着"false = 不限制次数，AI 也不会再因「额度耗尽」观望"——这是一个**有意识的产品决策**（怕 AI 因为额度用完而被迫空仓、错过机会），但后果是：README 中作为 V5 招牌成果宣传的"日上限 10 笔"从未真正在当前配置下生效过；`decision_feedback_service.py` 里"手续费占比过高 → 自动把 max_daily_trades 收紧到 7"这条反馈规则因为总开关关闭而变成死代码；系统实质上退回到了 V5 上线前"日交易最多 19 笔"那个被 README 自己列为 FAIL 项的状态（甚至更宽松，因为现在连 19 笔的软上限都没有）。

**修正建议**：Live 环境强制 `V5_DAILY_TRADE_CAP_ENABLED=true`，`V5_MAX_DAILY_TRADES` 设为 10-15（对齐 README 原始目标），`V5_MAX_SYMBOL_TRADES_PER_DAY` 设为 3-5；Paper 环境可以保留更宽松的值用于样本积累，但**不应该是"完全不设上限"**，建议至少设一个远高于预期但非无限的安全网（如 30/日）防止死循环式重复下单。

**问题 2｜ScalpRouter 完全绕过单笔风险硬顶**

```13333:13434:001Alpha/Hyper-Alpha-Arena/backend/services/full_auto_trading_service.py
_scalp_size_pct = float(os.getenv("SCALP_SIZE_PCT", "0.30"))
_margin_est = equity * _scalp_size_pct * _size_mult
...
paper_engine.place_order(...)
```

`position_sizing_agent.py` 里"单笔最大亏损 ≤ 权益 1.5%"这道被 README 称为"最后一道闸"的硬约束，**ScalpRouter 完全不经过它**，而是直接用 30% 权益 × 杠杆（可达 10-20x）计算保证金。这意味着短线仓位一旦触发止损，实际亏损占权益比例可能远超 1.5%，与 README 宣传的"单笔风险硬顶"名不副实。

**修正建议**：Scalp 路径开仓前也调用 `position_sizing_agent.build_plan` 做二次校验，或至少加一行硬约束：`notional_value × sl_pct ≤ equity × V5_MAX_TRADE_RISK_PCT`，超出则强制缩小仓位而非直接下单。

**问题 3｜盈亏比与最小止盈检查在 TP/SL 缺失时被整体跳过**

```327:337:001Alpha/Hyper-Alpha-Arena/backend/services/decision_core/unified_gate.py
if tp > 0 and sl > 0:
    rr = tp / sl
    if rr < min_rr:
        return _block(...)
if tp > 0 and tp < _paper_min_tp:
    return _block(...)
```

如果调用方传入 `tp_pct=0` 或 `sl_pct=0`（例如上游某条路径没有正确计算止盈止损），这套检查会被**整体跳过**而不是拒绝——这恰恰是 V5 设计初衷"消灭负期望交易"要防的情况。

**修正建议**：`tp`/`sl` 任一缺失时，强制用 `pipeline._tier_tp_sl_defaults` 兜底重新计算后再检查；若兜底后仍缺失，直接 block 而不是放行。

### 4.4 P1 级问题：逻辑冲突 / 参数不合理

**问题 4｜Paper 模式的硬编码下限系统性抵消反馈闭环的收紧动作**

```314:317:001Alpha/Hyper-Alpha-Arena/backend/services/decision_core/unified_gate.py
if _is_paper:
    min_rr = min(min_rr, 1.3)
```

`decision_feedback_service` 观察到"均亏 > 均盈"后会把 `min_risk_reward` 收紧到 2.0，`by_nature.trend_follow.min_risk_reward` 也可能被设到 1.8——但只要是 Paper 模式，这两个数字统统被 `min(..., 1.3)` 压制。**这意味着反馈闭环在 Paper 环境里永远无法被验证是否真的改善了盈亏比**，因为门槛从未真正提高过。这与 CHANGELOG 里"盈亏比倒挂"是根因问题的定性直接矛盾。

**修正建议**：Paper 模式应该有自己独立的、可被反馈闭环调整的基线（比如初始 1.5），而不是用 `min()` 强行压过 runtime 调整后的值；否则 Paper 环境的所有验收数据都会系统性偏乐观，掩盖真实问题。

**问题 5｜`by_nature` 系列运行时参数没有夹紧边界**

```43:56:001Alpha/Hyper-Alpha-Arena/backend/services/decision_core/unified_gate.py
if "max_daily_trades" in raw:
    data["max_daily_trades"] = max(3, min(20, int(raw["max_daily_trades"])))
...
```

顶层 `max_daily_trades`/`scalp_min_confidence`/`min_risk_reward` 都有 `[min, max]` 夹紧保护，但 `by_nature.swing.*`、`by_nature.trend_follow.*` 这些更细粒度的参数**完全没有**，可以被离线进化（NSGA-II）或人工（OpenCode）写入任意极端值（比如 `min_score=95` 导致该 nature 事实上永久停摆，或 `min_risk_reward=0.1` 导致门禁形同虚设）。

**修正建议**：为 `by_nature.*` 增加与顶层 schema 一致的 clamp 逻辑，在 `runtime_tuning_store.py` 写入时统一校验。

**问题 6｜震荡市缩仓系数未传导到 Scalp 路径**

`regime_agent.classify_regime` 在震荡市返回 `size_multiplier=0.5`，这个折扣只在走 `unified_gate` 常规路径的仓位计算里生效，`scalp_execution_gate.py` 只处理 `allow_open=False`（极端态禁止），**不读取 `size_multiplier`**。震荡市本该是短线噪声最多、最该缩仓的场景，但 Scalp 路径仍按 `SCALP_SIZE_PCT=0.30` 满仓开。

**修正建议**：Scalp 成交前统一乘以 `regime.size_multiplier`。

**问题 7｜编排器覆盖路径与 short_tier 检查均为 fail-open**

```307:308:001Alpha/Hyper-Alpha-Arena/backend/services/decision_core/unified_gate.py
except Exception as err:
    logger.debug("[V5Gate] short_tier 检查跳过: %s", err)
```

`short_tier_entry_gate` 本身的存在理由就是"DB 证实短线近 20 笔赢 6 输 14"（见文件头注释），是专门为了堵住短线裸奔的漏洞而建的硬门，但它自己被包在一个"任何异常都跳过（仅 debug 日志）"的 try/except 里。编排器覆盖路径（`full_auto_trading_service.py` ~L9677）异常时也是同样模式：记 warning 但不设置 `_gate_blocked=True`。这两处都与主路径"V5 门控异常 fail-closed"的原则不一致，形成了两条可被异常"意外放行"的旁路。

**修正建议**：两处均改为异常时返回 block（`_block(..., "short_tier_error", ...)` / 设置 `_gate_blocked=True`），与主路径的 fail-closed 纪律保持一致。

### 4.5 P2 级问题：绕过 / fail-open / 配置遗漏

| # | 问题 | 位置 | 建议 |
|---|---|---|---|
| 8 | REST API `/api/paper/order` 完全裸下单，不经过任何门禁 | `paper_trading_routes.py:167-185` | 接入 `evaluate_open_decision`，或限制为仅管理员/测试用途并加鉴权 |
| 9 | Legacy 分析师回退路径异常时 fail-open | `full_auto_trading_service.py:7134-7135` | 改为异常时设置 `_legacy_skip=True` |
| 10 | Scalp Flash Veto 默认 `SCALP_VETO_FAIL_OPEN=true` | `settings.py:459` | Live 环境改为 `false`，LLM 超时/异常时不放行 35-44 分的边缘信号 |
| 11 | `fee_context` 费用统计失败时按 0 计算（`opens_today=0`） | `fee_context.py:98-99` | 数据库异常时应保守计为已达上限而非视作"今日未开仓" |
| 12 | `V5_DECISION_CORE_ENABLED=false` 时一键全放行，没有启动期断言 | `unified_gate.py:179-180` | Live 模式启动时加 assert，禁止以此状态启动生产交易 |
| 13 | 因子否决层异常时不否决（fail-open） | `full_auto_trading_service.py` ~L7943 | Live 环境改为 fail-closed 或降级为强制缩仓 |

### 4.6 本次审查独立发现（未见于既往文档）

**发现 A｜`.env` 存在 3 个重复定义的配置键**

```
LAYER_BUDGET_SCALP                  出现 2 次（0.60 与 0.40）→ dotenv 取最后一次 = 0.40
SCALP_FACTOR_SCAN_INTERVAL_SEC      出现 2 次（45 与 60）    → 取最后一次 = 60
QAA_ANALYST_STREAM_SAFETY_CAP_S     出现 2 次
```

这类问题的危险之处不在于当前生效值是否合理（本例中恰好是合理值生效），而在于**配置文件的"最后写入者获胜"是一种脆弱的隐式规则**：未来任何人在 `.env` 顶部再加一行同名配置用于"临时测试"，都可能因为忘记删除或顺序调整而意外覆盖生产值，且没有任何工具或校验会提醒。建议在部署/CI 流程里加一个简单的 `.env` 去重校验脚本。

**发现 B｜`V5_TREND_FOLLOW_MIN_CONFIDENCE` 的 paper/live 分支实际上是死代码**

```206:001Alpha/Hyper-Alpha-Arena/backend/services/decision_core/unified_gate.py
_paper_trend_gate = int(V5_TREND_FOLLOW_MIN_CONFIDENCE) if _is_paper else int(V5_TREND_FOLLOW_MIN_CONFIDENCE)
```

对照同一段代码里 scalp 门槛（204/205 行）、盈亏比（207 行）、最小止盈（208 行）都明确对 paper 做了放宽，唯独长线置信度门槛这一行的三元表达式两个分支**完全相同**，等价于 `_paper_trend_gate = V5_TREND_FOLLOW_MIN_CONFIDENCE`，paper 模式下没有得到任何放宽。这很可能是过去某次重构的遗留（原本可能想给 paper 一个更低的值，后来改动时两个分支被填成了一样）。结合 `MID_LONG_STRATEGY_DESIGN_AND_FEASIBILITY_2026-07-04.md` 文档记载的"近 3 天 short 204 笔、mid 1 笔、long 0 笔"这一长线零成交现象，这行代码是**一个此前未被记录、值得纳入根因排查范围的候选因素**（不能确定是唯一原因，但方向上与"long tier 在 paper 环境下门槛意外偏高"完全吻合，建议下一轮修复时一并复核）。

**修正建议**：明确 paper 模式下长线置信度门槛的目标值（例如比照 scalp 的处理方式设为 live 值 -10~15），修正三元表达式。

**发现 C｜短线熔断/冷却状态是进程内存变量，重启或多进程部署下会失效或不一致**

```19:24:001Alpha/Hyper-Alpha-Arena/backend/services/short_tier_entry_gate.py
_same_dir_short_opens: Dict[str, List[float]] = {}
_symbol_loss_tracker: Dict[str, dict] = {}
```

这两个字典是 Python 进程内存里的普通字典，**没有写入数据库或 Redis**。README 自己在"AI 策略卡死排查"一节里提到过"是否有多个后端启动方式同时运行"是常见故障场景，也提到过要给调度器加跨进程文件锁——但这两个熔断/冷却状态完全没有对应的持久化或跨进程同步机制。后果：① 后端因代码热更新/崩溃重启后，所有连续亏损计数和冷却窗口清零，被熔断的币种立即"解禁"；② 若部署时启用多个 worker 进程，每个进程各自维护一份独立状态，同一个币种可能在一个 worker 眼里"已熔断"、在另一个 worker 眼里"从未开过仓"，导致熔断规则形同虚设。这与本报告第 5 章"2026-04-25 熔断状态仅内存、重启丢失"那次已修复的历史 bug是**同一类问题的不同实例**，只是这次发生在短线熔断而非日亏熔断上，说明"状态持久化"没有被当作一条团队级的编码规范去检查所有新增的熔断/冷却逻辑。

**修正建议**：至少写入 SQLite/Postgres 的一张轻量表（`short_tier_circuit_state`），启动时加载；如果短期内不想做持久化，最低限度也应该在文档里显式警示"该保护仅在单进程不重启期间有效"，避免运维人员误以为这是可靠的安全网。

### 4.7 参数合理性综合判断表

| 用户关心的参数 | 期望/宣传值 | 代码实际现状 | 判断 |
|---|---|---|---|
| 止盈止损比例 | TP:SL ≥ 1.8:1 | Live 合理落地；**Paper 被压到 1.3:1** | ⚠️ 部分合理 |
| 单笔风险硬顶 1.5% | 全路径生效 | 主 AI 路径合理；**ScalpRouter 完全绕过** | ❌ 不合理 |
| 日交易上限 10 笔 | 硬性生效 | **总开关关闭，默认 50 笔无限制** | ❌ 未生效 |
| 同 symbol 日内 3 笔 | 硬性生效 | **同上，未生效（默认 20）** | ❌ 未生效 |
| Scalp 置信度 70% | 统一标尺 | Live 70 + runtime 覆盖 75；**Paper 硬编码 65**；与因子分制（25-45 分）**不是同一标尺**，两套并存 | ❌ 标尺冲突 |
| 极端行情禁止开仓 | 生效 | ✅ 生效（`regime_agent`） | ✅ 合理 |
| Live 宪法风控（日亏熔断等）| 不可覆盖 | ✅ 2026-07-05 已落地 | ✅ 合理 |

---

## 5. 历史重大问题时间线（证据链）

> 以下时间线整合了 `honest_project_diagnosis_2026-05-08.md`、`repair-2026-04-26.md`、`deep_dive_round2/3_2026-05-08.md`、`full_auto_monitoring_round4_2026-05-08.md`、`TRADING_SYSTEM_OPTIMIZATION_DESIGN.md`、README、以及 `logs/backend.error.log`、`data/exit_audit_report.json`、`data/ai_feedback/trade_perf_20260706.json` 等一手数据。**标注"⚠️ 复发"的条目需要重点关注**——它们是"同一类问题在工程修复后以新形式再次出现"的直接证据，说明问题的根因可能在于工程流程/编码规范层面，而不只是某一次的代码 bug。

| 日期 | 事件 | 量化影响 | 状态 |
|---|---|---|---|
| 2026-01-16~17 | DB 迁移期间后端错误率极高 | 3.5万行日志中 ERROR 占 17.1%（6085条）| ✅ 已处理 |
| 2026-04-13 | 策略表现差 + 前端 7/9 页面路由失效 | 总盈亏 -$4,820.87 / 278 笔 | ✅ 已修 |
| 2026-04-13 | 近 2 天集中亏损 | 22 笔，净亏 -19.66 USDT，胜率 13.6%，PF 0.12 | 策略问题 |
| **2026-04-25~26** | **日亏损熔断永久失效**：`realized_pnl_today` 硬编码为 0 | 日亏超 5% 熔断从不触发 | ✅ 已修 |
| 2026-04-25~26 | 熔断状态仅存内存，重启丢失 | 重启后风控保护消失 | ✅ 已修（DB 持久化）⚠️ **但同类问题在短线熔断上复发，见 4.6 发现C** |
| 2026-04-25~26 | 震荡市继续交易，胜率仅 6.6% | 多 Session PnL：-1581、-1168、-353、-1070 | ✅ 加暂停规则，⚠️ **后续"死锁救援"逻辑使暂停形同虚设** |
| 2026-04-26 | Master Close Guard 默认关闭，平仓路径胜率 0-5% | 累计亏 ~$33 | ⚠️ flag 长期默认关闭 |
| 2026-04-26 | `max_symbol_entries_per_day=3` 从未被执行 | 单币种可无限开仓 | ⚠️ **与本报告 4.3 问题1 是同一类问题的历史先例** |
| 2026-04-28 | 决策链路"假死"：`decision_snapshots` 停止更新 | `full_auto_sessions=0` | 2026-05-08 诊断修复 |
| 2026-04~05 | `strategy_trades` 数据污染（168 笔假 PnL） | `position_size==exit_price` 168/168，`opened_at==closed_at` 168/168 | ✅ 已修，158 条标记 legacy_dirty |
| 2026-05-08 | 剔除污染数据后的真实亏损 | 158 笔，累计 -1,318.97 USD，胜率 34.2% | 数据修复后更可信 |
| 2026-05-08 | 编排器强行覆盖 LLM 的 hold 决策 | 3011 条决策中 426 条(14.1%)被覆盖，总矛盾 1520 条(2.9%) | ✅ 默认关闭覆盖 |
| 2026-05-08 | 50% 下单路径完全无风控 | paper/live 裸 `place_order` | ✅ 已接入统一检查 ⚠️ **本报告 4.2 发现 REST API 下单路径仍是裸下单，同类问题未完全根治** |
| 2026-05-08 | Prompt 自进化 36/36 全部失败 | 0 个新模板生效 | ✅ 2026-06-11 起默认禁用 |
| 2026-05-08 | `datetime.now()` 系统性时区 typo | 35 个文件 95 处 | ✅ 批量修复，⚠️ **Round4 又发现 5 个文件遗漏 import** |
| 2026-05-08 Round4 | Paper 全自动上线后 100% 被风控拦截，从未成交 | 误把账户权益当作 $100（实际 $10,000） | ✅ 修复后首笔成交 |
| 2026-05-08 Round4 | 风控用名义价值而非保证金判断敞口，杠杆账户 100% 被拦 | 例：名义 $16,000 > 25%×$10,000 | ✅ 已修（改用保证金基准）|
| 2026-05-08 Round4 | 震荡市暂停规则被"死锁救援"逻辑强制解除 | 6/7 币种处于震荡态 | ⚠️ **文档明确写"暂停=没暂停"，workaround 而非根治** |
| 2026-06-10 | 高胜率但因持仓超时大亏 | 17 笔，总亏 -4,439.79；`max_hold_timeout` 6 笔 -3,906 | 出场规则设计缺陷 |
| **2026-06-11（README）** | **V5 上线前 14 天基线三项 FAIL** | 日交易最多 19 笔；最大单笔亏损 5.86% 权益；均亏>均盈 | V5 上线目标治理这三项 |
| 2026-06-27 | 短线拖累整体，长线独立盈利 | intraday/short 全历史 -25,324；trend_follow +61,233 | 策略结构性问题 |
| 2026-07-01~04 | 三周期严重失衡 | 近 3 天：short 204 笔、mid 1 笔、long 0 笔 | ✅ 07-04 设计已处理根因，72h 验收待观测 |
| **2026-07-05~06（当前日志）** | **FullAuto 主循环反复超时挂死** | `backend.error.log` 中"循环超时(360s)"至少 42 次 | ⚠️ **与 2026-04-28"决策假死"是同类故障，跨 3 个月复发** |
| 2026-07-05~06 | LLM API 读超时 | 6 次 | 降级/重试机制待加强 |
| 2026-07-05~06 | PostgreSQL 死锁 | `midlong_independent_tick` 检测到 1 次 DeadlockDetected | 并发写入需要复核 |
| **2026-07-06（当前数据）** | **出场审计：持仓超时是最大亏损通道** | 近 30 天 `max_hold_timeout` 113 次，**-136,677.66 USDT**，胜率 34.5% | ⚠️ **比 06-10 的 -3,906 扩大了 35 倍，未根治，持续恶化** |
| 2026-07-06 | 止损通道大额亏损 | 近全量 642 笔平仓中，`sl` 止损通道 54 笔 -109,522.68（胜率 14.8%），全账户总盈亏仍为 +40,077.18 | 出场/品种结构问题，被其他盈利通道掩盖 |

### 5.1 需要重点关注的"复发型"问题（跨月复发，说明是工程流程问题而非单次 bug）

1. **持仓超时强制平仓的亏损规模持续扩大**：从 2026-06-10 的 6 笔 -3,906 USDT，扩大到 2026-07-06 的 113 笔 -136,677.66 USDT（30天）。这是当前系统**单一最大的亏损来源**，且规模在持续恶化而非收敛，应作为下一轮修复的第一优先级（详见 7.1）。
2. **FullAuto 主循环挂死/假死**：2026-04-28 决策快照停更 → 2026-05-08 会话数为 0 → 2026-05-31 会话在跑但零策略 → 2026-07-05/06 循环超时 42+ 次。跨越 3 个月反复出现，说明"长时间运行下的稳定性"这一类问题没有被纳入常规回归测试。
3. **熔断/暂停机制被"救援逻辑"架空**：2026-04-26 震荡市暂停规则 → 死锁救援强制恢复，文档自己承认"暂停=没暂停"；本次审查在完全独立的短线熔断代码里（4.6 发现 C）又发现了同一类"安全机制形同虚设"的问题。建议把"熔断/暂停类代码是否有能绕过它的兜底/救援逻辑"作为代码审查的固定检查项。
4. **静默吞异常导致风控"看起来正常"实际不工作**：`_get_balance_info` 缺失导致风控静默跳过（05-08）、`datetime` typo 被 try/except 吞掉（05-08，Round4 又复发 5 处）、本次审查发现 `verify_gap_closure.py` 验收脚本运行时自己就打印了一条被忽略的 `FeeContext` 异常。这是一个跨越 3 个月、跨越多个模块反复出现的**工程文化问题**，建议纳入 7.4 节的流程性改进。

---

## 6. 差距对比总表

| 维度 | 本项目现状评分（1-10）| 行业标杆做法 | 主要差距 | 参考对象 |
|---|---|---|---|---|
| 决策架构 | 6.0 | 单一编排 + 专职提案链，Proposer 只提案不下单 | 历史上"5 套并行入口"，2026-07-05 起才收敛为 TCP；仍有主循环与独立循环并行运行的过渡态残留（3.3 P0#3）| TradingAgents、AKIVA-AI |
| 风控/门禁 | 6.0（本报告下调，原 GAP 文档估 6.5）| 8-12 项硬门禁不可覆盖，Fail-closed 是公理，DB 级熔断 | 日交易上限总开关关闭；ScalpRouter 绕过单笔风险硬顶；至少 6 处 fail-open 旁路；短线熔断状态无持久化 | HyperGuard(14门)、AKIVA-AI、Quant-Nanggroe-AI(9检查点) |
| 回测/研究闭环 | 5.5 | 回测与实盘同一撮合/评估管道 | ReplayHarness 刚 MVP，ATAS 因子链路仍独立于统一 evaluate | NautilusTrader、Freqtrade |
| 审计/可观测性 | 6.0 | 结构化决策快照 + 可回放 + 摘要链 | DecisionSnapshot v2 刚落地字段扩展，event_log 与 analytics 库仍部分分裂 | 多数生产级系统标配 Postgres+审计链 |
| 数据契约 | 7.0 | 数据降级=禁止开仓，不用兜底数据硬撑 | STRICT_DATA_GATE 已有，但过期 K 线"有比没有强"的例外仍存在（3节 #23）| — |
| 学习闭环 | 6.0 | 注册表化的策略 A/B + 可回滚 | v5_gates 通道有效，但 Prompt 自进化 36/36 失败已停用，认知层学习基本空转 | FinCon(概念强化学习)、FinMem(分层记忆) |
| **三周期架构** | **7.5**（本报告较 GAP 文档 8.0 略保守，因发现多处新 P0）| 多数竞品是单周期或强耦合周期 | 已有分层预算与独立调度的理念，但四套不一致的周期定义、调度器竞态、H1-H5约束死代码拉低了实际执行质量 | 领先大多数开源竞品，但"设计领先、实现有硬伤" |
| 衍生品/市场结构信号 | 7.0 | OI/CVD/清算倾向标配 | 已有 funding/OI/多空比，缺 OI变化率、CVD背离、清算倾向的编排器注入（GAP文档域8，2026-08起排期）| — |

**综合评分**：本报告独立评估约为 **5.9-6.2 / 10**（与项目自评 6.1 基本一致，风控维度略下调因发现新的 fail-open 与绕过路径），距离 GAP_CLOSURE 文档设定的 12 个月目标 8.4 仍有明显差距，其中**门禁维度是本次审查发现问题最集中、最应优先补强的领域**。

---

## 7. 改进方案与分优先级实施步骤

### 7.1 P0（本周内，零架构改动，纯配置/小函数修复）

| 序号 | 行动 | 涉及文件 | 预计工作量 |
|---|---|---|---|
| 1 | Live 环境开启 `V5_DAILY_TRADE_CAP_ENABLED=true`，设 `V5_MAX_DAILY_TRADES=10~15`、`V5_MAX_SYMBOL_TRADES_PER_DAY=3~5` | `.env` | 5 分钟 |
| 2 | 修复 `full_auto_trading_service.py` L12746 的 `or ["mid","long"]` 空列表兜底 bug | 同上 | 30 分钟 |
| 3 | 修复 `mark_tier_run` 只标记实际执行的 tier | 同上 + `tier_tick_scheduler.py` | 1 小时 |
| 4 | ScalpRouter 开仓前增加 `notional×sl_pct ≤ equity×V5_MAX_TRADE_RISK_PCT` 硬检查 | `full_auto_trading_service.py` scalp 分支 | 2 小时 |
| 5 | `short_tier_entry_gate`/编排器覆盖路径的 fail-open 改为 fail-closed | `unified_gate.py:307-308`、`full_auto_trading_service.py:9677` | 1 小时 |
| 6 | 修正 `V5_TREND_FOLLOW_MIN_CONFIDENCE` 的死代码三元表达式，明确 paper 值 | `unified_gate.py:206` | 30 分钟 |
| 7 | 清理 `.env` 中 3 个重复键，加一个部署前的去重校验脚本 | `.env` + 新增 `scripts/check_env_duplicates.py` | 1 小时 |
| 8 | RR/最小止盈检查在 tp/sl 缺失时改为强制走 tier 默认值后再校验，而非跳过 | `unified_gate.py:327-337` | 1 小时 |
| 9 | 针对"持仓超时"这一当前最大亏损通道（近 30 天 -13.6 万 USDT）单独立项复盘：逐笔抽样 20-30 笔 `max_hold_timeout` 平仓，判断是止损距离设置问题还是超时阈值设置问题 | `position_hold_time.py`、`sub_position_manager.py` | 1-2 天 |

### 7.2 P1（2 周内）

| 序号 | 行动 |
|---|---|
| 10 | 接入或彻底删除 `_apply_frequency_constraints`（H1-H5 死代码），若接入需先修复 H1 的周期语义错误 |
| 11 | `strategy_coordinator._apply_multi_freq_constraints` 的 `constraint_violated` 标志接入 `unified_gate` 做实际拦截 |
| 12 | 建立单点的 tier→timeframe 映射配置，替换四个模块里各自的定义 |
| 13 | Scalp 路径接入 `regime.size_multiplier` 缩仓 |
| 14 | 为 `by_nature.*` 运行时参数增加夹紧边界 |
| 15 | `POST /api/paper/order` 接入统一门禁或加鉴权限制 |
| 16 | 短线熔断/冷却状态持久化到数据库（至少加一张轻量表）|
| 17 | 完成 README/GAP_CLOSURE 中承诺的 72 小时 Paper 验收（mid≥3 笔、long≥1 笔、双开重复=0），并把结果写入验收记录 |

### 7.3 P2（1-2 月，中期架构收敛，对齐 GAP_CLOSURE 路线图 Phase B）

| 序号 | 行动 |
|---|---|
| 18 | 完成 `ReplayHarness` 覆盖三个 tier，把 ATAS 因子链路正式接入统一 evaluate（GAP 文档域3方案A）|
| 19 | 把 Paper 模式的门禁基线（RR、TP、置信度）改为"独立可调、可被反馈闭环验证"，而不是硬编码 `min()` 压制 |
| 20 | `BudgetService` 统一 Layer 预算与 Tier 预算两套配置（GAP 文档域7）|
| 21 | 编排器状态无锁问题：per-symbol 锁或状态外置，解决 P1 #15 的多线程竞态 |
| 22 | 引入类似 HyperGuard 的"拦截挽回金额"统计看板，让每一层门禁的实际效果可量化、可复盘 |
| 23 | 参考 AKIVA-AI 的"熔断器用数据库触发器实现"思路，把 Live 宪法层的日亏熔断改为数据库层强制，不依赖应用层代码正确性 |

### 7.4 P3（3-12 月，长期能力建设）

| 序号 | 行动 |
|---|---|
| 24 | 引入类似 TradingAgents 的"看多/看空辩论"环节，替代当前"六路分析师加权求和"的简单聚合方式 |
| 25 | 参考 FinCon 把反馈闭环从"只调数值"升级为"调整 Agent 信念文本"（认知层学习），重启 Prompt 自进化但改为更小步长、可回滚的方式，避免重蹈 36/36 全部失败的覆辙 |
| 26 | 增加相关性敞口控制（多个 symbol 同向高相关时的总敞口限制），补齐 Live 宪法层缺失的一类风控 |
| 27 | 补充"低流动性时段自动降杠杆"机制（参考 Seneca Risk Shield 设计）|
| 28 | `full_auto_trading_service.py` 的物理拆分（该项从 2026-04 的 `ARCHITECTURE.md` 就已列为 P3，至今未做，17497 行的单文件已经是审查和维护的实质性障碍）|
| 29 | 建立"熔断/暂停类代码是否存在绕过路径"作为代码审查的固定检查清单项，从流程上防止 5.1 节列出的复发型问题再次出现 |

---

## 8. 验证方法

### 8.1 门禁修正验证

```bash
# 1. 确认日额度门禁生效
grep "V5_DAILY_TRADE_CAP_ENABLED" backend/.env
# 期望：true

# 2. 三周期自检（应保持全绿，且新增检查项）
cd 001Alpha/Hyper-Alpha-Arena
backend\.venv\Scripts\python.exe scripts\verify_three_cycle_strategy.py --no-live

# 3. 差距缩短自检
backend\.venv\Scripts\python.exe scripts\verify_gap_closure.py
# 期望：stderr 不再出现 [FeeContext] 费用统计失败

# 4. .env 去重校验（需新增）
backend\.venv\Scripts\python.exe scripts\check_env_duplicates.py

# 5. 实时验证日额度生效（跑一段时间后）
grep "daily_cap" logs/backend.log | tail -20

# 6. 验证 ScalpRouter 风险硬顶接入
grep "\[ScalpSizing\]\|scalp.*V5_MAX_TRADE_RISK" logs/backend.log | tail -20

# 7. 验证 fail-closed 改造
grep "short_tier_error\|_gate_blocked=True" logs/backend.log | tail -20
```

### 8.2 三周期修正验证

```bash
# mid/long tier 标记与实际执行一致性核对
grep "MidLongAgent独立" logs/backend.log | tail -30

# 确认同一 tick 同一 symbol+tier 不再出现双开
grep -E "SwingAgent独立|TrendAgent独立" logs/backend.log | tail -50

# 72h 验收标准（对齐 DECISION_PATH_CONVERGENCE 文档）：
#   mid 成交笔数 >= 3
#   long 成交笔数 >= 1
#   同 symbol+tier 同分钟内独立循环+Master 双开次数 = 0
```

### 8.3 持仓超时问题的专项验证

```bash
# 建议先跑一次专项归因脚本（若无现成脚本，可临时用 SQL/pandas 对
# data/exit_audit_report.json 按 close_reason=max_hold_timeout 分组，
# 统计 symbol/tier/nature/持仓时长/止损距离 的分布，定位是"超时阈值过长"
# 还是"止损距离过宽导致扛单到超时才被强平"）
cd backend && PYTHONPATH=.. python3 -m services.trade_performance_analyzer ../data/alpha_arena.db
```

### 8.4 验收基线（建议采纳 README 已有目标，加严门禁后应重新走 2 周固定配置验收）

| 指标 | 目标 |
|---|---|
| 手续费/毛利 | ≤ 10% |
| 均亏 vs 均盈 | 均亏 ≤ 均盈 |
| 日交易笔数 | ≤ 10-15 笔/日（Live）|
| 最大单笔亏损 | ≤ 1.5% 权益（含 Scalp 路径，此前未覆盖）|
| 持仓超时亏损占比 | 30 天滚动 ≤ 历史基线（-136,677.66 USDT）的 30%，作为本轮修复的核心 KPI |

---

## 9. 参考文献与竞品

**学术论文**

- Xiao, T. et al. *TradingAgents: Multi-Agents LLM Financial Trading Framework*. arXiv:2412.20138, 2024.
- Yu, Y. et al. *FinCon: A Synthesized LLM Multi-Agent System with Conceptual Verbal Reinforcement for Enhanced Financial Decision Making*. arXiv:2407.06567, 2024.
- Yu, Y. et al. *FinMem: A Performance-Enhanced LLM Trading Agent with Layered Memory and Character Design*. arXiv:2311.13743, 2023.
- Zhu, W. et al. *A Multimodal Foundation Agent for Financial Trading (FinAgent)*. arXiv:2402.18485, 2024.

**工程/产品参考**

- TauricResearch/TradingAgents（GitHub）
- virattt/ai-hedge-fund（GitHub）
- Freqtrade、Microsoft Qlib、NautilusTrader、QuantConnect Lean、FinRL/FinRL-X、FinRobot/FinGPT
- cwklurks/hyperguard — 14 层风控中间件
- AKIVA-AI/enterprise-crypto — 10 Agent 层级化风控，fail-closed 公理
- dhaher-labs/Quant-Nanggroe-AI — LangGraph 状态图 + 9 检查点宪法风控
- Seneca（tomserres.com 案例分析）— 动态建仓/Trim Lock/结构化止损四层风控

**项目内部既有文档（本报告站在其肩膀上）**

- `docs/GAP_CLOSURE_AND_SURPASS_DESIGN_2026-07-05.md`（现有最权威的目标态设计）
- `docs/DECISION_PATH_CONVERGENCE_2026-07-05.md`
- `docs/MID_LONG_STRATEGY_DESIGN_AND_FEASIBILITY_2026-07-04.md`
- `docs/industry_quant_agent_comparison_report_2026-05-08.md`
- `docs/honest_project_diagnosis_2026-05-08.md`
- `README.md` V5 决策核心章节

---

## 附：本报告与既有文档的关系说明

本报告不是要推翻过去半年的诊断与修复工作——事实上，本报告引用的绝大多数背景数据都来自项目自己的诚实诊断文档，这本身说明团队的自我审查文化是健康的。本报告的增量价值在于：

1. **用运行自检脚本 + 实际配置文件核验，把"文档声称已落地"和"代码运行时真正生效"这两件事分开验证**，发现了日交易上限这类"文档说已解决、代码实际未生效"的落差；
2. **补充了三周期 Agent 代码层面 25 项此前文档未系统列出的具体逻辑错误**（多为竞态、死代码、周期定义不一致类问题）；
3. **把"复发型问题"作为独立视角单独提炼**，指出团队更需要的可能不是继续修下一个具体 bug，而是补齐"熔断类代码审查清单""持久化状态检查清单"这样的工程流程护栏；
4. **用 2026 年最新的行业范例（HyperGuard/AKIVA-AI/Quant-Nanggroe-AI）补强了风控对比维度**，这是 05-08 竞品报告相对薄弱的部分。
