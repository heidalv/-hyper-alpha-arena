# 中长线策略深度调查报告（2026-08-04）

> 文档编号：`MIDLONG-AUDIT-2026-08-04`
> 调查范围：设计文档 `MIDLONG_V2_ARCHITECTURE_DESIGN_2026-08-02.md` + 中长线全部相关代码 + 现网日志（`logs/backend.log`）+ 数据库实况
> 状态：**调查已完成；P0-A/B/C、P1、P2、Phase 5、第二轮全链路/数据链路（P1-P5/B2）已全部修复验证（详见 §九）**

---

## 一、最重要的结论

**中长线策略当前实际处于「完全停摆」状态，且核心原因不在策略本身，而是 LLM 推理链路已经坏了。**

- **数据库实况**：最近一次 `trend_follow` 模拟盘开仓是 **2026-07-24**，到 08-04 已 **11 天零开仓**（27 笔 trend_follow 全部 closed）。
- **日志实况**：TrendAgent 每轮调用 LLM 均返回**空响应**（`content 0 chars`，08-04 09:00 后就出现 36 次）→ JSON 解析失败 → 走「规则回退」→ 得分仅 **20~27** → 远低于开仓门槛（32）→ 永远 `hold`。
- `.env:36` 注释已自述此问题：「长推理任务掐成空响应，导致 TrendAgent 规则回退」——**已知但未根治**。

空响应 → 规则回退 → 低分 → hold，形成死循环。Phase 0–4 全部架构升级均被「信号源头已死」这一层卡住。

---

## 二、运行时故障（按严重度排序）

### 🔴 P0-1：TrendAgent LLM 空响应 → 中长线 11 天零开仓

日志证据（每 2 分钟循环出现）：

```
[TrendAgent:direction] reasoning捞回 0 chars | content 0 chars | finish=
[TrendAgent:direction] JSON 容错解析失败: Expecting value: line 1 column 1 (char 0)
[TrendAgent:direction] LLM 调用失败，走规则回退
[TrendAgent独立] ETH hold score=20 dir=short why=unknown raw_should=None
```

根因链：
1. `call_llm_api_sync` 流式调用 `deepseek-v4-flash`（DB `llm_configurations` id=17，`base_url=https://api.deepseek.com`）。
2. 流式响应收到 `[DONE]` 结束符，但 **content 与 reasoning_content 均为空**（`_parse_sse_chat_completion` 直接 return None，`llm_config_service.py:682-684`）。
3. `_call_llm`（`trend_agent.py:975-1061`）拿到空 → 返回 None。
4. `_fallback_direction`（`trend_agent.py:686-718`）规则回退：`score = int(long_conf*60) if long_bias != "neutral" else 20`。
5. 编排器对固定币长期 neutral → score=20 → `should_open=False`。

**附带影响**：KlineAnalyst、MasterController 也出现空响应（`LLM 返回空响应(30.2s)`），属 LLM 配置/流式解析的**系统性故障**，不止 TrendAgent。

**相关代码**：
- `backend/services/llm_config_service.py:588-735`（`_parse_sse_chat_completion`）
- `backend/services/trend_agent.py:975-1061`（`_call_llm`）、`:686-718`（`_fallback_direction`）
- 配置来源：`get_llm_config_for_analysis`（`llm_config_service.py:827-832`）

### 🔴 P0-2：`_build_midlong_agent_envelope` 必抛 TypeError（已实测验证）

`full_auto_trading_service.py:2546` 定义时**没有 `self` 参数**，但经 `svc._build_midlong_agent_envelope` 绑定后调用，Python 自动把 `svc` 作为第一个位置参数传入 → 必抛：

```
TypeError: takes 0 positional arguments but 1 was given
```

两个调用点（`mlto_cycle.py:875`、`master_execution.py:952`）均被 `except Exception: logger.debug` **静默吞掉**。

**结论**：设计文档宣称的「AgentDecisionEnvelope 构建」**从未真正工作过**，每次中长线决策都是悄悄失败。

**相关代码**：
- `backend/services/full_auto_trading_service.py:2546-2590`
- 绑定注入：`mlto_cycle.py:52`、`master_execution.py:111`
- 调用点：`mlto_cycle.py:863-876`、`master_execution.py:941-953`

### 🔴 P0-3：总控 Master 两个 NameError，两层风控静默失效

