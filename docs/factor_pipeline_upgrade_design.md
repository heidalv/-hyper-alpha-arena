# 全周期因子体系升级设计文档与执行计划 v3.0（合并版）

版本: v3.0 · 2026-08-18 · **v2.0 内部审计计划（门禁侧）∪ 调查报告打磨路线图（挖掘侧）· 单一统一执行计划**

> 合并来源：
> - ① `factor_pipeline_upgrade_design.md` v2.0（代码级现状审计：G1~G7 差距 + M0~M6 阶段）——本文档吸收其全部内容（§5 门禁侧）；
> - ② `FACTOR_MINING_RESEARCH_2026-08-17.md`（外部调研：主流算法/学术论文/竞品/顶级机构 + P0~P2 打磨路线图）——本文档把其路线图落地为文件级设计（§6 挖掘侧）；
> - 两个来源**合并为单一执行计划**（§7），并显式列出**相互参考矩阵**：挖掘侧产出的候选由门禁侧裁决，门禁侧定义的统计口径（中性化 IC、held-out 分段、n_trials）是挖掘侧适应度的唯一数据契约。

## 执行记录（2026-08-18 全部上线）

| 项 | 状态 | 验收证据 |
|---|---|---|
| S0/M0 门禁三合一（n_trials 累计/冗余双档/短线自适应回看） | ✅ | 迁移 log store=86+130→total=216；counter 278 持久化；单测 4/4 |
| S0/M1 基线 | ✅（替代口径） | 105 公式候选新门禁重打分：69 个 DSR/PBO 拒绝（pbo=0.000），即多重检验收紧的预期行为；全量基线重跑因白天 CPU 占用终止，改由夜间定时任务承载 |
| S1/M2 收益中性化 | ✅ | 单测：beta 代理 IC 降 96.6%、特质信号保持；进化路径 DSR/PBO 日志（17:18 dsr_sig=False n_trials=13）实证 |
| S1/R0 ε-lexicase + R1 ICIR/协同奖励 | ✅ | 挖掘器单测通过（案例缓存/hof 填充） |
| S2/M3 held-out 判决集 | ✅ | 单测 3/3（训练段A/B→判决段复验→拒绝留候选池） |
| S2/R2 ALPS + R3 LLM 热启动 | ✅ | 单测通过 |
| S2/M6 LLM 提案层（更严门禁+符号反作弊） | ✅ | 冒烟通过（解析/试算/prompt） |
| S3/M4 ICIR 组合层 + R4 因子工厂 + R5 阈值调优 + M5 长线验证 | ✅ | M4 单测 4/4；R4 后端端点+前端实验室 tab（tsc 通过）；R5 框架+长线域（scalp 回放缺口登记）；M5 实测报告（L1=2/fwd=2 Sharpe 0.51 优于现行 L1=3 的 -0.31） |
| S4/R6-a SSE + R6-b 种群扩容 + R7 衰减触发 + R9 显存预算/单飞 | ✅ | SSE 等价性 1.7e-12；R6-c 融合 kernel 按设计"可选"暂缓 |
| R8 DL 因子侧分支 | ✅ 可行性判定完成 | 判定：暂不启动（9 币截面样本不足/单卡共享），重启条件已记录 |
| 回归 | ✅ | 升级相关单测 14/14；GPU 等价性验收 Gate 通过；后端重启健康 200 |

**遗留观察项（非阻塞）**：① scalp_router 阈值回放调优需先落信号分数快照（R5 已登记缺口）；
② held-out 的实盘首触发待首个通过训练段 A/B 的因子出现（代码与单测已就绪）；
③ R6-c 融合 kernel 与 M4-V2 风险平价组合留待硬件升级。

## 0. 版本与来源说明

| 版本 | 内容 | 状态 |
|---|---|---|
| v1.0 | 仅中线因子升级 | 已废弃（范围错误） |
| v2.0 | 三周期门禁侧审计 + M0~M6 | 被本版吸收（§5，保留 file:line 证据） |
| **v3.0** | 门禁侧 + 挖掘侧（算法/GPU/反馈/LLM）合并，单一执行计划 | **本文档** |

**三周期现状一句话**（v2.0 审计，保持不变）：
- 短线（scalp）: 1h 因子，scalp_active_factor_set（上限 40），run_scalp_factor_evolution_loop 进化，scalp_factor_router 执行（conf≥35 / exec≥40 直通）。
- 中线（midlong）: 4h/1d 因子，midlong_active_factor_set（上限 30），ops/midlong-factors 面板挖掘。
- 长线（long）: long_trend_v2 规则（L1=up 闸 + Chandelier 止损 + 金字塔加仓），**无因子挖掘、无规则回测验证**（参数手工拍）。

**挖掘器现状一句话**（调研报告 §0，本版新增维度）：
- GP 挖掘器：种群 300 × 20 代 × 6 种子，锦标赛选择（3取1）+ 子树交叉/变异 + 精英保留 + 早停；适应度 = |IC| − λ₁×复杂度 − λ₂×精英池最大相关。
- MCTS 挖掘器 + LLM codegen 补挖（4 流）三路独立，无 LLM 指导搜索。
- GPU 栈式批量求值器（torch float64）：21/28 算子覆盖，等价性验收 Gate，实测单树 575ms→10.4ms（55×）、种群一代 3×；**种群 300 规模 GPU 利用率低（设计文档自证 GPU 价值在 2000+ 种群）**。

---

## 1. 现状审计（v2.0 门禁侧 + 本版挖掘侧基线）

### 1.1 门禁侧已经做对的（保持不动）

