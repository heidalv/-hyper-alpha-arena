# Alpha研究部 · 五Agent自动化闭环升级研究（v0.3 因子研究中心 → Agent侧核心）

日期：2026-09-17 ｜ 状态：研究稿（供因子研究中心评审）
范围：将 v0.3"文献驱动管道"扩展为五 agent 自动化闭环（AlphaAgent + RD-Agent 范式）。
纪律基线：v0.3 全部设计保留 —— 校准集（WorldQuant 101 / GTJA 191）回归、版权纪律、
RD-Agent 对照引擎、factors_lab 隔离库、生命周期状态机，均不变更，只在其上叠加。

---

## 0. 结论（TL;DR）

1. **范式选型有据**：五 agent 结构 = **RD-Agent(Q) 的循环骨架**（Research→Develop→反馈枢纽 + 经验学习）
   **+ AlphaAgent 的正则化内核**（在目标函数里显式惩罚"与既有因子池的高相似"，直接对抗 alpha decay）。
   两篇均已被同行评议接收（RD-Agent(Q): NeurIPS 2025；AlphaAgent: KDD 2025），且 RD-Agent 全开源可作对照引擎。
2. **仓库资产复用率高，五 agent 大部分是"组装"而非"从零建"**：
   DSL 白名单与编译审计（`factor_engine/expr/`）、LLM 提案层（`llm_proposal.py`，已含被拒提示+活跃避重）、
   GP 评估栈（`evolution/gp_miner.py` + GPU 批量求值）、回测门禁（`factor_backtest_scorer` + DSR/PBO）、
   经验教训库（`evolution_memory_v7.py`）、RAG（`rag_knowledge_service.py`）、晋级状态机（`lifecycle.py`）全部现成。
   真正的新建物只有四件：文献情报 agent、假设层 schema 与经验记忆、ADR-21 三分量正则打分、GTJA 191 校准集实现。
3. **多样性正则（ADR-21）建议直接采用 AlphaAgent 式公式并做三分量扩展**：
   数值相关（已有）+ AST 结构相似（已有 expr_id 哈希，需补子树同构距离）+ 假设语义距离（新增）。
   在假设生成与因子工程**两个环节**分别注入，而不是只在末端打分。
4. **命名冲突预警**：仓库现有 P10/P12 是 2026-09-10 的整改补丁号（`log_retention_service.py`、
   `full_auto/midlong_helpers.py`），与本文晋级评审关卡 P10/P12 撞名。落地时建议晋级关卡显式命名为
   **REV-P10 / REV-P12**，且直接挂到 `lifecycle.py` 已有的 `APPROVAL_REQUIRED`（→SMALL_LIVE / →ACTIVE）审批点上，
   不新造第二套状态机。

---

## 1. 范式研究

### 1.1 AlphaAgent（arXiv 2502.16789，KDD 2025）

《AlphaAgent: LLM-Driven Alpha Mining with Regularized Exploration to Counteract Alpha Decay》。

**三 agent 结构**：
- **Idea Agent**：链式推理整合外部知识（金融理论/实证直觉/研报/市场观察），产出结构化假设 h，
  含五组件：知识、市场观察、论证、假设本体、实现规格（窗口等参数约束）。初始种子 h₀ 由专家提供，此后由反馈驱动迭代。
- **Factor Agent**：把假设翻译为因子表达式——在算子库上做**符号组装**成 AST（叶=原始特征，内部节点=算子）；
  为每个假设生成多个候选 → 按复杂度与对齐指标过滤 → 迭代优化。维护**失败案例库**（按失败模式分类：假设不一致、复杂度超标…），生成时主动规避。
- **Eval Agent**：多维回测（IC/RankIC/AR/IR/MDD/稳定性）+ 可执行性/数值稳定性检查 + 与现有因子池相似度检索；
  维护评估历史，归纳成功/失败模式反馈给 Idea Agent。

**正则化探索（本文对 ADR-21 最直接的依据）**：

