# 交易学习样本标准格式规范 v2.0

> **文档版本**：v2.0（重启自训练计划）
> **创建日期**：2026-07-10
> **关系**：升级 `dataset_builder.py` 当前输出的极简 `{"messages": [...]}` JSONL；向上兼容 HuggingFace `datasets` / Unsloth / TRL / DeepSpeed 全栈。
> **硬件前提**：GPU 算力机 6× RTX 2080ti 22GB（SM75 / Turing，共 132GB）+ 交易机（无 GPU）

---

## 0. 设计目标与原则

| 目标 | 说明 |
|---|---|
| **可直接训练** | 落盘格式无需二次加工即可喂给 Unsloth/TRL 的 SFTTrainer |
| **全量保留** | 交易过程产生的每一笔可学习信号都完整保留，不丢字段 |
| **可扩展** | schema 版本化（`schema_version`），新增字段不破坏旧样本 |
| **可复用** | 同一原始样本可派生 SFT / DPO / 评估 / 影子对比多用途 |
| **可审计** | 哈希链 + 来源标记，任何样本可回溯到具体交易快照 |
| **Turing 友好** | 数值类型/序列长度均按 22GB / fp16 / 无 Marlin 约束设计 |

**核心设计决策：原始层 + 标准层 + 训练层 三层分离。**

```
┌─────────────────────────────────────────────────────────┐
│  原始层 (Raw)    DecisionSnapshot 原始字段，只增不减       │  ← 数据库 / Raw JSONL
│         ↓ 清洗 · 去重 · 脱敏 · 哈希链                       │
│  标准层 (Canonical)  TradeLearnSample 标准对象            │  ← Parquet（长期归档）
│         ↓ 派生 · 标注 · 切分                               │
│  训练层 (Training)  SFT/DPO/Eval JSONL                    │  ← 喂给训练框架
└─────────────────────────────────────────────────────────┘
```

**为什么分三层：**
- 原始层保证"永不丢数据"（满足"完整保留"硬要求）。
- 标准层用 Parquet 列存，支持谓词下推快速过滤，是长期复用资产。
- 训练层是轻量派生物，格式随训练框架演进（SFT→DPO→GRPO）随时重生。

---

## 1. 存储格式选型

| 层 | 格式 | 理由 |
|---|---|---|
| **原始层** | JSONL（每行一个 `DecisionSnapshot` 快照）+ SQLite | 与现有 `decision_snapshots` 表一致，append-only，审计友好 |
| **标准层** | **Parquet**（按 `dataset_version`/`regime` 分区） | 列存压缩比高（JSON 的 1/4~1/8）、谓词下推、HuggingFace datasets / pandas / DuckDB 原生读 |
| **训练层** | **JSONL**（每行一个样本对象） | Unsloth/TRL/LLaMA-Factory/Axolotl 的通用输入格式，流式可读 |

> **不用 CSV**：嵌套字段（market_snapshot_json、messages）会破坏 CSV 结构。
> **不用单一巨型 JSON**：无法流式读写，损坏一处全文件报废。
> **训练层坚持 JSONL**：虽然 Parquet 更省空间，但所有主流训练框架的 loader 都先吃 JSONL/streaming，Parquet 留作归档与检索。

---

## 2. 字段定义

### 2.1 标准层 TradeLearnSample Schema（v2.0）

这是核心数据契约。每个标准样本是一个扁平化 + 结构化混合对象。

