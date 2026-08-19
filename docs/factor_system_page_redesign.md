# 因子系统页面重构 · 智能学习报告 + 设计方案

版本: v1.0 · 2026-08-19 · 先学习后设计，不写代码

---

## 第一部分 智能学习报告：系统现状全景

### 1.1 因子体系五大块（后端真实存在的东西）

| 块 | 内容 | 核心 API | 实测状态 |
| --- | --- | --- | --- |
| ① 因子引擎 | 46 个实时因子（RSI/动量/波动/量价等），每币 44 个值 | GET /api/factors/values/{symbol} | 9 个固定币全有 44 个因子 |
| ② 因子池 | 18 个因子带状态（ACTIVE/PAPER/SMALL_LIVE/RESEARCH/QUARANTINE） | GET /api/ops/factor-pool | 1 tradable（seed_rev20, PAPER, icir 0.05）+ 17 QUARANTINE（16 个因 IC 衰减 drift 被隔离） |
| ③ 挖矿管线 | 进化循环：种子模板 + GP 遗传编程 + MCTS 树搜索 + Alpha101 灌库 + 冷池复扫 + 任务队列 | GET /api/ops/midlong-factors、GET /api/ops/evolution-funnel、GET /api/factors/jobs | 中线: 活跃 0 · 候选 53 · 已拒 100+；7 天晋升 0 |
| ④ 门禁 | DSR/PBO 多重检验（max_pbo 0.5、min_symbols 4）+ 冗余去重 + min_sharpe + 自适应回看 | gate_config / pipeline_health | DSR required=true；候选通过页 117 |
| ⑤ 选币与规则 | 固定币候选池 9 币（无 AI 选币也在）+ AI 选币（4 币/24h 过 15）+ 长线 long_trend_v2 规则 | GET /api/ops/training、GET /api/ops/long-trend-v2 | fixed_pool: ASTER,BNB,BTC,ETH,SOL,UNI,VIRTUAL,XPL,XRP |

### 1.2 前端现状（问题所在）

| 页面 | 现状 | 问题 |
| --- | --- | --- |
| /factors（因子系统） | 只显示「实时因子值 + 归一化概览 + 因子报告卡」，币种原本硬编码 4 个（已修成 9 固定币） | **与真实系统完全脱节**：看不到因子池、看不到挖掘、看不到门禁、看不到被拒原因——它还是「因子引擎原始值查看器」，而系统早已长成「挖掘→池→门禁→活跃」的生产线 |
| /ops（运维台） | 完整控制台：心跳矩阵、进化漏斗、因子池、选币绑定、训练、报错、挖矿、中线因子、TP/SL | 功能齐全 |

### 1.3 学习发现的已知问题清单

| # | 问题 | 证据 | 影响 |
| --- | --- | --- | --- |
| B1 | 因子报告卡数据源坏 | GET /api/monitor/factor-eval/BTC 返回 Insufficient kline data；_load_klines_for_eval 走 unified_data_pool（DC_ONLY 下为空）+ kline_service 兜底失败 | 报告卡 Tab 永远空白 |
| B2 | /factors 与 /ops 职责重叠且 /factors 过时 | /factors 是旧查看器；/ops 是 2026-08 新建的真控制台 | 用户进「因子系统」看到的不是因子系统 |
| B3 | 币种硬编码 | factors/page.tsx 原 SYMBOLS=[BTC,ETH,SOL,BNB]（已修为固定币池） | 已修 |
| B4 | 页面无门禁可视化 | 旧页面只有因子值，没有 DSR/PBO/冗余/admission 门槛展示（subtitle 里却写着 admission 门槛可视化） | 名不副实 |

---

## 第二部分 页面重构设计

### 2.1 定位（与 /ops 的分工）

- **/factors（因子系统）= 因子生命周期总览**：回答「我的因子体系现在怎么样」——池子状态、生产进度、门禁判定、每个币的因子值与质量。
- **/ops（运维台）= 管线运维**：心跳、报错、绑定、训练、车道（保持现状不动）。
- 复用原则：/factors 的「弹药生产」区直接复用 OpsMidlongFactors 组件（不重写），其余分区用现有端点组装。

### 2.2 页面结构（上→下 9 个区块）

```
┌─ A. 因子脉搏 KPI 带 ────────────────────────────────────────┐
│   可交易因子 1 · 候选通过 117 · 7d晋升 0 · 池 18(隔离17) ·     │
│   固定币 9 · AI选币 4 · 进化心跳 2h前                         │
├─ B. 币种带：固定币候选池（9 币 chip + 因子值计数）──────────┤
├─ C. 因子池状态（tradable / research / quarantine 三视图）────┤
│   + 隔离原因分布（IC衰减 16） + 池明细表 + 门禁健康           │
├─ D. 弹药生产（挖掘）：一键挖掘 + job 进度 + 预检（自适应回看）│
│   + 候选列表(53) + 已拒列表(100+ 带原因)                       │
├─ E. 进化漏斗：mine/promote/reject 计数 + 最近拒绝理由 ────────┤
├─ F. 实时因子值（保留升级）：44 因子按类别分组 + 归一化概览 ──┤
├─ G. 因子报告卡（修好数据源后）：IC/ICIR/衰减/换手/单调 + 门禁徽章│
├─ H. 门禁可视化：DSR/PBO/冗余/上限/admission 门槛 ────────────┤
└─ I. 长线规则：long_trend_v2 L1 状态/score/strength ──────────┘
```