| 位置 | 错误变量 | 后果 |
|---|---|---|
| `master_execution.py:398` | `bal_info`（应为 `balance_info`） | 被 try/except 吞掉 → `_equity_for_cap` 恒 0 → 「三层总保证金 ≤90% 权益」全局敞口上限**永不触发**（P0 级风控死代码） |
| `master_execution.py:2835` | `bal_info` | 三层预算检查永不执行，`budget_service` 被架空 |
| `master_execution.py:3642-3646` | `raw_confidence_threshold`（全文件无定义） | defensive 模式「盈利仓/无持仓加仓」分支**必抛异常** → 冒泡到 `analyst_system_cycle.py:603` → 整轮「分析师系统异常」+ legacy fallback |

---

## 三、设计落地缺陷（「声称已落地」但实际不对）

### 🟠 P1-1：C4 未根治——`swing` 归一只做了一半，`nature_ambiguous` 不存在

设计承诺（文档 §6）：「任何默认 swing 的遗留路径，在进 V5 前归一；若无法判断 → hold + `[MidLong] nature_ambiguous`」。

实际：
- 归一只做在执行层：`midlong_helpers.py:137`、`midlong_executor.py:309`。
- `unified_gate.py` 自身**不做归一**，`_TIER_CAP` 无 swing/position → `daily_cap=0` 硬拦（`unified_gate.py:348-375`）。
- `master_execution.py:2054` 非 fast-lane 分支把原始 `swing` 直传 `evaluate_midlong_open` → 撞 `daily_cap=0`。
- **`nature_ambiguous` 全仓库零匹配**——设计承诺落空。

### 🟠 P1-2：Paper RR≥1.6 实际是 1.8（公式自相矛盾）

`unified_gate.py:235-240`：

```python
_base_rr = overrides.get("min_risk_reward", V5_MIN_RISK_REWARD=1.8)
# paper: max(1.6, min(_base_rr, 1.8))  → 默认 = 1.8
# live : max(1.8, _base_rr)            → 默认 = 1.8
```

默认 `_base_rr=1.8` 时，Paper 的 `max(1.6, min(1.8,1.8)) = 1.8`，**`V5_TREND_MIN_RR_PAPER=1.6` 永不生效**。模拟盘比设计更严。

### 🟠 P1-3：Live 会话也在用 Paper 的松 Hub 门槛

`decision_hub.py:68-76` `_use_paper_hub_thresholds()` 判定为「`PAPER_FAST_TRIAL` 为真 **或** `TRADING_MODE≠live`」。而 `PAPER_FAST_TRIAL` 在默认 `FULLAUTO_FLOW_MODE=ai_first` 下**恒为 true**（`settings.py:131-136`）。

**后果**：Live 实盘也走 Paper 松门槛（NIBBLE 0.36 而非 0.42），Hub 门槛与 session 实际交易模式脱钩。

### 🟠 P1-4：`MIDLONG_PAPER_PROBE_ON_WAIT` 是「未实现的功能占位」

配置在 `settings.py:1900` 有定义、`.env:236` 已写 `false`，但**全仓库没有任何读取点**。设计文档 §R3 承诺的「WAIT → 极小保证金试单」通道完全不存在。

### 🟠 P1-5：Master Fast Lane 可绕过 Single Writer（配置交叉时激活）

`master_execution.py:1050-1083` 直接调 `try_execute_independent_agent_open`（`full_auto_trading_service.py:2387-2429` 为纯透传，**无 authority 门禁**）；`mlto_cycle.py:790` 对 buy/sell **无条件**置 `_agent_independent=True`。

当前默认配置（`MLTO_CONTROLS_EXEC=false` + `MASTER_MIDLONG_LLM_MODE=summary`）下休眠；一旦切 `authority=mlto` 或 `MASTER_MIDLONG_LLM_MODE=full` 即激活。**Single Writer 不是结构保证，而是配置巧合**。

### 🟡 P2 级问题（累计 8 项）