```jsonc
{
  // ── 元数据（必填）──────────────────────────────────────
  "schema_version": "2.0",                 // 格式版本，Breaking change 时升 major
  "sample_id": "tls_01J7X...8F2",          // ULID，全局唯一，时间有序
  "source_snapshot_id": 1234567,           // 对应 decision_snapshots.id（回溯用）
  "source_session_id": "sess_abc123",
  "created_at": "2026-07-10T08:30:00Z",    // ISO8601 UTC
  "pipeline_version": "dataset_builder@2.0.0",  // 生成此样本的代码版本
  "content_hash": "sha256:9f2a...",        // 样本内容哈希（去重用）

  // ── 哈希链（审计，可选但推荐）──────────────────────────
  "prev_hash": "sha256:1b8c...",           // 上一条样本的 content_hash，形成链

  // ── 市场上下文特征（必填，模型输入 X）─────────────────
  "market_context": {
    "asset_class": "crypto",               // crypto | a_share | hk | us（决定路由到哪个 LoRA）
    "regime": "trending",                  // trending | ranging | volatile | quiet
    "symbol": "BTC-USD-PERP",
    "window_start": "2026-07-01T00:00:00Z",
    "window_end": "2026-07-08T00:00:00Z",
    "window_days": 7,
    "volatility_at_window": 0.42,          // ATR/price 或已实现波动率
    "trend_strength": 0.65,                // |ADX| 或回归 R²
    // ── 按 asset_class 条件填充的字段（其他市场为 null）──
    "sector": null,                        // 股票：科技/消费/金融…；crypto 为 null
    "market_cap_tier": null,               // 股票：large|mid|small；crypto 为 null
    "crypto_specific": {                   // 仅 crypto 填充
      "funding_rate_regime": "positive"    // 资金费率状态
    },
    "equity_specific": null                // 仅股票填充：{t_plus_one, limit_up_down, ...}
  },

  // ── 统计特征（必填，模型输入 X）───────────────────────
  "stats": {
    "trades": 28,
    "win_rate": 0.42,
    "avg_win_pct": 1.82,
    "avg_loss_pct": 2.31,
    "net_pnl_pct": -3.4,
    "fee_gross_ratio": 0.35,               // 手续费侵蚀指标（近似）
    "max_drawdown_pct": 5.2,
    "sharpe_estimate": -0.8                // 窗口 Sharpe（若有）
  },

  // ── 当前门控参数（必填，模型输入 X 的一部分）──────────
  "current_gates": {
    "min_risk_reward": 1.8,
    "scalp_min_confidence": 70,
    "max_daily_trades": 12
  },

  // ── 标签（必填，模型输出 Y）───────────────────────────
  "label": {
    "target_gates": {                      // 监督目标
      "min_risk_reward": 2.0,
      "scalp_min_confidence": 72,
      "max_daily_trades": 7
    },
    "label_kind": "tighten",               // tighten | relax | neutral
    "label_method": "rule_v1",             // rule_v1 | backtest_grid_v2 | dpo_pair
    "label_confidence": 0.75,              // 标签置信度（供过滤用）
    "reasoning": "净亏(-3.4%)且平均亏损(2.31)>平均盈利(1.82)，提高盈亏比门槛"
  },

  // ── 质量与标注（必填）─────────────────────────────────
  "annotation": {
    "quality": "usable",                   // usable | marginal | reject
    "regime_confidence": 0.88,             // regime 判定置信度
    "sample_weight": 1.0,                  // 训练时的样本权重（噪声样本降权）
    "flags": ["high_fee_erosion"]          // 标记数组，便于过滤
  },

  // ── DPO 偏好对（可选，DPO 训练时填充）─────────────────
  "preference": {                          // 仅 label_method="dpo_pair" 时存在
    "chosen_gates": {"min_risk_reward": 2.0, "...": "..."},
    "rejected_gates": {"min_risk_reward": 1.5, "...": "..."},
    "preference_source": "retrospective_was_correct"
  },

  // ── 训练样本派生（训练层填充，标准层可空）─────────────
  "messages": null,                        // 标准层留空，训练层派生时填充
  "split": null                            // train | val | test（切分时填）
}
```

### 2.2 字段必填/选填矩阵

| 字段 | 原始层 | 标准层 | SFT 训练层 | DPO 训练层 |
|---|---|---|---|---|
| `schema_version` | — | ✅必填 | ✅ | ✅ |
| `sample_id` | — | ✅ | ✅ | ✅ |
| `source_snapshot_id` | ✅(id) | ✅ | — | — |
| `content_hash` / `prev_hash` | ✅ | ✅ | — | — |
| `market_context` | ✅(分散) | ✅ | — | — |
| `market_context.asset_class` | — | ✅(v2.0+) | ✅(LoRA路由) | ✅ |
| `stats` | ✅(分散) | ✅ | — | — |
| `current_gates` | ✅(runtime) | ✅ | — | — |
| `label.target_gates` | — | ✅ | ✅(→assistant) | — |
| `label.reasoning` | — | ✅ | ✅(→assistant) | — |
| `preference` | — | ○可选 | — | ✅ |
| `annotation` | — | ✅ | ✅(过滤用) | ✅ |
| `messages` | — | — | ✅必填 | ✅(chosen/rejected) |
| `split` | — | — | ✅ | ✅ |

---

## 3. 数据标注规范

### 3.1 标签方法（`label_method` 字段）

| 方法 | 说明 | 何时启用 |
|---|---|---|
| `rule_v1` | 现有 `_label_params` 规则派生（与 decision_feedback 4 条规则一致） | **当前主路径**，数据 <5k 样本 |
| `backtest_grid_v2` | 预留接口 `_label_v2`：对窗口做参数网格回测，取 Sharpe 最高组 | 数据 ≥5k 后启用，提升标签质量 |
| `dpo_pair` | 从 `DecisionRetrospective.was_correct` 构造 chosen/rejected 对 | DPO 精调阶段 |
| `llm_distill_v1` | 云端强模型（DeepSeek/GPT-5）对 10% 样本润色 reasoning（蒸馏标注） | 可选增强 |

