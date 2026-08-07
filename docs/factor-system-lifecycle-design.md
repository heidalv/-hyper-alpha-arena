# 因子系统全生命周期闭环监控 — 诊断与设计方案

> 文档版本：v1.0
> 创建日期：2026-07-23
> 适用范围：Hyper-Alpha-Arena 因子引擎全量代码

---

## 一、现状盘点（生产实测）

### 1.1 因子库规模

| 维度 | 数值 | 说明 |
|---|---|---|
| 注册总数 | **1102** | 启动日志 `Total factors loaded: 1102` |
| AI 生成因子 | **994** | `factors/ai_generated/*.py` 文件数 |
| 内置因子 | **108** | technical/behavioral/sentiment/derivatives/macro/onchain/external/fundamental/composite/legacy_compat |
| 无法加载（broken） | **15** | `Skip registering`（抽象方法未实现） |
| IC 评估覆盖 | **969** | factor_performance_logs 唯一因子数 |
| IC 记录总量 | **796,852** | factor_performance_logs 总行数 |

### 1.2 因子有效性 — IC 分布（最新 2000 条）

**关键发现：双峰分布，两极分化严重**

| IC 区间 | 数量 | 占比 | 含义 |
|---|---|---|---|
| **>0.05** | 809 | **40%** | 优秀（有预测力） |
| 0.02-0.05 | 166 | 8% | 可用 |
| 0-0.02 | 94 | 5% | 弱信号 |
| -0.02-0 | 105 | 5% | 无效 |
| **<-0.02** | **826** | **41%** | **反向！信号方向相反** |

**结论：1100 个因子里约 41% 是反向的（有害的），只有 40% 是有效的。**

### 1.3 因子权重管理

权重文件 `data/factor_runtime_weights.json`（969 个因子权重）：

| 权重区间 | 数量 | 含义 |
|---|---|---|
| 等权 (1.0) | 796 | 默认（82%） |
| 0.5-1.0 | 98 | 轻微降权 |
| <0.3 | **31** | 严重降权 |
| >1.0 | **44** | 升权（IC>0.05 的好因子） |

**权重规则**（factor_ic_evaluator.py）：
- 胜率 > 60% → 权重 1.2（温和升权）
- 胜率 < 40% → 权重 0.25
- 胜率 < 45% → 权重 0.5
- 其余 → 1.0

### 1.4 生命周期闭环状态（8 环节）

| # | 环节 | 代码存在 | 实际运行 | DB 证据 | 状态 |
|---|---|---|---|---|---|
| 1 | **挖掘（Mine）** | ✅ AlphaMiner + 种子因子 | ⚠️ 注册了但 `_mine_candidates` 输出 0 个候选 | factor_evolution_log: **0 行** | ⚠️ 空壳 |
| 2 | **验证（Evaluate）** | ✅ IC 评估器 | ✅ 969 个因子，10 分钟一次 | factor_performance_logs: **796K 行** | ✅ 运行中 |
| 3 | **样本外（OOS）** | ✅ 120 天切 90+30 | ⚠️ 训练/验证切分代码存在但未真正执行 | - | ❌ 未接通 |
| 4 | **清洗（Purge）** | ✅ `_purge_and_select` | ❌ 从未调用 | factor_active_set: **0 行** | ❌ 缺失 |
| 5 | **晋升（Promote）** | ✅ 代码框架 | ❌ 从未执行 | factor_active_set: **0 行** | ❌ 缺失 |
| 6 | **影子期（Paper）** | ❌ 无代码 | ❌ | - | ❌ 完全缺失 |
| 7 | **监控衰减（Monitor）** | ⚠️ IC 评估器间接做 | ⚠️ 降权但不退役 | current_weight=0.000 in DB（文件有值） | ⚠️ 部分 |
| 8 | **在线权重（Online Weights）** | ✅ runtime_weights.json | ✅ 每天 1 次更新 | - | ✅ 运行中 |

**总结：8 个环节只有 3 个真正运行（验证 + 权重 + 间接监控），5 个缺失或空壳。**

### 1.5 数据库表状态

| 表名 | 行数 | 说明 |
|---|---|---|
| `factor_performance_logs` | **796,852** | IC 评估记录（有数据 ✅） |
| `factor_quality_reports` | **0** | 质量报告（从未生成 ❌） |
| `factor_active_set` | **0** | 活跃因子集（为空 ❌） |
| `factor_evolution_log` | **0** | 进化日志（从未记录 ❌） |
| `trend_prediction_records` | **0** | 趋势预测（从未记录 ❌） |

---

## 二、关键缺失分析

### 2.1 样本外验证 — ❌ 完全缺失

当前 IC 评估用的是**全量数据计算 Pearson corr(因子方向, 多头等效收益)**——没有时间切分。

