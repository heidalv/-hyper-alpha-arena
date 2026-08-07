# 自有大模型训练计划 v2.0（重启方案）

> **项目**：Hyper-Alpha-Arena 加密永续合约 AI 全自动交易平台
> **文档版本**：v2.0（在 v1.0 `LOCAL_LLM_SELF_TRAINING_DESIGN.md` 基础上重启升级）
> **创建日期**：2026-07-10
> **硬件**：GPU 算力机 6× RTX 2080ti **22GB**（魔改版，SM75/Turing，共 132GB）+ 交易机（无 GPU）
> **互连**：**已采购 NVLink 桥（BR02，2-slot / 相邻双卡，100GB/s 双向）**——2080Ti 是最后一代支持 NVLink 的消费级卡，此为稀缺资源，直接解锁 vLLM-2080Ti-Definitive 双卡 TP=2 满血路径
> **配套**：样本格式规范 [`TRAINING_SAMPLE_FORMAT_SPEC.md`](./TRAINING_SAMPLE_FORMAT_SPEC.md) · 格式脚本 `training/tools/sample_pipeline.py`

---

## 0. 为什么要重启 v2.0

v1.0 设计（2026-06-24）方向正确——"门控参数优化器 + Qwen3-30B-A3B + Unsloth QLoRA + vLLM"——并已落地 `dataset_builder.py`。本次重启**不是推翻重来**，而是针对四个新要求升级：

| 新要求 | v1.0 现状 | v2.0 升级 |
|---|---|---|
| 样本标准化 + 可扩展复用 | 极简 messages JSONL，无 schema 版本 | 三层分离（Raw/Canonical Parquet/Training JSONL）+ 哈希链 + 质量分级 |
| 对接 vLLM-2080Ti-Definitive | 通用 vLLM/AWQ | 明确 Turing 硬约束，定制量化/内核选型 |
| 前沿文献落地 | QLoRA 基础 | 引入 2025-2026 最新优化（GaLore、FSDP+QLoRA、长上下文优化）|
| 多卡并行策略 | "单卡即可，其余闲置" | 6 卡显式分工 + **NVLink 双卡 TP=2**（桥已采购）+ 可选数据并行 |

**重启核心立场**：方案必须**今天能落地**，但**一年内不落后**。

---

## 1. 第一性原理：Turing 架构的硬约束（决定一切选型）

> ⚠️ 这是整个方案最关键的一节。所有技术选型都被这三条物理约束框定，任何忽视它们的方案都会在生产中失败。

RTX 2080 Ti 是 **Turing 架构，SM75，compute capability 7.5**。它有三个无法绕过的硬件约束：

| 约束 | 影响 | 对策 |
|---|---|---|
| **① 不支持 bfloat16**（需 CC≥8.0） | QLoRA 教程默认 `bf16=True` 会直接报错；vLLM 默认 dtype 推断 bf16 会崩溃 | 全链路强制 `fp16`，训练用 `GradScaler` 防 NaN |
| **② 不支持 FP8 硬件计算**（需 CC≥8.9） | vLLM 原生 FP8 内核（E4M3/E5M2）无法加载；Marlin 内核（需 SM80）不可用 | 推理量化用 AWQ/GPTQ INT4（走默认 AWQ 内核，SM75 兼容），**不用 FP8** |
| **③ 部分 Triton 内核不适用** | Unsloth 最新加速内核部分针对 Ampere+ 优化 | 用 Unsloth 但接受"加速幅度小于 A100"，必要时回退 bitsandbytes |

**关于 vLLM-2080Ti-Definitive 的"FP8 weight"声明**：该项目宣称"support of FP8 weight"，但在 Turing 上这必然是通过**自定义 dequant-to-fp16 内核**实现（运行时把 FP8 权重反量化成 FP16 再计算），而非原生 FP8 tensor core。这意味着：
- 权重体积小（省显存，可塞更大模型/更长上下文）✅
- 但**计算速度没有 FP8 加速**（反量化有开销），解码速度优势主要来自 Qwen MoE 的低激活成本 + KV cache 优化
- 对本项目（低频调参，非高并发推理）**完全够用**，但不应期待 H100 级吞吐

> **诚实结论**：vLLM-2080Ti-Definitive 的价值在于"在 Turing 上把 vLLM 跑起来 + 262K 长上下文 + 100+ tok/s 单请求解码"，而非"FP8 硬件加速"。本项目采纳其运行时配方，但量化路径以 AWQ INT4 为主、FP8-weight 为备选（仅当需要塞下更大模型时）。