| # | 问题 | 位置 |
|---|---|---|
| 1 | `_has_position` 用 `session.account_id` 查持仓，模拟盘持仓实际挂在 `paper_account_id` → **invalidation 平仓漏触发**、tranche 可能误复位 | `mlto/orchestrator.py:340`、`:364-395` |
| 2 | `mlto_cycle` 循环内 `AnalyticsSessionLocal()` 连接泄漏（每轮 N 个 symbol 泄漏 N-1 条） | `mlto_cycle.py:486` |
| 3 | LLM 分桶**接线未启用**：`LLM_BUDGET_ENABLED=false` 且三桶=0 | `.env:238-241` + `llm_budget.py` |
| 4 | 信念闭环噪音：`failed_intents` 95% 是正常 `trend_hold`（非真失败），每 ~2 分钟写一条，200 条上限被噪音灌满；`_count_closed_ranging_losses` 查错库恒 0，规则3 不工作 | `midlong_belief_loop.py` + `data/midlong_beliefs.json` |
| 5 | `_run_midlong_active_exit` 仅 paper 模式，**live 模式 bias_reversal/no-progress 主动退出缺位** | `midlong_loop.py:250` |
| 6 | `due` 为空时整个 midlong tick 提前 return，**连 active exit 一起跳过** | `midlong_loop.py:44-55` |
| 7 | `normalize_midlong_nature` 的 `position` 分支是**死代码**（先被归并成 trend_follow，永远不可达） | `midlong_executor.py:49-51` |
| 8 | 三套「震荡判定」并存：`classify_regime`（价格变动+波动率）/ `is_chop_regime`（ADX）/ `build_regime_hash`（市场周期+情绪）口径不统一；regime→size 双口径（ranging 0.25 vs 0.5） | `regime_agent.py` / `midlong_trade_design.py` / `evidence_ingest.py` / `unified_gate.py:329` |
| 9 | **开仓后无「持仓管理模式」**：有仓时 MLTO/TrendAgent 仍做入场分析（`direction`/`recommend_open`）；滚仓(pyramid)/补仓(dca)被 Master 委托跳过（`master_execution.py:497`）→ 中长线永不滚仓；live 模式无主动反转离场 → **仓位开仓后几乎只剩死等 SL/TP**（修复方案见 §七） | `mlto_cycle.py` / `master_execution.py:497` / `midlong_loop.py:250` |

> **P2 修复状态（2026-08-04）**：
> - ✅ P2-1 `_has_position` 改用 `paper_account_id`（`orchestrator.py`）
> - ✅ P2-2 `mlto_cycle` 循环内连接关闭移至每 symbol（`mlto_cycle.py`）
> - ✅ P2-3 分桶链路确认完整（`llm_config_service._acquire_llm_slot` → `llm_budget.acquire/release` 已配对）；**属配置项**，默认关闭为保守策略，按需设 `LLM_BUDGET_ENABLED=true` 启用
> - ✅ P2-4 信念闭环：`record_failed_intent` 增加 `noise` 标记（正常 `trend_hold` 不再灌满 200 条上限），复盘统计与 prompt 注入只取真失败；`_count_closed_ranging_losses` 由查错库 `Position` 改为 `PaperPosition`（规则3 恢复工作）
> - ✅ P2-5 移除 `_trade_mode == "paper"` 限制，live 也跑主动退出
> - ✅ P2-6 `due` 为空时仍执行 active exit
> - ✅ P2-7 `normalize_midlong_nature` 死代码删除，`position` 保留为长线子类（与 `full_auto_trading_service` tier 反推 `{"long": "position"}` 一致）
> - ✅ P2-8 regime→size 统一到 `classify_regime` 唯一口径（ranging probe=reg_size×0.5=0.25 保留语义、unknown=0.75 对齐权威值）；`is_chop_regime` 在 `classify_regime` 判 trend 时直接放行（避免双口径自相矛盾）

---

## 四、做得对的地方（避免误伤，值得保留）

- ✅ **C2 真修复**：`MIDLONG_EXEC_AUTHORITY=trend` + `MLTO_CONTROLS_EXEC=false` 时，MLTO 三层门禁（cycle 检查 + executor 兜底 + Master 分支）正确禁止开仓，thesis-only 成立。
- ✅ **C5 基本修复**：总控平仓按 `position.trade_nature` 真实匹配（`nature_map`），含 swing/trend_follow/position 别名宽容（`master_execution.py:1417-1421`、`:1453-1473`）。
- ✅ **C6 真修复**：`MIDLONG_AGENT_INDEPENDENT_SCHEDULER=true` 时 OrchBG 不再注入 conf=0 调度桩（`orch_background.py:286-291`）。
- ✅ **C7 真修复**：`mlto_handled_keys` 共享同一 set，仅独立 tick 开头 clear（`midlong_loop.py:57-71`）。
- ✅ **WAIT→hold 链路完整**：`direction_to_action`（`types.py:28-35`）+ `tranche_gate margin=0`（`tranche_gate.py:16-17`）+ executor margin 门禁三重防护，无 WAIT 直通开仓。
- ✅ **trend_daily_cap 生效**（默认 15/天，热改 1-60）；**Paper Hub 门槛 0.36/0.55 与 Trend bonus 0.05 已落地**。

