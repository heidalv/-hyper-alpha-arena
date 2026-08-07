# 中长线策略体系升级设计文档

> 文档编号：`MIDLONG-ARCH-2026-08-02`  
> 状态：**Phase 0–4 已落地**（2026-08-02）  
> 范围：中长线一体（`trend_follow`）+ 总控 Agent + MLTO Thesis + TrendAgent  
> 不改动原则：短线/中长线**配额解耦**；模拟盘**高成交样本**优先；不复活独立 mid 日配额  
> 当前 Single Writer：`MIDLONG_EXEC_AUTHORITY=trend`（Trend 开仓，MLTO thesis-only）  

---

## 0. 一页纸摘要

当前中长线「不通畅」的根因不是「没跑 AI」，而是：

1. **分析与执行职责重叠**：TrendAgent、MLTO、总控 Master 三套脑并行；Master 对 mid/long **故意不开新仓**，却仍跑完整 LLM → 观感「有决策无成交」。  
2. **开仓主脑未定死**：`MIDLONG_MLTO_CONTROLS_EXEC=false` 注释写「Trend 开仓」，代码上 MLTO 仍可尝试开 → 双路径空转/抢单。  
3. **信号在最后一米夭折**：TrendAgent `score_low` / `should_open=false`；Hub 常停在 `WAIT`（永远不成 buy/sell）；残留 `swing` 会撞上 V5 `daily_cap=0`。  
4. **平仓 nature 错乱**：总控按 `mid/swing` 找仓，实际仓可能是 `trend_follow` → 「找不到匹配仓位」。  
5. **资源互相拖死**：独立循环 + 总控 150s+ 周期抢 LLM/DB。  
6. **开仓后无「持仓管理模式」**：已有仓时 MLTO/TrendAgent 仍重复「入场思维」提问（是否开仓 / 方向），不做围绕仓位的**发展分析**（滚仓、分批止盈推进、止盈止损调整、反转离场），滚仓/补仓在中长线被 Master 委托跳过 → 有仓后几乎只剩死等 SL/TP。

**升级目标（对标业界论文后的产品定稿）**：

> **一条策略脑（Thesis/Trend）+ 一条确定性风控闸 + 总控只做组合监督与平仓协调**；  
> 分析与热路径执行解耦（TiMi）；Manager–Analyst 单向汇流（FINCON）；NO_TRADE 合法且不可被偷偷改成成交（Circuit/TradingAgents）。

---

## 1. 问题基线（来自现网日志 + 代码）

### 1.1 现网会话观察（2026-08-02，`fa_10d44c724e`）

| 观察 | 证据 |
|------|------|
| 中长线只扫固定币 | `固定币种=['ETH','SOL','BTC']` |
| TrendAgent 全 hold | `score_low(31<38)` / `llm_should_open_false` |
| 编排器 long 空桩 | `L=M=S=neutral(0%)` + `TrendAgent 调度桩 conf=0` |
| 总控想平 KAITO 失败 | `close[mid/swing] 找不到匹配仓位` |
| 主循环极慢 | 单周期 ~152s，LLM 忙 |
| 有 thesis 无成交 | MLTO `maintain tick … slot=create`，未见稳定开仓成功 |

### 1.2 代码级矛盾清单

| ID | 矛盾 | 位置 |
|----|------|------|
| C1 | Master 委托跳过 mid/long 新开，但仍做深度 LLM | `master_execution.py` MidLongLane |
| C2 | `MLTO_CONTROLS_EXEC=false` 时 Trend 可开，MLTO 仍执行开仓分支 | `mlto_cycle.py` |
| C3 | Hub `WAIT` → `direction_to_action=hold` + tranche margin=0 | `types.py` / `tranche_gate.py` |
| C4 | `swing` 无 `_TIER_CAP` → V5 `daily_cap=0` 硬拦 | `unified_gate.py` |
| C5 | 平仓按声明 nature 过滤，不按仓位真实 nature | `master_execution.py` |
| C6 | OrchBG 用调度桩 conf=0 冒充 Trend 信号 | `orch_background.py` Fix18 |
| C7 | analyst 每轮清空 `mlto_handled_keys` → 与独立循环竞态 | `analyst_system_cycle.py` |

---