```
f* = argmax  L(f(X), y) − λ·R_g(f, h)
R_g(f,h) = α₁·SL(f) + α₂·PC(f) + α₃·ER(f,h)
ER(f,h)  = β₁·S(f) + β₂·C(h,d,f) + β₃·log(1+|F_f|)
```

- `SL(f)` 符号长度（复杂度）；`PC(f)` 自由参数个数；`log(1+|F_f|)` 惩罚使用过多原始特征。
- `S(f)` **原创性惩罚** = 对既有 alpha zoo 取最大相似度；两因子相似度由 **AST 最大同构公共子树的节点数**定义
  （子树同构检测量化结构重叠，比数值相关更能识别"换皮因子"）。
- `C(h,d,f)` **假设-因子语义对齐**（LLM 打分 ∈[0,1]）：c₁ 评估因子描述 d 是否为假设 h 的有效实现，
  c₂ 评估表达式与描述的语义一致性（例：描述声称刻画流动性但表达式不含成交量 → c₂ 低）。
- 目标非凸 → L 与 R_g 交替优化。

**抗衰减机制对应两成因**：过拟合 → 三重复杂度惩罚；因子拥挤 → AST 相似度约束迫使 LLM 放弃 RSI/动量类已知结构。
**关键数字**：CSI500 测试期 IC 0.0212 / 年化超额 11.00% / IR 1.488，全面超 LSTM/AlphaForge/RD-Agent 等基线；
有效因子比率 0.29 vs 无正则 0.16（**+81%**）；5 年逐年看 Alpha158/GP/RSI 的 IC 衰减到≈0 而 AlphaAgent 因子 IC 稳定≈0.02；
与 RD-Agent 对照：RD-Agent 各轮 IC 方差小（同质化/拥挤），AlphaAgent 均值更高且方差随轮次扩大（探索更广）。

### 1.2 RD-Agent(Q)（arXiv 2505.15155，NeurIPS 2025，microsoft/RD-Agent 开源）

《R&D-Agent-Quant: A Multi-Agent Framework for Data-Centric Factors and Model Joint Optimization》。

**五单元两阶段**（Research：Specification + Synthesis；Development：Implementation + Validation；枢纽：Analysis）：
- **Specification**：元组 S=(B,D,F,M)——背景先验、数据接口、输出格式、执行环境（Qlib 回测）。任何因子必须：输入取自 D、输出落入 F、在 M 中可执行。**LLM 从不接触原始行情与时间切分，只看 schema 级信息（防泄漏）**。
- **Synthesis**：模拟研究员试错。维护 SOTA 集合，按动作类型筛选历史假设集与反馈集，生成新假设，形成"假设森林"；成功→提升复杂度，失败→结构调整或换变量。
- **Implementation（Co-STEER）**：检索知识库中最相似历史任务的代码作参考再生成；任务失败则复杂度分数 α+=δ 并重排序（先做更简单的任务积累知识）；内层修复循环上限 10 次，单任务 600s。
- **Validation**：**IC 去重**——新因子与 SOTA 池逐日截面相关取时序均值，最大 IC≥0.99 判冗余剔除；剩余进 Qlib 回测。
- **Analysis（反馈枢纽）**：评估当前实验、更新 SOTA、诊断失败原因生成改进建议回传 Synthesis；
  用**两臂 Thompson 采样 bandit**（状态=8 维指标向量）决定下一轮优先优化因子还是模型。

**经验学习**：知识库持续增长 (任务, 代码, 反馈) 三元组，text-embedding-ada-002 嵌入，新任务按嵌入相似度检索参考代码（可取超阈值 θ 的集合）；假设文本 Sentence-BERT 嵌入 + 层次聚类用于复盘探索模式（局部精炼/战略性回访/多路径协同）。

**关键数字**：CSI300 测试期 IC 0.0532 / IR 1.74；约 2× 年化收益、因子数 −70%、总成本 **<$10**；
消融显示 bandit 调度 > LLM 调度 > 随机；因子分支是信号质量主驱动，模型分支主要平滑回撤。

### 1.3 取舍：为什么是"两者拼接"