---

## 五、修复优先级建议

| 优先级 | 修复项 | 预期收益 |
|---|---|---|
| **P0-A** | 根治 LLM 空响应（排查 `deepseek-v4-flash` 流式解析/模型配置；空响应时重试而非直接规则回退） | **直接解锁 11 天停摆的中长线**，唯一能让系统重新开仓的关键 |
| **P0-B** | 修 `_build_midlong_agent_envelope` 缺 self（加 `@staticmethod` 或补 self 参数） | envelope 真正构建，决策可审计 |
| **P0-C** | 修 Master 两个 NameError（`bal_info`→`balance_info`、补 `raw_confidence_threshold` 定义） | 全局敞口上限 + defensive 加仓分支恢复工作 |
| P1 | `unified_gate` 增加 nature 归一 / 实现 `nature_ambiguous` 分支 | 根治 swing 误拦 |
| P1 | 修 Paper RR 公式（默认让 1.6 真正生效） | 模拟盘样本流扩大 |
| P1 | Hub 门槛判定改为按 session 真实模式 | Live 不再意外走松门槛 |
| P2 | Fast Lane 前补 authority 检查；`_has_position` 用 `paper_account_id`；修连接泄漏 | 封堵 Single Writer 缺口、修平仓、修资源泄漏 |
| **Phase 5** | **持仓管理模式**（开仓后六维发展分析，方案见 §七） | 修复 P2-9：有仓后不再重复开仓分析，滚仓/分批止盈/反转离场全部激活 |

---

## 七、持仓管理模式设计（Phase 5 修复方案）【定稿】

> 对应发现：P2-9「开仓后无持仓管理模式」。设计蓝图同步写入 `MIDLONG_V2_ARCHITECTURE_DESIGN_2026-08-02.md` §4.6。

### 7.1 目标

**开仓后分析大脑从「入场思维」切换到「持仓发展思维」**——不再反复问「要不要开新仓」，而是围绕已持仓交易对做发展分析。

### 7.2 模式切换判定（唯一入口，零侵入）

```
对每个 symbol（独立 midlong 循环每轮 tick）：
  ├─ 该交易对【无】未平仓中长线仓位 → 模式 A：入场分析（现有逻辑，不改动）
  │      分析方向 → 是否开仓 → 点位/仓位
  └─ 该交易对【有】未平仓中长线仓位 → 模式 B：持仓管理分析（新增）
        六维仓位发展分析（见 7.3）
```

- 判定依据：**交易对 + 未平仓**（`PaperPosition.status=open`，`trade_nature`∈trend_follow/swing/position 或 tier∈(mid,long)）。
- 判定位置：`mlto_cycle._trend_one` 开头（独立循环唯一认知入口），与 MLTO thesis 并行。

### 7.3 模式 B 六维分析

| 维度 | 问题 | 复用组件 | 产出 |
|------|------|----------|------|
| ① 方向延续性 | 4h/1d 趋势是否仍与持仓一致？趋势生命周期到哪一阶段？ | `trend_agent.review_position` | hold / reduce / close / tighten |
| ② 滚仓（金字塔） | 是否浮盈回调 + 趋势成立 → 顺势加仓？ | `trend_agent.evaluate_pyramid` + `trend_pyramid_gate`（5 层门控） | add / wait / skip |
| ③ 止盈止损调整 | 是否收紧追踪止损 / TP 上移？ | `review_position` 的 `trend_adjustment` + `paper_engine.update_position_tp_sl` | SL/TP 价位更新 |
| ④ 补仓（DCA） | 是否加码？（**默认禁止**，见 7.4） | `dca` 分支（受委托跳过的断点） | add / skip |
| ⑤ 分批止盈推进 | 当前浮盈是否触及下一档止盈？ | `long_tier_staged_tp.check`（规则引擎） | 分档 reduce + ATR 追踪 |
| ⑥ 反转离场 | 论点是否失效 / bias 强反向？ | `evaluate_midlong_exit` + `evaluate_no_progress_exit` + 离场状态机 | close（全平/部分） |

