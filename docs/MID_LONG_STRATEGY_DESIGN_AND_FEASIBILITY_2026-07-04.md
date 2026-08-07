# 中线 / 长线策略设计文档与可行性认证

> **版本**：v1.1  
> **日期**：2026-07-04  
> **状态**：Phase 0 已落地，Phase 1 待验证后实施  
> **范围**：SwingAgent（mid）、TrendAgent（long）、MLTO 降级、门控瘦身、三周期调度均衡  
> **硬性约束**：**减门不加门** — 不新增拦截层，只解除/合并现有 block  
> **关联文档**：[MID_LONG_AGENT_UPGRADE_DESIGN_2026-06-27.md](./MID_LONG_AGENT_UPGRADE_DESIGN_2026-06-27.md)、[MID_LONG_EXECUTION_LANE.md](./MID_LONG_EXECUTION_LANE.md)、[MLTO_ARCHITECTURE.md](./MLTO_ARCHITECTURE.md)

---

## 第 0 章：执行摘要

### 0.1 问题一句话

近 3 天 paper 模式下 **short 204 笔、mid 1 笔、long 0 笔** — 不是 AI 不会分析，而是 **执行链与调度把 mid/long 拦死了**，同时 Scalp 在小仓 + 反向净额 + 紧 trailing 下产生无意义微利 churn。

### 0.2 解决思路

| 维度 | 旧行为 | 新行为（Phase 0） |
|------|--------|-------------------|
| 开单控制权 | MLTO Hub + open_gate 控 mid/long | SwingAgent / TrendAgent **独立直控** |
| 编排器 | `ORCHESTRATOR_HARD_GATE=true` 硬 veto | 软注入，不拦开仓 |
| FactGuard | paper enforce 改 hold | shadow 审计 |
| 调度 | mid 120s / long 240s，绑主循环 | mid 45s / long 90s，对齐 Scalp |
| 资金 | Scalp 占 60% | Scalp 40% / Swing 45% |
| MLTO | 开单门 + Hub adj 天花板 ~0.29 | 仅 thesis 面板 + 学习，不控开单 |

### 0.3 综合可行性结论

| 维度 | 评分 | 判定 |
|------|------|------|
| 技术可行性 | 8/10 | Agent 直控分支已存在，配置开关齐全，无 DB 迁移 |
| 运维可行性 | 7/10 | `.env` 回滚简单；验收脚本与 Fast Trial UI **已同步** |
| 业务/风险 | 6/10 | mid/long 质量需 7 天 paper 观察；V5 + PEO 仍兜底 |
| **Go/No-Go** | **Go（Phase 0）** | 重启后端即可验证；Phase 1 等 Phase 0 通过后再做 |

### 0.4 本文档与实施完成度对照

| 类别 | 内容 | 状态 |
|------|------|------|
| **文档本身** | 第 0–10 章全文（592 行） | 已完成 v1.1 |
| **文档本身** | 架构图 ×2、门控 18 层、可行性认证、Go/No-Go、验收 SQL | 已完成 |
| **Phase 0 代码** | `.env` / settings / swing / hub / full_auto / V5 | ✅ 已完成 |
| **Phase 1** | evaluate_midlong_open + Fast Lane + block→scale | ✅ 已完成 |
| **Phase 2/P3** | MTF 融合 + MC tail + 周限额 + 独立循环扫市场 | ✅ 已完成 |
| **P4 Prompt** | swing/trend 阈值/regime/计数变量 | ✅ 已完成 |
| **配套** | Fast Trial midlong 预设 / 验收脚本 / EXECUTION_LANE | ✅ 已完成 |
| **运行观测** | 72h mid≥3 long≥1 | ⏳ 后台自动跑 |

---

### 1.1 数据证据（PostgreSQL `alpha_arena`，排查窗口 2026-07-01 ~ 2026-07-04）

| 层级 | 成交笔数（近 3 天） | 典型现象 | 最后成交 |
|------|---------------------|----------|----------|
| short | 204（200 已平） | 微利频繁平仓（+0.01~1U / -0.01~-1U），平均保证金 ~8.77 USDT | 持续 |
| mid | 1 | SwingAgent hold 1121 vs sell 成交 1 | 2026-07-01 |
| long | 0 | MLTO hub adj≈0.29，gate 原因 `hub_action=WAIT` | 2026-06-30 |

**AI 决策分布（近 7 天）**：mid hold 3827、long hold 1754 — LLM 在跑，但几乎不转化为成交。

### 1.2 五大根因（机制 + 代码锚点）

#### 根因 1：调度不对称

