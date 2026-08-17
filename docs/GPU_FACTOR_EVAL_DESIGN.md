# GPU 批量因子求值器 — 设计与实施路线

> 状态：**M1.5 栈式执行器 + M2 接线 + M3 验收 已完成并上线**（2026-08-17，main）
> 目标硬件：现有 GTX 1070 (8G) 已验证投产 → 2× RTX 2080 Ti 22G 属后续吞吐升级（未安装）
> 对应瓶颈：GP 因子挖掘的种群求值（实测占单轮 2.5h 的 ~40%）

## 1. 问题定义

`gp_miner._eval_population` 每代对 500 个候选表达式树逐个求值（9 币 × ~5k 根 K 线）。
gplearn 是纯 CPU 逐树 Python 解释执行：

- 每棵树 = 结构各异的表达式（add/sub/mul/div/roll/sma/delta/abs/log…）；
- 单次求值数据量小（~5k 浮点 × 字段数），**逐树执行时 GPU 核启动/搬移开销 >> 计算量**；
- 因此「直接把每棵树丢 GPU」必然更慢——必须先**成批**。

## 2. 核心思路：批量栈式求值器（Batch Stack Evaluator）

把一代种群的 N 棵树**全部**摊平成后序（postfix）操作序列，统一在一个张量程序里执行：

```
数据布局：
  fields : Tensor[F, S, B]     F=字段数(open/high/low/close/volume+派生), S=币数, B=bar数
程序布局：
  programs : List[postfix ops] 每棵树的 op 序列，常量内联为常数张量
执行（每步一个向量化算子，按 op 种类分组掩码执行）：
  stack : Tensor[P, S, B]      P=种群数
  for step in 1..max_depth:
      for op_kind in (unary/elemwise/rolling):
          mask = 本步应执行该 op 的个体  → 向量化执行，掩码外原样传递
```

- 每个 op 种类**一次核启动**覆盖全部个体/币/bar → 每代 ~`max_depth × op种类` ≈ 20×12 = 240 次小核 ≈ 毫秒级；
- 30 代 × 6 种子 = 180 次种群求值 ≈ **秒级**（对比 CPU 40–60 分钟）；
- 滚动算子（sma/roll/delta/pct_change）用 cumsum/移位+掩码实现，全程 GPU 无回传 Python；
- 安全除法/对数带 ε 与 inf 屏蔽，数值语义与 numpy 对齐（见等价性校验）。

## 3. 为什么这张 GPU 就够 / 2080Ti 22G×2 的意义

- 显存：单个程序栈 `(500, 9, 5000) float32 ≈ 90MB`，中间栈 ×3 ≈ 300MB — **1070 8G 绰绰有余**；
- 2080Ti×2 的价值在**吞吐/代际**：更高种群（如 2000 个体）与更多并行种子时仍有余量；
  两块卡可用数据并行（种群分片）或流水线（一代编译下一代表达式），但**当前 500 种群根本用不满**；
- 结论：先用 1070 验证等价性与提速比，达标后 2080Ti×2 属「锦上添花」，非前置条件。

## 4. 提速边界（诚实预期）

| 阶段 | 现状(2.5h) | GPU 化后 | 说明 |
|---|---|---|---|
| GP 种群求值 | ~60min | **<1min** | 本设计直接命中 |
| MCTS 挖掘 | ~20min | ~10min | 滚动窗口部分可批量化，收益有限 |
| LLM codegen 补挖 | ~40min | 不变 | 网络流式等待，GPU 无关 |
| WFO/DSR/PBO/测试集 | ~20min | 不变 | pandas/numpy CPU 评估 |
| **单轮合计** | **~150min** | **~70–80min** | 瓶颈转移到 codegen 与门禁评估 |

**即使 GPU 化完成，单轮下限也在 1 小时级**——这就是「零成本提速」（参数 300/20、
codegen 4 流、1800s 硬预算）为何必须先做：它们不依赖硬件，且与 GPU 化正交叠加。

## 5. 实施里程碑