## 2. 文献与业界设计调研

### 2.1 核心论文 / 框架（可引用）

| 来源 | 年份 | 关键主张 | 对本项目的可迁移点 |
|------|------|----------|-------------------|
| **TradingAgents** (Xiao et al., arXiv:2412.20138) | 2024–25 | 仿交易公司：分析师→多空辩论→Trader→风险辩论→最终决策；共享状态机 | 角色分工清晰；**风险层在 Trader 之后**；记忆反思闭环 |
| **FINCON** (NeurIPS 2024) | 2024 | Manager–Analyst **层级汇流**；概念化言语强化（verbal reinforcement）；削减无意义 peer 通信 | **总控不应与分析师抢执行**；信念（belief）向下选择性广播 |
| **TiMi** (arXiv:2510.04787) | 2025 | **策略深度与分钟级部署解耦**；Policy→Optimize→Deploy；加密+股票验证 | 慢循环写 thesis/策略，快循环只执行已编译意图；降 LLM 热路径占比 |
| **FS-ReasoningAgent** (arXiv:2410.12464) | 2024 | 事实 vs 主观分层推理；加密交易；反思自适应 | 量化 brief / LLM thesis 分轨；震荡市降低主观权重 |
| **Orchestration Framework for Financial Agents** (arXiv:2512.02227) | 2025 | Algo→Agentic；regime 调仓位与门槛；确定性风控 | Regime 管 size/门槛，不单靠 LLM |
| **Regime-Aware Bot / Merehead 实践** | 2025–26 | Regime 作路由层；专家按 regime 加权；风控纯代码 | 与现有 `regime_agent` + Hub 权重对齐 |
| **Circuit Framework**（开源实践） | 2025 | 共享不可变 Snapshot；Bull/Bear→Trader→**Deterministic Risk Gate**；`NO_TRADE` 不可变成交 | Proposal 契约 + V5 确定性闸；禁止 WAIT 被静默改成开仓 |

### 2.2 从文献抽出的「不可违背原则」（融入本项目）

| 原则 ID | 原则 | 文献依据 | 本项目落点 |
|---------|------|----------|------------|
| R1 | **分析与执行解耦** | TiMi | Thesis/Trend = 慢认知；`try_execute` = 热路径 |
| R2 | **单一执行权威（Single Writer）** | FINCON Manager–Analyst | 同一时刻只有一个组件有权发开仓 Proposal |
| R3 | **NO_TRADE / WAIT 合法且不可偷换** | Circuit / TradingAgents | WAIT≠「偷偷缩仓开」；要么明确 probe 策略，要么 hold |
| R4 | **确定性风控在 LLM 之后、下单之前** | TradingAgents Risk / Circuit Gate | 保留并强化 V5 + EV + 组合闸；算术不用 LLM |
| R5 | **Regime 路由策略，而非装饰** | RegimeRisk / Merehead | Hub 权重与是否允许 trend 开仓随 regime 变 |
| R6 | **层级通信单向汇流** | FINCON | Analyst→Manager 报告；Manager 不反向抢开仓 |
| R7 | **事后概念强化 / 反思** | FINCON / TradingAgents Reflector | 复用学习闭环，向 Hub 权重与门槛回灌 |
| R8 | **高周期定方向、低周期择时** | MTF 经典 + 本项目 mid_view | Trend 定 dir；mid_view 只调 size/时机，不另开独立配额 |

---

## 3. 目标架构（Hyper-Alpha-Arena MidLong v2）

### 3.1 角色重定义