| 维度 | AlphaAgent 给的 | RD-Agent(Q) 给的 | 本设计采用 |
|---|---|---|---|
| 循环骨架 | 三 agent 单线循环 | 五单元 + 反馈枢纽 + bandit 调度 | RD-Agent 骨架（五 agent 天然对齐） |
| 对抗衰减 | 正则化目标函数（核心贡献） | IC≥0.99 去重（较粗） | AlphaAgent 正则（ADR-21，扩展三分量） |
| 经验记忆 | 失败模式分类库 | (任务,代码,反馈)三元组+嵌入检索+DAG调度 | RD-Agent 结构（落 v7_lessons SQLite） |
| 假设质量 | 假设五组件 schema | SOTA 驱动的假设森林 | AlphaAgent schema（更严） |
| 成本控制 | 交替优化省 token | 600s/任务、6h/模块预算 | RD-Agent 预算纪律 |
| 防泄漏 | — | LLM 只见 schema | RD-Agent 纪律（沿用） |

注：文献情报 agent（①）是两篇论文都没有完整对应的——RD-Agent 主打经验驱动、文献只是可选输入。
① 承接的是我们 v0.3 因子研究中心的原有职能（网站监控/人工投喂→知识抽取），是本设计对两范式的**自有扩展**。

---

## 2. 五 Agent 设计

统一约定：所有 agent 产出走**结构化 JSON 落盘**（`backend/data/factors_lab/`），不共享内存态；
LLM 调用统一走现有 LLM 网关；每个 agent 独立可测、可单独人工触发（半自动降级模式）。

### ① 文献情报 agent（原因子研究中心职能延续）

- **输入**：a) 监控源清单：arXiv q-fin / SSRN 摘要页 / WorldQuant BRAIN 社区公开帖 / 竞品研报目录页；
  b) 人工投喂入口（API + 前端表单：粘贴摘要/DOI/URL/PDF 文本）。
- **处理**：抓取 → 去重（DOI/标题哈希）→ LLM 抽取**知识卡（knowledge card）**，schema：
  `{paper_id, title, source, date, phenomenon(现象), mechanism(经济学机制), testable_claims[](可检验命题), data_requirements, applicable_regime(趋势/震荡/高波), horizon, cited_formulas_meta(仅元数据), quality_score}`。
- **输出**：知识卡写入 RAG（`rag_knowledge_service` 新增 collection `factor_knowledge`，复用 bge-large-zh-v1.5 + ChromaDB），
  并产出日度摘要供 ② 拉取。
- **版权纪律（v0.3 保留）**：只存元数据 + 自写摘要 + 页码级引用；不存付费全文原文、不逐字复制付费研报公式实现。
  WorldQuant 101（Kakushadze 2016, arXiv 公开）与 GTJA 191（公开研报流传）属校准集例外，走校准通道。
- **现有锚点**：`intelligence_routes.py` / `news_feed.py`（情报中心基建，改造成学术源）；`rag_knowledge_service.py` 直接扩 collection。
- **新建量**：源适配器 + 知识卡 schema + 抽取 prompt。量小。

### ② 假设生成 agent

- **输入**：知识卡 RAG top-k + **经验记忆**（v7_lessons 教训 + 新增"假设层经验"表，见 ⑤）+ **市场状态**
  （当前 regime / 波动率分位 / 资金费率环境 / 最近因子池衰减事件——接 `decay_trigger` 与 `drift_watcher`）。
- **处理**：AlphaAgent 五组件假设 schema：
  `{hyp_id, knowledge(引用知识卡/理论), observation(近期市场观察), argument(论证), hypothesis(可检验命题), spec(窗口/字段/方向约束), expected_ic_sign, horizon(scalp|midlong), applicable_regime, diversity_note(与近期假设的差异声明)}`。
  生成时**前置注入多样性正则**：候选假设的嵌入向量与"近 30 天已立假设簇"质心的最大相似度超阈值 → 要求重生成（见 §3）。
