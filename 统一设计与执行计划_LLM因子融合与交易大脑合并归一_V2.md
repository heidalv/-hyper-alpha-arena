# 统一设计与执行计划：LLM×因子决策链 + 交易大脑认知环 合并归一 V2（终稿）

> **日期**: 2026-08-25
> **状态**: 终稿（两份代码复查 38 项证据并入，替代 V1）
> **被归一的设计/战线**（六条）：
> ①《_LLM因子混合决策架构设计.md》P0-P4（8/24 设计，未系统执行）
> ② 融合改造 阶段0-4 + PhaseG（8/23-25 已执行）
> ③ 短线赚钱改造 A~J + 短线深挖 B~J + 信号准确率/交易流动恢复（8/23-25 已执行/在飞）
> ④《交易大脑_持续学习认知架构设计_V1.md》M0-M6（8/25 设计，未执行）
> ⑤《_M3-2_单一出口编排器设计.md》（8/22 设计，未执行）
> ⑥《AI策略Agent群组审查_202608.md》P0-P3 清理清单（8/17 设计，未执行）
> 另含横切运维战线（挂机根治/DeepSeek 成本治理/垃圾数据治理，8/25 在飞）
>
> **方向裁决（用户 8/25，最高优先级）**：撤销"中/长线 0 LLM"决定。但**不是恢复旧的全栈 LLM**——旧 LLM 时代 8/10 一天 4,227 次调用本身就是 token 浪费反例；而是**结合现有底座（本地 14B 常驻、v3 元模型、pwin 仲裁、成本治理）做升级引入（LLM 2.0）**：三级模型分工、结构化复用、事件驱动、调用级问责，**用 ≤旧时代 20% 的 token 拿回 trend_follow/swing 正期望层与智能出场**。依据：《_LLM因子切换亏损根因调研报告》（去 LLM 致亏损加速 4.6×；trend_follow +28.62 规则化后 0 笔；swing +2.16→断账）。

---

## 0. 摘要

六条线归一为一个原则：**一条决策链（打每一仗）+ 一个认知环（从每一仗学习）+ 一个出场权威 + 一套尺子 + 一个 LLM 2.0 协同层（中长线重点）**。

1. **决策链 = 混合决策 L0-L7**。其中 L4（融合仲裁/来源归因）、L5（统一离场）、部分 L2 已被②③实现大半——设计文档降级为"权威设计 + 残余差距清单"。
2. **认知环 = 交易大脑 M0-M6**。归因部分已被阶段2/4 覆盖（source_attribution、按腿入账）——大脑 M1 改为"在事实归因之上做认知归因（运气vs技能、反事实、教训蒸馏）"，不重复造归因。
3. **LLM 2.0 协同层（新增，中长线重点）**：见 §3.2 与 U1——升级引入，不是恢复；token 预算 + 调用级问责是硬约束。
4. **出场权威**：`exit/unified_exit_state_machine.py` 已是"唯一决策出口"且 master_running_close 已对 scalp 免除（代码实证）；M3-2 的 `exit_orchestrator` 未实施且不再需要——⑤合并关闭。
5. **执行计划 U0-U6**：先收口（修正文档/定稿冲突参数/清理死代码）→ LLM 2.0 协同引入（重点）→ 数据与尺子 → 决策链补齐 → 认知环 → 资金仓位 → 自进化。

---

## 1. 六条战线现状盘点（代码复查定稿，2026-08-25）

### 1.1 ② 融合改造 阶段0-4 + PhaseG（✅ 已执行，主体可直接合并）

| 项 | 复查结论 | 证据 |
|---|---|---|
| 阶段0 融合仲裁器 | ✅ `decision_fusion_arbiter.py`，scalp_loop 两处 decide_scalp 接线（pwin 主轴+RR 下限+fusion 标签）；FUSION_MODE=hybrid | scalp_loop.py:690-721/1544-1600/1706 |
| 阶段1 pwin 同源+空头回退+MIDLONG_REVIEW_LLM=true | ✅ 已落地 | 475807a |
| 阶段2 source_attribution | ⚠️ **半落地**：归因写 JSON（data/fusion_attribution.json），**不落 DB 表**；信用分=二值 shadow（0/1），非连续分；窄 LLM 仲裁接 scalp_loop.py:1580；仓位标签在内存→JSON | source_attribution.py:17-20/142-146/189 |
| 阶段2 通道熔断 | ⚠️ **双实现**：真生效=source_attribution.record_close 的 breaker（JSON 持久化）；`decision_fusion_arbiter.py:202 ExitChannelBreaker` 是**死代码**（仅测试引用） | :147-158 |
| 阶段3 R2 风控事件 | ✅ 单笔>1.5% 权益→symbol_penalty→24h 禁开，三入口（scalp_loop:1536/mlto_cycle:341/midlong_factor_route:387） | paper_trading_engine.py:1613-1624 |
| 阶段4 统一来源标签/按腿归因/重锚/终检 | ✅ 全部落地（partial 腿 :1917、final 腿 :1631；空覆写防护 :89-105） | 5 提交实证 |
| 阶段4 6h 维护任务 | ✅ 已注册（learning_loop_service.py:50/61/166/404）| ⚠️ **一处不符**：docstring"回落 0.55"实际 `_set_floor(None)` 回落到 env=0.40（:427） |
| PhaseG 长线 V2 全接管 | ✅ LONG_TREND_V2=1；decide_long 唯一权威；_v2_long_managed 判定生效 | paper_trading_engine.py:2855-2865 |

### 1.2 ③ 短线战线（✅ 主体已执行；台阶1 指标未达标，详见 §1.6）

| 项 | 复查结论 | 证据 |
|---|---|---|
| B MR 结构反转 | ✅ SL[1.2,1.6]/TPcap1.2 仅作用于 MR 路径；trend 路径仍 SL[0.7,1.15]/TP[0.9,1.5]/RR1.5（正确区分） | scalp_ranging_mr.py:56-65；structure_stop_calculator.py:104-108 |
| C 空头分层 | ✅ MR 豁免+Paper 分档 40/55（⚠️ SCALP_SHORT_* 不在 .env，走代码默认） | scalp_execution_gate.py:132-199 |
| E2 重锚 | ✅ 恒等变换 bug 已修（gate 值回写+绕真实成交价重建+方向终检） | scalp_loop.py:802-803/1426-1485/1464-1472 |
| **E 手续费** | ✅ **已落地**：force_maker=False；盘口穿越判 taker/maker；平仓 taker 费率 | paper_trading_engine.py:1114/1167/1863/2052 |
| F 因子链贯通 | ⚠️ allowlist 已接（router:468/loop:464/pipeline:252），但 **factor_active_set ACTIVE=0**（PAPER=3/QUARANTINE=15）→ 短线实际只跑公式因子 | DB 快查 |
| I LLM 确认层 | ⚠️ 已接线 fail-open，但 **usage_scope scalp_confirm 84/85 未证实**（未进 LLM_USAGE_REGISTRY，llm_configurations 0 行）→ 实际走租户默认 LLM 兜底，成本治理失真 | scalp_llm_confirm.py + llm_config_service.py:404-413 |
| aa7e0dd v3 元模型 | ✅ scalp_meta_trainer.py（41 特征 pwin），usable=true，接 decide_scalp pwin 主轴 | scalp_loop.py:637-663 |
| 578fd14 趋势翻转/动态门槛 | ✅ ema_slope 翻转方向 + .env mtime 免重启 + FUSION_RR_FLOOR=0.9 | scalp_factor_router.py:201-233 |