**竞品标准**：
- WorldQuant BRAIN：强制 sample → out-of-sample 双段测试
- AlphaGen：按时间顺序的 train/val/test 切分
- QuantConnect：PSR ≥ 80% + 强制实时跟踪 3 个月
- 学术：DSR（Deflated Sharpe Ratio）+ 多重检验校正（t > 3.0）

**差距**：我们的 IC 评估本质上是用**同一批交易**的因子值和收益算相关——这是"事后归因"而非"事前预测验证"。

### 2.2 影子期（Paper Trading）— ❌ 完全缺失

没有任何代码实现"候选因子在影子账户跑 N 天，验证实盘表现后晋升"的机制。

**竞品标准**：
- WorldQuant：Submit → Simulation → OOS Test → Onboard
- QuantConnect：3 个月-2 年实时跟踪期
- Two Sigma：试运行 → 生产

### 2.3 自动晋升/退役 — ❌ 缺失

- `factor_active_set` 表为空（0 行）→ **没有任何因子被正式"晋升"到活跃集**
- 中线 `midlong_active_factor_set.build_snapshot()` 返回 count=0 → **中线决策没有任何活跃因子输入**
- 退役：IC 评估器会降权（0.25/0.5），但**不会完全退役（weight 不会降到 0）**，因子永远留在注册表里

### 2.4 因子相关性管理 — ❌ 缺失

994 个 AI 生成因子没有去冗余。竞品标准：
- WorldQuant：不允许与现有因子相关性 > 阈值的新因子入库
- Robeco：150+ 因子 zoo 实际只需 ~15 个就能覆盖
- Feng/Giglio/Xiu：双重 LASSO 增量贡献测试

---

## 三、前端整合页面设计

### 3.1 现状

当前 `frontend-next/src/app/factors/page.tsx`（166 行）只展示单 symbol 的因子值列表——**没有生命周期、IC 时间序列、质量分布**。

### 3.2 设计：因子系统监控仪表盘

#### Tab 1：因子库总览

```
┌──────────────────────────────────────────────────────┐
│  因子库总览                                           │
├──────────┬──────────┬──────────┬──────────┬──────────┤
│ 总数 1102 │ 活跃 796 │ 降权 129 │ 反向 ~400 │ 退役 0  │
├──────────┴──────────┴──────────┴──────────┴──────────┤
│  IC 分布直方图（双峰可视化）                           │
│  ██████████  >0.05(优秀)  40%                        │
│  ██           0.02-0.05   8%                         │
│  █            0-0.02      5%                         │
│  █            -0.02-0     5%                         │
│  ██████████  <-0.02(反向) 41%                        │
├──────────────────────────────────────────────────────┤
│  按 category IC 均值排名                              │
│  composite_v3   avg_ic=0.008  n=1616                 │
│  segmented_ic   avg_ic=0.007  n=3384                 │
└──────────────────────────────────────────────────────┘
```

#### Tab 2：生命周期看板（漏斗图）

```
挖掘中 ──→ 待验证 ──→ 影子期 ──→ 实盘 ──→ 衰减退役
  0          0          0        969        0
                                        ↑ 只有这一层有数据
```

#### Tab 3：单因子详情

```
因子: ai_gen_dust_squeeze
公式: (volume_rank * abs(close-open)) / (high-low+1e-10)
状态: 降权(weight=0.25) | IC=+0.989 | decay=0.000
IC 时间序列: [折线图，带滚动均值 + 置信带]
IC 衰减曲线: [IC vs 持有期 1/5/10/21 根K线]
样本内 Sharpe: 1.2 | 样本外 Sharpe: N/A（未验证）
当前权重: 0.25 | 上次更新: 2026-07-23 13:18
```

#### Tab 4：回测验证进度

```
候选因子队列:
  ai_gen_new_001  IC=0.082  状态: 评估中  进度: ████░░ 67%
  ai_gen_new_002  IC=-0.015 状态: 待清洗  进度: ░░░░░░ 0%
```

### 3.3 API 端点设计

```
GET /api/factors/overview          → 总览统计（总数/活跃/降权/反向/退役）
GET /api/factors/ic-distribution   → IC 分布直方图
GET /api/factors/lifecycle         → 生命周期漏斗
GET /api/factors/{name}/detail     → 单因子详情（IC 时间序列/衰减曲线/权重历史）
GET /api/factors/evolution/status  → 进化循环状态（阶段/进度/队列）
```

---

## 四、竞品最佳实践（摘要）

### 4.1 竞品对比表

