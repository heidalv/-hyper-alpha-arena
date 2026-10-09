# Agent Wall v2 设计：三块并行区域（主策略 / 因子 / 学习进化）

日期：2026-09-19　状态：**设计 v1（待评审；未改代码）**
用户指令（记录在案）：
- 「因子模块与学习模块应该是**和这个整体策略画布并行**的，**单独一个大的组合区域**，然后放置他们的各个子模块，显示各个子模块的关系」
- 「主脑层可是有很多 agent，完全忽略了」（⇒ 本设计 §1 待清点结果填入后重画）
- 「先设计」——本文件是设计，不含实现。

---

## 0. 布局总览（三区并行，各自独立画布）

```
┌─────────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐
│ 区 1 · 主策略（Strategy）     │  │ 区 2 · 因子（Factor）    │  │ 区 3 · 学习进化（Learn）  │
│                             │  │                         │  │                         │
│ 1A 主脑层  ← 待清点后重画      │  │ 2A 计算与缓存            │  │ 3A 学习闭环              │
│ 1B 观察型 Agent 群            │  │ 2B 因子路线（含已停 A/B）  │  │ 3B 后端注册表与智慧库     │
│ 1C 车道与执行                 │  │ 2C 因子池与登记           │  │ 3C 进化与挖掘            │
│ 1D 审计与观测                 │  │ 2D 挖掘与验证退役         │  │ 3D 实验卡与评分（断链红标）│
└──────────┬──────────────────┘  └──────────┬──────────────┘  └──────────┬──────────────┘
           │  跨区边（只在有实测数据流时画）  │                            │
           └──────────────► 因子只产证据 ────┘                            │
           └──────────────► 学习回写参数/规则（当前未确证，见 §3.4）◄──────┘
```

**交互**：三区各自独立画布（独立缩放/平移/布局持久化），顶部切换或三区并排（可折叠）；
**跨区边**用不同颜色 + 显式标签（如「只产证据」「回写未确证」），且**跨区边必须比区内边更严格地有证据**。

---

## 1. 区 1 · 主策略（Strategy）

### 1A 主脑层（**清点结果：用户所指的"主控+6分析师"是死路径；真正活的是 MLTO**）

**必须先把这件事讲清楚**（子代理 2026-09-19 实测，38.6h 日志 + DB）：

| 体系 | 位置 | 判定 | 关键证据 |
|---|---|---|---|
| **MLTO LLM 论题主脑** | `mlto/brain.py:1345 refresh_thesis`（子进程） | **✅ 唯一活的主脑** | 四来源交叉验证 **≈590 次/24h（每 2.4min）**：`analysis_runs` consensus 583／`ai_decision_logs` brain 587／`mlto_thesis_events` 588／`mlto_episodes` 588／子进程 refresh 594 |
| **"主控 + 6 分析师"** | `backend/services/trading_analysts.py`（`MasterController:1805` + Position/Market/Intel/Risk/Strategy/Kline） | **❌ 死路径** | `caller=MasterController:synthesize`＝**0**；`[Analysts]`/`[MasterController]`＝**0 命中**；`full_auto_sessions.analyst_reports`＝**NULL**；入口被封：`coordinator_loop.py:101-104`（`_ai=[]` ⇒ `_run_trading_cycle` 永不执行，`master_execution.py:229-234` 自陈）+ `maintenance_only=True` 封掉另两条 |
| ↳ 其中 `KlineAnalyst` | `trading_analysts.py:825` | **⚠️ 活跃但空转、无消费者** | 被 `full_auto_trading_service.py:4740 _warmup_analyst_reports` 调（`[KlineAnalyst] 全量分析`=105，last 09-19 02:12），但 `TREND_REQUIRES_KLINE_DEEP=false` ⇒ 声明消费者 `master_execution.py:2183`（本身死）的布尔链恒 false ⇒ **只烧 LLM、无产出** |
| QAA 卡片层（含 `master_controller`/`mt_orchestrator`） | `qaa/cards.py:107/124` | 已退役 | `/api/qaa/*` 返回 `status:"retired"`；ADR `docs/ADR_QAA退役_20260919.md`；⚠️ ADR 待办#4（给文件加 RETIRED 头）**未落地**，`cards.py:1-4` 仍看不出已退役 |
| `strategic_analyst` 宏观战略 | `strategic_analyst/engine.py:51` | 活跃但 **LLM 阶段死** | `[StrategicEngine] 战略分析完成`=51/24h（last 18:15:19）；但 `report_generator`(46)/`macro_data_collector`(44) 全是"拒绝公用默认配置"警告，`caller=strategic_analyst`＝**0** |