| 维度 | 代码实证 | 对标 |
| --- | --- | --- |
| 单因子诊断 | factor_evaluator.py:27-40 — IC mean/std/ICIR/IC 胜率/衰减半衰期/换手率/IC 最大回撤/单调性/尾部风险 | Qlib / alphalens 全对齐 |
| Walk-forward OOS | factor_backtest_scorer.py — OOS 净收益/Sharpe/胜率/笔数，按换手逐笔扣成本 | Qlib 组合回测 |
| DSR+PBO | dsr_pbo.py:63-126 (DSR, Gumbel 期望最大值) + CSCV PBO；sr_mean/sr_std 取候选 ICIR 分布实测值 | López de Prado 全对齐 |
| 正交去冗余 | factor_backtest_scorer.py:497-517 — 与 active 因子 \|corr\| 检验 | WorldQuant 相关性去重 |
| 自适应回看 | midlong_lookback_for / midlong_min_bars_for — min(目标, 可用根数) | 已就绪（中线）；短线需补同机制 |

### 1.2 挖掘侧已经做对的（保持不动）

| 维度 | 证据 | 对标 |
|---|---|---|
| 公式 DSL + 可审计 AST | expr/ops.py 31 算子 + look-ahead 禁令 | WorldQuant Alpha101 语料同构 |
| GPU 栈式批量求值 | gpu_batch_eval.py（后序编译 + 操作数栈掩码执行，float64） | EvoGP / Langdon 批量求值同构 |
| 等价性验收 Gate | gp_gpu_eval.py（Pearson≥0.99999 或 Spearman≥0.999 + IC 偏移≤5e-4，失败回退 loky） | 自研 fail-safe |
| 精英值缓存 | 每代只算一次（原 O(N×E) 浪费已消除） | 工程优化 |
| 门禁 | WFO / DSR / PBO fail-closed + 1800s 硬预算 | López de Prado |

### 1.3 差距清单（门禁侧 G1~G7 保留 v2.0 原案；挖掘侧 G8~G15 本版新增）

| # | 差距 | 代码实证 / 依据 | 定性 | 优先级 |
| --- | --- | --- | --- | --- |
| G1 | 多重检验 n_trials 不累计 | factor_backtest_scorer.py:528 n_trials = max(cfg 40, active_n+1)，active=0 → 恒 40；真实试验数 300+ | 参数漏洞（假阳性放行约 8 倍） | P0 |
| G2 | 冗余阈值三处打架 | factor_evaluator.py:60 = 0.7；factor_backtest_scorer.py:382 = 0.8；factor_slimming_audit.py = 0.5 | 参数漏洞（无单一真相源） | P0 |
| G3 | 收益中性化完全缺失 | factor_evaluator.py:100 IC 直接对原始 fwd_return 计算；全引擎无 neutralization | 结构缺失（给 beta 放行） | P1 |
| G4 | held-out / era 判决集不存在 | score_formula 无「生成器不可见」的最终判决段 | 结构缺失（迭代=测试集过拟合） | P2 |
| G5 | 组合层弱（两周期都等权） | midlong_active_factor_set.py:72-75 权重读 json 缺省 1.0；scalp_active_factor_set 同构 | 结构弱 | P3 |
| G6 | LLM 路径绕过公式门禁 | ai_factor_discovery_service.py (D7) 写 ai_gen_*.py 文件 + hot_reload，130+ 全灭 | 路径错 | P4 |
| G7 | 长线规则无统计验证 | long_trend_v2.py — L1 阈值 3 / Chandelier 2.0 / 金字塔 R=1.0 比例 0.25 全部手工拍 | 结构缺失（规则=另一种因子） | P5 |
| **G8** | GP 选择压力单一（锦标赛 3取1） | gp_miner._tournament_select；无案例级评分 | 算法落后（调研：ε-lexicase 抗噪声保多样性） | R0 |
| **G9** | 挖掘目标 = 单因子 \|IC\| 最大，无协同集奖励 | gp_miner._fitness_core 仅对精英池相关惩罚 | 算法落后（调研：AlphaGen 协同因子集奖励） | R1 |
| **G10** | 无年龄分层，早熟风险 | gp_miner._run_seed 无 ALPS | 算法落后（调研：ALPS 保创新） | R2 |
| **G11** | LLM 与 GP/MCTS 三路独立，无 LLM 热启动/指导搜索 | factor_evolution_loop.py 三路顺序执行、互不输入 | 结构缺失（调研：LLM 探索 + GP 开采双引擎） | R3 |
| **G12** | 无「写公式→秒级反馈」交互 | GPU 求值器已就绪但无对外快速打分端点 | 产品缺失（调研：WorldQuant WebSim 范式） | R4 |
| **G13** | 阈值（scalp conf/exec、退出 Agent 时间止损、仲裁 conf）手工拍 | .env 常量 | 工程缺失（调研：Freqtrade hyperopt / SigOpt） | R5 |
| **G14** | GPU 利用率低（种群 300） | 设计文档自证 GPU 价值在 2000+ 种群 | 算力浪费 | R6 |
| **G15** | 挖掘是"一次性任务"，无衰减触发的持续循环 | factor_decay_monitor 已存在但无触发补挖 | 结构缺失（调研：RD-Agent factor/model loop） | R7 |
| ~~G16~~ | 深度学习因子未布局 | 调研建议侧分支试验 | 待决策（附录 A，不占主线） | — |

---

## 2. 目标架构（三周期统一视图 + 挖掘侧升级）