### 1.1 NVLink：2080Ti 的稀缺红利（已采购）

> **2080Ti 是最后一代支持 NVLink 的消费级显卡**——从 RTX 30 系起，NVIDIA 砍掉了消费级卡的 NVLink。项目这 6 张 2080Ti 若配上 NVLink 桥，是当下能买到的、最便宜的、原生 NVLink 多卡消费级平台。

**已决策**：采购 **1 组 NVLink 桥（BR02，2-slot / 相邻双卡）** 先验证，跑通后再评估加购。

#### NVLink vs PCIe 的差距（为什么值得买）

| 互连 | 双向带宽 | vLLM 张量并行表现 |
|---|---|---|
| PCIe 3.0 x16（无桥） | ~5 GB/s | all-reduce 每层都卡在 PCIe，TP=2 几乎无收益甚至变慢 |
| **NVLink（2080Ti BR02）** | **100 GB/s** | **20 倍**带宽，TP=2 接近线性加速 |

vLLM 张量并行（TP）在**每个 Transformer 层**都要做一次 all-reduce 同步，所以卡间带宽是命门。PCIe 的 3-5GB/s 会让通信成本压垮计算收益；NVLink 的 100GB/s 让 TP=2 真正可用。这正是 [vLLM-2080Ti-Definitive](https://github.com/weicj/vLLM-2080Ti-Definitive) 标题强调 "dual + NVLink" 的原因。

#### 采购规格（2-slot 相邻 / 已确认）

| 项 | 规格 | 说明 |
|---|---|---|
| 型号 | NVIDIA BR02 NVLink Bridge | 官方桥；第三方（技嘉 AORUS / AliExpress）也可 |
| **间距** | **2-slot（~40.64mm）** | ⚠️ 由主板插槽间距决定，**买错装不上** |
| 参考价 | ~$79（官方）/ ~$60-88（第三方） | 3-slot/4-slot 同价 |
| 验收 | `nvidia-smi nvlink -s` 或 `nvidia-smi topo -m` | 应显示两卡间 `NV#` 而非 `SYS`/`PHB` |

> **2-slot 桥的购买提醒**：NVIDIA 官方 GeForce RTX 桥只列了 3-slot / 4-slot。2-slot 相邻需买 **Quadro RTX NVLink 桥（兼容 2080Ti，实测可达满血 100Gbps）** 或第三方 2-slot 桥。下单前务必用尺量准两张卡金手指间距 ≈ 40mm。

#### NVLink 解锁的能力（从"备选"升级为"已具备"）

| 能力 | 无 NVLink | **有 NVLink（现在）** |
|---|---|---|
| vLLM TP=2 推理 | ❌ PCIe 太慢，不建议 | ✅ **接近线性加速，吞吐翻倍** |
| 单请求长上下文（262K） | ⚠️ 单卡 22GB KV cache 紧张 | ✅ **双卡 44GB，长上下文从容** |
| 装更大模型（如未来 Qwen3.6-35B FP16） | ❌ 单卡放不下 | ✅ 双卡分片放下 |
| vLLM-2080Ti-Definitive 双卡满血配方 | ❌ 退化 | ✅ **官方推荐配置** |

#### 验证 NVLink 已生效

```bash
# 1. 拓扑：两卡间应显示 NV# 而非 SYS/PHB
nvidia-smi topo -m

# 2. vLLM 启用 TP=2（指定两张相邻卡）
CUDA_VISIBLE_DEVICES=0,1 vllm serve merged/gate-optimizer-v1 \
  --tensor-parallel-size 2 \
  --dtype float16 --quantization awq \
  --host 0.0.0.0 --port 8000 ...

# 3. 对照：不指定 TP（=1）跑同样负载，对比 tok/s
#    TP=2 + NVLink 应接近 2x；TP=2 + PCIe 可能 <1x
```

---

## 2. 模型选型建议

### 2.1 主推：Qwen3-30B-A3B-Instruct（MoE）

沿用 v1.0 结论，理由不变且被新调研强化：

| 维度 | 数值 | 来源 |
|---|---|---|
| 架构 | MoE，总参 30B，**激活仅 3B** | Qwen 官方 |
| Unsloth QLoRA 显存 | **17.5GB**（单卡 22GB 富余 4.5GB） | [Unsloth 官方](https://unsloth.ai/docs/models/tutorials/qwen3-how-to-run-and-fine-tune) |
| 推理速度（vLLM Turing） | 70-100 tok/s 单请求 | vLLM-2080Ti-Definitive 实测 |
| 中文金融能力 | 强 | Qwen 中文优势 + 项目 prompt 已为中文优化 |

**Turing 适配关键**：MoE 每次只激活 3B，意味着反量化/计算量本就小——**Turing 的算力短板被 MoE 架构天然弥补**。这是选 MoE 而非稠密 30B 的根本原因。

### 2.2 备选梯队（按场景）

| 模型 | 场景 | 显存 | 备注 |
|---|---|---|---|
| Qwen3-14B（稠密） | MVP 验证链路 / 快速迭代 | ~15GB | 训练快，质量略低 |
| Qwen3-8B | 超快实验 / CI 冒烟测试 | ~9GB | 用于数据管线回归测试 |
| Qwen3.6-35B-A3B | 主模型成熟后升级（若开源权重可用） | ~20GB | 注意 2507 变体 QLoRA 需 ~30GB，22GB 卡需进一步量化 |

### 2.3 排除项

| 模型 | 排除理由 |
|---|---|
| DeepSeek V4 Flash | [本地不可行](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/discussions/12)，只适合调 API |
| Qwen3.5-397B-A22B | 太大，132GB 也装不下 |
| 任何稠密 ≥30B | 激活全量，Turing 算力吃不消 |

---

## 3. 多卡并行策略（6×22GB 显式分工）

### 3.1 推荐分工（稳态运行，NVLink 双卡 TP=2 已启用）

```
6 × 2080ti 22GB (共 132GB)    ┃ NVLink 桥 (BR02, 2-slot) 连接 卡0↔卡1
┌──────────────────────────────────────────────────────────┐
│ 卡0 ┃ vLLM 生产推理 ┐                                     │
│ 卡1 ┃  TP=2 NVLink ┘  Qwen3-30B-A3B AWQ                   │ 接 Governor 调参闭环
│      │            ↑ 双卡分片，吞吐近2x，KV cache 翻倍      │ （主力，vLLM-2080Ti-Definitive 配方）
│ 卡2  │ vLLM 影子推理 (上一代模型 TP=1, A/B 对比)           │ 效果评估
│ 卡3  │ Unsloth QLoRA 训练 (17.5GB, 每周离线)               │ 训练流水线
│ 卡4  │ 数据生成 / 标签蒸馏 / 回测网格                       │ 数据流水线
│ 卡5  │ 热备 / 第二组 NVLink 候选位                         │ 扩展预留
└──────────────────────────────────────────────────────────┘
```

**关键变化（相比 NVLink 未采购版）**：生产推理从"单卡 TP=1"升级为"**双卡 NVLink TP=2**"——这是 vLLM-2080Ti-Definitive 的官方推荐配置，吞吐近 2 倍，且双卡 44GB 显存让长上下文（最高 262K）和未来更大模型（Qwen3.6-35B）都有空间。训练仍用单卡（QLoRA 17.5GB，无需多卡）。

> **卡选择约定**：NVLink 桥物理连接哪两张卡，TP=2 就用哪两张。用 `CUDA_VISIBLE_DEVICES=0,1` 锁定，`nvidia-smi topo -m` 确认两卡间显示 `NV#`。桥接的两张卡必须是**相邻物理槽位**（本项目为 2-slot 相邻）。

**为什么默认单卡训练而非多卡并行**——这是有意识的工程权衡：

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **单卡 QLoRA**（推荐） | 配置简单、17.5GB 稳跑、无通信开销、与现有文档一致 | 训练时间长（5k 样本约 2-4h） | **当前阶段最佳** |
| 多卡 DDP | 近线性加速 | Qwen3 MoE 有已知 DDP OOM bug（[unsloth #3942](https://github.com/unslothai/unsloth/issues/3942)）；每卡都要装下全模型 | 数据量大后再评估 |
| 多卡模型并行（device_map） | 能训放不下的模型 | per-step 更慢，有跨卡通信 | 仅超大模型用 |

### 3.2 升级路径：何时切多卡

| 触发条件 | 切换动作 |
|---|---|
| 单次训练 >8h 且每周训 2 次以上 | 评估 DDP（先验证 unsloth #3942 已修复） |
| 样本 >50k 需全参微调 | 切 FSDP + QLoRA（ZeRO-3 + CPU offload） |
| 要训 Qwen3.6-35B（放不下单卡） | 切模型并行 `device_map="balanced"` 或 NVLink 张量并行 |

### 3.3 NVLink 部署指引（桥已采购，2-slot 相邻）

NVLink 桥（BR02，2-slot）已采购，连接两张相邻 2080Ti，开启 vLLM 张量并行 TP=2。

**启用三步**：

```bash
# 1. 装桥后验证物理连通（两卡间应显示 NV#，非 SYS/PHB）
nvidia-smi topo -m
# 预期：      GPU0  GPU1  ...
#         GPU0   X    NV#   ...    ← NV# 表示 NVLink 直连
#         GPU1  NV#    X    ...

# 2. vLLM 用 TP=2 启动（CUDA_VISIBLE_DEVICES 锁定有桥的两卡）
CUDA_VISIBLE_DEVICES=0,1 vllm serve merged/gate-optimizer-v1 \
  --tensor-parallel-size 2 \
  --dtype float16 --quantization awq \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.90 \
  --host 0.0.0.0 --port 8000 --api-key local-secret-2026

# 3. 对照基准：临时切 TP=1 跑同样请求，对比吞吐
#    TP=2 + NVLink 应接近 2x；若 <1.3x 说明 NCCL 未走 NVLink，检查环境变量：
#    export NCCL_P2P_DISABLE=0   （确保不误禁 P2P）
#    export NCCL_IB_DISABLE=1    （单机多卡不需要 InfiniBand）
```

**何时用 TP=2 vs TP=1**：

| 场景 | 选择 | 理由 |
|---|---|---|
| 生产门控优化器（低频、单请求） | TP=2 | 吞吐翻倍 + KV cache 翻倍，支持更长窗口统计上下文 |
| 影子 A/B（另一代模型，独立卡） | TP=1 | 影子模型单独占卡2，不抢生产卡的 NVLink 对 |
| 需 262K 超长上下文 | TP=2（必选） | 单卡 22GB 装不下 262K KV cache，双卡 44GB 才够 |
| 训练期间（卡0/1 让出给生产） | 生产临时降 TP=1 | 训练在卡3，生产可继续 TP=2，互不干扰 |

**后续加购评估**：1 组桥验证收益后，若影子推理也想上 TP=2（装下更大模型），可给卡2↔卡3 加第 2 组桥。2080Ti 每卡仅 1 个 NVLink 接口，所以每组桥固定绑 2 张卡，互不冲突。

---

## 4. 显存预算（精确到 GB）

### 4.1 训练侧（单卡，Qwen3-30B-A3B QLoRA）

| 项 | 显存 | 说明 |
|---|---|---|
| 4-bit NF4 基座权重 | ~9 GB | 30B × 0.5 bytes/param（double_quant 再省）|
| LoRA adapter（fp16） | ~0.3 GB | rank=16，仅 attention 投影 |
| 激活值（seq=2048, batch=1） | ~5 GB | MoE 仅激活 3B，梯度检查点可再降 |
| 优化器状态（AdamW 8-bit） | ~1 GB | 仅 LoRA 参数 |
| CUDA context + 碎片 | ~2 GB | |
| **合计** | **~17.3 GB** | ✅ 22GB 富余 4.7GB |

> 富余显存可用于：加大 batch_size、加长序列、或梯度检查点关闭换速度。

### 4.2 推理侧

**模式 A：单卡 TP=1（Qwen3-30B-A3B AWQ INT4）**

| 项 | 显存 | 说明 |
|---|---|---|
| AWQ INT4 权重 | ~16 GB | 30B × 0.5 bytes（+少量缩放因子）|
| KV cache | ~3 GB | max-model-len=8192，并发数=2 |
| 激活/临时 | ~1.5 GB | MoE 激活 3B |
| **合计** | **~20.5 GB** | ✅ 刚好 22GB，`--gpu-memory-utilization 0.92` |

**模式 B：双卡 NVLink TP=2（推荐主力，桥已采购）**

| 项 | 显存（每卡） | 说明 |
|---|---|---|
| AWQ INT4 权重 | ~8 GB | TP=2 对半分片，每卡 15B 权重 |
| KV cache | ~10 GB | **max-model-len 可拉到 32768+，并发翻倍** |
| 激活/临时 | ~1 GB | 分片后更小 |
| **合计** | **~19 GB/卡** | ✅ 双卡各 22GB 富余，**吞吐近 2x** |

> **模式 B 的核心红利**：不是"省显存"，而是"用富余显存换更长上下文 + 更高并发"。单卡 KV cache 只够 8K 上下文×2 并发；双卡可上 32K+ 上下文，对"注入更多历史交易窗口统计"的进阶 prompt 有直接价值。262K 极限长上下文则需配合 FP8-weight 压权重。

### 4.3 6 卡总预算（NVLink 已启用）

| 卡 | 用途 | 峰值显存 |
|---|---|---|
| 卡0 ┐ | 生产推理 TP=2（NVLink 对）| ~19 GB |
| 卡1 ┘ | | ~19 GB |
| 卡2 | 影子推理 TP=1（A/B）| 20.5 GB |
| 卡3 | 训练 / 数据回测 | 17.3 GB / <10 GB |
| 卡4-5 | 热备 / 第二组 NVLink 候选 | 0 |

---

## 5. 训练与推理框架（优先适配 vLLM-2080Ti-Definitive）

### 5.1 训练框架：Unsloth（主） + LLaMA-Factory（备）

| 框架 | 角色 | 理由 |
|---|---|---|
| **Unsloth** | 主训练框架 | 原生 Qwen3 MoE 支持、QLoRA 显存最低（17.5GB）、2026 Faster MoE 1.8x |
| **LLaMA-Factory** | 备选/多卡升级时 | 成熟 DeepSpeed/FSDP 集成、YAML 配置、社区 Qwen 配置丰富 |

**Turing 训练配置（关键，区别于网上多数教程）**：

```python
from unsloth import FastLanguageModel
import torch

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="Qwen/Qwen3-30B-A3B-Instruct",
    max_seq_length=2048,
    dtype=torch.float16,          # ⚠️ 不是 bfloat16！Turing 不支持
    load_in_4bit=True,            # NF4 QLoRA
)

model = FastLanguageModel.get_peft_model(
    model,
    r=16, lora_alpha=32, lora_dropout=0.05,
    target_modules=["q_proj","k_proj","v_proj","o_proj",
                    "gate_proj","up_proj","down_proj"],
    use_gradient_checkpointing="unsloth",
)

# Trainer
from trl import SFTTrainer, SFTConfig
trainer = SFTTrainer(
    model=model, tokenizer=tokenizer,
    train_dataset=ds,
    args=SFTConfig(
        output_dir="checkpoints/sft_v1",
        num_train_epochs=4,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=16,   # 等效 batch=16
        learning_rate=2e-4,
        lr_scheduler_type="cosine",
        fp16=True,    # ⚠️ 必须 fp16，不能 bf16
        bf16=False,   # ⚠️ Turing 不支持
        logging_steps=10,
        save_strategy="epoch",
    ),
)
```

**fp16 训练的 NaN 防护**：fp16 比 bf16 更易梯度溢出。HF Trainer 的 `fp16=True` 自带动态 loss scaling，通常足够。若仍 NaN：
1. 降学习率（2e-4 → 1e-4）
2. 加梯度裁剪 `max_grad_norm=1.0`（默认已有）
3. 极端情况启用 `bf16=False, fp16=False`（纯 fp32，慢但稳，显存会超——仅调试用）

### 5.2 推理框架：vLLM（适配 vLLM-2080Ti-Definitive 配方）

**部署路径**：WSL2 + Ubuntu 22.04（vLLM 原生仅 Linux）。

**Turing 推理启动命令（核心）—— NVLink 双卡 TP=2（主力推荐，桥已采购）**：

```bash
# NVLink 对的两张卡（用 nvidia-smi topo -m 确认显示 NV#）
CUDA_VISIBLE_DEVICES=0,1 vllm serve merged/gate-optimizer-v1 \
  --host 0.0.0.0 --port 8000 \
  --api-key local-secret-2026 \
  --dtype float16 \                    # ⚠️ 不用 auto/bfloat16
  --quantization awq \                 # 或 gptq；不用 fp8
  --max-model-len 32768 \              # 双卡 KV cache 充裕，可拉长上下文
  --gpu-memory-utilization 0.90 \
  --tensor-parallel-size 2             # ✅ NVLink 双卡，吞吐近 2x
```

**单卡 TP=1 回退命令（影子推理 / NVLink 不可用时）**：

```bash
CUDA_VISIBLE_DEVICES=2 vllm serve merged/gate-optimizer-prev \
  --host 0.0.0.0 --port 8001 \         # 影子用不同端口
  --api-key local-secret-2026 \
  --dtype float16 --quantization awq \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.92 \
  --tensor-parallel-size 1 \
  --enforce-eager                       # 可选：禁 CUDA graph 省显存
```

**适配 vLLM-2080Ti-Definitive 的要点**：
1. **采纳其环境配方**：特定 vLLM 版本 + CUDA 12.1 + Turing SM75 编译 flags（解决官方 Docker 不认 2080Ti 的问题）
2. **量化选 AWQ INT4 而非 FP8**：Turing 无 FP8 tensor core，AWQ 默认内核 SM75 兼容且质量损失小
3. **若需 262K 长上下文**：启用其 FP8-weight 方案（反量化到 fp16），把权重从 16GB 压到 ~8GB，腾出 KV cache 空间——本项目当前 max-len=8192 暂不需要
4. **参考 [2080Ti-LLM-Toolbox](https://github.com/weicj/2080Ti-LLM-Toolbox)** 的 SM75 补丁与 benchmark

### 5.3 量化路径对比（Turing 视角）

| 方法 | 显存（30B） | Turing 兼容 | 质量 | 本项目选择 |
|---|---|---|---|---|
| FP16（不量化） | ~60GB | ✅ | 满血 | ❌ 放不下 |
| INT8 | ~30GB | ✅ | 高 | ❌ 单卡放不下 |
| **AWQ INT4** | **~16GB** | **✅（默认内核）** | 高 | **✅ 推理主选** |
| GPTQ INT4 | ~16GB | ✅（ExLlamaV2 内核） | 高 | 备选 |
| bnb NF4（训练用） | ~9GB | ✅ | 训练用 | ✅ 训练专用 |
| FP8 weight | ~8GB | ⚠️ 仅反量化，无硬件加速 | 高 | 仅长上下文场景 |
| Marlin INT4 | ~16GB | ❌ 需 SM80 | — | ❌ 不可用 |

---

## 6. 数据管线（端到端）

```
┌─────────────────────── 交易机（无 GPU）───────────────────────┐
│                                                               │
│  交易决策 → DecisionSnapshotWriter → decision_snapshots 表     │
│                      → DecisionRetrospective（平仓对错标签）   │
│                          │                                    │
│  dataset_builder v2.0 ◄──┘                                    │
│    ① 滑窗聚合 ② rule_v1/backtest_grid_v2 标签                 │
│    ③ 质量分级 ④ 哈希链                                        │
│    ⑤ 写标准层 Parquet + 派生 SFT/DPO JSONL                    │
│                          │                                    │
│  training/data/ ◄────────┘                                    │
│    canonical/v2.0.parquet   sft/train.jsonl   dpo/pairs.jsonl │
│                          │ rsync / 内网共享                    │
└──────────────────────────┼────────────────────────────────────┘
                           ▼
┌─────────────────────── GPU 算力机 ────────────────────────────┐
│  training/tools/sample_pipeline.py validate sft/train.jsonl   │
│                          │                                    │
│  Unsloth QLoRA 训练 ◄────┘  (CUDA_VISIBLE_DEVICES=0)          │
│    checkpoints/sft_vN/ → merge_and_quantize → AWQ             │
│                          │                                    │
│  vLLM serve (卡1) ◄──────┘    gate-optimizer-vN               │
│                          │                                    │
│  /v1/chat/completions ◄──┘ OpenAI 兼容                        │
└──────────────────────────┼────────────────────────────────────┘
                           ▼
┌─────────────────────── 交易机接入 ────────────────────────────┐
│  gate_optimizer_service → call_llm_api_sync(内网IP:8000)      │
│    → submit_intent("local_llm_optimizer") → RuntimeGovernor   │
│    → runtime_tuning.json → unified_gate → 下一笔交易          │
│    → 新 DecisionSnapshot → 闭环                                │
└───────────────────────────────────────────────────────────────┘
```

**数据量规划**：
- 当前：90 天历史，滑窗后约 0.5k-2k 样本（v1.0 现状）
- 6 个月目标：5k-20k 样本（触发 backtest_grid_v2 升级）
- 标签质量提升路径：rule_v1 → backtest_grid_v2 → +llm_distill（10% 云端润色）

---

## 7. 迭代更新机制

### 7.1 模型版本号

`gate-optimizer-v{N}`，N 单调递增。每版记录：
- 训练数据版本（Parquet 的 `dataset_version`）
- 基座 + LoRA config 哈希
- 训练 loss 曲线
- 影子 A/B 对比结果（见 7.3）

### 7.2 训练触发节奏

| 频率 | 触发 | 动作 |
|---|---|---|
| 每日 | 定时器 | `dataset_builder` 增量生成新样本，追加 Parquet |
| 每周 | 定时器 / 手动 | 用累积样本训练 `gate-optimizer-v{N+1}` |
| 每版 | 训练完成 | 影子 A/B 评估 → 决定是否切换生产 |

### 7.3 影子 A/B 安全切换（复用现有基础设施）

新模型不直接上岗，先在卡2影子跑：

```
卡1 生产模型 vN ──┐
                  ├─→ 对同一窗口统计各出参数建议 ──→ gate_optimizer_logs 表
卡2 影子模型 vN+1 ┘                                  （记录两组建议 + 随后窗口实际 Sharpe）
                                                        │
                                  连续 3 个窗口 vN+1 建议 Sharpe ≥ vN ──→ 切换生产
                                  否则 ──→ 保留 vN，分析 vN+1 退化原因
```

**安全护栏（全部复用现有，不新增）**：
1. `RuntimeGovernor` 优先级仲裁：manual(100)/opencode(80) 可随时覆盖 local_llm_optimizer(55)
2. `v5_gates_rollback.flag` 熔断：亏损超阈值撤销所有反馈意图
3. `unified_gate` 硬边界：参数超界自动截断
4. TTL 过期：local_llm_optimizer 意图 36h 后自动失效

### 7.4 持续学习（避免灾难性遗忘）

| 机制 | 说明 |
|---|---|
| 增量数据 | 每次训练用最近 90 天全量重训（非仅增量），保证 regime 覆盖 |
| 样本权重 | 近期样本 weight=1.0，旧样本 weight 衰减（如 90 天前 weight=0.5）|
| 正则 | LoRA rank=16 本身是强正则，全参微调时才需额外 EWC/回放 |
| 回滚 | 每版权重保留，A/B 失败可秒回 vN |

---

## 8. 实施里程碑（重启后 6-8 周路线）

### 阶段 0：环境验证（1 周）
- [ ] GPU 机 WSL2 + CUDA 12.1 + vLLM 安装，按 vLLM-2080Ti-Definitive 配方验证 2080Ti 可用
- [ ] `nvidia-smi` 确认 6 卡 22GB；vLLM 用 AWQ 跑通 Qwen3-30B-A3B 推理（FP16 dtype）
- [ ] Unsloth QLoRA 在单卡跑通 10 步训练（验证 fp16 不 NaN）
- [ ] **安装 NVLink 桥（BR02，2-slot）→ `nvidia-smi topo -m` 确认两卡间显示 `NV#`**
- [ ] **vLLM TP=2 跑通，对照 TP=1 确认吞吐提升（应近 2x）**

### 阶段 1：数据管线升级（1-2 周）
- [ ] 落地 `training/tools/sample_pipeline.py`（标准层 Parquet + 验证脚本）
- [ ] 升级 `dataset_builder.py` 为 v2.0（三层输出）
- [ ] 现有 90 天数据回填标准层 Parquet

### 阶段 2：MVP 链路（2 周）—— 不微调，prompt-only
- [ ] vLLM 部署现成 Qwen3-30B-A3B 到 **卡0+卡1（NVLink TP=2）**
- [ ] 交易机加 LLMConfiguration，接通 `gate_optimizer_service`
- [ ] 验证"LLM 出参数 → Governor 仲裁 → 写 runtime_tuning"全链路

### 阶段 3：首次微调（2 周）
- [ ] 用 v2.0 数据管线生成首批 SFT 数据集
- [ ] Unsloth QLoRA 训练 `gate-optimizer-v1`（卡3 单卡）
- [ ] 合并 + AWQ 量化 + 部署到 **卡0+卡1（生产 TP=2）**，基座移到卡2（影子 TP=1）

### 阶段 4：评估与迭代（1 周）
- [ ] 影子 A/B 对比 v1 vs 基座
- [ ] 可选 DPO 精调（用 DecisionRetrospective 偏好对）
- [ ] 确认收益后固定为周度训练节奏

---

## 9. 风险与缓解（v2.0 新增项）

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Turing fp16 训练 NaN | 中 | 中 | 动态 loss scaling + 降 lr + 梯度裁剪；最坏退 8B 模型验证 |
| vLLM 官方 Docker 不认 2080Ti | 高 | 高 | 用 vLLM-2080Ti-Definitive 配方或 WSL2 源码装 |
| AWQ 在 Turing 质量退化 | 低 | 中 | A/B 影子对比基座，退化则回退 GPTQ 或 FP16 多卡 |
| Qwen3 MoE DDP OOM（若上多卡） | 中 | 中 | 训练坚持单卡；多卡前先验证 unsloth #3942 修复 |
| 训练样本 <1k 过拟合 | 高 | 中 | MVP 先 prompt-only；rule_v1 标签 + sample_weight；积累后再微调 |
| 262K 长上下文显存不足 | 低 | 低 | **已缓解**：NVLink 双卡 44GB，8192-32K 充裕；262K 极限再用 FP8-weight |
| NVLink 桥规格买错装不上 | — | — | **已确认 2-slot 相邻**；下单前量准金手指间距≈40mm；验收 `nvidia-smi topo -m` |
| NCCL 未走 NVLink（TP=2 无加速） | 中 | 中 | 设 `NCCL_P2P_DISABLE=0`；`nvidia-smi topo -m` 确认 NV#；对照 TP=1 基准测 |

---

## 10. 参考资源

### 开源项目（本项目直接适配）
- [vLLM-2080Ti-Definitive](https://github.com/weicj/vLLM-2080Ti-Definitive) — Turing 上跑 vLLM 的权威配方
- [2080Ti-LLM-Toolbox](https://github.com/weicj/2080Ti-LLM-Toolbox) — SM75 补丁与 benchmark

### 前沿文献与技术报告（2024-2026）
- [A Study of Optimizations for Fine-tuning LLMs](https://arxiv.org/html/2406.02290v2) — 微调显存/运行时权衡综述
- [Unsloth Qwen3 微调文档](https://unsloth.ai/docs/models/tutorials/qwen3-how-to-run-and-fine-tune) — 17.5GB QLoRA 实证
- [Unsloth Faster MoE](https://unsloth.ai/docs/basics/faster-moe) — MoE 微调 1.8x 加速
- [Unsloth 多卡 DDP](https://unsloth.ai/docs/basics/multi-gpu-training-with-unsloth/ddp) — 多卡训练（含 Qwen3 MoE 注意事项）
- [LLaMA-Factory 分布式训练](https://llamafactory.readthedocs.io/en/latest/advanced/distributed.html) — DeepSpeed/FSDP 备选
- [vLLM 量化文档](https://docs.vllm.ai/en/latest/features/quantization/) — 各量化方法与内核的 CC 要求
- [MARLIN 论文](https://arxiv.org/pdf/2408.11743) — 解释为何 Marlin 需 SM80+（Turing 不可用）
- [QLoRA + bitsandbytes](https://huggingface.co/blog/4bit-transformers-bitsandbytes) — NF4 原理，Turing 兼容
- [FP16 混合精度训练陷阱](https://gigagpu.com/mixed-precision-training-guide/) — Turing 上 GradScaler 防 NaN

### 金融领域私有化部署
- [FinGPT](https://github.com/ai4finance-foundation/fingpt) · [arXiv](https://arxiv.org/html/2306.06031v2) — 金融 LLM 开源框架
- [FinLoRA 金融微调基准](https://arxiv.org/html/2505.19819v1)
- [FinGPT 2025 本地部署指南](https://www.qwe.edu.pl/ai-tools/fingpt-open-source-finance-llm-install-guide/) — RTX 3090 本地部署参考
- [Cohere: 银行私有化 AI 部署](https://cohere.com/blog/private-ai-deployments-for-banks)
- [LLMs Meet Finance (IDS2025)](https://www.cloud-conf.net/datasec/2025/proceedings/pdfs/IDS2025-3SVVEmiJ6JbFRviNl4Otnv/966100a057/966100a057.pdf) — Qwen2.5/DeepSeek-R1 金融微调基准

### 项目内既有文档（本方案升级对象）
- [`LOCAL_LLM_SELF_TRAINING_DESIGN.md`](./LOCAL_LLM_SELF_TRAINING_DESIGN.md) — v1.0 设计（方向不变）
- [`LOCAL_LLM_GPU_HOST_GUIDE.md`](./LOCAL_LLM_GPU_HOST_GUIDE.md) — GPU 机部署
- [`LOCAL_LLM_TRADING_HOST_GUIDE.md`](./LOCAL_LLM_TRADING_HOST_GUIDE.md) — 交易机接入
- [`TRAINING_SAMPLE_FORMAT_SPEC.md`](./TRAINING_SAMPLE_FORMAT_SPEC.md) — v2.0 样本格式规范

---

> **文档结束**。本方案的所有选型都受第一节"Turing 三大约束"框定，凡与该约束冲突的"先进技术"（FP8 硬件加速、Marlin 内核、bf16 训练）均已明确排除或降级。方案在 2026-07 落地，核心路径（Unsloth QLoRA + AWQ vLLM + 单卡训练）保守稳定，升级路径（多卡 DDP、长上下文 FP8-weight、全参 FSDP）预留且条件清晰。