```
┌─────────────────────────────────────────────────────────────┐
│  L0 数据层：统一 Market Snapshot（K线/资金费率/OI/编排偏向）   │
└────────────────────────────┬────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  L1 认知慢循环（独立 midlong，~120s）                          │
│   ├─ TrendAgent：方向 + should_open + 建议 TP/SL（Trader）    │
│   └─ MLTO Thesis：证据链 + mid_view 择时 + Hub 融合分         │
│         ★ 二者产出「意图包 Intent」，不直接下单（见模式开关）   │
└────────────────────────────┬────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  L2 MidLong Executor（唯一开仓 Writer）                        │
│   Intent → Nature归一(trend_follow) → Proposal                 │
│   → open_gate → V5 → EV → MTF → Portfolio → 1.5%风险硬顶 → 单 │
└────────────────────────────┬────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  L3 总控 Master（组合监督，非中长线开仓脑）                     │
│   · 不做 mid/long 新开（保持委托）                             │
│   · 负责：组合级减仓/平仓/风控覆盖；按【仓位真实 nature】匹配   │
│   · 中长线深度 LLM：降频或改为「只读 Intent 摘要」              │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 开仓主脑模式（必须二选一，默认推荐 A）

| 模式 | 配置 | 谁发 Intent | 谁写仓 | 适用 |
|------|------|-------------|--------|------|
| **A. Thesis-Led（推荐）** | `MIDLONG_EXEC_AUTHORITY=mlto` | MLTO Hub 必须 NIBBLE/BUILD；Trend 分作信号输入 | MidLong Executor | 要「论点可审计」 |
| **B. Trend-Led** | `MIDLONG_EXEC_AUTHORITY=trend` | TrendAgent `should_open`；MLTO 只更新 thesis 禁止开仓 | MidLong Executor | 要「更简单、更快样本」 |
| ~~C. Dual~~ | 禁止 | — | — | **现网问题源，废除** |

兼容旧开关映射：

- `MIDLONG_MLTO_CONTROLS_EXEC=true` → 映射为模式 A  
- `=false` → 映射为模式 B，且 **强制关闭** MLTO 开仓分支（修 C2）

### 3.3 Intent 契约（结构化，防幻觉）

```text
MidLongIntent {
  symbol: str
  direction: long|short|neutral
  action: buy|sell|hold          # hold 对应 NO_TRADE
  authority: mlto|trend
  confidence: 0-100
  hub_action?: WAIT|NIBBLE|BUILD
  hub_adj?: float
  tp_pct, sl_pct: float          # 必须满足 V5_TREND_MIN_RR*
  nature: "trend_follow"         # 强制，禁止 swing 进入执行
  tier: "long"
  thesis_id?: str
  cited_fact_ids?: list
  regime: trend|ranging|extreme|unknown
  size_hint_mult: float          # 仅提示，最终以风控为准
  snapshot_id: str               # 同 Circuit：绑定当时行情快照
}
```

规则：

- `action=hold` **不得**被下游改成 buy/sell（R3）  
- `nature` 进入 Executor 前一律 `normalize → trend_follow`（修 C4）  
- Paper 可开 `probe` 子类型：Hub=WAIT 且 `MIDLONG_PAPER_PROBE_ON_WAIT=true` 时，允许 **极小保证金试单**（单独标签，便于学习），Live 默认关  

---

## 4. 关键流程设计（目标态 tick）

### 4.1 独立 midlong 循环（唯一认知入口）

```
every ~120s:
  1. 取 fixed symbols（CORE_BASKET ∪ session 固定币 − auto_coin）
  2. 构建不可变 Snapshot（注入 1h/4h/1d/1w）
  3. 并行：
       TrendAgent.analyze → TrendSignal
       MLTO.run_tick → Thesis + HubDecision
  4. fuse_to_intent(mode A or B) → MidLongIntent
  5. if action != hold:
       MidLongExecutor.execute(intent)  # 单 Writer
  6. 主动退出扫描（论点失效 / 无进展）
  7. 统一日志：[MidLong] stage=... symbol=... result=...
```

### 4.2 fuse_to_intent（模式 A：Thesis-Led）

```
inputs: TrendSignal T, Hub H, Thesis Th
if H.direction == neutral or H.action == WAIT:
    if PAPER and PAPER_PROBE_ON_WAIT and Th.recommend_open:
        return probe_intent(size_mult=0.15)   # 可选
    return hold
if not Th.recommend_open: return hold
if T.score < paper_floor and not PAPER_FAST:  # 软参考，不双杀
    size_mult *= 0.5