### 1.3 ① 混合决策架构 P0-P4（设计 8/24；复查结论：**大量断言过时/错误，见 §2**）

| 项 | 现状 |
|---|---|
| P0-1 空头条件化 | ✅ 已被深挖 C/D 超越（MR 豁免+分档），设计稿"三条件硬门"过时 |
| P0-2 FlashVeto tiered | ❌ SCALP_VETO_MODE=off；代码支持 tiered 完好（settings.py:635），veto 带=[25,30)（设计稿"35-44"过时） |
| P0-3 AI 复审恢复 | ❌ SCALP_AI_REVERSE_DISABLED=true（根因报告：ai_reverse/reverse_netting 是净正通道，需按证据恢复） |
| P0-4 mid/long LLM 复查 | ✅ **已 true 且语义定论**：true=回滚 trend_agent LLM 复查（midlong_position_manager.py:442/547、hold_timeout_trend_review.py:257）——**无需"恢复"动作，U1 只做升级约束** |
| P0-5 手续费 | ✅ 已落地（=短线方案 E，复查实证） |
| P0-6 scalp 乘数灰度 | ❌ 未做；EV Governor 现为簇级乘数（pause0.5/reduce0.75/premium1.2），无 0.1/0.25/0.5 阶梯 |
| P1 thesis shadow | ❌ MIDLONG_MID_VIA_MLTO=false（mlto_cycle.py:765/784 跳过）；**thesis 层=中长线 LLM 协同最大缺口，U1 重点** |
| P2 互证上线 | ⚠️ pwin 仲裁已接真数据；**mlto_signal_weights win/loss 已接线但被 thesis_id 门控 dormant**（unified_learning_service.py:230）→ **重启 thesis 一并解锁 owm 调权** |
| L0 尺子 | ✅ PBO 已改时序 CSCV（dsr_pbo.py:137）；BACKTEST_FILL_MODEL 默认 next_open（backtest_engine.py:127）；⚠️ factor_backtest_scorer walk-forward 仍 close-to-close fwd_ret（残留问题）；开关名实为 FACTOR_SCORER_DSR_MIN_SYMBOLS |
| L2 regime | ⚠️ KlineAnalyst 仍输出自由 Markdown（kline_ai_analysis_service.py:634-667），结构化升级未做；v3 元模型=scalp_meta_trainer（与 KlineAnalyst 无关） |
| L3 thesis | ⚠️ mlto 四模块均被生产代码引用（设计稿"仅 tests 引用"错）；invalidation_json 字段存在 |
| L7 学习闭环 | ⚠️ factor_decay_monitor **已接 6 处调用点**（设计稿"死代码"过时）；trading_wisdom 走 RAG；PROMPT_EVOLUTION_ENABLED=true |

### 1.4 ④ 交易大脑 M0-M6（设计 8/25，未执行；与②③衔接定义如下）

- M0 认知数据层：brain_* 表 + thesis_id 绑定。**复查事实**：strategy_trades 无 decision_source/thesis 字段（models.py:940-970，仅 JSON context）；归因现状在 JSON 文件不落 DB——**M0 = 把归因/标签/信用分从 JSON 收编为 DB 表**（升级 source_attribution，不新建重复表）；
- M1 复盘官：挂在 process_outcome 之后（unified_learning_service.py:223，已有 thesis postmortem 钩子 :237 可扩展）——做认知归因（运气vs技能、反事实、教训蒸馏），消费阶段2/4 事实归因；
- M2 委员会：= §3.2 LLM 2.0 协同层的组织形态（KlineAnalyst 升级 + thesis + 风险官 + 组合经理），shadow-first；
- M3 记忆蒸馏：复用 trading_wisdom（RAG）+ strategy_memories + trend_cycles；
- M4 元学习：研究任务竞价接 evolution_scheduler；
- M5 仓位大脑：在 L6（fractional Kelly/vol targeting）之上叠加认知折扣；
- M6 自进化：与因子晋升/退役管线合并。

### 1.5 ⑤⑥ 裁决

- ⑤ M3-2 单一出口：**合并关闭**。unified_exit_state_machine 已是唯一决策出口（docstring+接线实证），master_running_close 已对 scalp 免除；M3-2 的 exit_orchestrator/EXIT_ORCH_V2 不再实施；其"shadow 对拍方法"保留为任何出口改造的通用方法。
- ⑥ Agent 群组审查：**部分撤销**（见 §2 C6 裁决）：SwingAgent 仍删；"删 MLTO thesis / trend_agent 标 deprecated / 删 master_execution 长线段 / 删 mid_view"一律撤销。

### 1.6 短线台阶1 指标现状（8/24 日检，8/25 生成——诚实基线）

| 指标 | 现状 | 台阶1 目标 | 判定 |
|---|---|---|---|
| 已平 1910 笔净额 | -84.93（费用 27.67） | 转正 | ❌ 未达 |
| 胜率 | 41.15% | ≥42% | ⚠️ 接近 |
| TP 命中率 | 9.5%（181 笔） | ≥45% | ❌ 远差 |
| SL 命中率 | 11.8%（226 笔） | ≤20% | ✅ 达 |
| 超时率 | 39.6%（756 笔） | ≤25% | ❌ 未达 |
| breakeven_tp | 110 笔 +96.7 | — | ✅ 佐证"退出结构"是正期望来源 |

**含义**：短线仍在亏，但出血结构已变（SL 降到 11.8%、breakeven_tp 正贡献）——参数对齐只做了一半，U2 继续；pwin 主轴/RR0.9 放行口径（§2 冲突 1/2）直接影响剩余亏损，必须定稿。

---