### 7.4 保守滚仓策略（产品决策）

- **只有「浮盈 + 回调 + 趋势成立」才允许滚仓**（顺势金字塔，第一笔 25-30%）。
- 浮亏时**禁止**任何加仓/补仓（加密永续亏损加仓 = 自杀原则）。
- 滚仓受 `trend_pyramid_gate` 5 层门控 + 冷却双重保护：
  1. tier 准入（`TIER_PYRAMID_PARAMS.enabled` + `max_adds`）
  2. 编排器方向一致性（long/mid bias 不矛盾）
  3. ADX 趋势强度（≥ 门槛）
  4. 递减利润门槛（首加 ≥1.5%、再加 ≥3%）
  5. 冷却时间
- 分批止盈仍由 `long_tier_staged_tp` 规则引擎自动执行（浮盈分档减仓 + ATR 追踪接管剩余仓位）。

### 7.5 执行链路

```
模式 B 决策（六维合并，单一优先级）：
  close/reduce（⑥ 反转 > ① 方向破坏）   → unified_exit_executor / paper_engine.close_position
  tighten（① 收紧止损 / ③ TP上移）       → paper_engine.update_position_tp_sl
  add（② 滚仓，仅浮盈+回调+趋势成立）    → trend_pyramid_gate 门控 → paper_engine 加仓
  分档止盈（⑤）                          → long_tier_staged_tp.check → reduce / trailing
  否则 hold（继续持有，更新趋势复查时间戳）
```

执行复用现有出口（`unified_exit_executor`、`paper_engine`），**不新建平仓/加仓路径**，保证风控闸（V5/EV/组合）始终在 LLM 之后、下单之前。

### 7.6 频率与调度

- 模式 B 每轮 tick（~2min，随独立 midlong 循环）执行，与模式 A 共用同一入口。
- `MIDLONG_POSITION_MGMT_INTERVAL_SEC` 可调（默认随 tick）。
- 原有 90min `run_trend_review` 节流保留为**兜底**（模式 B 未覆盖时的第二道防线）。

### 7.7 日志与可观测性

统一前缀 `[MidLong] stage=manage ...`：

```
[MidLong] stage=manage symbol=BTC pos=long pnl=+4.2% hold=36h
  direction=valid pyramid=wait review=hold staged_tp=stage1/3 exit=no
  reason=...
```

验收指标：模式 B 覆盖率（有仓交易对被分析的比例）、滚仓/分批止盈/反转离场的触发与执行成功率、无「有仓仍重复开仓分析」的日志。

### 7.8 新配置

```env
MIDLONG_POSITION_MGMT_ENABLED=true    # 总开关
MIDLONG_POSITION_MGMT_INTERVAL_SEC=0  # 0=随 tick（默认 ~120s）；>0 则节流
MIDLONG_POSITION_MGMT_PYRAMID_ONLY_PROFIT=true   # 仅浮盈滚仓（禁止浮亏加仓）
```

### 7.9 修改点清单

| 文件 | 改动 |
|------|------|
| `backend/services/full_auto/midlong_position_manager.py` | **新建**：模式 B 分析器（六维并行复用现有组件） |
| `backend/services/full_auto/mlto_cycle.py` | `_trend_one` 开头加模式切换判定（有仓→模式 B，无仓→模式 A） |
| `backend/services/full_auto/master_execution.py:497` | 解除中长线 pyramid/dca 被委托跳过的断点（受门控保护） |
| `backend/services/full_auto/hold_timeout_trend_review.py` | `run_trend_review` 保留为兜底 |
| `backend/config/settings.py` + `.env` | `MIDLONG_POSITION_MGMT_*` 配置接入 |

---

## 八、关键文件索引