- **ScalpRouter** 独立循环 ~45s（`scalp_factor_router.py`），不依赖主循环。
- **mid/long** 原先绑主循环 120s/240s（`TIER_MID_AI_TICK_SEC` / `TIER_LONG_AI_TICK_SEC`）。
- 主循环分析超时（24h 内约 71 次）时，mid/long tick 被跳过，**只剩 Scalp 在交易**。
- 修复：`TIER_MID_AI_TICK_SEC=45`、`TIER_LONG_AI_TICK_SEC=90`；`full_auto_trading_service.py` 超时 stub 注入 mid+long。

#### 根因 2：MLTO Hub 评分天花板

- `decision_hub.fuse_signals()` 在震荡市 composite 0.35~0.55 时，consistency 惩罚将 `adjusted` 压至 **~0.29**。
- `open_gate` 要求 readiness≥45（mid）/≥50（long AI 放宽 adj≥0.42）→ **永远 WAIT**。
- 修复：consistency 下限（`decision_hub.py` L100-102）；`MIDLONG_MLTO_CONTROLS_EXEC=false` 绕过 Hub 控单；`MIDLONG_THESIS_OPEN_GATE=false`。

#### 根因 3：SwingAgent 自限

- Prompt（`task_swing_agent.md`）：RR≥2、conf≥55。
- 代码 paper（`swing_agent.py` L277-286）：RR≥1.5、conf≥48（已放宽）。
- FactGuard paper enforce 会将违规决策改 hold。
- 修复：`AGENT_FACT_GUARD_PAPER_ENFORCE=false`、`AGENT_FACT_GUARD_MODE=shadow`。

#### 根因 4：编排器硬门控

- `ORCHESTRATOR_HARD_GATE=true` 时，编排器 WAIT/neutral 直接改 hold（`full_auto_trading_service.py` L7443+）。
- 修复：`ORCHESTRATOR_HARD_GATE=false`。

#### 根因 5：Scalp 资金与行为

- 小仓 + `PAPER_ONE_WAY_REVERSE_NETTING=true` + 紧 trailing + `master_running_close` → 无意义微利 churn。
- 修复：`LAYER_BUDGET_SCALP=0.40`、`SCALP_OPEN_COOLDOWN_SEC=120`、`PAPER_ONE_WAY_REVERSE_NETTING=false`、scalp trailing 放宽（`paper_trading_engine.py`）。

---

## 第 2 章：现状架构（As-Is）

### 2.1 架构图

```mermaid
flowchart TB
    subgraph scalp [Scalp 独立循环 ~45s]
        SR[ScalpRouter]
        SR --> SE[paper_engine]
    end
    subgraph mainLoop [主循环 原120s/240s]
        HC[HealthCheck/OrchBG]
        HC --> Stub[Fix18 stub inject]
        Stub --> Branch{MIDLONG_MLTO_CONTROLS_EXEC}
        Branch -->|true 旧默认| MLTO[MLTO Hub+open_gate]
        Branch -->|false 新默认| Agent[SwingAgent/TrendAgent]
        MLTO --> GateChain
        Agent --> GateChain
    end
    subgraph GateChain [约18层门控链]
        G1[DataReady] --> G2[QuantBrief]
        G2 --> G3[PreScreener]
        G3 --> G4[Agent should_open]
        G4 --> G5[FactGuard]
        G5 --> G6[MLTO open_gate]
        G6 --> G7[Persistence]
        G7 --> G8[DCP]
        G8 --> G9[Orch/Risk]
        G9 --> G10[V5 UnifiedGate]
        G10 --> SE
    end
```

### 2.2 关键文件映射

| 职责 | 文件 |
|------|------|
| 中线 Agent | `backend/services/swing_agent.py` |
| 长线 Agent | `backend/services/trend_agent.py` |
| 执行主链 | `backend/services/full_auto_trading_service.py` |
| MLTO Hub | `backend/services/mlto/decision_hub.py` |
| MLTO 开单门 | `backend/services/mlto/open_gate.py` |
| V5 最后一道 | `backend/services/decision_core/unified_gate.py` |
| 门控门面 | `backend/services/decision_core/pipeline.py` |
| 调度 | `backend/services/tier_tick_scheduler.py` |
| 配置 | `backend/config/settings.py`、`.env` |
| Swing Prompt | `docs/opencode/prompts/tasks/task_swing_agent.md` |
| Trend Prompt | `docs/opencode/prompts/tasks/task_trend_agent_direction.md` |

### 2.3 默认执行路径（Phase 0 后）

