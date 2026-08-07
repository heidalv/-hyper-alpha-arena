# 专有模型训练方法论

> **项目**：Hyper-Alpha-Arena 多市场 AI 交易平台
> **文档版本**：v1.0
> **创建日期**：2026-07-10
> **定位**：本文是"取胜关键"——回答"用什么方法训练出真正有 alpha 的专有模型，并支撑从虚拟币扩展到 A股/港股/美股"。
> **关系**：本文是 [`LOCAL_LLM_TRAINING_PLAN_V2.md`](./LOCAL_LLM_TRAINING_PLAN_V2.md) 的**方法学深化**。后者讲"怎么部署"，本文讲"怎么训练出真东西"。

---

## 0. 必须先说清楚的一个问题

### 0.1 当前方案的根本缺陷：rule_v1 标签没有真实 alpha

现有 `dataset_builder.py` 的 `_label_params()` 用 4 条硬编码规则派生标签（亏损窗口→收紧、手续费高→降频…）。用这种标签做 SFT，模型学到的是：

> "给定市场统计 → 输出规则本来就会输出的参数"

**这不是学习，是复述。** 训出来的模型最多是"规则的慢速、模糊副本"——它不可能比规则本身更好，因为监督信号的上界就是规则。真正的 alpha 来自**规则未覆盖的模式**，而 rule_v1 标签把它们全部抹平了。

### 0.2 本方法论的纠正：标签必须来自真实结果，而非规则

> **核心原则：监督信号必须来自市场给出的真实结果（盈亏、Sharpe、回撤），而不是人为预设的规则。模型的目标是发现"规则发现不了的、状态→参数的更优映射"。**

本方法论的全部设计都围绕这一原则展开。下面是三阶段训练法、标签工程、多市场架构的具体方案。

---

## 1. 训练范式总览：三阶段递进

不是一次 SFT 完事，而是**三个阶段递进**，每个阶段解决不同问题，且可以独立评估、独立回滚：

```
┌─────────────────────────────────────────────────────────────────┐
│  阶段 A：领域注入（CPT）                                          │
│  让底座"懂数字货币/股票语言"，解决领域距离问题                      │
│  监督信号：海量无标注金融语料（研报、K线描述、新闻）                 │
│  产出：领域增强底座（所有市场共享）                                 │
│  状态：数据足够前可跳过（见 §2 风险评估）                           │
├─────────────────────────────────────────────────────────────────┤
│  阶段 B：任务监督微调（SFT）                                      │
│  让模型学会"市场状态 → 参数"的核心映射                             │
│  监督信号：真实结果反推的最优参数（backtest_grid_v2，非 rule_v1）   │
│  产出：每市场一个 LoRA adapter                                    │
│  状态：当前主战场，本文 §3 重点                                    │
├─────────────────────────────────────────────────────────────────┤
│  阶段 C：偏好对齐（DPO/GRPO）                                     │
│  让模型学会"在两个参数方案间选更好的"，精修决策边界                  │
│  监督信号：交易胜负的偏好对（DecisionRetrospective）               │
│  产出：在 SFT LoRA 上的精调                                       │
│  状态：数据 >5k 且 SFT 稳定后启用，本文 §4                         │
└─────────────────────────────────────────────────────────────────┘
```

**每个阶段的关系**：A 是可选的地基，B 是主力，C 是精修。三者数据要求、显存、风险递增，严格按 A→B→C 顺序，不跳级。

---

## 2. 阶段 A：领域注入（Continued Pre-Training, 可选）

### 2.1 要不要做 CPT——诚实评估

CPT 的价值取决于**领域距离**和**语料规模**。对本项目：

| 判据 | 本项目情况 | 结论 |
|---|---|---|
| 任务是自由文本生成 还是 结构化数值？ | **结构化数值**（输入统计特征，输出参数 JSON）| CPT 收益有限 |
| 底座已"懂金融"吗？ | Qwen3 中文+金融能力已较强 | 边际收益小 |
| 有海量无标注金融语料吗？ | 当前不足 | CPT 数据不够 |
| 22GB 卡能跑 CPT 吗？ | 全参 CPT 需多卡 FSDP | 工程复杂 |