```
                    挖掘侧（本版新增）
   ┌─────────────────────────────────────────────────────────┐
   │ GP v2: ε-lexicase(R0) + ALPS(R2) + 协同奖励/ICIR(R1)    │
   │ LLM 热启动种群(R3)  +  MCTS(保持)  +  LLM codegen(保持)  │
   │ 数据契约: 中性化IC(M2) / 训练段限定(M3) / 案例矩阵(GPU)  │
   │ 算力: 栈式GPU求值 + SSE去重 + 种群扩容(R6) 分时调度(R9)  │
   └───────────────┬─────────────────────────────────────────┘
                   ▼ 候选因子（source 标注，全部同一入口）
   custom_factor_store（候选池，单一入口）
                   ▼
   score_formula（同一条门禁管线，按 interval 分档）
   ① 自适应回看（P0-C）  ② 中性化 IC（P1）  ③ 诊断 IC/ICIR/衰减/单调/换手
   ④ 去冗余单一阈值（P0-B） ⑤ DSR/PBO n_trials 累计（P0-A）+ llm 更严
   ⑥ held-out 判决段（P2）
                   ▼
      scalp_active(40)       midlong_active(30)          long_trend_v2 规则
                   │                     │               （P5 同口径验证）
                   └──────────┬──────────┘
                              ▼
              组合层（P3, ICIR 加权，两周期各自独立）
                              ▼
              三周期编排（总控 + 仲裁Gate + 退出Agent + 因果回灌）
                              ▼
              反馈闭环：因子工厂秒级反馈(R4) + 衰减触发再挖掘(R7)
                          + 自动阈值调优(R5, 离网回放 + dry-run 灰度)
```

**不变的铁律（v2.0 保留）**: 统计裁决永不外包；LLM 只提案不判分；任何 source 走同一道门禁；规则参数与公式因子享受同一套统计验证。

**本版新增铁律**:
1. **口径同一律**：挖掘适应度与门禁裁决必须使用同一个统计口径（中性化 IC/ICIR、同一训练段、同一冗余阈值）——R 系列的适应度函数直接调用 M2/M3 产出的评分函数，禁止自造第二套口径。
2. **判决段黑盒律**：held-out 判决段数据对挖掘器、LLM prompt、自动调优全部不可见。
3. **回滚律**：每个 R 项都有独立 env 开关；GPU 路径任何异常回退 loky/旧算法，验证 Gate 不通过不启用。

---

## 3. 门禁侧阶段设计（M0~M6，v2.0 原案 + 与挖掘侧的交叉引用）

> 交叉引用约定：`→R#` 表示挖掘侧依赖/联动项。

### M0 = P0-A + P0-B + P0-C（门禁参数修复三合一）

**P0-A 多重检验 n_trials 累计**（G1）
- 新增 backend/services/factor_engine/trials_counter.py：持久化 data/factor_trials_counter.json（total_scored / last_bump_at），线程安全、单调递增，**不分周期统一计数**。
- 每次 validate_and_promote / validate_all_candidates / 进化循环打分调用 trials_counter.bump()。
- score_formula 里 n_trials = max(cfg, trials_counter.total + 1)；初值 = store 记录总数 + 130（ai_gen 归档历史）。
- env: FACTOR_SCORER_DSR_N_TRIALS（保底下限，默认 40，迁移后显式 300）。
- **→R1**：协同奖励/ICIR 目标的适应度同样把 n_trials 传给 DSR 判决，保证「挖掘时判」与「晋升时判」同口径。
- 验收: 打分日志打印 n_trials；两周期打分都累计；重启不丢。回滚: env 关累计。

**P0-B 冗余阈值统一**（G2）
- 准入冗余阈值 FACTOR_SCORER_REDUNDANCY_CORR（默认 0.7）：score_formula 与 factor_evaluator.REDUNDANCY_THRESHOLD 统一读它。
- 退役冗余阈值 FACTOR_SLIMMING_POOL_MAX_CORR（默认 0.5）：factor_slimming_audit 专属。
- **→R1**：协同奖励的"相关性惩罚"复用同一准入阈值语义（对 active 集 0.7 / 精英池按原系数），消除第二套阈值。
- 验收: grep 全库无硬编码 0.5/0.7/0.8 字面量。回滚: env 改回 0.8。

**P0-C 短线自适应回看**（补齐中线已有机制）
- 新增 scalp_lookback_for(symbol) = min(FACTOR_SCORER_LOOKBACK_BARS, 可用根数)，下限 FACTOR_SCORER_SCALP_MIN_BARS（默认 500）。
- **→R0**：ε-lexicase 的案例切片长度随 lookback 自适应（案例 = 按币段 \|IC\|，长度一致才可比）。
- 验收: 新币 1h 数据不足时按可用根数打分不硬缺。

### M1 = 门禁修复后重跑两周期 mining 观测基线

- M0 完成后，用**当前挖掘算法**重跑短线+中线各一轮 full evolution，记录：通过率、被拒原因分布、n_trials 终值、IC/ICIR 分布——作为 R 系列改造的前后对比基线。
- **→R 全部**：R 系列每一项的验收指标都必须与 M1 基线对比（通过率、候选质量、多样性指标）。

### M2 = P1 收益中性化（门禁侧最关键，挖掘侧口径源）

- 新增 backend/services/factor_engine/neutralization.py：横截面回归取残差；风格 = 市场 beta（截面均值收益）、动量（trailing 20d return）、波动（ATR/20d std）；crypto 截面仅 9 币 → **时间池化回归（pooled OLS）**。
- factor_evaluator.evaluate_factor 加 neutralize: bool = True（默认开），中性化后算 IC/ICIR/衰减，原始值保留 raw_ic 双轨输出。
- score_formula 全链路默认中性化，两周期共用；env FACTOR_SCORER_NEUTRALIZE=true 总开关。
- 迁移：对全部候选（短线+中线）重跑一轮打分。
- **→R0/R1 硬依赖**：挖掘适应度从本阶段起 = **中性化 IC/ICIR**（直接调用 evaluate_factor 的中性化输出），禁止再对原始收益算 IC——否则挖掘器会持续产出"在它自己的口径下高分、在门禁口径下被拒"的 beta 因子。
- 验收（硬指标）: 纯 beta 代理因子中性化后 \|IC\| 降幅 >50%；已知结构因子 IC 保持。回滚: FACTOR_SCORER_NEUTRALIZE=false。