**中线**（`full_auto_trading_service.py` L7980-8070）：

```
tier=mid → MIDLONG_MID_VIA_MLTO=false（默认）
  → SwingAgent.analyze() 独立 LLM
  → should_open (conf≥48, RR≥1.5, paper)
  → 门控链 → V5 → place_order
```

**长线**（L8114-8216）：

```
tier=long → MIDLONG_MLTO_CONTROLS_EXEC=false（默认）
  → TrendAgent.analyze_direction() 独立 LLM
  → should_open_trend + trend_score
  → 门控链 → V5 → place_order
```

**MLTO**（thesis 仍更新，但不控开单）：

```
MIDLONG_THESIS_LEDGER_ENABLED=true（可保留）
  → thesis_update 每 tick
  → decision_hub 展示 adj/readiness
  → open_gate 跳过（MIDLONG_THESIS_OPEN_GATE=false）
```

### 2.4 门控链 18 层清单

| # | 层级 | 模块 | Phase 0 | Phase 1 目标 |
|---|------|------|---------|--------------|
| 1 | 数据 | `data_readiness_gate` | 保留 | 保留 |
| 2 | 量化 | QuantBrief | 软证据 | 软证据 |
| 3 | 预筛 | `SignalPreScreener` | 保留 | 合并入 evaluate |
| 4 | Agent | `should_open` / trend_score | 保留 | 保留 |
| 5 | 事实 | FactGuard | **shadow** | shadow |
| 6 | MLTO | `open_gate` | **关闭** | 仅展示 |
| 7 | 持久 | 2-tick persistence | 保留 | scale 替代 block |
| 8 | 方向 | DCP | 可选关闭 | scale |
| 9 | 编排 | Orchestrator | **软注入** | scale |
| 10 | 风险 | `unified_risk_gate` | 保留 | 保留 |
| 11 | 一致性 | `decision_consistency_gate` | 保留 | 合并 |
| 12 | Tier 预算 | layer budget | 保留 | 保留 |
| 13 | V5 | `unified_gate.evaluate_entry` | **最后一道** | block→scale |
| 14 | CryptoV | crypto_alpha veto | 保留 | 保留 |
| 15 | PEO | 退出执行 | 保留 | 保留 |
| 16 | 方向门 | direction_gate | 条件触发 | 合并 |
| 17 | PC gate | position close gate | 保留 | 保留 |
| 18 | Legacy | legacy_gate_block | 保留 | 删除/合并 |

---

## 第 3 章：行业对标（Best Practice）

| 维度 | 行业实践 | 本项目现状 | 差距 | 改进方向 |
|------|----------|------------|------|----------|
| 多周期分层 | Regime → Direction → Entry 三层解耦 | Agent Prompt 设计符合 | 执行链串行叠加 | 减门、Regime 前置一次 |
| 信号→仓位 | block 少、scale 多（Kelly / vol targeting） | 多层 hard block | 信号衰减 | block → `size_multiplier` |
| 调度 | 各周期独立 tick + 非阻塞 | Scalp 独立；mid/long 曾绑主循环 | 已部分修复 | 彻底 decouple 主循环（P2） |
| 评分体系 | 单一 confidence/score | Hub adj + LLM conf 双轨 | 面板与执行不一致 | 统一展示；MLTO 不控单 |
| Prompt 治理 | Prompt 阈值 = 代码阈值 | RR/conf 不一致 | LLM 偏保守 | P1 对齐或 Prompt 注明 paper |
| 纸面/实盘 | Paper 略宽、Shadow 审计 | FactGuard 曾 enforce | 已改 shadow | 7 天 shadow 评估后再 enforce |

**结论**：分析层 / Prompt 接近行业前沿；**执行链与调度**是主要短板。改进方向：**减门、对齐调度、统一分数** — 严格遵守不加新 gate。

---

## 第 4 章：目标架构（To-Be）

### 4.1 架构图

```mermaid
flowchart LR
    subgraph ticks [独立 Tick 调度]
        T45[TIER_MID 45s]
        T90[TIER_LONG 90s]
        T45 --> Swing[SwingAgent]
        T90 --> Trend[TrendAgent]
    end
    subgraph mltoPanel [MLTO 仅面板/学习]
        Thesis[thesis_update]
        Hub[decision_hub 展示]
    end
    Swing --> Eval[evaluate_open_decision]
    Trend --> Eval
    Thesis -.->|不控开单| Eval
    Eval --> V5[V5 UnifiedGate]
    V5 --> Order[place_order]
    Order --> PEO[PEO 退出]
```

### 4.2 设计原则