| 系统 | 样本外验证 | 关键指标 | 自动化 | 核心设计要点 |
|---|---|---|---|---|
| **WorldQuant BRAIN** | 平台内置 OOS 测试 | Fitness = Sharpe × √(Returns/Turnover) | crowdsourcing | 复合 Fitness 分数惩罚高换手 |
| **QuantConnect** | PSR≥80% + 3 个月跟踪 | QC Market Rank = PSR × Sharpe × Capacity | 自动排名 | 多维乘积评分 |
| **AlphaGen** | 按时间 train/val/test 切分 | 集成 IC + 协同奖励 | RL 全自动挖掘 | 协同奖励避免冗余因子 |
| **qlib** | 滚动窗口 | IC/ICIR/Rank IC + signal_delay_check | YAML 工作流 | 信号质量 vs 组合质量分离 |
| **Alphalens** | 前向收益对齐 | 持有期 IC 衰减曲线 | 分析工具 | IC 衰减曲线是核心诊断 |
| **学术(DSR/Zoo)** | DSR + 多重检验 | t > 3.0（不是 1.96） | - | 跟踪实验次数 N 并惩罚 |

### 4.2 6 层架构标准

1. **发现层**：表达式 / RL / LLM 多模式挖掘
2. **评估层**：IC/ICIR/Rank IC + IC 衰减曲线 + 半衰期
3. **样本外层**：时间切分 + DSR + PSR + 多重检验校正
4. **相关性层**：相关性矩阵 + 层次聚类 + >80% 去冗余
5. **衰减监控层**：滚动 IC 告警 → 降权 → 隔离 → 退役
6. **自动化层**：硬阈值晋升 + 连续评分排名 + 自动入产/退役

---

## 五、分阶段实施方案

### 阶段 1（紧急，1-2 天）：让现有数据可见

**目标**：把已有的 796K 条 IC 数据展示到前端，让系统从"黑盒"变"透明"

| # | 任务 | 文件 |
|---|---|---|
| 1.1 | 新增 `/api/factors/overview` API | `backend/api/factor_routes.py`（新建） |
| 1.2 | 新增 `/api/factors/ic-distribution` API | 同上 |
| 1.3 | 重构 `frontend-next/src/app/factors/page.tsx` | 加总览/IC 分布/生命周期/详情 4 个 Tab |
| 1.4 | 新增 `/api/factors/{name}/detail` API（IC 时间序列） | 查 factor_performance_logs |

### 阶段 2（关键，3-5 天）：补齐样本外验证

**目标**：让每个活跃因子有样本外验证背书

| # | 任务 | 说明 |
|---|---|---|
| 2.1 | 实现 forward returns IC | 用 K 线数据计算 N 根前向收益，与因子值算 Spearman 相关 |
| 2.2 | 实现时间切分验证 | 训练集 90 天 + 验证集 30 天（代码已有框架 `_evaluate_candidates`） |
| 2.3 | 填充 `factor_quality_reports` 表 | 每个因子生成 IC_mean/ICIR/coverage/decay_half_life |
| 2.4 | 填充 `factor_active_set` 表 | 从 969 个因子筛选 IC>0.02 + ICIR>0.3 的进活跃集 |

### 阶段 3（重要，5-7 天）：自动晋升/退役

| # | 任务 | 说明 |
|---|---|---|
| 3.1 | 接通 `_purge_and_select` | 清洗 41% 反向因子（IC < -0.02 → 退役） |
| 3.2 | 实现影子期监控 | 候选因子在 paper 账户跑 7 天，IC>0.02 自动晋升 |
| 3.3 | 实现自动退役 | 滚动 IC < 0 连续 30 天 → 退役 |
| 3.4 | 接通 FactorEvolutionLoop 完整 8 阶段 | 从"只跑阶段1取数"到"全流程闭环" |

### 阶段 4（优化，长期）：相关性管理 + DSR

| # | 任务 | 说明 |
|---|---|---|
| 4.1 | 因子相关性矩阵 | 计算 969 个因子的时间序列相关，>80% 的聚类去冗余 |
| 4.2 | DSR（Deflated Sharpe Ratio） | 跟踪实验次数 N，对因子 Sharpe 做多重检验惩罚 |
| 4.3 | 协同奖励（AlphaGen 模式） | 评估因子的增量贡献（组合加入后的 IC 提升），而非单因子 IC |
| 4.4 | 994 → ≤50 因子清理 | 从"上千个因子"精简到"每个都有样本外验证背书" |

---

## 六、最紧急的 1 步（立即可做）

**让 796K 条 IC 数据可见**——这是从黑盒到透明的第一步。

当前 `factor_performance_logs` 有 796,852 条 IC 记录，但：
- 前端只展示单 symbol 因子值（不展示 IC）
- 没有 IC 分布可视化
- 没有因子质量排名
- 没有生命周期状态

**最小可行方案**：
1. 新建 `backend/api/factor_routes.py`，4 个 API
2. 重构 `frontend-next/src/app/factors/page.tsx`，4 个 Tab
3. 数据全部来自现有的 `factor_performance_logs` 表（无需新计算）