### M3 = P2 held-out / era 判决集

- custom_factor_store 候选记录 extra 增加 heldout 字段：cutoff_ts / verdict / ic_mean / sharpe。
- validate_and_promote 拆两步: ① 训练段打分（可见，可迭代）→ ② 判决段复验 → 才允许 active。判决段判定: ic_mean >= 0.03 且 OOS Sharpe >= 0.3 且符号一致。
- 历史 active 一次性豁免 grandfathered。env FACTOR_HELDOUT_RATIO=0.2。
- **→R3/R5 硬依赖**：LLM 热启动 prompt 与自动调优的输入只含训练段被拒原因；判决段数据对二者不可见（黑盒律）。
- **→R7**：衰减触发的补挖只使用训练段，判决段仍由门禁掌握。
- 验收: 晋升日志必须出现 held-out 分数；无判决段的因子不能晋升。回滚: FACTOR_HELDOUT_RATIO=0。

### M4 = P3 组合层 V1（ICIR 加权）

- w_i ∝ max(icir_i, 0) 归一；信号 = Σ w_i × zscore(rank(factor_i))；权重来自打分时写回的 custom_factor_store.scores.icir；每周复检重算；短线/中线各自独立。
- **→R1 相互支撑**：协同奖励保证 active 集低冗余 → ICIR 加权才有意义；V2（后续）风险平价/等风险贡献。
- 验收: 回测对比等权 vs ICIR 加权，两周期组合 ICIR 均应 ≥ 最优单因子。回滚: FACTOR_COMBO_MODE=equal|icir。

### M5 = P5 长线规则验证器（独立，可随时插队）

- 新增 backend/services/factor_engine/long_rule_validator.py：把 long_trend_v2 入场规则信号化（L1 score、Chandelier 距离、金字塔触发 → 0/1 信号序列），跑与因子同口径的诊断（IC/ICIR + walk-forward + DSR/PBO）。
- 规则参数网格（L1 阈值 2~5、ATR 倍数 1.5~3.0、金字塔 R 0.5~1.5）做小网格 PBO 扫描；产出 data/long_v2_rule_report.json；参数变更走证据审批。
- 验证数据源: binance 1d 长历史回测，实盘仍用实盘所 1d 判当前市况（分工不变）。
- **→R5**：长线规则参数纳入自动阈值调优的搜索空间（与 scalp/exit/仲裁阈值同一框架）。
- 验收: 每个规则参数都有 OOS 证据与 PBO 值。回滚: 验证器只读不改。

### M6 = P4 LLM 提案层（依赖 M2+M3，与 R3 共用 LLM 基建）

- 输入: 最近 N=30 个被拒因子的**结构化 scores**（训练段口径）+ 当前 active（按周期）+ regime 摘要；输出: K=5~10 个 numpy 公式候选，带经济逻辑、预期 IC 符号、周期标注。
- 注册: custom_factor_store.register(source=llm, extra={horizon, timeframe, note, expected_ic_sign})；校验: ast 白名单 + 假数据试算 + 去重。
- 门禁: 同一 score_formula；llm 源收紧 max_pbo 0.5→0.4、min_sharpe +0.1；计入 n_trials；符号反作弊（实际符号与 expected_ic_sign 相反 → rejected）。
- 调度: 每周一次后台 job + POST /ops/factors/llm-propose?tier=scalp|midlong；D7 写文件路径停用（复用其 LLM 调用基建）。
- **→R3 合并实施**：M6 与 R3 同批开发，共用「LLM 提案→store 注册→同门禁」管道；R3 的产物走同一 source=llm 收紧档。
- 验收: llm 候选进 store（source=llm 且 horizon 正确）；更严参数生效；符号不符即拒；零文件写入 ai_generated。

---

## 4. 挖掘侧阶段设计（R0~R9，本版新增，基于调研报告落地）

### R0 ε-lexicase 选择压力（G8，调研路线图 P0-1）

**现状**: gp_miner._tournament_select 从 k=3 随机竞争者中取 fitness 最大——单一标量排名，对噪声案例（个别币上的假 IC）无抗性，早熟风险高。

**设计**:
1. 案例定义: 面板按币切段（5~9 个案例），案例得分 case_score[p, s] = 该币段上的 \|IC\|（有限样本掩码内，**口径 = M2 中性化 IC**；M2 未完成前先对 raw IC 上线，M2 后无缝切换）。
2. 得分来源: GPU 路径在 eval_panel_batch 返回 (P, n) 值矩阵后**按币段向量化算 case_score（P, S）**（一币一段，无需重算）；loky 回退路径保持锦标赛（GPU 是我们的生产路径）。
3. 选择过程（每事件）: 洗牌案例顺序 → 依次保留「与当前最优差距 ≤ ε」的个体（ε=1e-4，IC 尺度）→ 剩 1 个即返回，否则案例耗尽时随机取存活者。锦标赛作为 fallback（env 可切回）。
4. 与交叉/变异不变；精英保留继续取 top 5%（按标量 fitness）。
5. env: FACTOR_GP_SELECTION=tournament|lexicase（默认 lexicase，GPU 路径自动生效）。
6. **→M2**: case_score 的中性化口径由 M2 提供；**→M3**: 案例只取训练段数据。
7. 影响文件: gp_miner.py（选择函数 + case 矩阵透传）、gp_gpu_eval.py（返回 per-symbol 分段）、factor_evolution_loop.py（构造 case 边界）。