1. **三层独立决策**：mid = SwingAgent，long = TrendAgent；`MIDLONG_MLTO_CONTROLS_EXEC=false`。
2. **MLTO 降级为 thesis/学习**：Hub/open_gate 仅面板与 OWM，不拦截开单（`MIDLONG_THESIS_OPEN_GATE=false`）。
3. **编排器软注入**：`ORCHESTRATOR_HARD_GATE=false`；veto 改 size 建议，不 hard block。
4. **FactGuard shadow**：`AGENT_FACT_GUARD_PAPER_ENFORCE=false`；审计日志保留。
5. **资金再平衡**：Scalp 40% / Swing 45% / Long 余量（`LAYER_BUDGET_*`）。
6. **Scalp 降 churn**：cooldown 120s、关 reverse_netting、放宽 trailing。
7. **预期行为说明**：MLTO 面板可能仍显示 `hub_action=WAIT`，但 Agent 直控路径可独立开单 — **属正常，非 bug**。

### 4.3 与 2026-06-27 Agent 升级设计的关系

| 项目 | 06-27 设计 | 07-04 本文 |
|------|------------|------------|
| 焦点 | 证据链、记忆分库、Hermes 自进化 | **执行链解锁 + 调度均衡** |
| MLTO 角色 | thesis + Hub 控开单 | **MLTO 仅 thesis/学习** |
| Agent 升级 | FactGuard、scenario 落库 | FactGuard 先 shadow，不拦单 |
| 优先级 | P0 bug + 证据化 | **P0 已做：减门 + tick 对齐** |

两者互补：06-27 提升 Agent **质量**，07-04 解决 Agent **出不了单** 的结构性问题。

---

## 第 5 章：Prompt 设计规范

### 5.1 中线 SwingAgent

**文件**：`docs/opencode/prompts/tasks/task_swing_agent.md`

| 项 | 规范 |
|----|------|
| 时间尺度 | 1h 择时 + 4h 主趋势（Prompt 写 1h/4h；执行层可注入 1d 背景） |
| 核心纪律 | 回调入场、RR≥2、conf≥55、持仓 2-8h |
| 分析步骤 | 趋势 → 支撑阻力 → 成交量 → 衍生品 → regime → 历史 → RR → 反向假设 |
| 输出 JSON | `action`, `confidence`, `direction`, `sl_pct`, `tp_pct`, `risk_reward`, `cited_fact_ids`, `reasoning` |

**Prompt vs 代码阈值（paper）**：

| 参数 | Prompt 理想态 | 代码 paper 下限 | 说明 |
|------|---------------|-----------------|------|
| confidence | ≥55 | ≥48 | Prompt 引导 LLM 保守；代码允许试单 |
| risk_reward | ≥2.0 | ≥1.5 | 同上 |
| live | — | conf≥55, RR≥1.8 | live 更严 |

**P1 可选对齐**：统一为 RR≥1.8 / conf≥50，或在 Prompt 增加「paper 模式：RR≥1.5 可试单」段落。

### 5.2 长线 TrendAgent

**文件**：`docs/opencode/prompts/tasks/task_trend_agent_direction.md`

| 项 | 规范 |
|----|------|
| 时间尺度 | 4h / 1d / 1w，不看 5m/15m |
| 核心纪律 | 多周期共振、一周最多 1-2 次、SL 6-12% |
| 评分 | trend_score 0-100；≥70 强趋势；<50 输出 neutral |
| 场景 | scenario_a / b / c（主/备选/尾部） |
| 输出 JSON | `trend_score`, `trend_direction`, `should_open_trend`, `suggested_sl_pct`, `lifecycle`, `scenario_*`, `reasoning` |

**V5 对齐**：`V5_TREND_FOLLOW_MIN_CONFIDENCE=50`（`.env` L346）。

### 5.3 日志关键字（验收用）

| 关键字 | 含义 |
|--------|------|
| `[SwingAgent独立]` | 中线 Agent 独立路径生效 |
| `[TrendAgent独立]` | 长线 Agent 独立路径生效 |
| `[V5Gate] PASS` | 通过最后一道门控 |
| `[V5Gate] BLOCK` | 被 V5 拦截（查 rule= 字段） |
| `[Persistence]` | 2-tick 持久化拦截 |
| `[PreScreener]` | 预筛 hold |

---

## 第 6 章：门控瘦身方案

### 6.1 Phase 0 — 已落地（2026-07-04）

#### 6.1.1 `.env` 变更（L328-355）