- **输出**：每轮 N 条假设（建议 N≤5，RD-Agent 纪律：少而精）。
- **现有锚点**：`llm_proposal.py` 的 prompt 构造已是雏形（被拒因子提示 + 活跃公式避重 + DSL 函数表 + 周期域约束）——
  本 agent 是把它从"直接产公式"上移一层到"先产假设"。`alpha_miner.py` L721 已埋点
  "[2026-09-07 AlphaAgent 假设-因子语义对齐]"，团队已开始吸收该论文。
- **新建量**：假设 schema + 多样性前置检查 + 市场 state 注入。中。

### ③ 因子工程 agent

- **输入**：一条假设（含 spec 约束：字段域/窗口域/方向）。
- **处理**（AlphaAgent 流水线）：
  1. 每假设生成 **2~4 个候选 AST**（JSON AST，非 Python 代码——继承 expr DSL 的安全模型）；
  2. **编译校验**：`expr/parser.parse()` 先 audit 后编译，结构错误与 look-ahead 在 parse 期拦截（现成）；
  3. **最小单测**（新增）：合成数据数值性质测试——常数输入→常数输出、时间平移不变性、NaN 头部对齐、
     方向符合 `expected_ic_sign` 的构造性检查（例：假设说"超买回落"则因子对 close 单调性应为负）；
  4. **沙箱兜底**：不走 AST 的少数提案（numpy 公式源码，沿 `llm_proposal` 路径）必须过 `code_safety.py`
     受限 eval（`{"__builtins__": {}}`，无 IO/import/属性访问）；
  5. 按"复杂度 + 假设对齐打分（c₁/c₂）"过滤，输出排序候选。
- **输出**：候选因子注册进 **factors_lab 隔离库**（`source=agent_lab`），**不动主交易热路径**。
- **现有锚点**：`expr/{parser,ops,audit}.py`（白名单 + 审计 + expr_id 规范化哈希去重，全部现成）、
  `formula_ops.py`（ts_* 算子库）、`code_safety.py`、`custom_factor_store.py`（factors_lab 建议为其独立 namespace 实例）。
- **新建量**：最小单测生成器 + 对齐打分 prompt + factors_lab namespace。小-中。

### ④ 回测工程师 agent

- **输入**：③ 的候选因子 ID 集 + horizon 标注。
- **处理**：调用工具层 **gpfactor / gpbacktest**（对现有栈的包装命名，落地为 `factors_lab/tools/` 下两个入口）：
  - `gpfactor`：批量计算因子值 + 性能评估——包装 `gp_miner` 适应度栈 / `gp_gpu_eval` GPU 批量求值（相关性惩罚项在此复用为 ADR-21 分量）；
  - `gpbacktest`：回测与门禁——包装 `factor_backtest_scorer` + `real_factor_backtest` + `evaluation.py` 门禁 + `dsr_pbo` + `decay_trigger`。
  产出：**IC/RankIC/ICIR 时序、分组收益（quantile）、换手率、衰减曲线（IC 半衰期）、CPCV、DSR、PBO、容量**。
- **输出**：结构化评估报告 JSON（喂 ⑤）。
- **预算纪律（RD-Agent）**：单因子评估超时（建议 600s）跳过并标记；每轮总回测时长上限。
- **新建量**：工具包装层。小（核心全是现成的）。

### ⑤ 反馈 agent

- **输入**：④ 的评估报告 + ② 的假设 + ③ 的实现记录。
- **处理**：
  1. **结果归因**：IC 损失分解——衰减（半衰期短）/ 拥挤（与池相关上升）/ regime 错配（分层 IC 按 regime 切）/ 实现缺陷（单测边缘案例）；
  2. **写经验记忆**：成功配方 / 失败案例（按 AlphaAgent 失败模式分类：假设不一致、复杂度违规、门禁未过、数值不稳）/
     门禁教训 → 写入 `evolution_memory_v7` 的 v7_lessons（kind 扩充 `hypothesis_layer`）+ 新增**假设层经验表**
     （(假设, 结果, 归因) 三元组 + 嵌入，供 ② 检索——即 RD-Agent 的 K 库）；
  3. **修正下一轮假设**：归因结论生成"下一轮建议方向"注入 ② 的 prompt（RD-Agent Analysis→Synthesis 通路）；
  4. **多样性正则评估**：月度体检——因子池相关矩阵谱、AST 相似度分布、假设簇纯度，产出 ADR-21 健康报告。