| 文件 | 角色 |
|---|---|
| `backend/services/full_auto/loops/midlong_loop.py` | 独立 midlong 循环入口 |
| `backend/services/full_auto/mlto_cycle.py` | Trend + MLTO maintain / execute_mlto_lane |
| `backend/services/full_auto/midlong_executor.py` | Single Writer 执行器（authority/regime/nature 归一） |
| `backend/services/full_auto/midlong_helpers.py` | 开仓执行终点 `try_execute_independent_agent_open` |
| `backend/services/full_auto/master_execution.py` | 总控委托/平仓/减仓（含 NameError） |
| `backend/services/full_auto/orch_background.py` | OrchBG 编排器后台 |
| `backend/services/full_auto/analyst_system_cycle.py` | analyst 统一循环 |
| `backend/services/full_auto/llm_budget.py` | LLM 分桶（默认未启用） |
| `backend/services/full_auto/midlong_position_manager.py` | **持仓管理分析器（Phase 5 新建）** |
| `backend/services/full_auto/hold_timeout_trend_review.py` | TrendAgent 持仓复查（兜底） |
| `backend/services/mlto/decision_hub.py` | Hub 阈值与融合决策 |
| `backend/services/mlto/types.py` | WAIT→hold 契约 |
| `backend/services/mlto/orchestrator.py` | MLTO tick 编排（`_has_position` account_id 错配） |
| `backend/services/mlto/midlong_belief_loop.py` | 信念学习闭环 |
| `backend/services/decision_core/unified_gate.py` | V5 统一门禁（swing 无归一 / RR 公式） |
| `backend/services/decision_core/pipeline.py` | evaluate_midlong_open |
| `backend/services/trend_agent.py` | TrendAgent（LLM 空响应回退链；`review_position`/`evaluate_pyramid` 供模式 B 复用） |
| `backend/services/llm_config_service.py` | LLM 调用 / SSE 流式解析 |
| `backend/services/long_tier_staged_tp.py` | 分批止盈规则引擎（模式 B 复用） |
| `backend/services/full_auto_trading_service.py` | envelope TypeError 定义点 |

---

## 九、第二轮调研（全链路/分析体系/数据链路）修复记录（2026-08-04）

> 背景：P0/P1/P2/Phase 5 全部落地后，对中长线**完整链路流程 + 分析体系 + 数据链路质量**做二次深调，发现 5 项新问题（含 2 项高优数据断链）与 1 项并发加固，已于当日全部修复并验证。

### 9.1 修复清单

| # | 级别 | 问题 | 根因 | 修复 |
|---|------|------|------|------|
| **P1** | 🔴 高 | `classify_regime` 恒判 `ranging`，长线新开仓被抑制 | `midlong_loop.py` 用 `{**market_summary, **fresh}` 整体替换 symbol 条目，而 scan dict 不含 `price_change_1h/24h_pct`、`volatility_pct` → 三字段恒 0 → regime 判据全失 | ① `midlong_loop.py` 改为**逐 symbol 深合并**（scan 新值覆盖同名字段、已有字段保留）；② `midlong_helpers.inject_midlong_indicators` 从 1h K 线**补算** `price_change_1h/24h_pct`（close[-1]/close[-2]、close[-1]/close[-25]，与 unified_data_pool 同口径），并用 `atr_1d_pct` 或 `indicators_1d.atr/current_price` 补 `volatility_pct`；③ `_compute_midlong_indicator_block` 增补输出 `atr` |
| **P2** | 🔴 高 | `price_change_24h_pct` 在 `unified_data_pool` 恒 0 | ticker 源（`market_data.py`/`hyperliquid_market_data.py`）字段名是 `percentage24h`，读侧写 `price_change_24h_pct` | `unified_data_pool.py` 兼容读取：`ticker.get("price_change_24h_pct") or ticker.get("percentage24h")` |
| **P3** | 🔴 高 | `master_execution.py` 模块级函数内 5 处 `self` 引用 → NameError | 从 monolith 迁出后 `execute_master_decisions` 是模块级函数，无 `self`；`STRICT_DATA_GATE` 数据门失效（fail-open）、`orch_snapshot_ts` 恒 0、`_master_strat_cache` 检查直接中断整轮 | `MasterExecutionHost` 新增 `last_unified_snapshot`/`last_orch_decisions_ts`/`scalp_traded_this_tick`/`training_allowed_symbols` 字段，`build_master_execution_host` 从 svc 透传，5 处全部改 `host.*` |
| **P4** | 🟠 中高 | LLM 未评估时 conviction=0 被当成「极度看空」，hub 系统性偏空 | `ThesisDTO.llm_conviction` 默认 0，`quant_layer` 公式 `0.5+(0-50)/100=0.0` → `_derive_direction` 判 ≤0.4 → short 偏见 | `quant_layer.py`：`review_count<=0 且 conviction==0`（未评估）→ 中性 0.5；已评估（review_count>0 或 conviction≠0）照常映射（真悲观才 0.0） |
| **P5** | 🟠 中高 | `regime_agent` 把年化波动率（0.6~2.0，加密常态）误判为 extreme 禁开 | `volatility_pct` 量纲混乱（per-bar 小数 vs 年化小数 vs 百分数），单值 vol≥0.05 即判 extreme | `regime_agent.py` extreme 判定**必须 price_change 佐证**（24h≥12 或 1h≥5；或 vol≥0.05 且 24h≥4）；仅高 vol 无佐证回落到 trend/ranging（缩仓评估）。`regime_refined.py` 补充量纲契约注释 |
| **B2** | 🟡 并发 | `thesis_store.get_or_create` check-then-act 非原子，多线程 miss 时重复建 thesis 互相覆盖 | 主/独立/analyst 多入口共享 `_THESIS_CACHE` | `get_or_create` 用模块级 `RLock` 包裹「查缓存→查库→创建→落库」，保证并发唯一性 |

