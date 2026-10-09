# Agent Wall 扩展设计：节拍拆分（方案 B）+ 因子区 + 学习进化区

日期：2026-09-19　状态：**设计待评审**（本轮只设计，不改代码）
依据：`docs/Agent运行排查_20260919.md`、`_中长线全栈逐跳活性清单_20260919.md`、`docs/Agent画布模块设计_20260919.md`
用户判断（记录在案）：**"现在套路的不是画布问题，是后端的执行问题，有这个画布，问题就全部暴露出来了。"**

---

## 0. 先说三件被画布暴露出来的**后端执行问题**（这才是要修的）

| # | 问题 | 证据 | 性质 |
|---|---|---|---|
| **E1** | **中/长线节拍从未分开**：`get_due_ai_tiers()` 每轮返回 `['mid','long']`，180s/240s 两个独立节流全是死配置 | `mark_tier_run` 全仓唯一生产调用点 `coordinator_loop.py:115` 只标 `[t for t in _ai if t == "short"]`；`midlong_loop.py:476` 自注不标 tier；实测两 tier 抢同一子进程池 ⇒ mid p50=74s / long p50=71s | **要修**（见 §1） |
| **E2** | **中线 A/B 的"融合层"从未落地**：`decision_hub.fuse_signals`（`mlto/decision_hub.py:213`）与 `qual_layer.update_thesis`（`:89`）写的调用点是 `mlto/orchestrator.py`，而 **orchestrator 全 backend 生产 0 调用点** | 子代理：全 backend 非 tests 检索 `mlto.orchestrator` = 0；`hub_decision_log.py:23` 自称"调用点在 orchestrator.tick" 是空头支票 | **要决策**：接线 或 删掉（见 §2） |
| **E3** | **学习闭环的"落地"环节是 0**：`experiments` 表 **0 行**，而 `experiment_advance` 已空转 623+ 次 | `base.py:276-278` observe 模式不落卡 + 6 个 agent 全 observe + 唯一会发卡的 3 个 agent 可信度门全不过（3/12/0 < 30） | **要修**（见 §4） |

---

## 1. 方案 B：把"节拍"与"两条 tier"拆成 3 个节点（同时把节拍真正分开）

### 1.1 画布结构（3 节点 + 明确边）

```
                     ┌──────────────────────────────┐
                     │ midlong_loop 节拍器           │  XL
                     │ 声明：mid 180s / long 240s     │
                     │ 实测：p50=157s/tick，两 tier   │
                     │       同 tick 派发（死配置）    │
                     └───────┬──────────────┬───────┘
                             │              │
                 派发 mid tier│              │派发 long tier
                             ▼              ▼
        ┌────────────────────────┐   ┌────────────────────────┐
        │ mid_tier 中线            │   │ long_tier 长线          │
        │ 子进程 --tier mid       │   │ 子进程 --tier long      │
        │ 实测 p50=74s            │   │ 实测 p50=71s            │
        │ 论题 tier=mid           │   │ 论题 tier=long          │
        └──────────┬─────────────┘   └──────────┬─────────────┘
                   │                            │
          ┌────────┴────────┐          ┌────────┴─────────┐
          ▼                 ▼          ▼                  ▼
   factor_route_ab   midlong_executor  trend_e1_engine  trend_agent
   （因子路线，会开仓）（中线唯一 Writer）（长线唯一 Writer）（持仓复核 review/pyramid）
```

- 节点由 1 个（原来的「中/长线主循环」）拆成 3 个：`midlong_loop`（XL 节拍器）+ `mid_tier`（L）+ `long_tier`（L）。
- `brain_mid`/`brain_long` 两个旧节点**并入** `mid_tier`/`long_tier`（它们本来就是"派发出来的子进程"，不是独立调度者）——
  画布上从此**不再出现"中线主脑 180s／长线主脑 240s"这种假频率**。
- 节拍器节点必须**同时显示声明与实测**，并把"两 tier 同 tick 派发"标为**缺陷**（红色角标 + 指向 E1 审计条目）。

### 1.2 后端执行修复（让节拍真正分开）——两条路，需你选

| 路线 | 做法 | 风险/代价 |
|---|---|---|
| **T1：让 `mark_tier_run` 覆盖 mid/long**（推荐） | `coordinator_loop.py:115` 改为按实际派发的 tier 标记：`mark_tier_run(session_id, dispatched_tiers)`；`midlong_loop` 在派发后**也**标记（删掉 `:476` 那条"故意不标"的注释与其动机） | 中：一旦节流生效，**long 会从 71s 掉到 240s**（实际变慢 3.4×）。若当前高频是**有意**的（`PAPER_FAST_TRIAL` 快速试单），则不能改 |
| **T2：承认"合一节拍"是设计，改配置与文档** | 把 `TIER_MID/LONG_AI_TICK_SEC` 从 `.env` 与 `tick-intervals` 响应里**移除或标注废弃**；`/api/full-auto/tick-intervals` 只报 `midlong_loop` 的实测节拍 | 低：不动行为，只消灭"看起来分开"的谎言 |