- **现有锚点**：`evolution_memory_v7.py`（SQLite，quality/use_count/last_used_at/status=retired 的衰减淘汰机制现成，
  直接符合"不靠时间倒序"的要求）。
- **新建量**：归因 prompt + 假设层经验表 + 月度体检脚本。中。

---

## 3. 多样性正则（ADR-21）

**状态**：仓库目前无 ADR 体系（全库无 ADR- 前缀文档）。建议建立 `docs/adr/` 目录，本文设计作为 **ADR-21** 首篇录入，保留 v0.3 编号。

**形式**（AlphaAgent 式，三分量扩展）：

```
score(f, h) = L(f) − λ · R(f, h)
R(f, h) = β₁ · corr_sim(f, pool)      # 数值层：与既有因子池的最大截面相关（时序均值）
         + β₂ · ast_sim(f, pool)      # 结构层：AST 最大同构公共子树占比（换皮检测）
         + β₃ · hyp_sim(h, hist)      # 逻辑层：假设嵌入与既有假设簇质心的最大余弦相似度
         + α₁ · nodes(f) + α₂ · params(f) + α₃ · fields(f)   # 复杂度（AlphaAgent SL/PC/|F|）
```

- **corr_sim**：已有对应物——`gp_miner` 适应度里的 λ2×与精英池最大相关、`lifecycle` 的 `max_incremental_corr=0.50`。
- **ast_sim**：`expr_id` 规范化哈希已可精确去重；需补**子树同构相似度**（可用简化版：公共算子链 n-gram Jaccard 起步，精确同构后补）。
- **hyp_sim**：新增，嵌入复用 bge-large-zh（无需新模型）。

**两个环节显式优化（不是只在末端打分）**：
1. **假设生成时**（②）：候选假设 hyp_sim 超阈值 → 拒收并要求重生成（探索前置）；
2. **因子工程时**（③）：多候选排序按 R 加权，corr_sim/ast_sim 高的候选降序甚至丢弃（RD-Agent 的 IC≥0.99 去重作为硬闸保留）。

**对抗对象（论文归因）**：过拟合→复杂度分量；拥挤→corr/ast 分量；伪逻辑→假设对齐打分（c₁/c₂，alpha_miner 已有雏形埋点）。

---

## 4. 闭环编排与人的位置

**轮次状态机（无人值守）**：

```
IDLE → LITERATURE_SCAN(①) → HYPOTHESIZE(②) → ENGINEER(③) → BACKTEST(④)
     → ATTRIBUTE(⑤) → MEMORY_WRITE(⑤) → (回 HYPOTHESIZE，直至本轮预算耗尽) → COOLDOWN
```

- **预算**：每轮 token 上限 / 回测机时上限 / 假设条数上限（建议≤5）；单任务超时 600s（RD-Agent 纪律）。
- **失败处理**：编译失败→按 AlphaAgent 失败模式降复杂度重试（≤3 次）→仍败则记 failure_case；
  连续 N 轮零有效产出→触发"探索停滞"告警（多样性体检介入）。
- **看护**：watchdog 复用既有 2 分钟自愈模式（轮54 已有后端中断自愈经验）。

**人只在晋级评审**：P10 / P12（沿用 v0.3 编号）= **辩论 + 人工**。
- 对齐现有状态机：`lifecycle.py` 的 `APPROVAL_REQUIRED = {SMALL_LIVE, ACTIVE}` 审批点即评审关卡，
  超时默认拒（24h）的纪律保留——**闭环只推进到 PAPER，晋级由 ShadowJudge 周期驱动 + 评审放行**。