### 3.2 标签质量分级（`annotation.quality`）

| 等级 | 判定 | 处理 |
|---|---|---|
| `usable` | 窗口 trades ≥ 8，regime_confidence ≥ 0.6 | 正常训练 |
| `marginal` | 窗口 trades 5~7 或 regime 模糊 | `sample_weight` 降为 0.5，仅入 train |
| `reject` | trades <5 或 net_pnl 极端异常（|pnl|>50%，疑似脏数据） | 不入训练，仅归档 |

### 3.3 标签一致性约束（强校验）

所有 `label.target_gates` 值必须满足边界（与 `dataset_builder._PARAM_BOUNDS` 一致），校验失败则整条样本降级为 `reject`：

```python
PARAM_BOUNDS = {
    "min_risk_reward": (1.5, 3.0),
    "scalp_min_confidence": (55.0, 85.0),
    "max_daily_trades": (3.0, 10.0),
}
```

---

## 4. 清洗与预处理流程

```
DecisionSnapshot (DB)
    │
    ▼ ① 过滤：pnl_pct IS NOT NULL（已平仓）+ timestamp >= since
    ▼ ② 去重：按 content_hash（market_context+stats+label 联合哈希）
    ▼ ③ 异常剔除：|net_pnl|>50% 标 reject；trades<5 标 marginal/reject
    ▼ ④ 滑窗聚合：7天窗口，3天步长（_WINDOW_DAYS/_STEP_DAYS）
    ▼ ⑤ 标签构造：rule_v1（默认）/ backtest_grid_v2（升级）
    ▼ ⑥ 质量分级 + sample_weight 赋值
    ▼ ⑦ 哈希链：prev_hash = 上一条 content_hash
    ▼ ⑧ 分区落盘 Parquet（标准层）+ 派生 JSONL（训练层）
```

**去重策略**：同一 (regime, stats 桶, label_kind) 下，若 content_hash 碰撞则只保留 `created_at` 最早的一条，避免高度相似样本稀释训练。

---

## 5. 训练层格式（SFT / DPO）

### 5.1 SFT JSONL（与现有 `dataset_builder` 输出兼容）

```json
{"messages": [
  {"role": "system", "content": "你是加密永续合约交易平台的门控参数优化器。根据给定的市场状态和历史交易统计，输出建议的门控参数。决策原则：手续费占比高则降频收紧，平均亏损>平均盈利则提高盈亏比门槛，胜率高且赚多亏少则可适度放松。只能输出 JSON，不要输出任何其它内容。"},
  {"role": "user", "content": "当前市场环境：trending\n统计窗口（7天）：\n- 交易笔数：28\n- 胜率：42.0%\n- 平均盈利：1.8200\n- 平均亏损：2.3100\n- 净盈亏：-3.4000\n- 手续费侵蚀指标：0.3500\n\n请输出建议的门控参数，严格 JSON 格式：\n{\"min_risk_reward\": <1.5-3.0>, \"scalp_min_confidence\": <55-85整数>, \"max_daily_trades\": <3-10整数>, \"confidence\": <0-1>, \"reasoning\": \"<一句话理由>\"}"},
  {"role": "assistant", "content": "{\"min_risk_reward\": 2.0, \"scalp_min_confidence\": 72, \"max_daily_trades\": 7, \"confidence\": 0.75, \"reasoning\": \"净亏(-3.40%)且平均亏损(2.31)>平均盈利(1.82)，提高盈亏比门槛\"}"}
]}
```

> 这是 Unsloth `SFTTrainer` / TRL 的标准 chat 格式，**与现有 v1.0 输出 100% 兼容**。v2.0 的改进是：此文件由标准层 Parquet 派生而来，可随时重生，且派生时可注入 system prompt 变体。

### 5.2 DPO JSONL（TRL DPOTrainer 格式）

```json
{"prompt": "当前市场环境：trending\n统计窗口（7天）：...\n请输出建议参数：",
 "chosen": "{\"min_risk_reward\": 2.0, \"scalp_min_confidence\": 72, \"max_daily_trades\": 7, \"reasoning\": \"...\"}",
 "rejected": "{\"min_risk_reward\": 1.5, \"scalp_min_confidence\": 60, \"max_daily_trades\": 12, \"reasoning\": \"...\"}"}
```

`chosen` 来自该窗口实际盈利/回测最优参数，`rejected` 来自同输入下表现较差的参数（由 `DecisionRetrospective.was_correct=False` 的历史决策提供）。

---

## 6. 格式转换与验证示例