> **我的建议**：先做 **T2**（零风险、立刻消除误导），同时做一件事验证 T1 是否安全：
> 量一次"mid/long 各自被 LLM 真实调用"的比例（子代理已给出线索：`brain_subprocess.log` 12,833 次启动但
> `[MidLongBrain] refresh` 仅 3,159 次 ⇒ **≈75% 是空转**）。若空转占 75%，则 T1 让 long 变慢到 240s **几乎没有代价**——
> 因为多出来的那些 run 本来就没产出。**这个数据到手再决定 T1。**

---

## 2. 中线 A/B 是什么？"没落地的那个"是什么？

### 2.1 中线其实有**三条**并行路线（前缀极易混）

| 前缀 | 代码 | 是否下单 | 现状 |
|---|---|---|---|
| `[FactorRoute]` | 旧因子路线 | 设计上下单 | **不可达**（开关关闭） |
| **`[FactorRouteAB]`** | `factor_engine/midlong_factor_route.py:864` → `midlong_executor` | **✅ 会开仓** | **活**：近 24h 中线 9 笔开仓里 **2 笔 `entry_source=factor_route`**（另 7 笔 `mlto`=主脑） |
| `[FactorRouteShadow]` | 影子路线 | ❌ 只决策 | 活（用于对照，不下单） |

**A/B 的含义**：主脑（LLM 论题）与**因子路线**两条独立决策路径**并行**跑，日志里 `gate=ab_parallel(paper)` 即"在模拟盘 AB 并行"。
所以中线 = **主脑 vs 因子路线** 的双轨，两轨都能直达 `midlong_executor`（这是画布之前漏掉的那条边）。

### 2.2 "没有落地的那个" = **信号融合层（decision_hub）**

- 设计意图：主脑 + 因子路线 + 信号源 → `decision_hub.fuse_signals()` **融合** → `qual_layer` 质检 → 才允许开仓。
- 现实：`fuse_signals`（`mlto/decision_hub.py:213`）与 `update_thesis`（`mlto/qual_layer.py:89`）的调用点写在
  **`mlto/orchestrator.py`**，而该模块**全 backend 生产 0 调用点** ⇒ **融合层与质检层从未接线**，
  A/B 两轨的结论**直接进执行**（没有融合、也没有质检闸）。
- 这就是你感觉到的"没有落地的那个"：**框架写了、卡片画了、就是没接上**。

**需要你决策（三选一）**：
- **D1 接线**：把 `decision_hub`+`qual_layer` 挂到 `midlong_loop` 的派发链里（在 `midlong_executor` 之前）；
  代价：约 2 处接线 + 需要定义"融合规则"与"质检否决权"（这本身是要设计的策略问题）。
- **D2 删掉**：承认双轨直连是现状，删除 `mlto/orchestrator.py` 与 `decision_hub.fuse_signals`/`qual_layer.update_thesis`，
  在 ADR 里记录"融合层设计未采纳"，画布上**不画**这两个节点（按"没证据不画"纪律）。
- **D3 只画影子**：保留代码，画布上把两者画成 **灰虚线"设计未接线"**节点，审计里列为已知缺口。

---

## 3. 新增区块 A：**因子区（G5）**

### 3.1 节点（每条都已有可查证据来源）

| 节点 | 角色 | 实测/声明频率 | 流来源（adapter） |
|---|---|---|---|
| `factor_calc` 因子计算 | 单因子计算/缓存（`factor_calculator`） | 按需（被主脑/路线调用） | `logs/backend.log` filter `factor_calculator` |
| `factor_exposure` 因子暴露快照 | 定时落库 | **600s**（`factor_exposure_snapshots` 687,718 行，max ts 16:40:16） | 同上 filter `exposure` |
| `factor_route_ab` 因子路线 A/B | **会开仓**的中线因子决策路线 | 随 midlong tick | filter `FactorRouteAB` |
| `factor_route_shadow` 影子路线 | 只决策、不下单（对照） | 随 midlong tick | filter `FactorRouteShadow` |
| `factor_pool` 因子池 | 因子登记/状态（`/api/ops/factor-pool`） | 按需 | API `/api/ops/factor-pool` |
| `factor_mining` 因子挖掘 | `alpha_miner`/`gp_miner`/`mcts_miner` 生成新因子（`factors/ai_generated/*.py`） | 事件/调度 | filter `miner|alpha_miner|gp_miner` |
| `factor_validate` 因子验证与退役 | `quick_score` / `purge_pipeline` / `/api/factors/discovered` | 按需 | filter `purge_pipeline|quick_score` |

### 3.2 边

```
kline_collector → factor_calc → factor_exposure
                    │
                    ├→ factor_route_ab → midlong_executor   （已证实会成交）
                    ├→ factor_route_shadow                  （只决策，灰线）
                    └→ factor_pool → factor_mining → factor_validate → (回灌 factor_pool)
```