- 辩论层复用 `trading_analysts.py` 多空辩论基建（AAAI 2025 TradingAgents 论文依据已在库）：正方（因子辩护人，
  持假设卡+评估报告）/ 反方（审计人，持过拟合/拥挤/容量质疑）/ 裁判计分（`ai_prompt_layers` debate 计分模式）。
- ⚠️ **命名冲突**：仓库 P10/P12 已被 2026-09-10 补丁占用（`log_retention_service.py`、`full_auto/midlong_helpers.py`）。
  落地命名 **REV-P10（PAPER→SMALL_LIVE）/ REV-P12（SMALL_LIVE→ACTIVE）**。

**状态机不变**（v0.3 纪律）：`DRAFT→CANDIDATE→ORTHO→PAPER→SMALL_LIVE→ACTIVE→DEWEIGHT→QUARANTINE/REJECTED`
九态与阈值（min_icir=0.40、max_incremental_corr=0.50、max_pbo=0.35、paper_min_days=10 等）原样；
五 agent 闭环只在 DRAFT 之前工作，不触碰状态机语义。

---

## 5. 保留的 v0.3 纪律与现状差距

| v0.3 纪律 | 现状 | 差距动作 |
|---|---|---|
| WorldQuant 101 校准 | `alpha101_factors.py`（seed 灌库）+ 隔离区 20 因子迁移版 | 建**回归门禁**：expr DSL 重实现 101 子集 vs 参考实现，IC/数值容差断言，进 CI |
| GTJA 191 校准 | 无实现（仅调研文档提及） | 排期实现 191 中适合加密分钟级的子集（先 30~50 个），同上回归 |
| 版权纪律 | 未成文 | 写入 ① 的硬约束（§2①），只存卡片不存原文 |
| RD-Agent 对照引擎 | 调研文档已有（FACTOR_MINING_RESEARCH L122-169） | AR-4 里程碑：同数据跑 microsoft/RD-Agent factor loop vs 本五 agent，验收"IC 不低于对照、因子数与成本相当" |
| factors_lab 隔离库 | 事实对应物 `custom_factor_store` + `ai_gen_quarantine` | 建 factors_lab 独立 namespace（source=agent_lab），增量模块纪律：不改主库 schema、不进热路径（同 v7_memory 先例） |
| 状态机 | `lifecycle.py` 完整 | 不动 |

---

## 6. 落地映射总表（现有资产 → 五 agent）

| 现有资产 | 供给 agent | 说明 |
|---|---|---|
| `llm_proposal.py` | ②③ | prompt 构造雏形（被拒提示/活跃避重/DSL 函数表/周期域） |
| `expr/{parser,ops,audit}.py` | ③ | 白名单编译 + look-ahead 审计 + expr_id 去重 |
| `formula_ops.py` | ③ | ts_* 算子库（受限 eval 安全模型） |
| `code_safety.py` | ③ | 沙箱兜底 |
| `gp_miner.py` / `gp_gpu_eval.py` | ④ | gpfactor 工具内核；λ2 相关惩罚 → ADR-21 corr_sim 分量 |
| `factor_backtest_scorer` / `real_factor_backtest` | ④ | gpbacktest 工具内核 |
| `evaluation.py` / `dsr_pbo.py` / `decay_trigger` | ④ | CPCV/DSR/PBO/容量门禁 |
| `evolution_memory_v7.py` | ⑤ | 教训库（quality/use_count/retired 淘汰机制现成） |
| `rag_knowledge_service.py` | ①② | bge-large-zh + ChromaDB，扩 `factor_knowledge` collection |
| `intelligence_routes` / `news_feed` | ① | 网站监控基建改造为学术源 |
| `lifecycle.py` / `shadow_judge.py` | 晋级 | REV-P10/REV-P12 挂 APPROVAL_REQUIRED |
| `trading_analysts.py` 辩论层 | 晋级 | 辩论评审复用 |
| `alpha_miner.py` L721 语义对齐埋点 | ②③⑤ | c₁/c₂ 对齐打分雏形 |