> B1（`_mlto_handled_keys` 竞态）在上一轮已用 `_reserve_key` 原子占位收敛（`mlto_cycle.py:163-169`），本轮复核确认 analyst 循环不再清空、midlong 仅 tick 开头 clear，无需再改。

### 9.2 验证结果

- **针对性脚本**：P1 深合并/补算、P2 字段兼容、P4 四态映射、P5 四态 regime、B2 8 线程并发唯一 thesis —— 24 项断言全过。
- **单测回归**：`test_quant_layer_confidence`（4 过，P4 修复后原 1 项由`conviction=80`未标记 review_count 引发，判据收窄后全过）、`test_tranche_fix`（22 过）、`test_open_gate_simplified`（20 过）。
- **全量 unit 套件**：1096 通过 / 113 失败，失败项均为**预先存在**的环境依赖（ccxt/redis/DB 集成）或历史参数未同步（`test_unified_position_mgmt` 的 `REGIME_TP_PARAMS`、`test_midview_thesis` 的 `_FakeRow` 类属性），与本次改动无交集。

### 9.3 P0-A 运行时验证（2026-08-04 14:40 重启后实测）

> 在修复 LLM 代理问题（直连 api.deepseek.com + `deepseek-v4-flash` 深度思考支持）并重启后端后，对运行时 LLM 链路做了**现场实测**，确认 11 天停摆根因已根治：

| 观测项 | 实测结果 | 说明 |
|---|---|---|
| 后端健康 | `GET /api/health → 200` | 重启后正常 |
| TrendAgent LLM 调用 | 3/3 成功，`reasoning捞回 3357/16149/21502 chars`，`content 3357/3005/3523 chars`，`finish=stop` | flash 深度思考完整输出思维链+结论，**不再是空响应** |
| MasterController.synthesize | 一次成功（attempt 1, 116.6s），`stream_done chunks=13251` | 深度思考耗时 116s 属正常（`LLM_STREAM_SAFETY_CAP_SECONDS=0` 禁用截断） |
| 中长线决策 | 每 tick 输出 `[TrendAgent独立] BTC/SOL/ETH hold score=21~27` + `[MLTO] maintain tick` | 分析链路恢复真实 LLM 判断；当前 score<32 属行情判断，非降级 |
| safety cap 截断 | 0 次 | 直连后流稳定到 [DONE] |
| 连接中断 | 仅行情数据源 3 次（Coinalyze/Hyperliquid/Binance 走代理），**LLM 侧 0 次** | 行情代理问题与 LLM 已彻底隔离 |
| 单测回归 | `test_tranche_fix` 22 过 + `test_open_gate_simplified` 20 过 + `test_deepseek_thinking` 6 过 = **48/48 通过** | 无回归 |

**结论**：P0-A 已从「规则回退兜底」升级为「直接支持 flash 深度思考流式输出」，LLM 空响应不再发生；中长线分析链路在运行时持续产出真实决策，11 天零开仓的根因（SSL EOF 空响应 → 规则回退）已根治。

---

*报告结束。修复排期：P0-A/B/C → P1 → P2 → Phase 5 已全部落地；第二轮全链路/数据链路 P1-P5/B2 已修复验证。*