require RR(tp,sl) >= V5_TREND_MIN_RR*
return buy/sell intent(nature=trend_follow)
```

### 4.3 fuse_to_intent（模式 B：Trend-Led）

```
if not T.should_open or T.direction neutral: return hold
if T.score < effective_floor: return hold
Hub/Thesis 只写入审计字段，不否决（除非 open_gate 数据/固定币底线）
return intent from T (nature=trend_follow)
```

### 4.4 总控 Master（降负 + 修平仓）

| 能力 | 目标态 |
|------|--------|
| mid/long **新开** | 继续委托跳过（R6） |
| mid/long **深度 LLM** | `MASTER_MIDLONG_LLM_MODE=summary`：只读 Intent/Thesis 摘要，不重跑 Swing/Trend |
| **平仓/减仓** | 按 `position.trade_nature` + symbol 匹配；废除「必须 mid/swing」过滤（修 C5） |
| 组合覆盖 | 仅在回撤/敞口超限时强制 reduce/close（FINCON 双层风控的「日度风险」层） |

### 4.5 OrchBG 与调度桩

- **删除或降级** `TrendAgent 调度桩 conf=0` 写入编排器（修 C6）  
- Orch long bias 改为：优先读最近一次真实 TrendSignal / Hub direction；无数据则标 `unknown`，禁止伪装 0% 中性权威  

### 4.6 持仓管理模式（开仓后分析切换）【新增 · Phase 5】

> 目标：**开仓后分析大脑从「入场思维」切换到「持仓发展思维」**——不再反复问「要不要开新仓」，而是围绕已持仓交易对做发展分析。

#### 4.6.1 背景与现状差距

调查（2026-08-04，`MIDLONG-AUDIT-2026-08-04`）确认以下断点：

| 能力 | 现状 | 问题 |
|------|------|------|
| MLTO thesis（~2min/轮） | 有仓时仍问 `direction` / `recommend_open` | 重复入场分析，不感知仓位发展 |
| TrendAgent 持仓复查 | `review_position`（hold/reduce/close/tighten） | 90min 节流，不含滚仓/分批止盈推进 |
| 滚仓（pyramid） | `evaluate_pyramid` + `trend_pyramid_gate` 存在 | **中长线被 Master 委托跳过**（`master_execution.py:497`）→ 永不滚仓 |
| 分批止盈 | `long_tier_staged_tp` 规则引擎 | 纯规则，不感知趋势反转 |
| 补仓（DCA） | 分支存在 | 同样被委托跳过 |
| 反转离场 | `_run_midlong_active_exit`（bias_reversal） | **仅 paper 模式**，live 缺位 |

#### 4.6.2 模式切换判定（唯一入口，零侵入）

独立 midlong 循环每轮 tick，对每个交易对先做判定：

```
对每个 symbol：
  ├─ 该交易对【无】未平仓中长线仓位 → 模式 A：入场分析（现有逻辑，不改动）
  │      分析方向 → 是否开仓 → 点位/仓位
  └─ 该交易对【有】未平仓中长线仓位 → 模式 B：持仓管理分析（新增）
        六维仓位发展分析（见 4.6.3）