**真正的新建物清单**（按依赖序）：文献源适配器+知识卡 schema → 假设 schema+多样性前置 →
最小单测生成器+factors_lab namespace → gpfactor/gpbacktest 工具包装 → 假设层经验表+归因 prompt →
ADR-21 打分器（ast_sim/hyp_sim 两分量）→ GTJA 191 子集 → 辩论评审接线 → watchdog+预算。

---

## 7. 实施路线（四里程碑）

- **AR-1 骨架跑通**（先垂直切一条线）：factors_lab namespace + 五 agent 接口骨架 + 假设 schema +
  一轮端到端（人工投喂 1 篇论文 → 假设 → DSL 因子 → gpfactor/gpbacktest → 归因落 v7_lessons）。
  验收：全链 JSON 落盘可复盘；编译失败率 <30%；单轮 <2h。
- **AR-2 记忆与文献成型**：知识卡抽取管线 + 监控源 2 个 + 人工投喂入口 + 假设层经验表 + RAG 集成。
  验收：② 的 prompt 同时含知识卡 top-k 与经验 top-k；连续 3 轮无重复假设（hyp_sim 验证）。
- **AR-3 正则与校准**：ADR-21 三分量打分上线（假设前置 + 候选排序两环节）+ WorldQuant 101 DSL 回归门禁进 CI +
  GTJA 191 前 30 个实现。
  验收：换皮因子（仅改窗口/符号的变体）被 ast_sim 或 corr_sim 拦截率 >90%；101 回归全绿。
- **AR-4 无人值守与对照**：watchdog + 轮次预算 + 探索停滞告警 + 辩论晋级评审（REV-P10/REV-P12）接线 +
  RD-Agent 对照实验。
  验收：72h 无人值守连续运行无人工干预；对照实验报告产出（IC/因子数/成本三列对比）。

---

## 8. 风险与对策

| 风险 | 对策 | 现有依托 |
|---|---|---|
| LLM 幻觉公式 | 编译审计+最小单测+沙箱三道闸；AST 优先于自由代码 | expr audit / code_safety |
| 过拟合海选 | CPCV/DSR/PBO 门禁不放宽；复杂度分量入正则 | dsr_pbo / lifecycle 阈值 |
| 循环空转（同质假设刷分） | hyp_sim 前置拒收 + 停滞告警 + 轮次预算 | ADR-21 / watchdog |
| 相关性作弊（符号翻转换皮） | 符号反作弊（已有）+ expr_id 哈希 + ast_sim | llm_proposal 反作弊 / expr_id |
| 经验记忆污染 | quality 分 + use_count + retired 淘汰，不靠时间倒序 | v7_lessons 机制 |
| 算力失控 | GPU 批量求值 + 单任务 600s + 每轮机时上限 | gp_gpu_eval / RD-Agent 纪律 |
| 文献版权 | 只存知识卡元数据+自写摘要；付费原文不落库 | §2① 硬约束 |
| LLM 见原始数据泄漏 | LLM 只见 schema 与评估摘要，不见行情明细 | RD-Agent 防泄漏纪律 |

---

## 9. 参考文献

- AlphaAgent: LLM-Driven Alpha Mining with Regularized Exploration to Counteract Alpha Decay（[arXiv:2502.16789](https://arxiv.org/abs/2502.16789)，KDD 2025）
- R&D-Agent-Quant: A Multi-Agent Framework for Data-Centric Factors and Model Joint Optimization（[arXiv:2505.15155](https://arxiv.org/abs/2505.15155)，NeurIPS 2025；开源 [microsoft/RD-Agent](https://github.com/microsoft/rdagent)）
- Kakushadze, Z. 101 Formulaic Alphas（arXiv:1601.00991，WorldQuant 101 校准集来源）
- 仓库内部：`docs/FACTOR_MINING_RESEARCH_2026-08-17.md`（RD-Agent 调研）、`docs/factor_pipeline_upgrade_design.md` v3.0（R0/R2/R3/G15）、`docs/factor-system-lifecycle-design.md`