### 2.3 各区块数据源与展示

| 区块 | 数据源 | 展示 | 轮询 |
| --- | --- | --- | --- |
| A 脉搏 | GET /api/ops/pipeline（pulse 字段，已有 10s TTL） | 8 个 KPI + 进化最后时间 | 15s |
| B 币种带 | GET /api/ops/training → fixed_pool.symbols（已接） | 9 固定币 chips，选中态 | 30s |
| C 因子池 | GET /api/ops/factor-pool?view=tradable|research|quarantine | 状态分布 + 隔离原因 + 池明细（factor_id/state/icir/last_net_ic/weight）+ pipeline_health（dsr.max_pbo/min_symbols） | 15s |
| D 弹药生产 | 复用 OpsMidlongFactors（/api/ops/midlong-factors + mine/prune + jobs） | 挖掘按钮 + 预检 + 候选/已拒 + 闸门参数 | 15s |
| E 进化漏斗 | GET /api/ops/evolution-funnel?days=7 | action 计数 + 最近 40 条拒绝（factor_id/action/reason/metrics） | 15s |
| F 实时因子值 | GET /api/factors/values/{symbol} | 44 因子按类别分组 + 归一化概览（现有视图，币种换成 9 固定币） | 点击币种时拉 |
| G 报告卡 | GET /api/monitor/factor-eval/{symbol}（先修 B1） | 每因子 IC/ICIR/衰减/换手/单调/尾部 + grade 徽章 | 点击币种时拉 |
| H 门禁 | gate_config（midlong-factors）+ pipeline_health（factor-pool） | max_pbo 0.5 / min_symbols 4 / min_sharpe / 冗余阈值 / 上限 30+40 / 自适应回看参数 | 30s |
| I 长线规则 | GET /api/ops/long-trend-v2 | 每币 state/score/strength/note | 60s |

### 2.4 交互设计

1. 顶部 Tab：**总览 / 因子池 / 弹药生产 / 实时因子值 / 报告卡 / 门禁 / 长线**（总览 = A+B+C+E 组合；其余 Tab 单区放大）。
2. 「一键快速挖掘」按钮沿用现有 mine 流程（POST + job 轮询），挖完自动刷新 C/D/E。
3. 币种 chips 选中后 F/G 联动换币。
4. 因子池三个状态视图（tradable/research/quarantine）用 toggle 切换（沿用 ops.css 的 ops-toggle 样式）。

---

## 第三部分 配套后端修复（先于页面）

| # | 修复 | 位置 | 方案 |
| --- | --- | --- | --- |
| R1 | 报告卡数据源 | system_monitor_routes.py:162 _load_klines_for_eval | 改走 data_center.get_klines(purpose=research, 自适应回看 min(可用, 1200))，不再依赖 unified_data_pool |
| R2 | 报告卡中性化 | 同 R1 | 报告卡 IC 用中性化残差收益（与升级计划 P1 对齐，可后置） |
| R3 | 无（其余端点都已就绪，页面纯组装） | - | - |

---

## 第四部分 实施计划（设计确认后执行）

| 步 | 内容 | 工作量 | 验收 |
| --- | --- | --- | --- |
| S1 | 后端 R1（修报告卡数据源） | 0.5 天 | /api/monitor/factor-eval/BTC 返回 20 条报告 |
| S2 | 页面骨架：A 脉搏 + B 币种带 + Tab 框架 | 0.5 天 | 页面出现 8 KPI + 9 币 chips |
| S3 | C 因子池 + E 进化漏斗 | 1 天 | 三状态 toggle + 隔离原因 + 池明细；漏斗计数+拒绝列表 |
| S4 | D 弹药生产（复用 OpsMidlongFactors） | 0.5 天 | 挖掘按钮/job/预检/候选/已拒在 /factors 可用 |
| S5 | F 实时因子值 + G 报告卡 | 0.5 天 | 9 币切换出值/报告 |
| S6 | H 门禁 + I 长线规则 | 0.5 天 | 门禁参数 + 长线状态展示 |
| S7 | 打磨：与 /ops 导航分工、空态、轮询节流 | 0.5 天 | 回归 + next build 通过 |

---

## 第五部分 待确认的决策点

1. **Tab vs 单页滚动**：设计里用 Tab（总览 + 分区），你要单页滚动也可以（区块顺序不变）。
2. **/factors 与 /ops 是否合并**：建议保留两页分工（研究面 vs 运维面）；如果你想让 /factors 直接并掉 /ops 的因子相关面板，我可以把导航改成单入口。
3. **报告卡的中性化**：R2 可先不做（先用原始 IC），等升级计划 P1 中性化落地后自动一致。
4. **门禁可视化深度**：先做「参数 + 判定徽章」轻量版；「每个被拒因子的门槛明细钻取」可后置。