```

- 判定依据：**交易对 + 未平仓**（`PaperPosition.status=open`，按 `trade_nature` 属于 trend_follow/swing/position 或 tier∈(mid,long)）。
- 判定位置：`mlto_cycle._trend_one` 开头（独立循环唯一认知入口），与 MLTO thesis 并行。

#### 4.6.3 模式 B 六维分析（持仓管理分析）

| 维度 | 问题 | 复用组件 | 产出 |
|------|------|----------|------|
| ① 方向延续性 | 4h/1d 趋势是否仍与持仓一致？趋势生命周期到哪一阶段？ | `trend_agent.review_position` | hold / reduce / close / tighten |
| ② 滚仓（金字塔） | 是否浮盈回调 + 趋势成立 → 顺势加仓？ | `trend_agent.evaluate_pyramid` + `trend_pyramid_gate`（5 层门控） | add / wait / skip |
| ③ 止盈止损调整 | 是否收紧追踪止损 / TP 上移？ | `review_position` 的 `trend_adjustment` + `paper_engine.update_position_tp_sl` | SL/TP 价位更新 |
| ④ 补仓（DCA） | 是否加码？（**默认禁止**，见 4.6.4） | `dca` 分支（受委托跳过的断点） | add / skip |
| ⑤ 分批止盈推进 | 当前浮盈是否触及下一档止盈？ | `long_tier_staged_tp.check`（规则引擎） | 分档 reduce + ATR 追踪 |
| ⑥ 反转离场 | 论点是否失效 / bias 强反向？ | `evaluate_midlong_exit` + `evaluate_no_progress_exit` + 离场状态机 | close（全平/部分） |

#### 4.6.4 保守滚仓策略（产品决策）

- **只有「浮盈 + 回调 + 趋势成立」才允许滚仓**（顺势金字塔，第一笔 25-30%）。
- 浮亏时**禁止**任何加仓/补仓（加密永续亏损加仓 = 自杀原则）。
- 滚仓受 `trend_pyramid_gate` 5 层门控 + 冷却双重保护：
  1. tier 准入（`TIER_PYRAMID_PARAMS.enabled` + `max_adds`）
  2. 编排器方向一致性（long/mid bias 不矛盾）
  3. ADX 趋势强度（≥ 门槛）
  4. 递减利润门槛（首加 ≥1.5%、再加 ≥3%）
  5. 冷却时间
- 分批止盈仍由 `long_tier_staged_tp` 规则引擎自动执行（浮盈分档减仓 + ATR 追踪接管剩余仓位）。

#### 4.6.5 执行链路

```
模式 B 决策（六维合并，单一优先级）：
  close/reduce（⑥ 反转 > ① 方向破坏）   → unified_exit_executor / paper_engine.close_position
  tighten（① 收紧止损 / ③ TP上移）       → paper_engine.update_position_tp_sl
  add（② 滚仓，仅浮盈+回调+趋势成立）    → trend_pyramid_gate 门控 → paper_engine 加仓
  分档止盈（⑤）                          → long_tier_staged_tp.check → reduce / trailing
  否则 hold（继续持有，更新趋势复查时间戳）
```

执行复用现有出口（`unified_exit_executor`、`paper_engine`），**不新建平仓/加仓路径**，保证风控闸（V5/EV/组合）始终在 LLM 之后、下单之前。

#### 4.6.6 频率与调度

- 模式 B 每轮 tick（~2min，随独立 midlong 循环）执行，与模式 A 共用同一入口。
- `MIDLONG_POSITION_MGMT_INTERVAL_SEC` 可调（默认随 tick）。
- 原有 90min `run_trend_review` 节流保留为**兜底**（模式 B 未覆盖时的第二道防线）。

#### 4.6.7 日志与可观测性

统一前缀 `[MidLong] stage=manage ...`：

```
[MidLong] stage=manage symbol=BTC pos=long pnl=+4.2% hold=36h
  direction=valid pyramid=wait review=hold staged_tp=stage1/3 exit=no
  reason=...