## 2. 代码复查结论：设计文档的错误与修正清单（E1-E20，以代码为准）

| # | 错误/冲突 | 正确事实（证据） | 修正动作 |
|---|---|---|---|
| E1 | 混合决策§五"MIDLONG_REVIEW_LLM 现值 false" | .env:773=true；true=LLM 复查版（midlong_position_manager.py:442） | 文档修正；P0-4 标记已完成 |
| E2 | P0-1"三条件硬门" | 已升级为 MR 豁免+Paper 分档 40/55+两条件半仓（scalp_execution_gate.py:126-199） | P0-1 关闭；勿回退 |
| E3 | L5"TP 1.8%/SL 1.2%" | 那是 TIER_SHORT；scalp 权威=structure_stop_calculator.py:98-108（MR SL[1.2,1.6]/TP≤1.2；trend SL[0.7,1.15]/TP[0.9,1.5]/RR1.5）；深挖 B 的数字同样只覆盖 MR 路径 | 三方数字全部作废，以此为准 |
| E4 | L0 开关名"DSR_MIN_SYMBOLS" | FACTOR_SCORER_DSR_MIN_SYMBOLS（默认 4）；PBO 已时序 CSCV（已落地） | 文档修正 |
| E5 | L0 fill_model 文件指错 | 在 backtest_engine.py（BACKTEST_FILL_MODEL 默认 next_open）；factor_backtest_scorer walk-forward 仍 close-to-close fwd_ret | 文档修正；fwd_ret 口径列入 U2 复核 |
| E6 | L3"mlto 仅被 tests 引用" | 四模块均被生产代码 import | 文档修正 |
| E7 | L7"factor_decay_monitor 死代码" | 已接 6 处生产调用点（learning_loop_service.py:449 明确"修复…此前无调用点"） | 文档修正 |
| E8 | L3"预算 200 次/天复用 llm_budget" | llm_budget=并发 Semaphore 且关闭（LLM_BUDGET_ENABLED=false），无每日计数 | U1 自建每日 token 预算（见 §3.2） |
| E9 | L6"EV Governor 按层已存在" | 现为簇级乘数，无 nature 分层阶梯 | P0-6 重新立项（U5） |
| E10 | §五 HYBRID_* 命名 | 实际落地为 FUSION_*（FUSION_MODE=hybrid） | 合并后统一用 FUSION_* |
| E11 | L4"mlto_signal_weights 待接真数据" | 已接 learning_bridge，但被 thesis_id 门控 dormant | U1 重启 thesis 即解锁 |
| E12 | QAA"7 张 trading 卡" | 实为 9 张（cards.py） | 大脑设计文档修正 |
| E13 | L4"35-44 分边缘单" | 现 veto 带=[25,30) | 文档修正 |
| E14 | **FUSION_SCALP_PWIN_MIN 三方打架** | 设计 0.55 / 深挖报告 0.30 / .env 现值 **0.40**（.env.bak_20260825_pwin=0.45） | U0 影子对拍定稿（见 §4 U0） |
| E15 | **FUSION_RR_FLOOR 冲突** | 设计 1.2（"RR<1.2 必亏"回放证据）vs 578fd14 改 .env=**0.9** | U0 影子对拍定稿 |
| E16 | **SCALP_SIZE_PCT 冲突** | 深挖报告称 0.90，.env 现值 **0.60** | 以 .env 为准，文档修正 |
| E17 | PB_PAPER_SKIP 横跳 | 最终 **true**（.env:849，语义=paper 跳组合预算慢检查；PB 已异步化+6s 超时降级兜底） | 附录 A 定稿 |
| E18 | 深挖报告 C/I 声称新增 env | SCALP_SHORT_PAPER_EXEMPT_MIN/FULL_MIN、SCALP_LLM_CONFIRM_ENABLED **均不在 .env**，走代码默认 | 文档修正；U1 核查 84/85 绑定 |
| E19 | 6h 维护"回落 0.55" | 代码 `_set_floor(None)` 实际回落 env=0.40（learning_loop_service.py:427） | 统一为 0.40，改 docstring |
| E20 | 通道熔断双实现 | ExitChannelBreaker（decision_fusion_arbiter.py:202）死代码 | U0 删除，统一到 source_attribution |
| C6 裁决 | ①要复活 MLTO、⑥要删 MLTO | **升级引入**：thesis 恢复（JSON 结构化+限频）；SwingAgent 仍删；master_execution 长线段不恢复（由 midlong 循环+thesis 门承载）；trend_agent 恢复但低频+本地模型 | 见 §3.2 |

---

## 3. 归一后的统一架构

### 3.1 统一分层

```
决策链（秒~4h）                             认知环（4h~周）
──────────────────────────                 ──────────────────────────
L6 资金风控：fractional Kelly f=0.25       C4 仓位官：共识折扣×信用分×regime×相关性
   + vol targeting + 回撤调控 ◄────────── （大脑 M5，消费 L6 底座）
L5 出场：unified_exit_state_machine        C3 复盘官：事实归因之上做认知归因（大脑 M1）
L4 融合仲裁：pwin主轴+RR下限+来源归因+owm    （消费阶段2/4 归因；M0 把 JSON 归因收编 DB）
L3 Thesis：JSON 方向论题【U1 引入】          C2 委员会：研判→决策卡→滞后验证（大脑 M2）
L2 Regime：规则+v3元模型+LLM校准【U1 升级】   C1 记忆：四层+蒸馏/遗忘（大脑 M3）
L1 因子：信号生成+晋升消费链                 C0 元学习：研究任务竞价（大脑 M4）
L0 数据成本：真实手续费+尺子(PBO/DSR/fill)
```

**LLM 2.0 协同层 = L2/L3/L5 的 LLM 部分 + C2 委员会，统一受 §3.2 六条约约束。**

### 3.2 LLM 2.0 协同层设计（中长线重点；升级引入，不是恢复）

**反模式（旧 LLM 时代，8/10 实测 4,227 次/天——本次严禁重演）：**

| 旧调用 | 次数/天 | 浪费点 |
|---|---|---|
| KlineAnalyst | 1098 | 自由 Markdown 文本，无人结构化消费，重复生成 |
| thesis | 813 | 无失效复用、无缓存、无频率上限 |
| TrendAgent | 666 | 每持仓周期重复复查，长线段与 master 双入口 |
| flash_veto | 588 | 每单 LLM 否决，热路径延迟+token |
| SwingAgent | 430 | 与 MasterController 职责重叠，独立价值为零 |
| MasterController | 334 | 长线段现已被 midlong 循环取代 |