> **结论：当前阶段跳过 CPT，直接从 SFT 开始。** 等 SFT 跑通、且积累了足够金融语料（研报、新闻、历史行情描述文本，>1GB）后，再评估 CPT 的边际收益。AWS 金融 CPT 实践表明 CPT 是"全量预训练和 SFT 之间的性价比中间地带"——但前提是有语料。

### 2.2 未来 CPT 的语料构成（预留）

若日后做 CPT，语料按此混合（参考 [Databricks CPT 实践](https://www.databricks.com/blog/characterizing-datasets-and-building-better-models-continued-pre-training)）：

| 语料类型 | 占比 | 例子 |
|---|---|---|
| 通用文本（防遗忘） | 30% | 底座原始预训练语料采样 |
| 金融知识文本 | 40% | 研报、财报、金融教材、新闻 |
| **结构化→文本**（关键） | 20% | K线/统计特征转自然语言描述（与 SFT 输入分布对齐）|
| 多市场术语 | 10% | A股/港股/美股/币圈的术语对照 |

> **结构化→文本语料**是最关键的差异化：它让模型见过"统计特征用文字表达"的形式，直接对齐 SFT 阶段的输入格式。可用 `dataset_builder` 的 prompt 模板批量生成。

### 2.3 CPT 的工程规格（未来）

| 项 | 值 |
|---|---|
| 方式 | 全参（非 QLoRA），需多卡 FSDP（ZeRO-3 + CPU offload）|
| 显存 | 6 卡 × 22GB，offload 后可行 |
| 数据量门槛 | >1GB 金融语料 |
| 触发条件 | SFT 稳定运行 3 个月 + 语料达标 |

---

## 3. 阶段 B：任务监督微调（SFT）—— 当前主战场

这是决定成败的核心。关键是**标签工程**：用什么作为监督信号，直接决定模型上限。

### 3.1 标签工程：从 rule_v1 升级到 backtest_grid_v2（核心改进）

#### 当前 rule_v1 的致命问题（再强调）

```python
# dataset_builder._label_params() 现状
if net < 0 and avg_loss > avg_win:
    target = {"min_risk_reward": 2.0, ...}   # ← 这是规则硬编码的值！
```

标签的值（2.0）来自规则，不来自数据。模型学它 = 学规则。

#### backtest_grid_v2：用回测找真实最优标签

**核心思想**：对每个统计窗口，在参数空间做网格搜索回测，找**该窗口实际表现最好**的参数组作为标签。

```
对于每个统计窗口 W（如 2026-06-01~06-08, trending, 28笔交易）:
  对参数网格 G 的每组组合 (min_rr, conf, max_trades):
    在窗口 W 的实际行情上回测这组参数 → 得到 Sharpe_W(G)
  label = argmax_G Sharpe_W(G)   ← 真实最优，不是规则猜的
```

**参数网格**（与 `_PARAM_BOUNDS` 对齐，离散化）：

| 参数 | 网格点 | 数量 |
|---|---|---|
| min_risk_reward | 1.5, 1.8, 2.0, 2.2, 2.5, 3.0 | 6 |
| scalp_min_confidence | 55, 60, 65, 70, 75, 80, 85 | 7 |
| max_daily_trades | 3, 5, 7, 10 | 4 |
| **总组合** | | **168 组** |

每个窗口回测 168 组，取 Sharpe 最高（或 Calmar 最高）的一组为标签。

**为什么这会产生真实 alpha**：不同市场状态下最优参数确实不同，且这种"不同"是市场给出的，不是规则假设的。模型学到的映射 `状态→真实最优参数` 可能揭示规则未发现的模式（如"高波动+低胜率时，激进降频比保守收紧更优"）。

#### 工程实现要点（经代码审查确认：工程量小）

> **关键发现：回测引擎、参数网格、标签函数预留位均已就绪。** `backtest_grid_v2` 主要是"填充一个已有的空函数 `_label_v2`"，不需要新写回测引擎。

1. **回测引擎直接可用**：`backend/services/live_pipeline_backtest_engine.py` 的 `LivePipelineBacktestEngine.run(bars, pipeline_params, ...) -> BacktestResult`，注释明确"与 AI 自主交易（Full Auto）使用**完全相同的决策管线**"。返回的 `BacktestResult` 已含 `sharpe_ratio / win_rate / total_return / max_drawdown / profit_factor`。这正是 `run_backtest(window, gates) -> metrics` 的现成形态。
2. **网格搜索已实现**：`backend/services/backtest_engine/walk_forward.py` 的 `WalkForwardAnalyzer.analyze(strategy_factory, data, param_grid)` 已实现"参数网格穷举 + 按 sharpe/sortino/calmar 选最优 + PBO/DSR 过拟合诊断"——可直接复用。
3. **参数网格来源现成**：`backend/services/strategy_params_registry.py` 已有 `ParamSpec(default, min, max, step)` 和 `PIPELINE_PARAM_RANGES`，直接生成网格候选。
4. **标签函数预留位**：`dataset_builder.py:199` 的 `_label_v2(stats, db)` 当前是空占位（`return {}, ""`），`build_dataset()` 已有调用点（`:314`）。
5. **进阶可选**：`learning_core/cmaes_optimizer.py`（连续空间精调替代纯网格）、`map_elites_archive.py`（按 regime 归档最优标签）、`pbo_audit.py`（过滤过拟合组合）均可复用。
6. **历史 K 线加载**：`strategy_evolver._load_bars(db, symbol, timeframe, days)` 按窗口取行情。

**实现路径**：在 `_label_v2` 内，对每个 gate 组合调 `LivePipelineBacktestEngine.run(bars, pipeline_params)`，取 `sharpe_ratio` 最大（或 top-10% 加权平均）的一组为标签。计算量：168 组 × 每窗口 7 天行情 × N 窗口，离线任务在卡3 跑（100 窗口约 10 小时）。
7. **标签平滑**：不取绝对最优，取 top-10% 参数组的加权平均（避免过拟合到单一组合）。
8. **数据量门槛**：backtest_grid_v2 需要足够窗口才有统计意义 → **窗口数 >100（约 6 个月数据）才启用**，之前用 rule_v1 过渡。

#### `dataset_builder.py` 的改造点

```python
def _label_v2(stats, db=None):
    """升级标签：回测网格搜索。当前预留接口，需实现。"""
    # 1. 取该窗口的实际 K 线数据（market_data_adapters）
    # 2. 对 PARAM_GRID 每组回测（复用 backtest_engine）
    # 3. 取 Sharpe top-10% 加权平均为 label
    # 4. label_method = "backtest_grid_v2"
    best = run_grid_backtest(stats["window_start"], stats["window_end"], stats["symbol"])
    return best.gates, f"网格回测最优:Sharpe={best.sharpe:.2f}"
```

> **这是 v2.0 训练计划最重要的代码改动**。在它实现前，SFT 没有真实意义。优先级：高于一切训练框架配置。

### 3.2 SFT 训练配置

| 项 | 值 | 说明 |
|---|---|---|
| 基座 | Qwen3-30B-A3B-Instruct | 冻结 |
| 方式 | QLoRA NF4，rank=32 | （比 v1.0 的 rank=16 略增，提升表达能力）|
| 显存 | ~18GB（单卡 22GB） | rank 增加略增显存 |
| 数据 | backtest_grid_v2 标签的 SFT JSONL | 见 [`TRAINING_SAMPLE_FORMAT_SPEC.md`](./TRAINING_SAMPLE_FORMAT_SPEC.md) |
| epochs | 3-5 | cosine scheduler |
| lr | 1e-4（比 v1.0 的 2e-4 降半）| backtest 标签更"硬"，降 lr 防过拟合 |
| 正则 | dropout=0.05 + 早停 | 监控 val loss |

**数据量与训练目标**：

| 数据量 | 策略 |
|---|---|
| <1k（当前） | **不做 SFT**，先用 prompt-only（基座 + in-context 历史教训）|
| 1k-5k | SFT 启动，重点观察是否过拟合（train↓ val↑）|
| 5k-20k | SFT 稳定，引入 backtest_grid_v2 |
| >20k | 加入 DPO 精调（阶段 C）|

### 3.3 防过拟合（交易数据的头号风险）

交易数据天然信噪比极低（市场噪声 >> 信号），过拟合是最大敌人：

| 手段 | 实施 |
|---|---|
| 时间切分 | train / val / test 严格按时间，绝不随机切（防未来信息泄漏）|
| val 监控 | 每 epoch 算 val loss，连续 2 轮上升则早停 |
| 样本权重 | 近期样本权重高，噪声样本（`quality=marginal`）降权 |
| LoRA rank 控制 | rank=32 够用，不盲目加大（低 rank 本身是正则）|
| 影子 A/B | 必须在卡2影子跑赢基座才上岗（见 [`LOCAL_LLM_TRAINING_PLAN_V2.md`](./LOCAL_LLM_TRAINING_PLAN_V2.md) §7.3）|

---

## 4. 阶段 C：偏好对齐（DPO/GRPO）

### 4.1 为什么需要 DPO

SFT 学的是"绝对最优参数"，但现实中参数选择是**相对的**（A 比 B 好）。DPO 直接学习"在两个方案间做偏好选择"，对噪声标签更鲁棒——因为即使绝对最优难定，"A 优于 B"的序关系更稳定。

### 4.2 偏好对构造（本项目特有）

数据源：`DecisionRetrospective` 表（平仓时记录 `was_correct` + `lesson_learned`）。

```
对每对 (正确决策, 错误决策) 在相似市场状态下:
  chosen = 正确决策的参数
  rejected = 错误决策的参数
  prompt = 当时市场状态
```

关键：`was_correct` 是**市场给出的真实裁判**（这笔赚了/亏了），不是规则。这正是 §0.2 原则的体现。

**参考 [FinDPO](https://dl.acm.org/doi/full/10.1145/3768292.3770367)**：首个金融领域 DPO 框架，已验证 DPO 对算法交易偏好对齐有效。

### 4.3 DPO vs GRPO 选型

[Fin-o1（2025）](https://arxiv.org/html/2502.08127v3)实证对比了 PPO/DPO/GRPO 在金融推理任务：

| 方法 | 优点 | 缺点 | 本项目选择 |
|---|---|---|---|
| **DPO** | 无需 reward model，稳定，工程简单 | 偏好对质量敏感 | **✅ 首选**（阶段 C 初期）|
| GRPO | 不需要成对偏好，可用 outcome reward | 需要在线或半在线采样，工程复杂 | ⚠️ 阶段 C 成熟后评估 |
| PPO | 经典 RL | 需 reward model，训练不稳 | ❌ 不用（项目已排除 RL 路线，见 v1.0 §2.4）|

**DPO 配置**：

| 项 | 值 |
|---|---|
| 基座 | SFT 阶段的 LoRA（在其上继续）|
| beta | 0.1 |
| epochs | 1-2（DPO 易过拟合，少跑）|
| 数据 | DecisionRetrospective 派生的偏好对 |
| 触发 | SFT 稳定 + 偏好对 >2000 对 |

---

## 5. 多市场架构：一个底座 + 每市场一个 LoRA

> 这是支撑"虚拟币 → A股/港股/美股"扩展的核心架构决策。

### 5.1 架构选型对比

| 方案 | 做法 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| **A. 共享底座 + 每市场 LoRA**（推荐）| 冻结 Qwen3 底座，每市场训一个 LoRA | ✅ 零遗忘、✅ 独立迭代、✅ vLLM 原生支持 per-request 路由 | 底座不学跨市场共性 | **✅ 当前最优** |
| B. 单一融合 LoRA | 所有市场数据混训一个 LoRA | 学到跨市场共性 | 灾难性遗忘、市场间干扰 | ❌ 放弃 |
| C. 每市场独立全参微调 | 每市场一份完整权重 | 最深度定制 | 显存 ×N、无法共享、迭代重 | ❌ 6×22GB 扛不住多份 |
| D. MoE 路由（每市场一个 expert）| 改模型结构加路由 | 自动路由 | 改结构风险高、工程复杂 | ⚠️ 未来研究 |

### 5.2 推荐方案 A 详解

```
                    ┌── crypto_lora (当前，虚拟币)
冻结 Qwen3-30B-A3B ──┼── a_share_lora (A股，未来)
   (共享底座)        ├── hk_lora (港股，未来)
                    ├── us_lora (美股，未来)
                    └── ...（新市场加一个 LoRA 即可）
```

**为什么这是对的**：

1. **防灾难性遗忘**：底座冻结，各市场 LoRA 权重独立，A股训练完全不影响虚拟币（[学术界共识](https://pub.towardsai.net/a-guide-to-fine-tuning-large-language-models-llms-without-catastrophic-forgetting-4b2c926f14a4)）
2. **独立迭代**：虚拟币数据多、A股刚起步，两者训练节奏不同，LoRA 解耦
3. **vLLM 原生支持**：vLLM 支持单底座加载多个 LoRA，**按请求路由**（见 [vLLM LoRA 文档](https://docs.vllm.ai/en/latest/features/lora/)）。交易机请求时带 `market` 字段，路由到对应 LoRA
4. **显存友好**：6×22GB 共享一个底座（NVLink 双卡 44GB），N 个 LoRA 各约 0.3GB，轻松共存

### 5.3 vLLM 多 LoRA 部署

```bash
# 启动时预加载多个 LoRA（vLLM 原生支持）
CUDA_VISIBLE_DEVICES=0,1 vllm serve Qwen/Qwen3-30B-A3B-Instruct \
  --tensor-parallel-size 2 \
  --dtype float16 --quantization awq \
  --enable-lora \
  --max-loras 4 \
  --max-lora-rank 32 \
  --lora-modules \
    crypto=./loras/crypto_v3 \
    a_share=./loras/a_share_v1 \
    hk=./loras/hk_v1 \
    us=./loras/us_v1
```

**交易机请求时指定 LoRA**（OpenAI 兼容 API 扩展字段）：

```python
# gate_optimizer_service 调用时带 model 字段路由
response = call_llm_api_sync(
    model="crypto",   # ← 路由到 crypto LoRA；未来 "a_share" / "hk" / "us"
    messages=[...],
    base_url="http://192.168.1.100:8000/v1",
)
```

vLLM 会**动态按 adapter 分组 batch**，不同市场的请求可并发处理，开销极小（[vLLM 动态调度](https://medium.com/codetodeploy/multi-lora-in-production-designing-for-vllm-and-eks-e8bc6a8b4b92)）。

### 5.4 跨市场知识共享（方案 A 的补充）

方案 A 的缺点是底座不学跨市场共性。缓解：

1. **共享 CPT**（阶段 A）：若做 CPT，用**所有市场混合语料**训练底座，让底座有通用金融素养，各 LoRA 只学市场特异性
2. **LoRA 合并实验**（SLoRA，2025）：[SLoRA](https://aclanthology.org/2025.ijcnlp-srw.4.pdf) 支持把多个 LoRA 加性组合，可实验"crypto_lora + a_share_lora"叠加是否优于单独
3. **共享特征工程**：虽然 LoRA 独立，但输入特征（regime/统计窗口）格式跨市场统一（见 §6.2），底座的特征理解是共享的

---

## 6. 多市场扩展的工程改造路线

### 6.1 现状诊断（诚实）

经代码审查，当前系统**是加密永续合约专用**，扩展到股票是中等到较大改造（非加字段即可）：

| 层 | 现状 | 改造 |
|---|---|---|
| 交易所接入层 | 6 个加密所，硬编码 `defaultType=future`/USDT | **大**：A股需 tushare/akshare/券商API，美股需 yfinance/IBKR（ccxt 帮不上）|
| 数据层 | `market` 字段已存在，默认全 `"CRYPTO"` | **小**：字段已预留，需填充 |
| Symbol 抽象 | `symbol_registry` 只认 `BTC/USDT:USDT` 格式 | **中**：股票 symbol 体系不同（600519.SH / AAPL）|
| DecisionSnapshot | 只有 `symbol` 字符串，无市场维度 | **中**：加 `asset_class` 字段 + 训练样本加市场特征 |
| 门控参数 | 全局 + by_nature，无 by_market | **中**：引入 by_market 维度 |
| 训练样本/prompt | 硬编码"加密永续合约" | **中**：prompt 参数化 market |

### 6.2 标准化训练样本的多市场扩展

这是模型训练能"认出"市场的关键。在 [`TRAINING_SAMPLE_FORMAT_SPEC.md`](./TRAINING_SAMPLE_FORMAT_SPEC.md) 的 schema 上加字段：

```jsonc
{
  "market_context": {
    "asset_class": "crypto",     // ← 新增：crypto | a_share | hk | us
    "market": "CRYPTO",          // 复用已有字段
    "regime": "trending",
    "symbol": "BTC-USD-PERP",
    // 股票特有特征（可选，按 asset_class 填充）
    "sector": null,              // A股:科技 / 美股:consumer
    "market_cap_tier": null,     // large | mid | small
    "crypto_specific": {         // 仅 crypto 填充
      "funding_rate_regime": "positive"
    },
    "equity_specific": null      // 仅股票填充（涨跌停、T+1 等）
  }
}
```

**关键设计**：特征字段按 `asset_class` 条件填充，用 null 占位。底座 + 各市场 LoRA 学会"看 asset_class 决定关注哪些特征"。

### 6.3 渐进式扩展路线（不要一次全上）

| 阶段 | 市场 | 目标 | 前提 |
|---|---|---|---|
| **现在** | 虚拟币（crypto） | 跑通完整训练闭环 | 当前数据 |
| **+3 月** | + 美股（us） | 验证多 LoRA 架构（美股数据易得：yfinance） | crypto 闭环稳定 |
| **+6 月** | + A股（a_share） | A股需 T+1/涨跌停适配 | 美股 LoRA 验证通过 |
| **+9 月** | + 港股（hk） | 扩展 | A股稳定 |

**为什么先美股后A股**：美股数据免费易得（yfinance）、T+0 无涨跌停限制（与 crypto 机制接近，复用性高）；A股有 T+1/涨跌停/集合竞价等特有机制，改造量大，放后面。

### 6.4 决定何时新建一个 LoRA vs 复用

| 新市场特征 | 决策 |
|---|---|
| 交易机制相似（T+0、无涨跌停） | 先用最近的 LoRA 试，效果好就共享 |
| 交易机制差异大（T+1、涨跌停） | **新建独立 LoRA** |
| 数据量 <500 样本 | 用 in-context（prompt 注入），不训练 |

---

## 7. 模型评估：如何知道模型真有 alpha

没有可靠评估，训练就是盲飞。三层评估体系：

### 7.1 离线评估（训练时）

| 指标 | 方法 | 门槛 |
|---|---|---|
| Val loss | 时间切分的 val 集 | 持续下降，不过拟合 |
| 参数命中率 | 模型参数 vs backtest_grid 真实最优的 MAE | <10% 偏差 |
| JSON 合法率 | 输出可解析 + 边界内 | 100% |

### 7.2 影子评估（上线前）

| 指标 | 方法 | 门槛 |
|---|---|---|
| 影子 Sharpe | 卡2 跑模型建议参数 vs 基座，对比随后窗口 Sharpe | 模型 ≥ 基座，连续 3 窗口 |
| 参数合理性 | 人工抽查建议是否离谱 | 无越界、无荒谬值 |

### 7.3 在线评估（上线后，最终裁判）

| 指标 | 方法 | 门槛 |
|---|---|---|
| 实盘 Sharpe 改善 | local_llm_optimizer 生效后 vs 未生效 | 正向 |
| 熔断触发率 | `v5_gates_rollback.flag` | 不上升 |

> **最终裁判是实盘 Sharpe，不是 loss。** 模型 val loss 再漂亮，实盘不赚钱就是失败。影子 A/B 是最后一道关。

---

## 8. 迭代节奏与数据飞轮

```
        交易产生 DecisionSnapshot（带真实 pnl）
                    │
                    ▼
        dataset_builder（backtest_grid_v2 标签）  ← 周度
                    │
                    ▼
        SFT 训练新 LoRA（crypto_v{N+1}）          ← 周度
                    │
                    ▼
        影子 A/B（卡2）                            ← 每版
                    │ 达标
                    ▼
        vLLM 热加载新 LoRA（卡0+1）                ← 按需
                    │
                    ▼
        新交易产生新 snapshot（更好或更差的结果）   ← 持续
                    │
                    ▼
        （循环）新数据修正旧错误 → 更好的 LoRA
```

**飞轮效应**：模型越好 → 参数越优 → 交易结果越好 → 标签质量越高 → 模型更好。关键启动条件：**backtest_grid_v2 标签必须先到位**，否则飞轮转不动（垃圾标签 → 垃圾模型 → 垃圾数据）。

---

## 9. 风险与诚实的局限

| 风险/局限 | 说明 | 应对 |
|---|---|---|
| **标签噪声** | 即使 backtest_grid，历史最优≠未来最优 | 标签平滑 + 影子 A/B + 不盲目追高 |
| 过拟合 | 交易数据信噪比极低 | 时间切分 + 早停 + 低 LoRA rank |
| 市场机制变化 | regime 漂移使旧标签失效 | 滚动窗口重训（90天）+ 近期样本高权重 |
| **多市场冷启动** | 新市场数据不足 | 先 in-context，不急训 LoRA |
| CPT 可能无用 | 结构化任务 CPT 收益不确定 | 跳过 CPT，先验证 SFT |
| LLM 非时序预测器 | LLM 不擅长原始时序预测 | 本模型只做"统计特征→参数"，不做价格预测（价格预测交给现有 ML/RL 路径）|

### 最重要的诚实声明

> **这个模型不是"预测涨跌"的预言机。** 它的角色是"门控参数优化器"——在给定市场统计状态下，建议更优的风控/频率参数。它不预测价格（那需要专门的时序模型，项目已有 ML/RL 路径）。它的价值在于：把人类用 4 条规则做的调参，升级成能用千万次交易经验做更精细调参的专家。**降低预期，做对定位，才有真实收益。**

---

## 10. 行动优先级（具体到下一步做什么）

| 优先级 | 任务 | 依赖 |
|---|---|---|
| **P0** | 实现 `backtest_grid_v2` 标签（`_label_v2`） | 回测引擎 |
| **P0** | 数据 <1k 前保持 prompt-only，**不急着 SFT** | 基座部署 |
| P1 | 跑通 vLLM + 单 LoRA（crypto）推理闭环 | NVLink + vLLM |
| P1 | 积累数据到 >1k，启动首次 SFT | backtest_grid_v2 |
| P2 | DPO 偏好对数据积累（DecisionRetrospective） | 交易运行 |
| P2 | 多市场架构验证：加 `asset_class` 字段 | 代码改造 |
| P3 | 美股数据接入（yfinance）+ 首个跨市场 LoRA | crypto 稳定 |

---

## 11. 参考资料

### 训练方法论
- [Fin-o1: 金融推理 LLM 的 PPO/DPO/GRPO 对比](https://arxiv.org/html/2502.08127v3) — 决定 DPO vs GRPO
- [FinDPO: 金融偏好对齐](https://dl.acm.org/doi/full/10.1145/3768292.3770367) — DPO 用于交易
- [CPT/SFT 权衡建模（ICLR）](https://openreview.net/forum?id=guUUlHPXRw) — 何时做 CPT
- [AWS 金融领域 CPT](https://aws.amazon.com/blogs/machine-learning/efficient-continual-pre-training-llms-for-financial-domains/)
- [Databricks CPT 实践](https://www.databricks.com/blog/characterizing-datasets-and-building-better-models-continued-pre-training)

### 多市场/多 LoRA 架构
- [vLLM LoRA Adapters 文档](https://docs.vllm.ai/en/latest/features/lora/) — per-request 路由
- [Multi-LoRA 生产部署（Medium）](https://medium.com/codetodeploy/multi-lora-in-production-designing-for-vllm-and-eks-e8bc6a8b4b92)
- [Mixture-of-LoRAs（arXiv）](https://arxiv.org/html/2403.03432v1)
- [SLoRA 终身学习（IJCNLP 2025）](https://aclanthology.org/2025.ijcnlp-srw.4.pdf)
- [冻基底+多LoRA防遗忘指南](https://pub.towardsai.net/a-guide-to-fine-tuning-large-language-models-llms-without-catastrophic-forgetting-4b2c926f14a4)

### 项目内配套
- [`LOCAL_LLM_TRAINING_PLAN_V2.md`](./LOCAL_LLM_TRAINING_PLAN_V2.md) — 部署与硬件方案
- [`TRAINING_SAMPLE_FORMAT_SPEC.md`](./TRAINING_SAMPLE_FORMAT_SPEC.md) — 样本格式（含 asset_class 扩展）
- `training/tools/sample_pipeline.py` — 格式管线

---

> **文档结束。** 本方法论的核心论点：①标签必须来自真实结果而非规则（backtest_grid_v2）；②三阶段递进 CPT→SFT→DPO；③多市场用"冻底座+每市场LoRA+vLLM路由"。最大风险是标签噪声和过拟合，最大诚实地承认这个模型是"调参专家"而非"价格预言机"。行动第一优先级是实现 `backtest_grid_v2`——在那之前，所有 SFT 都没有真实意义。