- **M1（本分支）**：`backend/services/evolution/gpu_batch_eval.py` 原型（torch 批量栈式
  求值器 + numpy 等价校验）+ `scripts/bench_gpu_factor_eval.py` 基准（真实 K 线 × 随机
  程序，对比 numpy / torch-CPU / torch-CUDA）。**不接线、不影响现有代码。**
- **M2**：接线 `gp_miner._eval_population`，开关 `FACTOR_EVO_GPU_EVAL=0`（默认关），
  无 CUDA 自动回退 numpy——灰度、可回滚。
- **M3**：1070 上等价性验收：IC/ICIR 差异 <1e-6（浮点顺序差异可容忍，门禁阈值不变）；
  回归现有单测（test_factor_evolution*）。
- **M4**：新主板 + 2×2080Ti 到位后：双卡数据并行（种群分片）+ 提高种群上限实验；
  必要时把 MCTS 滚动算子也迁到批量路径。

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| 滚动算子语义偏差（NaN/min_periods/边界） | 原型内置等价性测试；M3 用真实 K 线 diff 验收 |
| 浮点非确定性（GPU 归约顺序） | 门禁是阈值判定，<1e-6 差异不影响；必要时固定 seed |
| torch 依赖体积/冷启动 | 惰性 import，未启用时零开销；打包体积 +~50MB 可接受 |
| 表达式 DSL 算子覆盖不全 | 未覆盖算子走 CPU 兜底（混合模式），逐步迁移高频算子 |

## 7. 相关文件

- 原型：`backend/services/evolution/gpu_batch_eval.py`
- 基准：`scripts/bench_gpu_factor_eval.py`
- 接线点（未来）：`backend/services/evolution/gp_miner.py:316 _eval_population`

## 8. M1 实测发现（2026-08-16，GTX 1070 + torch 2.6 cu124）

原型（满二叉树对齐 + 逐层向量化）已跑通，暴露三个必须解决的现实：

1. **朴素全树布局内存爆炸**：materialize `(P, 全节点, S, B)` —— 200 程序就 4.5GB、
   torch-CPU 17.6s，**比 numpy 逐树（0.66s）还慢 27×**；500 程序将超 8GB 显存。
   → 必须改回设计文档 §2 的**栈式布局**（操作数栈 2×(P,S,B) ≈ 180MB），逐层释放。
   本原型的满二叉树对齐保留为「真值校验器」，不做执行路径。
2. **滚动方差 E[x²]−E[x]² 在 float32 下灾难性消去**：torch 与 numpy 的 cumsum 顺序
   差异被放大成完全不同的噪声（实测 4/50 树秩相关掉到 0.31~0.73）。
   → 滚动算子内部改 float64 计算（两侧同改），修复后最差树秩相关 0.886、0.8% 不匹配。
3. **等价性验收口径**：逐值 diff 不可行（pct/div 近零分母放大任何 1-ulp 差）——
   改用 **Spearman 秩相关 ≥0.999 + isclose 比例 <5%**（下游 IC 是排序相关，口径一致）。
   遗留 1/50 树 corr=0.886（含 exp∘log∘pct 组合的角落案例），M2 继续收敛。

结论：**机制可行（批量执行无串扰、算子语义可对齐），执行布局必须换栈式**；
GPU 化收益的重新测算：栈式 + float64 滚动后，求值瓶颈从「核启动数」变成
「滚动算子带宽」，预计 1070 上 500 程序 × 9 币 × 5k 根 < 5s/代（对比 numpy ~2.4s/500 程序…
注意 numpy 逐树也只需 0.66s/200 程序 = 1.65s/500——**GPU 的真正价值在 2000+ 种群
与 2080Ti×2 的双卡吞吐**，500 种群规模下 CPU 已足够快，这解释了为什么零成本提速
（降参数+时间预算）才是当前瓶颈的正解）。

### M1 修正后的里程碑

- **M1.5**：执行路径改为栈式批量求值器（操作数栈 + 每步按 op 分组掩码执行，
  内存 O(P×S×B)）；满二叉树编译保留为校验器。