**六条升级原则（token 花在刀刃上）：**

1. **三级分工**：云端强模型（thinking）只做 thesis 生成与 regime 剧变研判；本地 14B（已常驻）做持仓复查/方向确认/scalp_confirm；统计层（v3 元模型 pwin、规则 L1、多周期共振）做秒级判断——**LLM 永不逐 tick，统计模型干得了的活不给 LLM**。
2. **结构化 + 复用**：KlineAnalyst 升级为 JSON 输出（regime 标签+置信度+依据），一次分析被 L4 仲裁、thesis、委员会多方消费；thesis 带失效条件，条件未触发不重算；无信号变化不重复调用。
3. **事件驱动**：复查只在"min_hold 满 + 结构事件（趋势破坏/regime 突变）"触发，不是每 tick。
4. **预算硬上限**：每日 token 预算 ≤ 旧时代 20%（≈800 次/天量级、成本 <$3/天），逐类配额；复用 8/25 成本治理的用量记账（裸 HTTP 路径已补）。
5. **调用级问责（新引入）**：每类 LLM 调用绑定 decision_id→结果回填（与 source_attribution 同源）→滚动 ROAS（每类调用的正边际）；**无正边际的调用类型自动降频→降级本地模型→下线**——这是大脑 M4 元学习在"LLM 预算分配"上的直接应用，也是"哪些 LLM 调用值得"由数据决定的机制。
6. **影子优先 + 与大脑设计完全一致**：thesis 先 shadow（方向一致率>55% 才转方向门+sizing）；委员会信用分/校准淘汰、控制面隔离、学习 ROI 问责全部适用于 LLM 调用本身。

**与旧时代的本质区别**：旧=全栈 LLM 每笔决策、无预算、无结构、无问责；新=**LLM 只做统计模型干不了的三件事（语义/叙事/失效条件判断 + 方向不对称 + 智能出场复查），且每一分 token 都要在滚动报表里证明自己**。

### 3.3 出场唯一权威（⑤裁决，已定）

`unified_exit_state_machine.py` 为唯一决策出口（已接线实证）；position_exit_orchestrator 作为其规则层执行器；unified_exit_executor 的 Tier 门控并入其 AI 层；ExitChannelBreaker（E20）删除；M3-2 的 shadow 对拍方法保留为通用切换方法。

### 3.4 单一事实源

1. 附录 A 开关真相表为唯一权威，其余文档数字以 U0 修正后为准；
2. 各旧设计文档头部加指针行（U0 执行）；
3. 验收统一到 §5 一张表。

---

## 4. 统一执行计划（U0-U6）

| 阶段 | 内容 | 依赖 | 验收 |
|---|---|---|---|
| **U0 收口修正（1-2天）** | ① E1-E20 全部修正落档（旧文档头部加指针）；② **定稿冲突参数**：pwin 0.40/0.45/0.55 与 RR 0.9/1.2 各跑 3 天影子对拍（回放证据法，复用 pwin 同源脚本），以"成本后净收益+胜率"定稿并回填设计文档；③ 删 ExitChannelBreaker 死代码（E20）；④ ⑥清理只删 SwingAgent（C6 裁决）；⑤ 附录 A 落成 | 本文件 | 文档与代码一致率 100%；冲突参数有对拍数据定稿 |
| **U1 LLM 2.0 协同升级引入【重点，用户 8/25 指示】（3-7天）** | ① KlineAnalyst JSON 结构化（regime+置信度+依据）+ 三方消费接线；② thesis shadow：MIDLONG_MID_VIA_MLTO=true + JSON 模板（direction/conviction/invalidation/missing_evidence）+ 每 symbol 每 4h 限频 + 失效复用；③ 复查层约束：MIDLONG_REVIEW_LLM（已 true）加事件驱动门槛 + 本地 14B 优先 + 调用绑定 decision_id；④ 长线 LLM 门：long_trend_v2 规则入场保留，thesis 做方向确认门+失效条件（LLM 判方向、规则执行——不是恢复 TrendAgent 全栈）；⑤ 每日 token 预算+逐类配额+ROAS 报表+自动降级规则（§3.2 原则 4/5）；⑥ usage_scope scalp_confirm 84/85 真实绑定核查（E18） | U0 | ①trend_follow 恢复开单且正期望；②swing 均持仓回升>6h；③thesis 方向一致率>55%（1周 rolling）；④LLM 调用 ≤旧时代 20%、成本<$3/天；⑤每类调用有 ROAS 记录；⑥thesis shadow 期与纯规则对拍不劣化 |
| **U2 数据与尺子（3-5天）** | ① L0 残留：factor_backtest_scorer walk-forward fwd_ret 口径复核（E5）、mid FactorRoute 跨域特征注入（CVD/OI/funding 一致性门，缺口确认仍在）；② 大脑 M0：归因/标签/信用分从 JSON 收编为 DB 表（source_attribution 升级）+ strategy_trades 加 thesis_id/decision_source 列 + brain_* 表；③ 短线台阶1 残余：TP 命中 9.5%→≥45%、超时 39.6%→≤25%（结构参数继续对拍） | U1 | 每笔平仓有 episode（thesis_id+归因，DB 可查）；尺子口径复核有结论 |
| **U3 决策链补齐（1周）** | ① SCALP_VETO_MODE=tiered 影子恢复（代码完好，仅切 env）+ SCALP_AI_REVERSE_DISABLED=false 影子恢复（根因报告：净正通道，过 min_hold）；② 委员会 C2 影子（研判会只写日志）；③ owm 调权解锁验证（U1 后 mlto_signal_weights 应吃真数据）；④ 短线因子池：factor_active_set ACTIVE=0 问题（晋升消费链继续修） | U2 | 影子对拍周报；否决通道净收益差>0 才转正；委员会校准度上线 |
| **U4 认知环上线（2-3周）** | ① 大脑 M1 复盘官（认知归因+反事实+lessons 入 ChromaDB）；② M3 weekly distill + RAG 注入 A/B；③ 决策卡→控制面通道（软约束/门禁参数 TTL 写入） | U3 | 30 笔后亏损聚类与人工审计一致；带/不带记忆 A/B 显著增益才默认注入 |
| **U5 资金与仓位（2-3周）** | ① L6：fractional Kelly f=0.25 + vol targeting + 回撤调控；② scalp 乘数证据阶梯（0.1→0.25→0.5，替代簇级乘数，P0-6 新立项）；③ 大脑 M5 折扣层影子→实盘 | U4 | 账户周回撤≤5%；MDD/波动率下降；乘数阶梯有日志轨迹 |
| **U6 自进化整合（持续）** | ① 大脑 M4 元学习调度（研究任务竞价）；② M6 策略基因库方向 + A/B 全自动晋升退役；③ 每 2 周三方对拍（混合 vs 纯因子 vs 纯 LLM 基线） | U5 | 研究任务完成率/晋升率≥固定调度基线；3 个月滚动 Sharpe/卡玛比高于 U5 基线 |