```ini
# 三周期均衡修复（2026-07-04）— 解锁 mid/long，减门不加门
MIDLONG_MLTO_CONTROLS_EXEC=false
MIDLONG_THESIS_OPEN_GATE=false
AGENT_FACT_GUARD_PAPER_ENFORCE=false
AGENT_FACT_GUARD_MODE=shadow
TIER_MID_AI_TICK_SEC=45
TIER_LONG_AI_TICK_SEC=90
MIDLONG_OPEN_READINESS_MIN_MID=25
MIDLONG_OPEN_READINESS_MIN_LONG=28
V5_TREND_FOLLOW_MIN_CONFIDENCE=50
SCALP_OPEN_COOLDOWN_SEC=120
SCALP_FACTOR_SCAN_INTERVAL_SEC=60
PAPER_ONE_WAY_REVERSE_NETTING=false
LAYER_BUDGET_SCALP=0.40
LAYER_BUDGET_SWING=0.45
QAA_ANALYST_STREAM_SAFETY_CAP_S=600
```

（另：`ORCHESTRATOR_HARD_GATE=false` 位于 `.env` L64。）

#### 6.1.2 代码变更清单

| 变更 | 文件 | 效果 |
|------|------|------|
| 默认 false | `backend/config/settings.py` | `MIDLONG_MLTO_CONTROLS_EXEC`、`MIDLONG_THESIS_OPEN_GATE`、`AGENT_FACT_GUARD_PAPER_ENFORCE`；tick 默认 45/90 |
| consistency 下限 | `backend/services/mlto/decision_hub.py` L100-102 | 震荡市 adj 不再压到 0.29 |
| 方向阈值 0.32 | `decision_hub.py` `_derive_direction` | 降低 neutral 判定 |
| 4h fallback 1h | `backend/services/mlto/quant_layer.py` | 缺 4h 指标时用 1h |
| min_adj 0.28 | `backend/services/mlto/open_gate.py` | re-enable 时可用 |
| paper conf≥48 RR≥1.5 | `backend/services/swing_agent.py` L277-286 | 中线试单门槛 |
| db 传参修复 | `full_auto_trading_service.py` | `db=_swing_db` |
| 超时 stub 含 mid+long | `full_auto_trading_service.py` | 主循环超时不跳过 mid/long |
| scalp trailing 放宽 | `backend/services/paper_trading_engine.py` | 减少微利平仓 |

### 6.2 Phase 1 — 待 Phase 0 验证通过后实施

| 任务 | 说明 | 预估 | 状态 |
|------|------|------|------|
| 门控合并 | `evaluate_midlong_open` 单点 DCP+V5+Regime+Orch soft | 1d | **已完成** |
| Regime 前置 | `mtf_resonance` + regime size_multiplier | 0.5d | **已完成** |
| block → scale | V5 `size_multiplier` + orch wait/frozen 缩仓 | 1d | **已完成** |
| Agent Fast Lane | 跳过 18 层串行，直接组合门控+下单 | 0.5d | **已完成** |
| Prompt 对齐 | swing/trend prompt 与代码阈值 | 0.5d | **已完成** |
| 文档同步 | `MID_LONG_EXECUTION_LANE.md`、`MLTO_ARCHITECTURE.md`、本文 | 0.5d | **已完成** |
| 验收脚本 | `verify_three_cycle_strategy.py` 断言 + load_dotenv | 0.5d | **已完成** |

**不在 Phase 1 范围**：新增任何 gate 层。

### 6.3 Phase 2 / P3 — 已完成（2026-07-04）

| 任务 | 文件 | 状态 |
|------|------|------|
| MTF 4h+1d 共振分 | `decision_core/mtf_resonance.py` | ✅ |
| Swing/Trend LLM+规则融合 | `swing_agent.py` / `trend_agent.py` | ✅ |
| Monte Carlo tail 缩仓 | `decision_core/monte_carlo_gate.py` | ✅ |
| 长线周开单上限 2 | `pipeline.evaluate_midlong_open` + settings | ✅ |
| 独立循环市场扫描 | `_run_midlong_independent` | ✅ |
| 三层预算 40/45/15 | `layer_budget_manager.py` + `.env` | ✅ |
| Fast Trial midlong 预设 | `paper_fast_trial_controller.py` | ✅ |

### 6.4 运行时优化 — 已完成（2026-07-04 晚）