完整可运行脚本见 `training/tools/sample_pipeline.py`（本仓库已提供）。下面是要点。

### 6.1 标准层 → SFT 训练层（派生）

```python
from training.tools.sample_pipeline import canonical_to_sft_jsonl
canonical_to_sft_jsonl(
    parquet_path="training/data/canonical/v2.0.parquet",
    out_path="training/data/sft/train.jsonl",
    split="train",
    system_prompt_variant="default",   # 可切换 prompt 变体
)
```

### 6.2 验证脚本（CI 必过）

```bash
python -m training.tools.sample_pipeline validate training/data/sft/train.jsonl
# 校验：schema_version、字段必填、label 边界、messages 结构、assistant JSON 可解析
# 退出码非0则阻断训练
```

### 6.3 Parquet 与 JSONL 互转

```bash
# 标准 JSONL → Parquet（归档）
python -m training.tools.sample_pipeline pack training/data/canonical.jsonl training/data/canonical/v2.0.parquet

# Parquet → 标准 JSONL（检索后导出）
python -m training.tools.sample_pipeline unpack training/data/canonical/v2.0.parquet training/data/canonical.jsonl
```

### 6.4 训练框架直读示例

```python
# Unsloth / TRL 直读（标准层 Parquet）
from datasets import load_dataset
ds = load_dataset("parquet", data_files="training/data/canonical/v2.0.parquet")
# 派生 messages 后即可进 SFTTrainer

# DeepSpeed / FSDP 多卡读 JSONL（streaming，避免全量加载）
ds = load_dataset("json", data_files="training/data/sft/train.jsonl", streaming=True)
```

---

## 7. 可扩展性机制

| 变更类型 | 机制 |
|---|---|
| **新增市场（A股/港股/美股）** | `market_context.asset_class` 字段已预留；按 asset_class 条件填充市场特有特征；训练时每市场独立一个 LoRA（见 [方法论 §5](./PROPRIETARY_MODEL_TRAINING_METHODOLOGY.md)）；vLLM 按 `model` 字段 per-request 路由 |
| 新增参数维度（如加 `max_leverage`） | `target_gates` 加字段；旧样本该字段缺省 → 派生时填 `_BASELINE_PARAMS` 默认值；`schema_version` 不升 major |
| 新增任务（如止损位预测） | 新增 `label.task_type` 字段；派生不同 `messages` 模板；Parquet 同表共存 |
| 新增特征（如链上数据） | `market_context` 加字段；旧样本该字段为 null，训练时 mask |
| Breaking 变更 | `schema_version` 升 major（2.0→3.0），保留旧 Parquet，新版本独立分区目录 |
| 训练框架换代 | 只改训练层派生逻辑（`canonical_to_*`），标准层不动 |

### 7.1 多市场样本分区（Parquet）

标准层 Parquet 按 `asset_class` 分区存储，便于按市场独立训练：

```
training/data/canonical/
├── asset_class=crypto/     ← 当前（虚拟币）
│   └── v2.0.parquet
├── asset_class=us/         ← 未来（美股）
│   └── v2.0.parquet
├── asset_class=a_share/    ← 未来（A股）
│   └── v2.0.parquet
└── asset_class=hk/         ← 未来（港股）
    └── v2.0.parquet
```

```python
# 只取某市场数据训练对应 LoRA
ds = load_dataset("parquet", data_files="training/data/canonical/asset_class=crypto/v2.0.parquet")
```

---

## 8. 与现有 `dataset_builder.py` 的关系

| 现状（v1.0） | v2.0 升级 |
|---|---|
| 直接输出极简 `{"messages": [...]}` | 先产标准层 Parquet，再派生训练层 JSONL |
| 无 schema 版本 | `schema_version="2.0"` |
| 无哈希链/去重 | content_hash + prev_hash，去重 |
| 无质量分级 | usable/marginal/reject + sample_weight |
| 标签只有 rule_v1 | 预留 backtest_grid_v2 / dpo_pair / llm_distill |
| 无 DPO 支持 | preference 字段 + DPO JSONL 派生 |
| 单一 JSONL 输出 | 三层分离（Raw JSONL / Parquet / 训练 JSONL） |

**迁移策略**：`dataset_builder.build_dataset()` 内部改为"写标准层 Parquet + 派生训练层 JSONL"，CLI 参数不变，对外行为向后兼容（`--out train.jsonl` 仍可用）。

---

> **配套文件**：
> - 端到端技术方案 → [`LOCAL_LLM_TRAINING_PLAN_V2.md`](./LOCAL_LLM_TRAINING_PLAN_V2.md)
> - 格式转换/验证脚本 → `training/tools/sample_pipeline.py`