**执行纪律**：U0 纯修正不新增交易行为；U1 起一切新能力 shadow-first；短线台阶1 指标随 U2-U3 持续追踪；任何阶段验收不过就停在该状态迭代。

---

## 5. 统一验收总表

| 维度 | 指标 | 来源 |
|---|---|---|
| 中长线 LLM 协同【重点】 | trend_follow 恢复开单且正期望；swing 均持仓>6h；thesis 方向一致率>55%；LLM 调用≤旧时代20%；成本<$3/天；每类调用 ROAS 为正 | 根因报告 + §3.2 + 用户裁决 |
| 短线结构 | TP 命中≥45%、超时≤25%、SL≤20%、WR≥42%、单笔≥+0.05% | 短线台阶1 |
| 因子链 | scalp 档 ACTIVE≥4 进入 allowlist 消费 | 台阶3/L1 |
| 归因 | 平仓归因覆盖率 100%（DB 可查，事实层+认知层） | 阶段4/大脑 M1 |
| 校准 | 委员会各 agent 校准优于随机 | 大脑 M2 |
| 资金 | 账户周回撤≤5%；乘数证据阶梯有日志 | L6 |
| 学习 | 学习 ROI 为正；研究任务≥固定调度基线 | 大脑 M4 |
| 终局 | 3 个月滚动、成本后、样本外：混合系统 > frozen baseline | 三线共同 |

---

## 6. 风险与护栏（三线归并）

1. 旧文档继续被各自执行 → 本文件为唯一执行入口，U0 加指针；
2. **token 浪费重演**（旧时代 4,227 次/天）→ §3.2 六原则 + 每日预算硬上限 + ROAS 自动降级，违反预算即熔断 LLM 调用；
3. 冲突参数拍脑袋定稿 → U0 影子对拍（回放证据法）定稿，不凭文档直觉；
4. 大脑与执行层耦合 → 控制面隔离 + TTL + 回滚快照；
5. 回测幻觉当学习信号 → 只用真实/纸面成交；手续费已修（E 复查实证）但 paper 结论仍按实盘再差 8bp 折算；
6. LLM 不可复现 → 温度 0、JSON 约束、影子先行、按 rolling 结果调权；
7. 学习期门禁收紧 → 大脑不得提交收紧类门禁变更；
8. 出场权威切换 → shadow 对拍偏差<1% 才切换，flag 一键回滚。

---

## 附录 A：开关真相表（唯一权威，U0 后回填设计文档）

| 开关 | 混合决策设计目标 | 当前 .env | 代码语义（复查定论） | 统一计划动作 |
|---|---|---|---|---|
| SCALP_VETO_MODE | tiered | off | off=整体跳过；tiered 代码完好，带=[25,30) | U3 影子恢复 |
| SCALP_AI_REVERSE_DISABLED | false | true | 禁用净正通道（根因报告 +1.51） | U3 影子恢复（过 min_hold） |
| MIDLONG_REVIEW_LLM | true（=LLM 版） | true | ✅ true=回滚 trend_agent LLM 复查 | U1 加事件驱动约束+本地模型优先 |
| MIDLONG_MID_VIA_MLTO | true（shadow） | false | false=thesis 任务跳过（mlto_cycle.py:765/784） | U1 重开 shadow→上线 |
| FUSION_MODE | 未涉及 | hybrid | 融合仲裁器生效 | 记录 |
| FUSION_SCALP_PWIN_MIN | 未涉及 | **0.40** | 与设计 0.55/报告 0.30 三方冲突（E14） | U0 对拍定稿 |
| FUSION_RR_FLOOR | 1.2 | **0.9** | 与"RR<1.2 必亏"回放证据冲突（E15） | U0 对拍定稿 |
| PB_PAPER_SKIP | 未涉及 | true | paper 跳组合预算慢检查（PB 已异步化兜底） | 记录（E17 定稿） |
| SCALP_SIZE_PCT | 未涉及 | **0.60** | 报告称 0.90 过时（E16） | 以 .env 为准 |
| SCALP_MR_MIN_RR | 未涉及 | 0.75 | MR 路径生效 | 记录 |
| SCALP_EV_MIN_PCT_PAPER | 未涉及 | -0.0060 | Paper EV 地板生效 | 记录 |
| SCALP_SHORT_PAPER_EXEMPT/FULL_MIN | 未涉及 | **不在 .env** | 代码默认 40/55 生效（E18） | U0 补写 .env 显式化 |
| SCALP_LLM_CONFIRM_ENABLED | 未涉及 | **不在 .env** | 代码默认 true；84/85 绑定未证实（E18） | U1 核查真实绑定 |
| LONG_TREND_V2 | 1（保持） | 1 | decide_long 唯一权威 | 记录 |
| MASTER_MIDLONG_LLM_MODE | summary（保持） | summary | 长线段 summary-skip | 记录（C6：不恢复长线段） |
| PROMPT_EVOLUTION_ENABLED | true | true | 进化链路活跃 | 记录 |
| FACTOR_SCORER_DSR_MIN_SYMBOLS | 未涉及 | （默认 4） | PBO 已时序 CSCV | 记录（E4 正名） |
| BACKTEST_FILL_MODEL | 未涉及 | next_open（默认） | 已消灭 close 虚高 | 记录（E5 正名） |
| HYBRID_EV_SCALP_MULT | 0.1 | 不存在 | 命名未采用，实际 FUSION_*（E10） | U5 证据阶梯新立项 |

---

## 状态标注

- 本稿已并入：两份代码复查报告（38 项对拍证据）、用户 8/25 方向裁决（中长线 LLM 2.0 升级引入）、六线现状、E1-E20 错误清单。
- U0 执行时将向①④两份被归一设计文档头部加"已被《统一设计与执行计划 V2》归一"指针行。


---

## 附录 B：U0 执行记录（2026-08-25，已落地）