```

新增验收指标：模式 B 覆盖率（有仓交易对被分析的比例）、滚仓/分批止盈/反转离场的触发与执行成功率、无「有仓仍重复开仓分析」的日志。

---

## 5. Hub / 门槛 / Regime（样本友好且可校准）

### 5.1 Hub 阈值（AI-first，对齐文献「可执行」）

现状（AI-first long）：WAIT 0.30 / NIBBLE 0.42 / BUILD 0.62。  
日志显示 adj 常在「开不开之间」→ 长期 WAIT。

**目标调整（仅 Paper 默认更松，Live 保持）：**

| 档 | Paper | Live |
|----|-------|------|
| WAIT 上限（低于则 WAIT） | 0.28 | 0.30 |
| NIBBLE | **0.36** | 0.42 |
| BUILD | 0.55 | 0.62 |

另增：`MIDLONG_HUB_TREND_SIGNAL_BONUS`——当 Trend `should_open` 且方向与 Hub 一致时，`adj += 0.05`（封顶 1.0），实现 Merehead「专家按 regime/一致性加权」的轻量版。

### 5.2 TrendAgent Paper 地板

- Paper 有效分：由 `max(30, V5_TREND_FOLLOW_MIN_CONFIDENCE-12)` 改为可配 `TREND_PAPER_SCORE_FLOOR` 默认 **32**（现日志大量 31–33 卡死）  
- 若 `llm_should_open=true` 且 score ≥ floor−5：允许开，但 `size_mult×0.6`（信任 LLM 意图、用仓位表达不确定）——对齐 FS「主观与事实分权」  

### 5.3 Regime 路由（R5）

| Regime | 中长线策略 |
|--------|------------|
| trend | 允许 NIBBLE/BUILD；size×1.0 |
| ranging | 默认禁止新开 trend_follow；除非 `MIDLONG_ALLOW_RANGE_PROBE=true`（Paper）且 size×0.25 |
| extreme | 禁止新开；只许减仓（已有） |
| unknown | size×0.5，门槛 +0.05 adj |

与短线 `regime_agent` **共用分类器**，避免两套 regime 打架。

### 5.4 V5 / nature（保持与短线解耦）

- 执行层 **强制** `trade_nature=trend_follow`  
- RR：Live ≥1.8，Paper ≥1.6（已落地的 V5_TREND_*）  
- 日配额：仅 `trend_daily_cap`（可继续加大样本）  
- **禁止**再引入 swing 日配额（产品决策）  

---

## 6. 与短线的边界（防再次搅浑）

| | 短线 | 中长线 |
|--|------|--------|
| Writer | ScalpExecutionLane | MidLongExecutor |
| Nature | scalp/intraday | trend_follow |
| 配额键 | scalp_daily_cap | trend_daily_cap |
| 主循环 | 独立 scalp 循环 | 独立 midlong 循环 |
| Master | 跳过新开 | 跳过新开；可监督平仓 |
| RR | V5_SCALP_* | V5_TREND_* |

任何「默认 swing」的遗留路径：在进 V5 前归一；若无法判断 → hold + 日志 `[MidLong] nature_ambiguous`。

---

## 7. 日志与可观测性（一次性治「看不懂」）

统一前缀与阶段枚举：

```
[MidLong] tick=<n> stage=snapshot|trend|mlto|fuse|gate|exec|exit
  symbol=BTC authority=mlto action=buy|hold reason=...
  hub=NIBBLE adj=0.44 trend_score=41 rr=2.1
  gate=PASS|BLOCK:<rule> order_id=...