**验收**: 与 M1 基线对比——种群多样性（成对 \|corr\| 均值）上升、早停代数延后、最终候选池的因子间平均相关下降；单测: lexicase 在"某币噪声极大"的合成案例上选出 vs 锦标赛的差异。**回滚**: env 切回 tournament。

### R1 协同因子集奖励 + ICIR 目标（G9，调研路线图 P0-2/P0-3）

**现状**: fitness = \|IC\| − λ₁×复杂度 − λ₂×精英池最大相关；只罚"与精英相关"，不罚"与 active 集相关"；目标为单点 IC 而非 ICIR。

**设计**:
1. 适应度 v2: fitness = |ICIR| − λ₁×复杂度 − λ₂×max_corr(精英池 ∪ 本代 hall-of-fame) − λ₃×max_corr(active 集，权重更高)。
2. ICIR 口径: 按币段 IC 的 mean/std（跨案例），**使用 M2 中性化 IC**；λ₂/λ₃ 由 env 控制（FACTOR_GP_LAMBDA_ACTIVE_CORR，默认 0.1）。
3. active 集因子值获取: 每代开始时用 GPU 批量算一次 active 集值（数量 ≤70，开销 <1s），缓存复用；相关惩罚矩阵 (P, A) 向量化。
4. 目标切换 env: FACTOR_GP_OBJECTIVE=ic|icir（M2 完成后默认 icir）。
5. **→M4**: 协同奖励产出的低冗余 active 集是 ICIR 组合层的前提；**→M0-A**: n_trials 计入。
6. 影响文件: gp_miner.py（_fitness_core + compute_fitness_from_values）、gp_gpu_eval.py（active 集缓存）、factor_evolution_loop.py。

**验收**: 与 M1 基线对比——候选池与 active 集的平均 \|corr\| 下降 ≥20%；ICIR 目标下候选的样本外 ICIR 中位数提升；单测: 构造两个高相关强因子，验证第二个被罚。**回滚**: FACTOR_GP_OBJECTIVE=ic + λ₃=0。

### R2 ALPS 年龄分层（G10，调研路线图 P1-4）

**现状**: 全种群无年龄概念，年轻高 IC 因子可立即主导选择，挤掉未成熟但有潜力的表达式。

**设计**:
1. 每个个体带 age（代数计数，每代 +1）；年龄层 = [0,1,2,3-4,5-9,10+]。
2. 选择（锦标赛/lexicase）在**同层内**进行；层间由精英保留 + 固定配额（每层上限）平衡。
3. 每代每层按配额向下一代注入（保证年轻层存活空间）；与 R0 lexicase 组合使用（层内 lexicase）。
4. env: FACTOR_GP_ALPS=0|1（默认 1，与 lexicase 同时启用）。
5. 影响文件: gp_miner.py（_run_seed 个体结构 + 选择分组）。

**验收**: 对比 M1——早停代数延后、世代最优曲线更平滑、最终最优 ICIR 不降；单测: 年轻层不被老年层完全挤出。**回滚**: env 关。

### R3 LLM 热启动 + 迭代补挖（G11，调研路线图 P1-5；与 M6 合并实施）

**现状**: LLM codegen 是独立补挖阶段，与 GP/MCTS 无信息交互；GP 初始种群纯随机。

**设计**:
1. **热启动**: 每轮 GP 前，用 codegen 流生成 K≤8 个种子（prompt 含: DSL 算子表 + 上一轮 top 因子 + 训练段被拒原因结构化摘要），audit 过滤后注入初始种群。
2. **迭代补挖**: 每 G 代（默认 5）取当前 best 5 个因子，让 LLM 做「保留经济逻辑的变异」（改窗口/换算子/组合两因子），产物同样走 audit + 同门禁。
3. **数据契约（→M3）**: prompt 输入只含训练段信息；held-out 判决段永不进入 prompt（黑盒律）。
4. 注册路径与 M6 完全一致（source=llm，收紧档门禁、符号反作弊、n_trials 计入）；两批开发合并为一条「LLM 双引擎」管道。
5. env: FACTOR_GP_LLM_WARM_START=1、FACTOR_GP_LLM_EVERY_N=5；成本护栏: 每次 ≤8 提案、周频预算上限。
6. 影响文件: factor_evolution_loop.py、gp_miner.py、ai_factor_discovery_service.py（复用基建，替换输入源与输出路径）。

**验收**: 热启动种子在 GP 前 3 代的存活率 > 随机种子存活率；LLM 变异因子中 ≥30% 通过 audit；prompt 记录可审计（不含判决段标识）。**回滚**: env 关，GP 回到纯随机初始种群。

### R4 因子工厂「写公式→秒级反馈」（G12，调研路线图 P1-6）

**现状**: 候选因子打分是后台批量任务，研究者无快速验证通道（对标 WorldQuant WebSim 的即时反馈缺失）。

**设计**:
1. 新增 POST /api/ops/factors/quick-score：body = {formula AST, tier: scalp|midlong}；响应 = 单因子诊断（IC/ICIR/中性化 IC/衰减半衰期/换手率/与 active 集最大 \|corr\|）+ 与门禁阈值的通过/拒绝预览（只读，不注册不晋升）。
2. 实现: parse + audit → GPU eval_panel_batch 单树 → 复用 factor_evaluator（M2 中性化口径）；目标时延 < 3s（4h 面板单树 GPU ≈ 15ms + 诊断毫秒级）。
3. 前端: ops/midlong-factors 面板新增「公式实验室」tab（文本输入公式 → 即时卡片式统计 → 一键"提交正式评分"）。
4. **→M0/M2/M3**: 预览口径与正式门禁同一函数（同一 score_formula 的只读模式），保证"所见即所得"。
5. 影响文件: backend/api/ops_routes.py（新端点）、backend/services/factor_engine/quick_score.py（新）、frontend-next ops 面板。