### 已完成
| # | 项 | 结果 |
|---|---|---|
| U0-2 | **冲突参数定稿（数据裁决）** | `_fusion_weekly_validation.py` 实测：pwin≥0.55 桶 7 天 n=4328 wr=60.3% 净 +7.16 OK、14 天 n=4541 wr=59.2% 净 +5.28 OK → **FUSION_SCALP_PWIN_MIN 0.40→0.55**（回放证据+当下数据双支持；动态门槛 mtime 免重启，.env 已改）。**FUSION_RR_FLOOR 保持 0.9 不动**：见 E21 |
| U0-3 | ExitChannelBreaker 死代码删除（E20） | class+模块实例删除（decision_fusion_arbiter.py），统一到 source_attribution.record_close 的 breaker；测试清理后 22/22 绿 |
| U0-4 | SwingAgent 清理（C6 修正） | 不删：文件自带完整 DEPRECATED 标记且 4 处生产 import（master_execution.py:218/556/633、qual_layer.py:719、trend_agent.py:389）——V2 §2 C6 已修正 |
| U0-5 | .env 显式化（E18） | 补写 SCALP_SHORT_PAPER_EXEMPT_MIN=40 / FULL_MIN=55 / SCALP_LLM_CONFIRM_ENABLED=true（与代码默认一致，零行为变化）；E19 随 pwin 恢复 0.55 自动消解（回落值=env=0.55，与 docstring"回落0.55"一致） |
| U0-1 | 后端健康基线 | ✅ healthy；boot=aa7e0dd、代码头=578fd14（U0 改动待下次重启加载，均为死代码删除零行为影响） |

### 新发现（U0 期间复查）
- **E21（新冲突）**：深挖 B 把 MR 的 MIN_RR 降到 0.75（"靠胜率不靠盈亏比"），但仲裁器 RR_FLOOR 证据值 1.2 会把所有 MR 单（rr≈0.75）拦死——**RR 按打法分层（MR 0.75 / trend 1.2）立项 U3**，在此之前 RR_FLOOR 保持 0.9 不恢复 1.2。
- 自动熔断机制运行实证：`breaker_shadow = {short|max_hold_timeout: True, short|tp: False}`——39.6% 超时出血通道已被系统自动 shadow，机制有效。

### 待办移交 U1
- U1-1 KlineAnalyst JSON 结构化（kline_ai_analysis_service.py:634-667 + prompt_templates.py:166 当前为自由 Markdown）
- U1-2 thesis shadow（MIDLONG_MID_VIA_MLTO=true + JSON 模板 + 4h 限频；复用 mlto thesis_store）
- U1-3 token 预算 + 调用级 ROAS（llm_budget 是并发信号量且关闭，需新建每日计数预算）
- U1-4 usage_scope scalp_confirm 84/85 真实绑定核查（E18 残留）

### U1-1 落地记录（2026-08-25）
- **KlineAnalyst 结构化 regime 升级**（trading_analysts.py，+21/-4，py_compile+import 验证通过）：
  1. prompt 新增第 6 维"市场状态(regime)判定"（trending_up/trending_down/ranging/high_vol/transition）；
  2. JSON 模板新增 `regime` / `regime_confidence` / `invalidation` 三个字段（原有 10 字段不动，向后兼容）；
  3. 解析段带默认值兜底：旧缓存/旧模型缺字段 → regime 留空（display "regime=?"），由规则快照兜底，缓存 TTL 到期自然刷新；
  4. 信号 `data` 暴露新字段供 L2 Regime Governor / L4 仲裁消费；detail 显示 regime+失效条件。
- **重要发现（修正 U1-1 范围）**：交易链 KlineAnalyst 的 prompt 本就要求严格 JSON（非自由 Markdown——自由文本是前端用户路径 `kline_ai_analysis_service.py`）；本次是**增量补 regime 字段**，非推倒重写。tier=quick 本地 14B 优先 + 每轮 KLINE_LLM_MAX_PER_CYCLE 预算 + 哈希缓存三层成本机制已存在且保持。
- 验证：py_compile OK、模块 import OK；无 KlineAnalyst 专测文件（git grep 零命中），由运行时日志观察首轮 regime 字段产出。

### U1-2 落地记录（2026-08-25）
- **thesis shadow 恢复（只写论点、绝不落单）**：
  1. 新模块 `backend/services/mlto/thesis_shadow.py`：JSON 结构化 thesis（direction/llm_conviction/invalidation/missing_evidence/thesis_summary/recommend_open/should_close），本地 14B 优先（usage=thesis, tier=quick）云端兜底；
  2. 成本纪律：每(symbol,tier) 4h 限频（THESIS_SHADOW_MIN_INTERVAL_S=14400）+ 每日 200 次硬上限 + 每轮最多 3 个（防阻塞主循环）；
  3. 落库：thesis_store.get_or_create(db=..) + _persist（**注意：thesis_store 无 save()，8/19 清尸后无提交块——本次在 mlto_cycle.py 空提交块处接线 run_shadow_batch**）；
  4. 与 FactorRoute 方向冲突仅记日志（双轨对拍原料）；任何异常吞掉不外溢（影子层零交易副作用）；
  5. 开关：MIDLONG_MID_VIA_MLTO=false→true（仅解锁 _thesis_jobs 收集，无其他引用点）；新增 THESIS_SHADOW_* 5 项（含 THESIS_SHADOW_INCLUDE_LONG=true 让长线 fixed 币种也进影子，绕过 V2 的 long 跳过）。
- 验证：py_compile + import + 冒烟（ENABLED=false→None；prompt 构建含 JSON 字段）；**生效需下次后端重启**（MIDLONG_MID_VIA_MLTO 非动态门槛）。
- 消费链预告：thesis 落库后，L4 decide_mid/decide_long 的 thesis 门（U3）与 mlto_signal_weights owm 调权（被 thesis_id 门控 dormant）即可吃真数据。

### U1-3 + U1-4 落地记录（2026-08-25）
- **U1-4 84/85 绑定核查【定论】**：`scripts/_check_llm_configs.py` 实锤——id=84(Ollama-Qwen3-14B) 与 id=85(DeepSeek) 的 usage_scope **本就含 scalp_confirm**（B 复查时"0 行"是 RLS 遮蔽误判）。真实缺口=LLM_USAGE_REGISTRY 无该条目（UI 无法分配）→ 已补 `scalp_confirm` 与 `thesis` 两条注册；并为 84/85 的 scope 补 `thesis`（否则 thesis 影子落到云端默认 17）。
- **E22（新发现并修复）**：`get_llm_config_local_first` 的云端兜底恒断——非默认 ollama 绑定(84)在云端查询同样排第一 → cloud==local → fallback=None。修复：`get_llm_config_for_usage` 增 `exclude_provider` 参数，云端查询排除 ollama。实测修复后：scalp_confirm/thesis/kline_analysis 全部 local=84 + cloud=85（本地失败可自动降级云端）。
- **U1-3 LLM2 每日预算治理**：新模块 `backend/services/llm_budget_governor.py`（scope 级硬上限：kline 400/thesis 200/scalp_confirm 100/master 60/other 150，全局 800；超限当日暂停该 scope=自动降级规则；状态落盘 data/llm2_budget_state.json 重启存活，UTC 日期自动清零）。钩子接入 3 个调用点（KlineAnalyst/thesis_shadow/scalp_llm_confirm，try/except fail-open）。报表脚本 scripts/_llm2_budget_report.py。冒烟实测：cap=3 时第 4 次拒绝+paused ✓。
- 验证：py_compile 全部通过；解析冒烟 84/85 双段正确。生效需后端重启。
- 遗留：ROAS 完整问责（decision 绑定回填 + 滚动报表 + 按 ROAS 自动降频）待 U4 复盘官落地后接入本模块。