| 任务 | 说明 | 状态 |
|------|------|------|
| 每 tick 单币轮询 | 奇数 tick=mid、偶数 tick=long，6 币轮转 | ✅ |
| `light_context` | Swing/Trend 跳过 `build_full_deep_context`，~35s/tick | ✅ |
| 指标轻量注入 | `_inject_midlong_indicators` 补 1h/4h/1d + MTF | ✅ |
| session/trade mode 分离 | `session_mode=status`，`trading_mode=paper` | ✅ |
| MLTO 重复 Swing 移除 | 轻量循环跳过 MLTO thesis 段 | ✅ |
| 验收日志 | `batch=['BTC'] mid/long`、`tick#2+`、`[TrendAgent独立]` | ✅ 19:50 已观测 |

---

## 第 7 章：可行性认证

### 7.1 技术可行性 — 高（8/10）

| 评估项 | 结论 | 依据 |
|--------|------|------|
| Agent 直控路径 | **可行** | `full_auto_trading_service.py` L7986-8123 已有 `not MIDLONG_MLTO_CONTROLS_EXEC` 分支；日志 `[SwingAgent独立]` / `[TrendAgent独立]` 已实现 |
| 配置开关 | **可行** | 全部 `.env` + `settings.py`；无 schema 变更 |
| tick 独立调度 | **可行** | `tier_tick_scheduler.py` + `TIER_*_AI_TICK_SEC` 已存在 |
| Hub 评分修复 | **部分可行** | 公式已改；adj 实际分布需 paper 观测 |
| 门控合并 P1 | **中等工作量** | `pipeline.evaluate_open_decision` 存在，调用点分散 |
| LLM 延迟 | **风险可控** | `QAA_ANALYST_STREAM_SAFETY_CAP_S=600`；mid/long 并行 tick 可能叠加 API QPS |
| 主循环 decouple | **P2** | stub 已缓解；彻底解耦需 refactor |

**技术阻塞项**：无硬阻塞。**必须重启后端** 使 `.env` 生效。

### 7.2 运维可行性 — 高（7/10）

| 评估项 | 结论 | 说明 |
|--------|------|------|
| 回滚 | **简单** | 恢复 5 个 flag + 重启（见第 9 章） |
| 监控 | **现有** | 日志 grep + PostgreSQL 查询 |
| 验收脚本 | **已更新** | `verify_three_cycle_strategy.py` 断言 Agent 直控；`--no-live` 已通过 |
| UI 覆盖 | **已改默认** | `paper_fast_trial_controller.py` MLTO 默认 false；注意用户手动切换仍会覆盖 `.env` |
| 数据库 | **无迁移** | 使用现有 `paper_positions`、`ai_decision_logs` |

### 7.3 业务 / 风险可行性 — 中（6/10）

| 风险 | 等级 | 概率 | 影响 | 缓解措施 |
|------|------|------|------|----------|
| mid/long 开仓增多但质量下降 | 中 | 中 | 中 | V5 + PEO 保留；7 天 paper PnL 监控 |
| Scalp 仍占主导 | 低 | 中 | 低 | 预算 40% + cooldown；观察 tier 成交占比 |
| MLTO 面板与执行脱节 | 低 | 高 | 低 | 文档说明预期；P1 统一 UI 文案 |
| Prompt/代码阈值不一致 | 中 | 高 | 中 | P1 对齐；短期靠代码下限放行 |
| 主循环仍超时 | 中 | 中 | 中 | stub 已含 mid/long；P2 decouple |
| Fast Trial 误开 MLTO | 中 | 低 | 高 | 运维检查面板；改 UI 默认值 |

### 7.4 资源与工期估算

| 阶段 | 内容 | 工期 | 依赖 |
|------|------|------|------|
| Phase 0 | 配置 + 小改 | **0.5d（已完成）** | 重启 |
| Phase 0 验证 | 30min–72h 观测 + SQL | 0.5d | DB 访问 |
| 本文档 + 脚本同步 | 设计文档 + 旧 doc + 验收脚本 | 1d | **已完成** |
| Phase 1 门控合并 | pipeline 统一 + block→scale | 2-3d | Phase 0 通过 |
| Phase 1 Prompt 对齐 | prompt + swing 阈值 | 0.5d | — |

### 7.5 Go / No-Go 检查表

| # | 检查项 | Phase 0 Go 条件 | 当前状态 |
|---|--------|-----------------|----------|
| 1 | `.env` 已更新 | 5 个核心 flag = false/新值 | 已更新 |
| 2 | 代码已合并 | decision_hub / swing / full_auto 改动 | 已合并 |
| 3 | 后端已重启 | 进程加载新 settings | **待运维确认** |
| 4 | 30min 内见独立 Agent 日志 | `[SwingAgent独立]` 或 `[TrendAgent独立]` | **待验证（需重启）** |
| 5 | 静态自检脚本 PASS | `verify_three_cycle_strategy.py --no-live` | **已通过（2026-07-04）** |
| 6 | 72h mid 有新成交 | `paper_positions` tier=mid | **待验证** |
| 7 | Fast Trial 未覆盖 MLTO | 面板 MLTO 控开单 = off | UI 默认已改 false |