**特别标注**：`[FactorRoute]`（旧前缀，不可达）**不画**，只在审计里说明"三个前缀中只有 AB 会开仓"——这正是子代理提醒的易误判点。

---

## 4. 新增区块 B：**学习进化区（G6）**

### 4.1 节点

| 节点 | 角色 | 实测/声明频率 | 证据 |
|---|---|---|---|
| `learning_loop` 学习闭环 | `learning_loop_service` 主循环 | 常驻 | filter `LearningLoop` |
| `backend_registry` 学习后端注册表 | `backend_loader`：`hermes_agent_wisdom`(priority 150) / `qaa` | 启动时 | 启动日志 `[BackendRegistry] 注册后端` |
| `wisdom_store` 智慧库 | `agent_decision_wisdom` 表 | 随平仓事件（末次 05:43） | SQLite `data/hermes_evolution.db` **156,888 行** |
| `evo_scheduler` 进化调度 | `evolution_scheduler` 周期任务 | 300s / 600s | filter `QAABridge|evolution` |
| `evo_kernel` 统一进化内核 | `learning_core`（CMA-ES 等） | 随调度 | filter `cmaes|learning_core` |
| `miners` 挖掘器 | `alpha/gp/mcts_miner`（与因子挖掘共用） | 事件/调度 | filter `miner` |
| **`experiment_card` 实验卡生命周期** | `experiment_advance` + `experiments` 表 | **3600s，但表 0 行** | `job_registry` rc=623；`experiments` count=0 |
| `scoring` 评分与可信度 | `analysis_ledger_scoring`（900s）+ `/api/agents/credibility` | 900s | job rc；anomaly n=1526 |
| `promotion` 晋级扫描 | `promotion_scan_service` | 调度 | filter `promotion` |
| `hypothesis` 策略假设 | `strategy_hypothesis_engine` | 事件 | filter `hypothesis` |

### 4.2 边 + **断链点（本区块的核心价值）**

```
观察型 Agent 群(G2)  ──预测──▶ scoring ──可信度门──▶ ┬─✅ 通过 → experiment_card → 采纳 → 配置/策略
                                                      └─❌ 不过 → 停在账本（现状：3/12/0 全不过）
midlong_executor ──平仓──▶ backend_registry ──▶ wisdom_store ──▶ prompt 注入（agent_deep_context）
evo_scheduler ──▶ evo_kernel ──▶ miners ──▶ factor_pool（与 G5 相接）
hypothesis ──▶ promotion ──▶ (配置/策略)
```

**必须在画布上标红的断链**：
1. `scoring → experiment_card`：**`experiments` 表 0 行**（observe 模式不落卡 + 可信度门不过）
   ⇒ **学习闭环的"落地"整段不存在**（这就是 E3，也是"学习了但不改变行为"的机制级原因）。
2. `experiment_card → 采纳`：即使有卡也没有消费方（`[experiments] 开始` 日志 0 命中）。
3. `wisdom_store`：156,888 条智慧**只进 prompt**，没有"智慧→参数/规则"的回写路径（需确认是否有回写，否则标注"只读注入"）。

---

## 5. 画布扩展后的分组总览（6 组）

| 组 | 名称 | 节点数（预计） | 说明 |
|---|---|---|---|
| G0 | 数据底座 | 4 | 行情/采集/因子计算/选币 |
| G1 | 论题库与决策 | 2 | 论题库 + （decision_hub/qual_layer 视 D1/D2/D3 定） |
| G2 | 观察型 Agent | 7 | 6 agent + 实验卡推进 |
| G4 | 车道与执行 | 7 | 节拍器 + mid_tier + long_tier + 中线执行 + 长线 E1 + 持仓复核 + 因子路线AB |
| **G5（新）** | **因子** | 7 | 见 §3 |
| **G6（新）** | **学习进化** | 10 | 见 §4 |

---

## 6. 建议实施顺序（做完一步看一步，避免大改）

1. **先做 T2（零风险）**：把 180/240s 标注为废弃（`.env` 注释 + `tick-intervals` 响应加 `deprecated` 标记），
   画布 `midlong_loop` 节点显示"实测 p50=157s／两 tier 同 tick"。
2. **再量一个数**：mid/long 各自 LLM 真实调用占比（已知线索：12,833 启动 vs 3,159 refresh ⇒ ≈75% 空转）。
   这个数字决定 T1 是否安全（若空转 75%，分开节拍几乎无代价）。
3. **然后决定 E2**（D1 接线 / D2 删掉 / D3 只画影子）——这是**策略问题**，不是工程问题，需要你定。
4. **按本设计实现 G5/G6 两个区块 + 方案 B 拆分**，每区块带：节点注册表、流 adapter、边（含断链红标）、
   审计条目、单元测试（注册表完整性 / 边引用 / 断链判定）、e2e（渲染 + 无 fan-out）。
5. **E3（实验卡 0 行）**单独立项：要么开 `advise` + 降可信度门到实际样本量，要么承认流水线当前设计性死亡并停掉 623 次空转。