### U2 落地记录（2026-08-25，本轮）
- **U2-2 大脑 M0 数据层（✅）**：
  1. 迁移 `backend/database/migrations/add_brain_m0_tables.py`（幂等）：brain_theses/brain_episodes/brain_attribution/brain_lessons/brain_agent_calibration/brain_research_tasks 六表已建；
  2. strategy_trades 新增可空列 `thesis_id` + `decision_source`（information_schema 守卫幂等）；
  3. source_attribution.record_close 写穿 brain_attribution（归因事实 JSON→DB 收编第一步，失败静默不影响主链路）。
- **U2-1a 中线资金流一致性门（✅）**：新模块 `backend/services/factor_engine/midlong_flow_gate.py`——CVD/Taker **双背离**才拦（fail-open，证据不齐绝不拦）；数据源=market_summary 内嵌 flow 优先，60s TTL 回退 capture_flow_indicators_for_symbol；接线 factor_route_open（融合仲裁前）；开关 FACTOR_ROUTE_FLOW_GATE。修复《混合决策》L0"mid 只吃 OHLCV"缺口。
- **U2-1b fwd_ret 口径复核（⏳ 未动）**：factor_backtest_scorer 连续口径 close-to-close 残留，涉及因子晋升尺子，风险较高——下轮单独处理（先影子对拍）。
- **U2-3 台阶1 追踪（数据基线）**：8/24 日报（8/25 生成）：已平 1910 笔、WR 41.15%（目标≥42%）、净 -84.93、TP 命中 9.5%（目标≥45%）、SL 11.8%（✓≤20%）、超时 39.6%（目标≤25%）、breakeven_tp 110 笔 +96.7。**结论：SL 与胜率接近达标，TP 命中与超时仍是主缺口——退出结构继续调（U2-1b 联动：超时通道已被 breaker shadow）**。
- 验证：py_compile 全过；建表+新列已实库验证（information_schema 查询确认）。

### U3 落地记录（2026-08-25，本轮）
- **U3-1a FlashVeto tiered 恢复（✅）**：.env `SCALP_VETO_MODE=off→tiered`（代码默认本就 tiered；paper fail-open=true / live fail-closed 语义完好）。生效后 veto 带=[25,30)（SCALP_VETO_BAND_LOW=25，与 SCALP_FACTOR_CONFIRM_THRESHOLD=25 对齐：25-29 分边缘单过 LLM 否决层）。需重启生效。
- **U3-1b AI 复审恢复（⏳ 暂缓，证据冲突）**：根因报告 reverse_netting +1.51 vs 深挖记录 ai_reverse -2.41 证据矛盾，按 V2「先影子对拍」原则暂不直接翻转 SCALP_AI_REVERSE_DISABLED，待 veto tiered 跑一周后对比边缘单否决净差再定。
- **U3-2a owm 调权解锁【接线点已定位，待下一块】**(`unified_learning_service.py:225-240`)：thesis 归因学习已被 `meta.thesis_id` 门控——thesis shadow（U1-2）落库后仍需**开仓时把活跃 thesis_id 写入 trade meta**（factor_route_open/execute_midlong_open 处读 thesis_store.get 绑定），下轮实现该绑定后学习桥即吃真数据。
- **重启后观察清单（累积 U1-U3）**：① `[ThesisShadow] ... (shadow, 不落单)` 日志；② KlineAnalyst detail 出现 `regime=xxx(nn%)`；③ `[LLM2预算]` 记账；④ FlashVeto tiered 的 veto 计数与 25-29 分带净差；⑤ brain_attribution 行随平仓增长；⑥ thesis 表行随 4h 限频增长。

### 重启验收 + U3-2a 落地记录（2026-08-25 17:06）
- **受控重启完成**：boot=c330469、matches_disk=True（隧道误杀事故后经用户重连远端完成；后端全程在远端操作）。
- **U1-U3 验收清单**：① ✅ ThesisShadow 活体产出——17:05:09 UNI/mid、17:05:27-35 BTC/ETH/UNI long（每轮 3 个上限生效），落库验证：analytics 库 mlto_thesis 最新 5 行 = shadow 产出（mlto_thesis 属 analytics 库，主库无此表属正常）；② ⏳ KlineAnalyst regime 字段（XPL 流式已跑，待下轮观察解析输出）；③ ✅ LLM2 预算（成功调用不打日志、仅超限告警，设计如此）；④ ⏳ FlashVeto tiered（等 25-29 分信号）；⑤ ⏳ brain_attribution（等平仓写穿）；⑥ ✅ thesis 行按 4h 限频增长。无关异常：ai_trade_journal 日复盘两条 PG 连接中断 Traceback = 重启后旧连接残留，服务自愈重连，非本次改动引入。
- **U3-2a thesis_id 开仓绑定（✅ 代码完成）**：三处补丁——① midlong_helpers.py 阶段4统一标签点（开仓成功后）读 thesis_store.get 把 thesis_id 挂进 tag_position meta；② source_attribution 新增 tag_meta() 回查；③ unified_learning_service.process_outcome 在 meta.thesis_id 缺失时从归因标签兜底回查 → 学习桥（learning_bridge + mlto_signal_weights owm 调权）在 shadow thesis 产出后即可吃真数据。冒烟 roundtrip 通过；**需下一次重启生效**（本轮后端已运行）。