**Phase 0 判定**：配置、代码、文档、静态自检 **Go**；运行验证 **Pending Restart**。

**Phase 1 判定**：No-Go 直至 Phase 0 运行观测 72h 通过（项 4、6）。

---

## 第 8 章：验收标准

### 8.1 重启后 30 分钟内（日志）

**Windows PowerShell**：

```powershell
Select-String -Path "logs\*.log" -Pattern "SwingAgent独立|TrendAgent独立|V5Gate.*PASS"
```

**Linux / Git Bash**：

```bash
grep -E "SwingAgent独立|TrendAgent独立|V5Gate.*PASS" logs/*.log | tail -50
```

**通过**：至少出现 1 条 `[SwingAgent独立]` 或 `[TrendAgent独立]`；若 Agent 给出 buy/sell，应见 `[V5Gate] PASS` 或 `[V5Gate] BLOCK`（含 rule 便于排查）。

### 8.2 自检脚本

```bash
cd 001Alpha/Hyper-Alpha-Arena
backend\.venv\Scripts\python.exe scripts/verify_three_cycle_strategy.py --no-live
backend\.venv\Scripts\python.exe scripts/verify_three_cycle_strategy.py --symbol BTC
backend\.venv\Scripts\python.exe scripts/phase0_tier_stats.py
```

> 静态检查（`--no-live`）已于 2026-07-04 通过（PASS=7 FAIL=0）。脚本已断言 `MIDLONG_MLTO_CONTROLS_EXEC=false` 并自动加载 `.env`。

### 8.3 72 小时 SQL 验收（PostgreSQL）

**连接**：`.env` 中 `DATABASE_URL=postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena`

```sql
-- 1. 按 tier 统计近 3 天成交（已平仓 + 仍持仓）
SELECT
    timeframe_tier AS tier,
    COUNT(*) AS cnt,
    MAX(opened_at) AS last_open,
    SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed_cnt
FROM paper_positions
WHERE opened_at > NOW() - INTERVAL '3 days'
GROUP BY timeframe_tier
ORDER BY tier;

-- 2. 按 tier 统计近 3 天已实现 PnL（已平仓）
SELECT
    timeframe_tier AS tier,
    trade_nature,
    COUNT(*) AS trades,
    ROUND(SUM(COALESCE(partial_realized_pnl, 0) + COALESCE(unrealized_pnl, 0))::numeric, 2) AS pnl_sum
FROM paper_positions
WHERE status = 'closed'
  AND closed_at > NOW() - INTERVAL '3 days'
GROUP BY timeframe_tier, trade_nature
ORDER BY tier;

-- 3. AI 决策 hold vs 开仓倾向（近 7 天，按 mid/long bias）
SELECT
    CASE
        WHEN mid_confidence IS NOT NULL AND mid_confidence > 0 THEN 'mid'
        WHEN long_confidence IS NOT NULL AND long_confidence > 0 THEN 'long'
        ELSE 'other'
    END AS tier_hint,
    operation,
    COUNT(*) AS cnt
FROM ai_decision_logs
WHERE decision_time > NOW() - INTERVAL '7 days'
  AND hyperliquid_environment IS NULL  -- paper
GROUP BY tier_hint, operation
ORDER BY tier_hint, operation;

-- 4. 最近一次 mid/long 成交详情
SELECT id, symbol, side, timeframe_tier, trade_nature,
       opened_at, closed_at, status, margin, partial_realized_pnl
FROM paper_positions
WHERE timeframe_tier IN ('mid', 'long')
ORDER BY opened_at DESC
LIMIT 10;

-- 5. V5 拦截原因抽样（从 reasoning 字段，若已写入 ai_decision_logs）
SELECT operation, symbol, LEFT(reason, 120) AS reason_snip, decision_time
FROM ai_decision_logs
WHERE decision_time > NOW() - INTERVAL '24 hours'
  AND (reason LIKE '%V5Gate%' OR reason LIKE '%Persistence%' OR reason LIKE '%PreScreener%')
ORDER BY decision_time DESC
LIMIT 20;
```

### 8.4 通过标准（建议）