```

必打点：

1. fuse 结果（为何 hold）  
2. V5 rule  
3. 总控跳过新开（一行 debug 即可，避免刷屏）  
4. 平仓匹配失败时打印「请求 nature vs 仓位 natures」  

验收脚本：`scripts/midlong_flow_audit.py`（新建）统计近 N 小时：Intent 数、hold 原因分布、开仓成功率、Master 空转耗时。

---

## 8. 实施计划（分阶段、可回滚）

### Phase 0 — 止血（0.5–1 天，不改策略哲学）✅ 已落地

1. **修 C2**：`MLTO_CONTROLS_EXEC=false` / `authority!=mlto` 时 MLTO **禁止**开仓（thesis-only）  
2. **修 C4**：`midlong_helpers` / `execute_midlong_open` 入口 `nature → trend_follow`  
3. **修 C5**：Master 平仓允许 swing/trend_follow/position 别名匹配  
4. **修 C6**：独立调度开启时去掉 Orch mid/long conf=0 调度桩  

回滚：各自 flag / 恢复旧分支。

### Phase 1 — Single Writer（1–2 天）✅ 已落地

1. `MIDLONG_EXEC_AUTHORITY=trend|mlto`（默认 `trend`）  
2. `backend/services/full_auto/midlong_executor.py`（Intent + Writer）  
3. 统一 `[MidLong]` 日志；`MASTER_MIDLONG_LLM_MODE=summary` 主循环减负  
4. Paper：`TREND_PAPER_SCORE_FLOOR=32` + `TREND_TRUST_SHOULD_OPEN_SOFT`  

推荐默认：**先 `trend`（模式 B）** 尽快恢复模拟盘样本流；稳定后再切 `mlto`（模式 A）提升可审计性。

### Phase 2 — Hub / 门槛 / Regime（1 天）✅ 已落地

1. Paper Hub：WAIT 0.28 / NIBBLE **0.36** / BUILD 0.55（`MIDLONG_HUB_*_PAPER`）  
2. `MIDLONG_HUB_TREND_SIGNAL_BONUS=0.05`：Trend `should_open` 且方向一致时 `adj+=0.05`  
3. Soft 通道补齐 `size×0.6`（`soft_open` / `size_hint_mult`）  
4. Regime→fuse：`apply_regime_to_open`（extreme 禁开 / ranging Paper 探针×0.25 / unknown×0.5）  

### Phase 3 — 总控减负（1 天）✅ 已落地

1. `MASTER_MIDLONG_LLM_MODE=summary`：Master 跳过 Trend/MLTO 深度 LLM  
2. `llm_budget.py` 分桶（midlong/master/scalp）+ `MIDLONG_MASTER_STAGGER_SEC` 错峰  
3. **C7**：analyst 不再清空 `mlto_handled_keys`；共享同一 set；仅 midlong tick 开头 clear  

### Phase 4 — 学习闭环（可选，对齐 FINCON）✅ 已落地

1. `midlong_belief_loop.py`：失败 Intent + 平仓样本 → 概念信念（如震荡勿追）  
2. 持久化 `data/midlong_beliefs.json`；有界回灌 OWM（降 llm）/ `by_nature.min_score`  
3. 信念块注入 TrendAgent / MLTO qual_layer prompt；挂到 `run_mlto_learning_tick`  

### Phase 5 — 持仓管理模式（1–2 天）🕐 设计中（2026-08-04）

1. **模式切换判定**：`mlto_cycle._trend_one` 开头按「交易对是否有未平仓中长线仓位」分流：有仓 → 模式 B，无仓 → 现有模式 A  
2. **新建 `midlong_position_manager.py`**：模式 B 分析器（六维：方向延续 / 滚仓 / 止盈止损调整 / 补仓 / 分批止盈推进 / 反转离场），并行复用 `review_position` + `evaluate_pyramid` + `long_tier_staged_tp` + `evaluate_midlong_exit`  
3. **修复滚仓/补仓断点**：解除中长线 pyramid/dca 被 Master 委托跳过的限制（受 `trend_pyramid_gate` 5 层门控 + 冷却保护）  
4. **统一日志** `[MidLong] stage=manage ...`；`MIDLONG_POSITION_MGMT_*` 配置接入

推荐默认：`MIDLONG_POSITION_MGMT_ENABLED=true`，先 Paper 验证样本流，再切 Live。

---

## 9. 配置清单（目标 `.env` 片段）

```env
# ── MidLong v2 ──
MIDLONG_EXEC_AUTHORITY=trend          # 或 mlto；禁止 dual
MIDLONG_MLTO_CONTROLS_EXEC=false      # 与 authority=trend 一致；true 时强制 authority=mlto
MIDLONG_MASTER_DELEGATE=true
MASTER_MIDLONG_LLM_MODE=summary

TREND_PAPER_SCORE_FLOOR=32
TREND_TRUST_SHOULD_OPEN_SOFT=true

MIDLONG_HUB_NIBBLE_PAPER=0.36
MIDLONG_HUB_BUILD_PAPER=0.55
MIDLONG_HUB_TREND_SIGNAL_BONUS=0.05

MIDLONG_PAPER_PROBE_ON_WAIT=false     # 需要更多样本时可 true
MIDLONG_ALLOW_RANGE_PROBE=true        # Paper only

# ── 持仓管理模式（Phase 5）──
MIDLONG_POSITION_MGMT_ENABLED=true    # 总开关
MIDLONG_POSITION_MGMT_INTERVAL_SEC=0  # 0=随 tick（默认 ~120s）；>0 则节流
MIDLONG_POSITION_MGMT_PYRAMID_ONLY_PROFIT=true   # 仅浮盈滚仓（禁止浮亏加仓）