**四套体系之间无生产数据流**（同层平行）：MLTO ｜ 分析师体系 ｜ QAA ｜ strategic_analyst。

**画布怎么画（修正后）**：
- G1 保留 `brain_mid`/`brain_long`（它们代表 MLTO 子进程），但应把 **MLTO 主脑内部**拆出来画真活模块：
  证据装配层（`context_pack`／`midlong_chart_gate` 图审／`episodic_memory`／`learning_bridge` OWM／`reflexion_memory`）、
  `dual_call` 双票+仲裁委员会（`model_gateway.py:894`）、闸门层（`midlong_circuit_gate`／`constitutional_veto`／`lane_permission`／`open_block_reason`）、
  产物层（`mlto_thesis`／`mlto_thesis_events`／`mlto_episodes`／`ai_decision_logs.brain`）。
- **6+1 分析师画成"死路径"**（灰 + 证据），**不得**画成活节点；`KlineAnalyst` 单独标"活跃但空转、无消费者、只在烧 LLM"。
- `mlto/` 内 **9 个无生产导入者**的模块（`orchestrator`／`qual_layer`／`quant_layer`／`debate_layer`／`evidence_ingest`／`layered_memory`／`sizing_overlay`／`pnl_basis`／`open_gate`）**不画成活节点**；
  `mlto_memory_events` 0 行、`mlto_debate_log` 0 行与之一致。
- **观测缺口**：主脑所有 LLM 调用共用 `caller=analysis.model_gateway`（硬编码），**无法按 agent 区分**。要让画布"按 agent 显示 LLM 调用"，需先补埋点（把 `dual_call` 的 task 注入 caller）——**属改代码，需批准**。

### 1B 观察型 Agent 群（已有，7 节点）
`anomaly`(900s) / `signal_review`(每日 08:00) / `event_impact`(每日 06:30) / `execution_qa`(每日 07:15) /
`timing`(已停用) / `param_search`(已停用) / `experiment_advance`(3600s)。

### 1C 车道与执行（已有 + 方案 B 待实施）
`midlong_loop` 节拍器 / `mid_tier` / `long_tier`（方案 B 拆分，**未实施**）、`midlong_executor`（中线唯一 Writer）、
`trend_e1_engine`（长线唯一 Writer）、`trend_agent`（持仓复核）、`direction_audit`（审计漏斗）、
`position_sizing`、`scalp_lane`（已关停）、`swing_agent`（废弃模块）。

### 1D 审计与观测（已有）
`gil_watch`（`/api/ops/gil-watch`）、`DB LeakGuard`、`FreshnessWatch`、`job_registry`（`/api/ops/jobs`）。

### 区 1 待办的画布修正（**不新增节点**，只改错）
1. T2：`midlong_loop` 的 180/240s 标注为"遗留孤儿配置"（已做）；
2. 方案 B 拆分（等实现）；
3. `factor_route_ab` 已改「已停用」（已做，因 A/B 关闭）；
4. 待 1A 清点后，**重画主脑层**。

---

## 2. 区 2 · 因子（Factor）

### 2A 计算与缓存
| 子模块 | 证据 |
|---|---|
| `factor_calc` 单因子计算 | `factor_engine/factor_calculator`（日志 `Calculating N factors for SYM TF`） |
| `factor_cache` / `dataset_cache` | `factor_engine/factor_cache.py`、`dataset_cache.py` |
| `expr_parser` 表达式因子 | `factor_engine/expr/parser.py`、`audit.py`（表达式安全审计） |
| `factor_exposure` 暴露快照 | 表 `factor_exposure_snapshots` **687,718 行**，max ts 16:40:16，**600s** |

### 2B 因子路线（**本区最关键、也最容易误判**）
| 子模块 | 是否下单 | 现状 |
|---|---|---|
| `[FactorRoute]`（旧前缀） | 设计上下单 | **不可达**（开关关闭） |
| `[FactorRouteAB]` | 曾并行开仓 | **2026-09-19 已按用户指令停止**（`.env MIDLONG_MID_FACTOR_ROUTE_AB=false`）⇒ 现**只产证据** |
| `[FactorRouteShadow]` | 只决策 | 活（对照用，不下单） |

> **画法**：三条路线**并列**画，各自标注"是否下单"；`FactorRouteAB` 画成**已停用**并注明指令来源与日期。
> 禁止再画成"开仓边"（这正是上次的错误）。

### 2C 因子池与登记
`factor_pool`（`/api/ops/factor-pool`）、`factor_sync`（`/api/factors/*`）、`factors_lab`（`/api/factors-lab/*`）。