| 指标 | 阈值 | 窗口 |
|------|------|------|
| mid 新成交 | ≥1 笔 | 72h |
| long 新成交或 V5 PASS | ≥1 笔成交，或 TrendAgent should_open + V5 PASS 日志 | 7d |
| short 成交频次 | 较修复前 3 天下降 ≥30% | 72h |
| 独立 Agent 日志 | 每 24h 至少 1 次 | 持续 |
| 主循环超时 stub | 超时日志中含 mid/long 注入 | 出现超时时 |

### 8.5 失败排查路径

```mermaid
flowchart TD
    A[mid/long 仍无成交] --> B{见 SwingAgent独立 日志?}
    B -->|否| C[查 TIER tick 调度 / 主循环超时 / 重启]
    B -->|是| D{should_open=true?}
    D -->|否| E[查 LLM 输出 / Prompt 阈值 / conf RR]
    D -->|是| F{见 V5Gate PASS?}
    F -->|否| G[查 V5Gate BLOCK rule= / Persistence / PreScreener]
    F -->|是| H[查 place_order / 预算 / 风控]
```

---

## 第 9 章：回滚方案

按顺序恢复 `.env` 并 **重启后端**：

| 顺序 | 环境变量 | 恢复值 | 恢复行为 |
|------|----------|--------|----------|
| 1 | `MIDLONG_MLTO_CONTROLS_EXEC` | `true` | MLTO 重新控开单 |
| 2 | `MIDLONG_THESIS_OPEN_GATE` | `true` | readiness / stable / reviews 门控 |
| 3 | `ORCHESTRATOR_HARD_GATE` | `true` | 编排器硬 veto |
| 4 | `AGENT_FACT_GUARD_PAPER_ENFORCE` | `true` | FactGuard 拦截 |
| 5 | `TIER_MID_AI_TICK_SEC` / `TIER_LONG_AI_TICK_SEC` | `120` / `240` | 旧调度频率 |
| 6 | `LAYER_BUDGET_SCALP` | `0.60` | Scalp 预算恢复 |
| 7 | `PAPER_ONE_WAY_REVERSE_NETTING` | `true` | 反向净额恢复 |

**快速回滚（仅恢复 MLTO 控单）**：仅改项 1-2 + 重启。

---

## 第 10 章：附录

### 10.1 文档与脚本同步清单（已完成）

| 文件 | 状态 | 说明 |
|------|------|------|
| [MID_LONG_EXECUTION_LANE.md](./MID_LONG_EXECUTION_LANE.md) | 已同步 | 默认 Agent 直控路径 + 链接本文 |
| [MLTO_ARCHITECTURE.md](./MLTO_ARCHITECTURE.md) | 已同步 | exec=false 说明 + 默认 flag |
| [verify_three_cycle_strategy.py](../scripts/verify_three_cycle_strategy.py) | 已同步 | 断言 Agent 直控 + load_dotenv |
| [paper_fast_trial_controller.py](../backend/services/paper_fast_trial_controller.py) | 已同步 | MLTO 默认 false，tick 45/90 |
| [settings.py](../backend/config/settings.py) | 已同步 | tick 默认 45/90 |
| [phase0_tier_stats.py](../scripts/phase0_tier_stats.py) | 新增 | Phase 0 验收 SQL 快捷脚本 |

### 10.2 Phase 0 与 06-27 设计差异对照

| 主题 | MID_LONG_AGENT_UPGRADE (06-27) | 本文 (07-04) |
|------|-------------------------------|--------------|
| 首要目标 | Agent 质量、自进化 | **解锁成交、调度均衡** |
| MLTO | Hub 控开单 | **仅 thesis/学习** |
| FactGuard | Phase 2 enforce | **先 shadow** |
| 门控 | 未强调减门 | **减门不加门** |
| Scalp | 未重点讨论 | **降 churn + 预算** |

### 10.3 相关验收脚本

```bash
backend\.venv\Scripts\python.exe scripts/verify_midlong_thesis_chain.py
backend\.venv\Scripts\python.exe scripts/verify_midlong_chain.py
backend\.venv\Scripts\python.exe scripts/mid_long_agent_acceptance_check.py
backend\.venv\Scripts\python.exe scripts/verify_three_cycle_strategy.py --symbol BTC
```

### 10.4 变更记录

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-07-04 | v1.0 | 初版：问题根因、Phase 0 基线、可行性认证、验收 SQL |
| 2026-07-04 | v1.1 | 修正文档内自相矛盾描述；同步清单标记已完成；补充 phase0_tier_stats.py |

---

*文档维护：Hyper-Alpha-Arena 策略组 · 下次评审：Phase 0 验证完成后更新 Go/No-Go 状态*