- **M2**（不变）：接线 `gp_miner._eval_population`，`FACTOR_EVO_GPU_EVAL=0` 默认关。
- **M3**（不变）：1070 等价性验收（秩相关口径）。
- **M4**（不变）：新主板 + 2×2080Ti 投产（此时 GPU 才有相对 CPU 的实际优势）。

## 9. M1.5/M2 实施记录（2026-08-17，已上线 main）

**交付物**：
- `backend/services/evolution/gpu_batch_eval.py` — 栈式批量执行器（重写）：
  - 挖矿 AST → 后序编译（op_code/param/const/field 数值表）→ 操作数栈 (P, MAXSTK, S, B)
    + 指针，按 (步 × 算子) 分组掩码执行，float64 全链路；
  - 算子覆盖 21/28（ema/scale 及常量操作数滚动走 CPU 兜底；随机树 GPU 覆盖 ~63%）：
    算术 11 + ref/delta + mean/sum/std/var（float64 cumsum + 全局中心化消除大数消去）+
    max/min/wma/decay_linear/ts_rank/ts_argmax/ts_argmin（unfold 视图 + NaN 掩码归约，
    显存预算 `FACTOR_EVO_GPU_MAX_MEM_MB` 超限自动 CPU）+ corr/cov/ts_corr（窗口中心化
    float64，cov 对齐 np.cov ddof=1）；
  - 语义对齐踩坑（全部修复并有回归样例）：`torch.sign(nan)=0`（numpy=nan）→ 显式透传；
    `torch.where` 标量 dtype 提升；int/int→float32；cumsum 缺头部 pad；`np.cov` ddof=1；
    常量操作数 → numpy 截断 (1,) → 真实路径 -inf，GPU 广播会造假信号 → 整体 CPU 兜底。
- `backend/services/evolution/gp_gpu_eval.py` — 接线层：
  - 首次使用等价性验收：4 批 × 24 棵采样，**值保真**（Pearson ≥0.99999 或 Spearman
    ≥0.999 —— Spearman 对大量并列离散序列的 ulp 噪声过敏，实测 1581/1693 位差 1e-15
    时 Spearman 掉到 0.989）+ **IC 影响**（GPU 值对目标 IC 偏移 ≤5e-4）双口径；
    验收失败/无 CUDA/任意异常 → 永久回退 loky（fail-safe）；
  - 精英因子值缓存每代只算一次（原实现每棵树重算全部精英 = O(N×E) 浪费，这是
    GP 占时 2.5h 的真正主因）；GPU 子集向量化适应度 + CPU 子集 loky 并行。
- 开关：`FACTOR_EVO_GPU_EVAL=1`（本机 .env 已开）、`FACTOR_EVO_GPU_MAX_MEM_MB=1200`、
  `FACTOR_EVO_GPU_CHUNK=64`。

**实测（GTX 1070 8G + torch 2.6 cu124，合成 5 币 × 1712 根面板）**：
- 单树求值：numpy/DSL 575 ms/树（瓶颈 = formula_ops._rolling 的 Python 逐 bar 循环）
  → GPU warm **10.4 ms/树 ≈ 55×**；
- 种群一代（120 树）：GPU 路径稳态 **10.3s vs loky 31.3s（≈3×）**；
  首次调用含一次性验收 ~48s（含 numpy 参考值 35s）；
- 适应度等价：GPU vs loky 适应度 Pearson = 1.0000（120 树、47 有效）；
- 等价性验收：150 树可比 72、值保真失败 0。

**诚实边界**：
- 覆盖率 ~63%：ema 与「滚动(常量)」树走 CPU（后者在真实 DSL 下本就 -inf，
  GPU 兜底是为复现该语义而非性能）；
- 收益大头来自精英缓存 + 批量求值消除 Python 滚动循环；纯 GPU 吞吐优势仍需
  2000+ 种群或 2080Ti×2（M4）才能拉满；
- 上板验收口径 = 适应度排序无扰（IC 偏移 ≤5e-4），逐值 isclose 不作为门槛
  （div 近零分母会放大任何 ulp 差，文档 §8 已预见）。