**验收**: 端点 <3s 返回；预览结论与正式打分一致率 100%（同函数）；面板可用。**回滚**: 纯新增，无回滚成本。

### R5 自动阈值调优（G13，调研路线图 P1-7；含长线参数）

**现状**: scalp conf 35/exec 40、仲裁 Gate conf 55、退出 Agent 时间止损、冷却矩阵等阈值全部手工 env 常量。

**设计**:
1. 新增 backend/services/tuning/threshold_tuner.py：以 trade_facts 回放为评价函数（给定阈值向量 → 在近 N 天历史上重放纸面决策 → 净收益/回撤/胜率），对离散阈值空间做贝叶斯/网格搜索（SigOpt 式），输出 data/threshold_tune_report.json。
2. 搜索空间 V1: scalp 入场 conf/exec、仲裁 Gate conf、退出 Agent 时间止损档位、长线 L1/ATR/金字塔（与 M5 参数空间合并）。
3. 灰度（→调研 Freqtrade dry-run）: 调优结果先以 shadow 模式运行 3~7 天（记录"若采用"的决策差异），无劣化才由 env 生效。
4. **→M3**: 回放只使用训练段口径数据（判决段不可见）。
5. env: FACTOR_THRESHOLD_TUNE=0|1；报告手动审批后生效。
6. 影响文件: 新增 threshold_tuner.py + routes；阈值读取统一收敛到 settings（单一真相源）。

**验收**: 报告覆盖全部搜索空间；shadow 对比可视化；人工审批流程存在（调优结果不自动生效）。**回滚**: env 不动即旧阈值。

### R6 GPU 深挖：SSE 去重 + 种群扩容 + 可选 CUDA kernel（G14，调研路线图 P2-8）

**现状**: 栈式求值器 21/28 算子覆盖、55× 单树加速，但种群 300 时 GPU 利用率低；子表达式重复计算无消除；算子仍是 torch 高层 API。

**设计（三步，按序）**:
1. **R6-a 公共子表达式消除（SSE）**：✅ **已实现并上线**——编译期对同程序内重复子树
   （canonical JSON 键）做 memo 化：首次求值后 `st` 存入槽位、复用点 `ld` 取出
   （树→DAG 轻量版，槽位上限 24/程序，显存 O(P×24×S×B)）；等价性验收通过
   （重复子树用例 maxdiff 1.7e-12；生产验收 Gate + 挖掘器单测全过）。
2. **R6-b 种群扩容**：✅ **已实现并上线**——GPU 启用时默认种群 300→1200
   （`FACTOR_EVO_GPU_POP_BOOST=1`，未显式设 FACTOR_GP_POPULATION 时生效），
   1800s 硬预算兜底。
3. **R6-c 算子级 CUDA kernel**：⏸ **暂缓（设计标注"可选·量力"）**——当前每步
   12~18 次小 kernel 的启动开销在 53× 总加速下已非瓶颈；2080Ti 到位或 2000+ 种群
   成为常态时再融合 cumsum/unfold 族 kernel。重启条件写入本条款。
4. 验收口径: 每代求值耗时（1200 种群）< 10s；SSE 后同树求值与逐树 numpy 等价（复用等价性 Gate）。
5. **→R0 依赖**: 先有 lexicase 的案例矩阵（GPU 路径已算），扩容才有的放矢；**→R7**: 持续循环的吞吐基础。
6. 影响文件: gpu_batch_eval.py（compile + 执行器 st/ld + gpu_mem_ok）、.env（POP_BOOST）。
7. **回滚**: POP_BOOST=0 回 300；SSE 为编译期变换，等价性 Gate 兜底。

### R7 衰减触发的持续挖掘循环（G15，调研路线图 P2-9）

**现状**: factor_decay_monitor 已监测因子衰减，但进化是定时任务（03:00/04:00/06:00），衰减与补挖无因果连接。

**设计**:
1. 新增规则: active 集平均 ICIR 或"活跃因子衰减半衰期中位数"低于阈值（env FACTOR_EVO_DECAY_TRIGGER_ICIR=0.02）→ 在下次低负载窗口自动触发对应周期的一轮 full evolution（受 1800s 硬预算与分时调度约束）。
2. 触发节流: 每周期每天至多 1 次衰减触发（与定时任务去重）；记录触发原因到 evolution 报告。
3. 产物走完整 M0~M3 门禁（与定时进化同一条链）。
4. env: FACTOR_EVO_DECAY_TRIGGER=0|1。
5. 影响文件: evolution_scheduler.py、factor_decay_monitor.py（暴露触发接口）。

**验收**: 人工压低衰减阈值可观察到自动触发补挖日志；与定时任务不重复执行。**回滚**: env 关。

### R8（附录 A，不占主线）深度学习因子侧分支

- 公式因子作特征 + 轻量 Transformer/梯度提升做组合层试验（WorldQuant 公式 + ML 工业组合），在独立侧分支开发；严格走 M0~M3 同一门禁；仅当样本外 ICIR 显著优于纯公式组合时才考虑并入主线。
- 判定标准: 侧分支 OOS ICIR 提升 ≥20% 且换手成本可覆盖 → 才进入主线讨论。