### U4 落地记录（2026-08-25，本轮）
- **复盘官最小实现（✅）**：新模块 `backend/services/mlto/trade_review_officer.py`——在事实归因之上做认知归因（运气vs技能/原因标签/反事实/教训），JSON 输出经本地 14B（usage=journal 复用 84 绑定）；钩子挂在 unified_learning.process_outcome 的 MLTO 块之后（静默失败）；预算 scope review=50/天 + 300s/币限频；落库 brain_episodes（归因 JSON）+ brain_lessons（教训文本，U2 表已建）。验证：py_compile+import ✓。生效需下次重启（无副作用）。
- **U3-2b 委员会影子（⏳ 下轮）**：研判会（独立观点+红队+决策卡只写日志）将以复盘官/影子 thesis 的产出为输入实现。
- 观察项更新：ThesisShadow 持续产出中（每轮 3 个、4h 限频）；brain_attribution/复盘官写穿等首笔平仓验证。

### U3-2b 落地记录（2026-08-25，本轮）
- **委员会影子研判会（✅）**：新模块 `backend/services/mlto/committee_shadow.py`——单模型红队（bull 最强论点→bear 最强论点→裁决 JSON：共识方向/置信度/red_flag/决策卡{预算倾向,倾斜,最大未知}）；挂在 thesis shadow 成功落库后（同 4h 节奏）；预算 scope committee=20/天；落库 brain_theses(source=committee_shadow，含当前 thesis_id 关联)；**只写日志与 brain 表，绝不写控制面**。验证：py_compile+import ✓，生效需下次重启。
- 校准对拍基础数据自此积累：决策卡方向 vs thesis 方向 vs 事后因子路由方向，供"各 agent 校准度显著优于随机才转正"验收。

### U5 + U6 落地记录（2026-08-25，本轮）
- **U5 仓位官折扣层（✅ 计算层）**：新模块 `backend/services/mlto/sizing_overlay.py`——V2 §7 公式纯函数实现（final = kelly × 共识折扣 × 信用分 × regime适配 × 相关性惩罚 × 波动率缩放 × 委员会hint），7 项单测全绿（test_sizing_overlay.py）。**热路径接线待下一批谨慎接入**（multi_symbol_kelly/midlong tranche/scalp size，开关 SIZING_OVERLAY_ENABLED）。
- **U6 元学习最小闭环（✅）**：复盘官教训原因 → 研究任务种子（regime_misjudge→regime_recalibration / factor_decay→factor_recheck / execution_slippage→execution_tuning / liquidity→liquidity_guard / discipline→discipline_review），去重后落 brain_research_tasks（pending）——"该学什么"由亏损归因驱动的研究队列自此有真实来源；下一步由调度器消费接 evolution_scheduler 算力。
- 验证：py_compile ✓；sizing 7 单测 ✓。生效需下次重启（无副作用）。

---

## 附录 C：执行收尾交接（2026-08-25，U0-U6 代码主体完成）

### 交付总览（10 个 commit，分支 fusion-20260823）
891135a U0 → fc97467/cfaef25/df93b2e U1 → 3fa5a22 U2 → c330469/96b1c51 U3 → 4ffb53f U4 → 79af7d9 U3-2b → 3d28536 U5/U6

### 剩余三项（按顺序执行，均需重启后运行时数据）
1. **重启加载**：受控重启后 boot_git_hash 应为 3d28536（当前进程为 c330469 之前批次）。重启后验收（日志关键词）：`[ThesisShadow]`、`[CommitteeShadow] 决策卡`、`[ReviewOfficer]`、`[ReviewOfficer] 研究任务种子`、`[LLM2预算]`（超限时）、KlineAnalyst detail `regime=`、`[FusionAttr] thesis 绑定`。
2. **U5 热路径接线**（审慎，先影子）：消费点 = multi_symbol_kelly 聚合输出、midlong_helpers tranche_margin_pct、scalp size 三处；统一乘 `compute_final_multiplier()`（sizing_overlay.py），开关 SIZING_OVERLAY_ENABLED 默认 false → 影子对拍 1 周后转正。验收：账户周回撤≤5%、MDD/波动率下降。
3. **U6 调度消费**：brain_research_tasks(pending) → evolution_scheduler 竞价分配算力（与固定调度基线对比完成率/晋升率）；学习 ROI 仪表盘接 llm2_report + 研究任务 ROI 回填。

### 运行时验收指标（重启后 1-4 周滚动）
- thesis 方向一致率 >55%（1 周 rolling，U1 验收）；LLM 调用 ≤ 旧时代 20%（llm2_report）；
- 复盘官归因覆盖率 100%（brain_episodes/平仓数）；委员会决策卡与事后方向对拍（校准转正前提）；
- 短线台阶1：TP 命中≥45%、超时≤25%（当前 9.5%/39.6%，退出结构继续调）；
- 终局判据：3 个月滚动、成本后、样本外，混合系统 > frozen baseline。

### 已知未修/遗留（诚实清单）
- U2-1b factor_backtest_scorer 连续口径 fwd_ret close-to-close（改前先影子对拍）；
- U3-1b AI 复审（SCALP_AI_REVERSE_DISABLED=true 保持）：正反证据冲突，等 veto tiered 一周对拍数据再定；
- E21 RR 按打法分层（MR 0.75 / trend 1.2）未实现，FUSION_RR_FLOOR=0.9 保持；
- ROAS 完整问责（decision 绑定回填）待复盘官数据积累后接入 llm_budget_governor。


## 附录 D：影子遗留清理专项（2026-08-25，用户指示）
| 机制 | 处置 | 说明 |
|---|---|---|
| THESIS_SHADOW_*（5 项） | **转正+改名**：THESIS_LLM_ENABLED/MIN_INTERVAL/MAX_PER_DAY/MAX_PER_CYCLE/ON_LIVE（旧名兼容读取） | thesis 已是真实方向门（mid 冲突否决/long standdown，conviction≥60） |
| COMMITTEE_SHADOW_ENABLED | **转正+改名**：COMMITTEE_CONTROL_ENABLED（旧名兼容） | 决策卡 budget_hint/tilt 已进真实控制面（pause 拦开仓/减半/加仓） |
| DRL_SHADOW_MODE=true | **废弃置 false** | DRL 无训练模型长期下线，影子无观察对象 |
| RISK_P3_HARDFAT_SHADOW=false | 保留（已 false） | hardfact 底线并行是风险兜底开关，非空转 A/B |
| source_attribution 信用分 shadow / breaker_shadow | 保留（真实生效） | 来源信用 0/1 直接拒单、出场通道熔断——非 A/B 观察 |
| opencode_shadow_worker / rl_core/shadow / drift_watcher 等 | 保留（独立域） | 属 OpenCode/RL 子系统，未发现"跑几天再执行"空转链 |
**清理原则（此后所有新机制）**：默认直连真实控制面（fail-open/可回滚开关），需要观察期时必须带**到期自动转正或自动废弃**的时间戳，杜绝"不了了之"。