### 2D 挖掘与验证退役
`alpha_miner` / `gp_miner` / `mcts_miner`（`services/evolution/*_miner.py`，产物 `factor_engine/factors/ai_generated/*.py`）、
`quick_score`（快速打分）、`purge_pipeline`（退役）、`/api/factors/discovered`（发现清单）。

### 2 区内部边（草案）
```
kline → factor_calc → {factor_cache, factor_exposure}
factor_calc → factor_route_ab(已停用) / factor_route_shadow(只决策)
factor_pool → alpha/gp/mcts_miner → quick_score → purge_pipeline → factor_pool（回灌）
```
**跨区边（区1↔区2）**：`factor_route_*` → 中线执行，**必须标注"证据 or 开仓"，当前=只产证据**。

---

## 3. 区 3 · 学习进化（Learning & Evolution）

### 3A 学习闭环
`learning_loop_service`（常驻）、`learning_core`（统一进化内核，`cmaes_optimizer`）、
`analysis/ledger_scoring`（**900s**，评分与可信度）。

### 3B 后端注册表与智慧库
`backend_loader`（启动注册：`hermes_agent_wisdom` priority=150、`qaa`）、
`hermes_agent_wisdom_engine`（产物 SQLite `data/hermes_evolution.db` 表 `agent_decision_wisdom` **156,888 行**，末次 05:43:49）。

### 3C 进化与挖掘
`evolution_scheduler`、`qaa_evolution_bridge`（**300s / 600s**，产物 `data/qaa_evolution/evolution_history.jsonl` 275,429 条；
⚠️ 文件 mtime 曾 11.4h 未更新，**"周期在跑"≠"产出在写"未确证**）、`alpha/gp/mcts_miner`（与区 2 共用，画成跨区虚线）。

### 3D 实验卡与评分（**本区核心断链**）
| 子模块 | 实测 | 判定 |
|---|---|---|
| `experiment_advance` | 3600s，run_count 623+ | 在跑 |
| **`experiments` 表** | **0 行** | **落地环节不存在**（observe 不落卡 + 可信度门 3/12/0 全不过） |
| 采纳消费方 | `[experiments] 开始` 日志 **0 命中** | **无消费者** |

### 3 区内部边（草案）
```
观察型(G2) --预测--> scoring --可信度门--> ┬─✅--> experiment_card --> 采纳(配置/策略)   ← 当前这条整段为红
                                            └─❌--> 停在账本
平仓事件 --> backend_registry --> wisdom_store --> prompt 注入（agent_deep_context）
evo_scheduler --> evo_kernel --> miners --> factor_pool（跨区到区 2，虚线）
```

### 3.4 未确证（设计里必须显式标注，不许画成绿的）
- `wisdom_store` 156,888 条智慧**是否只有 prompt 注入、没有回写参数/规则** → 未确证；
- `qaa_evolution_bridge` 的"周期在跑"与"历史文件 11.4h 未更新"矛盾 → 未确证；
- `latest_bucket_weights.json` 12 天未更新但任务今天 07:00 跑过 → 未确证。

---

## 4. 三区共用规则（吸取前两轮的错误）

1. **先设计后实现**：任何节点/边进入画布前，先在本文档里写清"证据 + 判定"；
2. **没有证据不画**：宁可少画一条边，也不画"永远绿"的假边（上次的 `thesis→trend[方向]`、`coordinator→brain` 就是反例）；
3. **停用/退役件必须显式**：状态 `disabled`/`dead` + 指令来源 + 日期 + 回滚方式（A/B、QAA、timing、param_search、scalp 都按此办）；
4. **频率一律写实测**，声明值只在括号里注明"声明 X（不生效）"；
5. **别名的坑**：`[FactorRoute]/[AB]/[Shadow]` 三个前缀、`latest_event_impact.json` 两个生产者、`risk_gate` 三个世界——凡涉及处必须在节点说明里点明"不是哪个"。

---

## 5. 待你决策（阻塞项，按优先级）

| # | 事项 | 选项 |
|---|---|---|
| 1 | **T1 节拍彻底分开** | a) 拆成 `fullauto_mid`(180s)+`fullauto_long`(240s) 两个 job（结构改动+回归）；b) **先量 mid/long 的 LLM 真实调用占比**（线索：12,833 启动 vs 3,159 refresh ⇒ ≈75% 空转）再定 |
| 2 | **`decision_hub` 融合层** | D1 接线（需定义裁决规则与质检否决权=**策略决定**）／D2 删掉／D3 只画灰虚线 |
| 3 | **实验卡 0 行** | 开 `advise`+降可信度门 ／ 承认设计性死亡并停掉 623 次空转 |
| 4 | 主脑层范围 | 子代理清点结果回来后，请你确认"哪些算主脑层"（可能有多个平行体系，需你定边界） |