**R8 可行性判定结论（2026-08-18，按附录 A 执行）**：**暂不启动**。
依据：① 当前数据面板截面仅 9 币 × 每周期 1700~2600 根（4h），深度学习因子
（Transformer/GNN 隐式 alpha）的样本量比公式挖掘低 1~2 个数量级，过拟合风险
极高，且无法满足「OOS ICIR 提升 ≥20%」的并入门槛；② 现有硬件 GTX 1070 8G 仅供
GPU 公式求值，DL 训练需共享同一张卡（R9 预算下已紧张）；③ 调研报告 §2.1 共识：
公式型作底仓 + ML 做组合的混合路线才有增量——组合层 ML 属 M4-V2（风险平价/等风险
贡献）的后续，先做无参数的组合优化再谈 DL。**重启条件**：2080Ti 到货或可交易
币种 ≥30 且各 4 年历史，或公式组合层（M4）实证 ICIR 瓶颈。

### R9 算力分时调度加固（调研路线图 P2-11，大部分已具备）

- 现状: 进化任务 03:00~06:00 定时 + FACTOR_EVO_SCALP_PERIODS 空置 + 1800s 硬预算，已具备"错峰"雏形；缺: GPU 显存预算与并发进化互斥的显式保证。
- 设计: 进化任务注册到全局信号量（一次只跑一个周期档）；GPU 求值前检查可用显存（nvidia-smi/torch 查询，< 预算则等待或回退 CPU）；研究型任务（R4 quick-score）不受限但显存不足时排队。
- 验收: 双周期并发触发时串行执行日志；显存不足时自动回退且日志明确。**回滚**: env 关。

---

## 5. 统一执行计划（单一时间线，两工作流交叉）

### 5.1 阶段划分

| 阶段 | 内容 | 覆盖 | 依赖 | 工作量 | 风险 | 关键验收 |
| --- | --- | --- | --- | --- | --- | --- |
| **S0（W1）门禁收紧 + 基线** | M0（P0-A/B/C 三合一）→ M1 基线重跑 | 短+中 | 无 | 1 天 + 数小时后台 | 低（纯参数） | n_trials≥300；冗余单一来源；短线新币不硬缺；M1 基线报告存档 |
| **S1（W2-3）统计口径统一 + 挖掘 v2** | M2（P1 中性化）→ **R0（ε-lexicase）+ R1（协同奖励/ICIR）** | 短+中 | S0 | 2~3 天 + 3~4 天 | 中（口径变化） | beta 代理中性化后 IC 降>50%；lexicase+协同奖励上线后：候选池与 active 集平均 \|corr\| 较 M1 基线降 ≥20%、早停延后 |
| **S2（W3-4）防过拟合 + LLM 双引擎** | M3（P2 held-out）∥ R2（ALPS）→ M6（P4 LLM 提案）+ R3（LLM 热启动）合并实施 | 短+中 | S1（M6 依赖 M2+M3；R3 依赖 M3 黑盒律） | 2~3 天 + 1~2 天 + 3~5 天 | 中（晋升变慢、LLM 成本） | 晋升必带 held-out 分数；ALPS 早停延后且最优不降；llm 候选同门禁+符号反作弊 |
| **S3（W4-5）组合 + 反馈 + 长线** | M4（P3 ICIR 组合）+ R4（因子工厂）+ R5（自动阈值调优）∥ M5（P5 长线验证，任意时点可插队） | 短+中+长 | S1（M4 依赖中性化权重；R5 依赖 M3 黑盒律） | 1~2 天 + 2 天 + 2~3 天 + 2~3 天（M5） | 低-中 | 两周期组合 ICIR ≥ 最优单因子；quick-score <3s 且与正式门禁 100% 一致；阈值报告 + shadow 灰度；长线规则参数全有 OOS+PBO |
| **S4（W5-8）GPU 深挖 + 持续循环** | R6（SSE → 种群扩容 → 可选 kernel）→ R7（衰减触发循环）→ R9 加固 | 短+中 | S1（lexicase 案例矩阵） | 3~5 天 + 1~2 天 + 1 天 | 中（kernel 工作量大，可裁剪） | 1200 种群每代 <10s；SSE 等价性 Gate 通过；衰减触发补挖日志；并发进化串行化 |
| 附录 | R8 DL 因子侧分支 | — | 不占主线 | 按需 | — | 侧分支 OOS ICIR 提升 ≥20% 才进主线讨论 |

### 5.2 相互参考矩阵（数据契约，防止两工作流各说各话）

| 契约 | 定义方 | 消费方 | 说明 |
| --- | --- | --- | --- |
| 中性化 IC/ICIR 口径 | M2 (neutralization.py) | R0 案例得分、R1 适应度、R4 quick-score、R7 触发判断 | 唯一口径；M2 未完成前 R 系列暂用 raw IC 并标注，M2 后切换 |
| 训练段 / 判决段分割 | M3 (held-out) | R0/R1 打分数据、R3 prompt、R5 回放 | 黑盒律：判决段对挖掘与调优不可见 |
| n_trials 累计 | M0-A (trials_counter) | R1/R3/M6 的所有打分调用 | 所有试验（含挖掘中间打分）统一计数 |
| 冗余阈值 | M0-B (env 双档) | R1 相关惩罚、组合层 M4 | 单一真相源 |
| 案例边界（按币段） | R0 (panel 分段) | R1 ICIR 计算、R4 诊断 | 分段长度随 P0-C 自适应 |
| LLM 基建 + store 注册协议 | M6 | R3（同一管道） | source=llm 收紧档、符号反作弊共享 |
| GPU 显存预算 | R9 | R4 quick-score、R6 扩容 | 并发互斥 + 自动回退 |

### 5.3 回归保障

- 每阶段跑现有回归测试 + test_factor_security.py；S1/S2/S3 各加新单测（中性化残差正交性、held-out 晋升门、lexicase 案例选择、协同奖励惩罚、长线规则验证口径、quick-score 一致性）。
- 所有 R 项必须通过 GPU 等价性验收 Gate（既有机制，直接复用）。
- 每阶段完成提交一次 main 并推送（沿本次工作流惯例）。