# 配额：继续解耦，可加大（不降）
# trend_daily_cap 以 runtime_tuning 为准
```

---

## 10. 验收标准

| 指标 | 通过条件 |
|------|----------|
| 职责 | 任意时刻仅一个 Writer 产生中长线新开；日志无双开竞争 |
| 样本 | Paper 中长线日成交 ≥ 改前（配额未降前提下）；hold 原因可分类占比 |
| 正确性 | 无 `swing`+`daily_cap 无独立配额` 拦截；平仓匹配成功率显著上升 |
| 性能 | Master 周期耗时下降（summary 模式目标 <80s 中位，视模型而定） |
| 审计 | 每笔开仓可追溯 `authority + thesis_id/trend_score + snapshot_id + V5 rule` |
| 边界 | 短线成交与 `scalp_daily_cap` 不受本次改造回退 |
| 持仓管理 | 有仓交易对每 tick 被模式 B 分析；滚仓/分批止盈/反转离场可触发并执行；无「有仓仍重复开仓分析」日志 |

---

## 11. 风险与非目标

**非目标（本次不做）：**

- 复活独立 mid 日配额 / SwingAgent 独立开仓  
- 降低短线配额  
- 用更多 LLM 辩论回合换胜率（热路径成本过高；辩论仅可放慢循环）  
- 替换 V5 确定性闸为 LLM 风控  

**风险：**

- 模式 B 可能开出「缺论点」的单 → 用审计字段强制挂 thesis_id（可空但打标）  
- Paper 降门槛 → 垃圾单增加 → 靠 EV/RR/1.5% 硬顶托底，并用 strategy_tag 分开评估  

---

## 12. 映射总表：论文洞见 → 本仓库改动

| 洞见 | 改动模块 |
|------|----------|
| TiMi 解耦 | `midlong_loop` 认知 vs `MidLongExecutor` 执行 |
| FINCON 层级 | Master 只监督；Analyst=Trend+MLTO |
| TradingAgents 风险在后 | 保持 V5/EV/Portfolio 在 Intent 之后 |
| Circuit Snapshot + NO_TRADE | Intent.snapshot_id；WAIT 不默认成交 |
| Regime 路由 | fuse + `regime_agent` 共用 |
| FS 事实/主观 | Quant brief vs LLM thesis 分轨（已有则加固） |
| Merehead 加权 | Hub bonus + 日度评估回写权重（Phase 4） |
| 持仓生命周期管理（本设计） | 模式切换判定 + `midlong_position_manager`（六维发展分析，Phase 5） |

---

## 13. 结论与下一步

本设计把「中长线不通畅」从经验问题提升为 **可执行的架构契约**：  
**Single Writer、Intent 契约、Master 减负、nature 归一、Hub/Trend 样本友好、Regime 真路由、开仓后持仓管理模式。**

**建议实施顺序：** Phase 0 止血 → Phase 1 Single Writer（默认 `authority=trend`）→ Phase 2 门槛 → Phase 3 总控减负 → **Phase 5 持仓管理模式**（开仓后六维发展分析，修复滚仓/分批止盈/反转离场断点）。

---

## 附录 A. 关键文件索引

| 文件 | 角色 |
|------|------|
| `backend/services/full_auto/loops/midlong_loop.py` | 独立循环入口 |
| `backend/services/full_auto/mlto_cycle.py` | Trend + MLTO maintain（**模式切换判定接入点**） |
| `backend/services/full_auto/midlong_position_manager.py` | **持仓管理分析器（Phase 5 新增）** |
| `backend/services/full_auto/midlong_helpers.py` | 当前开仓终点 |
| `backend/services/full_auto/master_execution.py` | 总控委托/平仓 |
| `backend/services/mlto/decision_hub.py` | Hub 阈值 |
| `backend/services/mlto/types.py` | WAIT→hold |
| `backend/services/decision_core/unified_gate.py` | V5 / nature 配额 |
| `backend/services/decision_core/pipeline.py` | evaluate_midlong_open |
| `backend/services/trend_agent.py` | review_position / evaluate_pyramid（模式 B 复用） |
| `backend/services/long_tier_staged_tp.py` | 分批止盈规则引擎（模式 B 复用） |

## 附录 B. 参考文献（链接）

1. Xiao et al. TradingAgents. https://arxiv.org/abs/2412.20138  
2. FINCON. NeurIPS 2024. https://proceedings.neurips.cc/paper_files/paper/2024/hash/f7ae4fe91d96f50abc2211f09b6a7e49-Abstract-Conference.html  
3. TiMi. https://arxiv.org/abs/2510.04787  
4. FS-ReasoningAgent. https://arxiv.org/abs/2410.12464  
5. Orchestration Framework for Financial Agents. https://arxiv.org/abs/2512.02227  
6. Circuit Framework（实践）. https://github.com/EthanXiang777/circuit-framework  
7. Regime-Aware Architecture. https://regimerisk.com/blog/regime-aware-crypto-trading-bot-architecture-guide  

---

*文档结束。实施前请确认：开仓主脑默认选 **trend** 还是 **mlto**。*
