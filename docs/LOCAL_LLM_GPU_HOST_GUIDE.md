# 本地 LLM GPU 主机部署指南

> **适用机器**：GPU 算力机（Windows，6× RTX 2080ti 22GB，共 132GB）
> **本机职责**：为交易机提供"门控参数优化器"的 **OpenAI 兼容推理 API**，并负责模型 **微调训练**。
> **本机不接触任何交易逻辑、不下单、不读交易决策链。**
> **配套文档**：交易机侧配置见 [`LOCAL_LLM_TRADING_HOST_GUIDE.md`](./LOCAL_LLM_TRADING_HOST_GUIDE.md)；整体设计与调研见 [`LOCAL_LLM_SELF_TRAINING_DESIGN.md`](./LOCAL_LLM_SELF_TRAINING_DESIGN.md)

---

## 目录

- [0. 职责边界（先读）](#0-职责边界先读)
- [1. 硬件与环境前提](#1-硬件与环境前提)
- [2. 网络规划](#2-网络规划)
- [3. 模型选型](#3-模型选型)
- [4. 推理服务部署](#4-推理服务部署)
- [5. 微调训练流程](#5-微调训练流程)
- [6. 模型更新与热加载](#6-模型更新与热加载)
- [7. 显存与卡分工](#7-显存与卡分工)
- [8. 健康检查与监控](#8-健康检查与监控)
- [9. 常见问题](#9-常见问题)

---

## 0. 职责边界（先读）

本机（GPU 机）只做两件事：

| 任务 | 说明 | 频率 |
|---|---|---|
| **推理服务** | 常驻进程，对外暴露 `http://本机IP:8000/v1/chat/completions`（OpenAI 兼容），供交易机调用 | 7×24 常驻 |
| **微调训练** | 接收交易机生成的训练数据（JSONL），用 Unsloth QLoRA 训练，产出新权重合并后加载到推理服务 | 每周/每日一次 |

**数据流向：**

```
交易机                        本机（GPU 机）
──────                        ──────────────
生成训练数据 JSONL  ──拷贝──>  Unsloth QLoRA 训练
                                   ↓
                              合并 LoRA + 量化
                                   ↓
交易机调用 /v1/chat/completions <── 加载到推理服务（热加载）
                                   ↓
                              返回参数建议 JSON
```

**不做的事：** 不连交易机的交易数据库做实时查询、不下单、不运行 Hyper-Alpha-Arena 后端代码。

---

## 1. 硬件与环境前提

| 项目 | 要求 |
|---|---|
| GPU | 6 × RTX 2080ti **22GB**（魔改版），共 132GB |
| 系统 | Windows 10/11，与交易机同一内网 |
| 驱动 | NVIDIA 驱动 ≥ 550，CUDA ≥ 12.1 |
| Python | 3.10 或 3.11（Unsloth/vLLM 兼容） |
| 磁盘 | ≥ 100GB 空闲（模型权重 + 训练 checkpoint） |

**验证 GPU：**

```powershell
nvidia-smi
# 应看到 6 张 2080ti，每张 22GB
```

---

## 2. 网络规划

### 2.1 确认本机内网 IP

```powershell
ipconfig
# 记下内网 IP，例如 192.168.1.100（后续交易机配置要用）
```

### 2.2 端口规划

| 服务 | 端口 | 用途 |
|---|---|---|
| vLLM（生产） | **8000** | OpenAI 兼容 API |
| Ollama（MVP） | **11434** | OpenAI 兼容 API |

### 2.3 防火墙放行（管理员 PowerShell）

```powershell
New-NetFirewallRule -DisplayName "vLLM-8000" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
New-NetFirewallRule -DisplayName "Ollama-11434" -Direction Inbound -LocalPort 11434 -Protocol TCP -Action Allow
```

### 2.4 关键：监听 0.0.0.0

**所有服务必须监听 `0.0.0.0`（所有网卡），默认只听 `127.0.0.1` 内网访问不了。** 各服务的 `--host 0.0.0.0` 参数见第 4 节。

### 2.5 安全

- 即使内网，**务必设置 API Key**（`--api-key`），防止同网其他机器误调
- 交换机层面建议把 8000/11434 端口只对交易机 IP 开放

---

## 3. 模型选型

**基座：Qwen3-30B-A3B-Instruct（MoE 架构）**

| 维度 | 数值 |
|---|---|
| 架构 | MoE，总参数 30B，**每次激活 3B** |
| 推理速度 | **70-100 tokens/s**（vLLM，单卡） |
| 微调显存 | **17.5GB（Unsloth QLoRA，单卡即可）** |
| 中文金融能力 | 强（Qwen 中文优势） |

**为什么 MoE：** 总参数大（质量接近 30B 稠密），但只激活 3B（推理快如 3B 小模型）——速度与质量兼得。

备选：Qwen3-14B（稠密，MVP 更轻）；Qwen3.6-35B-A3B（更新 MoE 版，如开源权重可用）。详见整体设计文档第 3 节。

---

## 4. 推理服务部署

### 4.1 方案 A：Ollama（MVP 首选，Windows 原生，最简单）

```powershell
# 1. 安装 Ollama Windows 版（官网下载）
# 2. 设置监听所有网卡 + API（环境变量，需在启动 serve 前设）
setx OLLAMA_HOST "0.0.0.0:11434"

# 3. 拉取模型
ollama pull qwen3:30b-a3b

# 4. 启动服务（常驻）
ollama serve
# 验证：访问 http://localhost:11434/v1/models
```

Ollama 自带 OpenAI 兼容层（`/v1/chat/completions`），交易机可直接调用。

### 4.2 方案 B：vLLM（生产首选，需 WSL2 或 Docker）

vLLM 原生只支持 Linux，Windows 上推荐 **WSL2**：

```powershell
# 1. 启用 WSL2（管理员 PowerShell）
wsl --install -d Ubuntu-22.04
```

```bash
# 2. WSL2 内安装 vLLM
pip install vllm

# 3. 启动服务（监听 0.0.0.0，设 API Key）
vllm serve Qwen/Qwen3-30B-A3B-Instruct \
  --host 0.0.0.0 \
  --port 8000 \
  --api-key local-secret-2026 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.9 \
  --quantization awq
```

> **WSL2 网络注意：** WSL2 默认用 NAT，内网访问需用 Windows 主机 IP + 端口转发，或配置镜像网络模式（`wsl.conf` / `.wslconfig` 的 `networkingMode=mirrored`）。镜像模式下 WSL 内 `0.0.0.0:8000` 直接等于 Windows 主机的 8000 端口。

### 4.3 验证服务就绪

```powershell
# 本机自测
curl http://localhost:8000/v1/models        # vLLM
curl http://localhost:11434/v1/models       # Ollama
```

应返回包含 `Qwen3-30B-A3B` 的模型列表。

---

## 5. 微调训练流程

### 5.1 接收训练数据

训练数据（JSONL 格式）由**交易机生成**（交易机读 `DecisionSnapshot` 并用其回测引擎计算最优参数标签）。本机只需接收：

- **方式 1（简单）**：交易机导出 `train.jsonl`，通过内网共享/拷贝到本机 `training/data/`
- **方式 2（可选）**：本机训练脚本直连交易机 PostgreSQL 只读拉取（需交易机开放 5432 内网访问）

每行 JSONL 格式（SFT）：

```json
{"messages": [
  {"role": "system", "content": "你是门控参数优化器……"},
  {"role": "user", "content": "在 trending 市场环境下，过去7天统计：胜率42%，平均盈1.8%，平均亏2.3%，手续费占比35%，样本28笔。当前参数 min_risk_reward=1.8。"},
  {"role": "assistant", "content": "{\"min_risk_reward\": 2.0, \"scalp_min_confidence\": 70, \"max_daily_trades\": 7, \"reasoning\": \"手续费占比35%侵蚀盈利且平均亏损>盈利，建议提高盈亏比门槛至2.0并收紧日交易上限\"}"}
]}
```

### 5.2 目录结构

```
training/
├── data/
│   └── train.jsonl              # 交易机生成的训练数据放这里
├── configs/
│   └── qwen30b_moe_qlora.yaml   # Unsloth 配置
├── train_sft.py                 # SFT 训练入口
├── train_dpo.py                 # DPO 精调（可选）
├── merge_and_quantize.py        # 合并 LoRA → 量化 → 导出
└── reload_model.sh              # 重载到推理服务
```

### 5.3 安装训练依赖

```bash
pip install "unsloth[cu121-torch250]"  # 按 CUDA/Torch 版本选
pip install datasets trl peft
```

### 5.4 训练配置规格

| 项 | 值 |
|---|---|
| 框架 | Unsloth（原生支持 Qwen3 MoE，1.8x 加速） |
| 方式 | QLoRA，rank=16, alpha=32, 4-bit NF4 |
| 显存 | **17.5GB（单卡即可）** |
| GPU | `CUDA_VISIBLE_DEVICES=0`（用第 0 张卡，其余闲置或跑推理） |
| SFT | 3-5 epochs，cosine scheduler，lr=2e-4 |
| DPO（可选） | beta=0.1，1-2 epochs |

### 5.5 训练执行

```bash
# SFT（主路径）
CUDA_VISIBLE_DEVICES=0 python train_sft.py \
  --model Qwen/Qwen3-30B-A3B-Instruct \
  --data data/train.jsonl \
  --output_dir checkpoints/sft_v1 \
  --epochs 4 --lr 2e-4

# 可选 DPO 精调（需偏好对数据）
python train_dpo.py --base checkpoints/sft_v1 --data data/dpo.jsonl
```

### 5.6 合并与量化

```bash
python merge_and_quantize.py \
  --base Qwen/Qwen3-30B-A3B-Instruct \
  --lora checkpoints/sft_v1 \
  --out merged/gate-optimizer-v1 \
  --quant awq
```

产出：
- `merged/gate-optimizer-v1/`（合并后权重）
- 或 GGUF（给 Ollama）/ AWQ（给 vLLM）

---

## 6. 模型更新与热加载

训练出新一代模型后，需要让推理服务加载它：

### Ollama

```bash
# 从 GGUF 创建新模型
ollama create gate-optimizer-v1 -f Modelfile
# Modelfile 内：FROM ./merged/gate-optimizer-v1.gguf
```

交易机侧改 `LLMConfiguration.model` 为 `gate-optimizer-v1` 即可，无需重启。

### vLLM

vLLM 不支持运行中热换权重，需**重启服务**加载新模型：

```bash
# 停旧服务 → 启新模型
pkill -f "vllm serve"
vllm serve merged/gate-optimizer-v1 --host 0.0.0.0 --port 8000 --api-key local-secret-2026 ...
```

> **建议**：用两套配置（如 `gate-optimizer-v1`、`gate-optimizer-v2`）交替部署，切换时交易机改一行配置，零停机。

---

## 7. 显存与卡分工

6 张 22GB 卡的推荐分工（训练与推理可并行，互不抢卡）：

```
卡0: 微调训练 (Qwen3-30B-A3B QLoRA, 17.5GB)   ← 每周/每日离线
卡1: vLLM 推理 (生产服务)                      ← 接交易机调参闭环
卡2: vLLM 推理 (影子对比模型, A/B)             ← 评估
卡3-5: 闲置 / 数据预处理 / 其他任务
```

**关键：** 训练用 `CUDA_VISIBLE_DEVICES=0` 锁定单卡，推理用其他卡，避免显存冲突。

---

## 8. 健康检查与监控

### 自检脚本（本机定期跑）

```powershell
# 服务存活
curl -s http://localhost:8000/v1/models | findstr Qwen
# GPU 占用
nvidia-smi --query-gpu=index,memory.used,memory.total,utilization.gpu --format=csv
```

### 日志

- vLLM/Ollama 自身日志（stdout，建议重定向到文件）
- 建议记录：每次训练的 loss 曲线、推理服务的 QPS/延迟

### 与交易机的协同约定

- 交易机调用失败（超时/连接拒绝）会**自动跳过本轮调参**，不影响交易
- 本机宕机时，交易机的 `RuntimeGovernor` 会让 `local_llm_optimizer` 意图按 TTL 过期，**自动回退到现有规则**——所以本机可闲时关机省电

---

## 9. 常见问题

| 问题 | 解决 |
|---|---|
| 交易机访问不了本机服务 | 检查 `--host 0.0.0.0`、防火墙规则、WSL2 网络模式 |
| Ollama 只听 127.0.0.1 | 确认 `OLLAMA_HOST=0.0.0.0:11434` 已设置并重启 serve |
| vLLM 启动 OOM | 降 `--gpu-memory-utilization`、用 `--quantization awq`、减 `--max-model-len` |
| 训练显存超 22GB | 确认是 QLoRA（非全参），降 batch size / 序列长度 |
| MoE 训练报错 | 用最新 Unsloth（2026 Faster MoE 支持），不要用稠密模型教程 |
| 交易机报 SSL 错误 | 内网用 `http://` 而非 `https://`，httpx 对 HTTP 默认不校验证书 |

---

## 速查：交易机会问本机的两个信息

实施时，交易机需要从本机拿到这两个值填到数据库：

1. **推理服务地址**：`http://<本机内网IP>:8000/v1`（vLLM）或 `http://<本机内网IP>:11434/v1`（Ollama）
2. **API Key**：`local-secret-2026`（或你自定义的）

> 训练数据 JSONL 由交易机生成后送到本机 `training/data/`，本机负责训练+部署，不参与数据生成逻辑。

---

> **配套**：交易机如何配置和接入 → [`LOCAL_LLM_TRADING_HOST_GUIDE.md`](./LOCAL_LLM_TRADING_HOST_GUIDE.md)