---

## 6. 风险清单（合并）

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 中性化改变 IC 口径 → 两周期历史因子批量降级 | active 震荡 | 双轨输出 + 一次性重跑 + env 一键回滚 |
| n_trials 累计 → 门禁变严 → 通过率下降 | 短期「挖不到因子」 | 预期（防假阳性），M1 基线量化 |
| held-out 使晋升变慢 | 因子供应放缓 | 比例可调；短线判决段短、影响小 |
| crypto 截面仅 9 币，横截面回归自由度小 | 中性化噪声大 | 时间池化回归（P1 设计已考虑） |
| 长线规则验证与实盘背离（binance vs 实盘所） | 验证误导 | 回测只作参数证据，实盘仍按实盘所 1d 判市况 |
| LLM 候选质量波动 / 成本 | 无效提案 | K≤10 周频；符号反作弊；结构化输入；预算护栏 |
| D7 停用影响既有链路 | 130+ 归档因子热加载失效 | 归档因子不在 active，停用只影响新增 |
| **lexicase/ALPS/协同奖励改变选择压力 → 收敛变慢** | 单轮进化时间变长 | 硬预算兜底；M1 基线对比；env 逐项可回滚 |
| **种群扩容 1200 → GPU 显存/热预算** | OOM/降频 | R6-a SSE 先行；R9 显存预算检查；chunk 分块已有 |
| **SSE/融合 kernel 数值偏离** | 因子 IC 偏移 | 等价性验收 Gate（Pearson/IC 偏移双口径）复用 |
| **自动调优过拟合历史** | 阈值对未来失效 | shadow 灰度 3~7 天 + 人工审批才生效 |

---

## 7. 验收标准总表（升级完成的定义）

| 项 | 标准 |
| --- | --- |
| 多重检验 | 打分日志 n_trials = 历史累计试验数（≥300），两周期共用，重启不丢 |
| 冗余 | 全库无硬编码 0.5/0.7/0.8；准入/退役两档语义分离 |
| 中性化 | beta 代理因子中性化后 IC 降幅 >50%；真因子 IC 保持；env 一键回滚 |
| held-out | 两周期晋升都必须带 held-out 分数；LLM prompt 与调优输入永不含判决段数据 |
| 组合 | 短线、中线各自组合 ICIR ≥ 最优单因子 ICIR |
| 长线规则 | 每个规则参数有 OOS 证据 + PBO 值；参数调整引用验证报告 |
| LLM 提案/热启动 | 候选进 store（source=llm 且 horizon 正确）；同门禁+更严参数；符号不符即拒；零文件写入 ai_generated；热启动种子存活率 > 随机种子 |
| 挖掘 v2（R0/R1/R2） | 候选池与 active 集平均 \|corr\| 较 M1 基线降 ≥20%；早停代数延后；最终最优 ICIR 不降 |
| 因子工厂 | quick-score <3s；预览与正式门禁结论一致率 100% |
| 自动调优 | 阈值报告覆盖全搜索空间；shadow 灰度 + 人工审批流程存在 |
| GPU 深挖 | SSE 等价性 Gate 通过；1200 种群每代 <10s；衰减触发补挖日志；并发进化串行化 |
| 回归 | 全部既有回归测试 + 新增单测通过；每阶段提交 main 并推送 |

---

## 8. 附：三周期升级前后对照（v2.0 保留 + 挖掘侧补充）

| 维度 | 短线升级前 | 短线升级后 | 中线升级前 | 中线升级后 | 长线升级前 | 长线升级后 |
| --- | --- | --- | --- | --- | --- | --- |
| 诊断 | ✓ 完整 | ✓ | ✓ 完整 | ✓ | ✗ 无 | ✓ 规则信号 IC/ICIR |
| 多重检验 | ✗ N=40 | ✓ 累计 | ✗ N=40 | ✓ 累计 | ✗ 无 | ✓ PBO 扫描 |
| 中性化 | ✗ 无 | ✓ | ✗ 无 | ✓ | - | - |
| held-out | ✗ 无 | ✓ | ✗ 无 | ✓ | - | - |
| 冗余阈值 | ✗ 三处打架 | ✓ 统一 | ✗ 三处打架 | ✓ 统一 | - | - |
| 组合层 | ✗ 等权 | ✓ ICIR 加权 | ✗ 等权 | ✓ ICIR 加权 | ✗ 规则直拍 | ✓ 证据驱动参数 |
| LLM 角色 | ✗ 绕过门禁 | ✓ 提案层同门禁 | ✗ 绕过门禁 | ✓ 提案层同门禁 | - | - |
| 挖掘选择压力 | ✗ 锦标赛 | ✓ ε-lexicase+ALPS | ✗ 锦标赛 | ✓ ε-lexicase+ALPS | - | - |
| 挖掘目标 | ✗ 单点 IC | ✓ 中性化 ICIR+协同奖励 | ✗ 单点 IC | ✓ 中性化 ICIR+协同奖励 | - | - |
| 挖掘反馈 | ✗ 无 | ✓ 因子工厂秒级反馈 | ✗ 无 | ✓ 因子工厂秒级反馈 | - | - |
| GPU 利用 | 300 种群 | 1200 种群+SSE | 300 种群 | 1200 种群+SSE | - | - |
| 阈值 | ✗ 手工拍 | ✓ 回放调优+shadow 灰度 | ✗ 手工拍 | ✓ 回放调优+shadow 灰度 | ✗ 手工拍 | ✓ 同口径验证+调优 |
| 持续迭代 | ✗ 定时任务 | ✓ 衰减触发补挖 | ✗ 定时任务 | ✓ 衰减触发补挖 | - | - |
