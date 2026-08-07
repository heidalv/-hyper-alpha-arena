# 本地化 LLM 自训练设计执行文档

> **项目**：Hyper-Alpha-Arena 加密永续合约 AI 全自动交易平台
> **文档版本**：v1.0
> **创建日期**：2026-06-24
> **状态**：设计定稿，待实施
> **硬件前提**：GPU 算力机 6× RTX 2080ti 22GB（共 132GB）+ 交易机（无 GPU），同一内网

---

## 目录

- [0. 文档摘要](#0-文档摘要)
- [1. 项目背景与目标](#1-项目背景与目标)
- [2. 第一部分：行业调研结论](#2-第一部分行业调研结论)
- [3. 第二部分：本地模型选型](#3-第二部分本地模型选型2026年6月)
- [4. 第三部分：核心定位——门控参数优化器](#4-第三部分核心定位门控参数优化器)
- [5. 第四部分：内网分离部署架构](#5-第四部分内网分离部署架构)
- [6. 第五部分：实现计划（5 个阶段）](#6-第五部分实现计划5-个阶段)
- [7. 第六部分：硬件适配方案](#7-第六部分硬件适配方案)
- [8. 第七部分：文件改动清单](#8-第七部分文件改动清单)
- [9. 第八部分：实施里程碑](#9-第八部分实施里程碑)
- [10. 第九部分：风险评估与安全护栏](#10-第九部分风险评估与安全护栏)
- [11. 附录 A：关键代码接口（file:line）](#附录-a关键代码接口fileline)
- [12. 附录 B：参考资料](#附录-b参考资料)

---

## 0. 文档摘要

本方案为 Hyper-Alpha-Arena 平台设计一套**本地化 LLM 自训练系统**，用于**优化交易门控参数**（如 `min_risk_reward`、`max_daily_trades`、`scalp_min_confidence` 等）。

**核心定位：** 训练一个专属本地模型，学习"在什么市场状态下、用什么参数最优"，通过系统现有的 `RuntimeGovernor`（运行时调参仲裁器）接入，**完全不触碰实时交易决策链**，安全且可回滚。

**关键设计决策：**

| 维度 | 决策 | 理由 |
|---|---|---|
| **模型定位** | 门控参数优化器（离线调参） | 不碰实时决策，多重护栏保护，风险最低 |
| **基座模型** | Qwen3-30B-A3B（MoE 架构） | 3B 激活成本，30B 质量，70-100 t/s |
| **微调方式** | Unsloth QLoRA，单卡 17.5GB | 无需复杂多卡分布式，单卡即可 |
| **部署架构** | 内网分离（GPU 机算力，交易机调用） | 算力与交易解耦，故障隔离 |
| **接入方式** | OpenAI 兼容 API，数据库加一行配置 | 代码零改动 |
| **数据闭环** | DecisionSnapshot → 训练 → 参数建议 → Governor → 新交易 | 自驱动迭代 |

---

## 1. 项目背景与目标

### 1.1 Hyper-Alpha-Arena 现状

Hyper-Alpha-Arena 是一个成熟的、生产级的 **LLM 编排的加密永续合约 AI 全自动交易平台**，核心能力包括：

- **双流派覆盖**：方向性 Alpha 交易（`full_auto_trading_service.py`，90s tick）+ 返点套利引擎（`rebate_arb/` S1-S8）
- **多智能体分层决策**：5 层管线 Direction → PositionSizing → RiskGate → Execution → Retrospective
- **多供应商云端 LLM**：GPT-5/Claude4/DeepSeek/Qwen 等，通过 OpenAI 兼容接口统一调用
- **完整的反馈闭环**：`decision_feedback_service` 用 4 条硬编码规则调参 + `RuntimeGovernor` 仲裁 + `v5_gates_rollback.flag` 熔断
- **丰富的数据资产**：`DecisionSnapshot`（决策上下文+结果）、`DecisionRetrospective`（对错标签+教训）、分层记忆（FinMem 启发）

### 1.2 为什么需要本地化 LLM 自训练

当前所有决策依赖**通用云端 LLM**，存在三个痛点：

1. **个性化缺失**：云端模型不懂"你的"交易风格和历史教训。虽然已有 prompt 注入历史教训（`ai_decision_service.py:1991`），但模型本身未内化这些经验。
2. **成本与延迟**：高频调用云端 API 成本高，且依赖外网稳定性。
3. **数据安全**：交易决策数据是核心资产，全量发给云端有泄露风险。

### 1.3 本方案的目标

> **训练一个专属本地模型，把它放在系统最安全的"离线调参"位置，持续把交易经验沉淀成更优的门控参数。**

不是替代云端 LLM 做实时决策（风险太高），而是**作为"参数顾问"增强现有的调参闭环**。

---

## 2. 第一部分：行业调研结论

### 2.1 成熟量化交易系统的两大流派

业界加密量化的系统分为两个流派，解决不同问题：

| 维度 | 方向性/Alpha 流派 | 做市/套利流派 |
|---|---|---|
| **代表** | Freqtrade | Hummingbot |
| **核心目标** | 赚价格方向波动 | 赚价差、流动性激励 |
| **盈利来源** | 趋势、均值回归、ML 信号 | 买卖价差、跨所价差、返点积分 |
| **风险特征** | 承担方向性风险 | 尽量中性，收益稳定 |
| **AI/ML 支持** | FreqAI 内置 | 较弱 |

**关键洞察：** Hyper-Alpha-Arena **同时覆盖两大流派**——`full_auto_trading_service.py`（Alpha）+ `rebate_arb/`（套利），成熟度超过多数公开框架。

**参考资料：** [Freqtrade GitHub](https://github.com/freqtrade/freqtrade) · [Hummingbot 官网](https://hummingbot.org/)

### 2.2 本地化 LLM 自训练技术栈

#### 金融领域基座：FinGPT

[FinGPT GitHub](https://github.com/ai4finance-foundation/fingpt) · [arXiv 论文](https://arxiv.org/html/2306.06031v2)

FinGPT 的核心差异化优势：**个性化能力**——能学习个人投资者偏好（风险厌恶、投资风格）。支持 LoRA 微调，可本地部署。

#### 微调技术栈（2025-2026）

| 关注点 | 推荐方案 |
|---|---|
| 金融任务起点 | [FinLoRA 论文](https://arxiv.org/html/2505.19819v1)（金融任务 LoRA/QLoRA 基准） |
| GPU 受限 | QLoRA（4-bit NF4 量化），单卡可跑 |
| 训练工具 | **Unsloth**（训练快 2 倍）或 axolotl |
| 推理部署 | vLLM（高吞吐）或 Ollama（单用户易部署） |

#### SFT vs DPO vs RL 选型

调研结论（[SFT/DPO/RLHF 指南](https://www.sundeepteki.org/advice/the-complete-guide-to-post-training-llms-how-sft-rlhf-dpo-and-grpo-shape-llm)）：

- **本方案主路径用 SFT**（监督微调，学习"市场状态→最优参数"映射），最稳、最可解释
- **DPO 仅作可选精调**（避开 Apple 研究警告的 DPO 分布外泛化弱的问题）
- **不走 RL 路线**（见 2.4 节 DRL 局限）

### 2.3 多智能体 LLM 交易框架对照（含本代码库分析）

#### TradingAgents（最相关）

[arXiv:2412.20138](https://arxiv.org/abs/2412.20138) · [GitHub](https://github.com/tauricresearch/tradingagents)

模拟真实交易公司组织架构，LLM 智能体扮演：基本面/情绪/技术分析师 → 多空研究员**多轮辩论** → 交易员 → 风控团队。

**对照本代码库：** 系统已有 TradingAgents 启发的辩论层（`trading_analysts.py:1580` `DebateLayer`），但是**一次性规则抽取**而非多轮 LLM 对抗。系统的分层记忆、历史类比 RAG、门控自调闭环**领先于论文**。

#### FinMem（分层记忆）

[arXiv:2311.13743](https://arxiv.org/abs/2311.13743) · [GitHub](https://github.com/pipiku915/finmem-llm-stocktrading)

三大模块：Profiling（动态角色设计）、Memory（分层记忆）、Decision-Making。

**对照本代码库：** 系统已显式实现 FinMem 启发的分层记忆（`trade_memory_context.py:9-13`）：
- 浅层：最近 15 笔战绩 + 30 天教训（`SHALLOW_LESSON_TTL_DAYS=30`）
- 深层：单笔亏损>2%权益 → 365 天 TTL（`DEEP_LESSON_TTL_DAYS=365`）
- 实现质量**超过论文**（有明确触发阈值、TTL、容量上限、Reflexion 异步反思）

#### 系统相对两篇论文的能力雷达

```
分层记忆     ████████████████████ 满分级（超过 FinMem）
历史类比检索 ████████████████████ 满分级（论文都没有）
反馈闭环     ████████████████████ 满分级（论文都没有）
多智能体协作 ████████░░░░░░░░░░░░ 中等（有分层，无真辩论）
```

### 2.4 DRL 的局限（解释为何不走 RL 路线）

主流 DRL 算法（PPO/SAC/TD3/DQN）应用加密货币交易，学界有**明确的局限性共识**：

| 局限 | 详情 |
|---|---|
| 回测过拟合 | DRL 智能体经常过拟合历史数据，上实盘就崩 |
| 高换手率 | PPO/SAC 策略交易过于频繁，滑点+手续费侵蚀 |
| 市场非平稳 | 加密货币 regime 切换快，挑战策略适应性 |
| 奖励设计 | 风险感知、成本感知的奖励函数仍是开放难题 |

**本代码库印证：** README 明确记载 DRL 当前 OFFLINED（无训练好的模型）。本方案**不走 DRL 路线**，改用 LLM 微调做参数优化——更稳、更可解释。

**参考资料：** [DRL 加密货币综述](https://www.emergentmind.com/topics/deep-reinforcement-learning-drl-for-cryptocurrency-trading)

---

## 3. 第二部分：本地模型选型（2026年6月）

### 3.1 Qwen 版本现状

| 版本 | 发布 | 开源 | 备注 |
|---|---|---|---|
| **Qwen3** | 2025-04 | ✅ 全开源 | 含 0.6B~32B 稠密 + **30B-A3B MoE** |
| **Qwen3.5** | 2026-02 | ✅ 开源（397B-A17B，太大不适合本地） | "最后一个开源旗舰" |
| **Qwen3.6** | 2026-03~04 | ⚠️ 部分开源（**35B-A3B 开源**，Plus 闭源） | 当前本地首选 |
| Qwen3.7-Max | 2026-05 | ❌ 闭源 | — |

**关键事实：** Qwen 从 3.5 起走"旗舰闭源、中型 MoE 开源"路线。本地部署应关注 **Qwen3-30B-A3B / Qwen3.6-35B-A3B**，不盲目追闭源旗舰。

### 3.2 为什么选 MoE 架构（核心选型逻辑）

**MoE（Mixture of Experts）= 总参数大，但每次只激活一小部分。**

以 Qwen3-30B-A3B 为例：
- 总参数 30B（知识容量大，质量接近 30B 稠密模型）
- **每次只激活 3B 参数**（推理速度≈3B 小模型）

这正是"**速度快 + 实用**"的最优解——用小模型算力成本，拿接近大模型的质量。

### 3.3 实测性能数据

| 指标 | 数值 | 来源 |
|---|---|---|
| 推理速度（RTX 3090, 30B-A3B） | **~72.9 tokens/s** | [r/LocalLLaMA 实测](https://www.reddit.com/r/LocalLLaMA/comments/1kdsp4z/) |
| Qwen3.6-27B（vLLM 优化） | **~100 tokens/s** | [PatentLLM 报告](https://media.patentllm.org/news/local-ai/qwen3-6-performance-boost-with-vllm-new-ollama-management-to-20260426) |
| 微调显存（30B-A3B, Unsloth） | **仅 17.5GB** | [Unsloth 官方](https://unsloth.ai/docs/models/tutorials/qwen3-how-to-run-and-fine-tune) |
| Unsloth MoE 加速 | **1.8x** | [Unsloth MoE 文档](https://unsloth.ai/docs/basics/faster-moe) |

### 3.4 选型结论

**主推基座：Qwen3-30B-A3B-Instruct（MoE）**

| 维度 | Qwen3-30B-A3B（选定） | 稠密 14B（备选） |
|---|---|---|
| 架构 | MoE（3B 激活） | 稠密（14B 全激活） |
| 推理速度 | 70-100 t/s | 6-15 t/s |
| 质量 | 接近 30B 稠密 | 14B 水平 |
| 微调显存 | 17.5GB（单卡即可） | ~15GB |

### 3.5 竞品排除理由

| 模型 | 判定 | 理由 |
|---|---|---|
| Qwen3.6-35B-A3B | ✅ 首选 | 开源权重，vLLM 原生支持，17.5GB 微调 |
| DeepSeek V4 Flash | ❌ 排除 | [太大，本地跑不动](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/discussions/12)，只适合调 API |
| GLM-5 / 5.1 | ⚠️ 次选 | 偏闭源/大模型路线，本地生态弱于 Qwen |

**参考资料：** [开源 LLM 对比 2026](https://lushbinary.com/blog/qwen-3-6-vs-gemma-4-llama-4-glm-5-1-deepseek-v4-open-source-comparison/) · [vLLM 部署指南](https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html)

---

## 4. 第三部分：核心定位——门控参数优化器

### 4.1 为什么不碰实时决策链

将本地微调模型放在系统**最安全的"离线调参"位置**，而非实时决策链，四大优势：

1. **零实时风险**：模型离线/低频运行，不碰交易决策，即使出错也被仲裁和现有规则保护
2. **数据闭环天然**：交易历史就是训练数据，参数调整效果立刻被新交易验证
3. **硬件适配**：离线训练对延迟不敏感，推理也是低频批处理
4. **可解释可回滚**：每次调整经 `RuntimeGovernor` 记录，有 rollback flag 兜底

### 4.2 接入现有调参闭环

系统现有调参闭环（`runtime_governor.py`）是一个带优先级仲裁的"调参中枢"：

```
当前：硬编码规则1-4 → submit_intent("decision_feedback") → Governor 裁决 → 写门槛
升级：本地微调模型 → submit_intent("local_llm_optimizer") → Governor 裁决 → 写门槛
                                       ↑（优先级低于 manual/opencode，高于规则）
```

`RuntimeGovernor` 接受多来源 `submit_intent(key, value, source, confidence, ttl_sec)`，按优先级裁决后唯一写入 `runtime_tuning.json`。新增 `local_llm_optimizer` 作为新来源，优先级设为 55（低于 manual 100 / opencode 80 / decision_feedback 60，高于 evolution_gc 50）。

### 4.3 数据闭环设计

```
交易产生 DecisionSnapshot（含市场上下文 + pnl 结果）
    ↓
聚合统计（按 regime + 时间窗口）
    ↓
构造 SFT 训练样本（市场状态 + 历史表现 → 最优参数）
    ↓
QLoRA 微调（学习"什么状态该用什么参数"）
    ↓
本地模型输出参数建议 + reasoning
    ↓
submit_intent("local_llm_optimizer") → Governor 仲裁
    ↓
写入 runtime_tuning.json → unified_gate 读取 → 影响下一笔交易
    ↓
新交易产生新 DecisionSnapshot → 回到起点（自驱动迭代）
```

---

## 5. 第四部分：内网分离部署架构

### 5.1 网络拓扑

```
┌─────────────────────────────────┐         ┌──────────────────────────────────┐
│   交易机 (Windows, 无GPU)        │         │   GPU算力机 (Windows, 6×2080ti)   │
│   Hyper-Alpha-Arena 后端         │         │                                  │
│                                  │  内网    │   vLLM 推理服务 (:8000)           │
│   ai_decision_service.py         │ ──────> │   ↑ Qwen3-30B-A3B (MoE)          │
│     call_llm_api_sync()          │  HTTP   │   │ OpenAI 兼容 /v1/chat/completions│
│        ↓ base_url=内网IP         │         │   │                              │
│   gate_optimizer_service.py      │         │   Unsloth 训练脚本 (离线/低频)    │
│        ↓ submit_intent()         │         │   ↓ 产出 LoRA 权重                │
│   runtime_governor (调参仲裁)    │         │   合并→量化→加载到 vLLM           │
└─────────────────────────────────┘         └──────────────────────────────────┘
```

### 5.2 零改动接入原理（关键）

系统已是 OpenAI 兼容 API 架构，接入 GPU 机器**仅需数据库加一行配置**：

- `call_llm_api_sync`（`llm_config_service.py:882, 929`）走标准 `POST {base_url}/chat/completions`
- `base_url` 由数据库 `LLMConfiguration.base_url` 字段决定（自由字符串）
- httpx client 对 `http://` 开头地址**默认不启用 SSL 验证**（HTTP 协议无 SSL），内网明文直接通
- `provider` 字段无枚举约束，填 `vllm` / `local` / `ollama` 即可

**从 `https://api.openai.com/v1` 改成 `http://192.168.x.x:8000/v1`，代码一行不用改。**

### 5.3 GPU 算力机部署方案

#### 方案 A：Ollama（MVP 首选，Windows 原生）

```bash
# Windows 原生版，安装即用
# 关键：设置监听所有网卡，否则内网访问不了
set OLLAMA_HOST=0.0.0.0:11434
ollama serve
ollama pull qwen3:30b-a3b
```

- 优势：Windows 原生，零配置，自带 OpenAI 兼容层
- 劣势：比 vLLM 慢，但对低频调参场景（每天/每周一次）完全够用

#### 方案 B：vLLM（生产首选，需 WSL2 或 Docker）

vLLM 原生只支持 Linux，Windows 上三条路：

| 方式 | 难度 | 性能 | 推荐 |
|---|---|---|---|
| Ollama Windows 版 | 极简 | 中 | MVP |
| WSL2 + vLLM | 中 | 高 | 榨取 MoE 性能 |
| Docker Desktop + vLLM 镜像 | 中 | 高 | 生产隔离 |

```bash
# WSL2 内
vllm serve Qwen/Qwen3-30B-A3B-Instruct \
  --host 0.0.0.0 \
  --port 8000 \
  --api-key local-secret-2026 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.9 \
  --quantization awq
```

**实施建议：先用 Ollama 跑通内网闭环（验证整个链路），再升级到 WSL2+vLLM 榨取 MoE 性能。**

### 5.4 网络层配置（3 件事）

#### 1. GPU 机器：开放防火墙端口

```powershell
# 管理员 PowerShell
New-NetFirewallRule -DisplayName "vLLM-8000" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
# 或 Ollama
New-NetFirewallRule -DisplayName "Ollama-11434" -Direction Inbound -LocalPort 11434 -Protocol TCP -Action Allow
```

#### 2. 交易机：验证连通性

```powershell
curl http://192.168.1.100:8000/v1/models
# 或 Ollama
curl http://192.168.1.100:11434/v1/models
```

#### 3. 内网安全

- **API Key 认证**：即使内网也设 `api-key`，防止同网其他机器误调
- **绑定内网网段**：交换机层面限制端口只对内网段开放

### 5.5 交易机数据库配置（唯一改动点）

```sql
-- 假设 GPU 机器内网 IP 是 192.168.1.100
INSERT INTO llm_configurations
(name, provider, model, base_url, api_key, is_default, is_active)
VALUES
('本地GPU-门控优化器', 'vllm', 'Qwen3-30B-A3B-Instruct',
 'http://192.168.1.100:8000/v1', 'local-secret-2026',
 'false', 'true');
```

### 5.6 训练数据流向

训练数据（交易历史）在交易机的 PostgreSQL 里。两种方式送到 GPU 机器：

- **简单**：定期 `pg_dump` 导出相关表，拷贝到 GPU 机器训练
- **优雅**：GPU 机器训练脚本直连交易机 PostgreSQL（内网只读，`postgresql://user:pass@192.168.1.x:5432/alpha_analytics`）

### 5.7 故障隔离优势

- GPU 机器宕机 → 交易机失去"调参建议"，但现有规则（decision_feedback 的 4 条规则）照常工作
- `RuntimeGovernor` 的 TTL 机制让 `local_llm_optimizer` 意图过期失效，自动回退
- 算力机器可闲时关机省电，交易机 7×24 跑

---

## 6. 第五部分：实现计划（5 个阶段）

### 阶段 1：训练数据集自动生成模块

**新建** `backend/services/local_llm/dataset_builder.py`

从现有数据库表自动构造 SFT 指令数据集。

**数据源 → 训练样本映射：**

```
DecisionSnapshot (market_snapshot_json + regime + pnl_pct)
  → 输入: "在 {regime} 市场环境下，过去7天统计：胜率X%，平均盈Y%，平均亏Z%，
          手续费占比W%，样本N笔。当前门控参数为 {当前gates}。"
  → 目标输出: {"min_risk_reward": 2.0, "scalp_min_confidence": 70, "max_daily_trades": 7,
              "reasoning": "近7天手续费占比35%侵蚀盈利，且平均亏损>平均盈利，
                           建议提高盈亏比门槛至2.0并收紧日交易上限"}
```

**关键设计：**
- 聚合 `DecisionSnapshot` 按 `(regime, 时间窗口)` 统计胜率/盈亏/手续费
- 窗口末端的"实际最优参数"用网格搜索回测确定（哪组参数在该窗口 Sharpe 最高）
- 生成 `(市场状态特征, 历史表现) → 最优参数` 映射，模型学"什么状态该用什么参数"
- 可选生成少量 DPO 偏好对（相同输入下好参数 vs 差参数）
- 输出标准 JSONL，兼容 HuggingFace `datasets`

**云端辅助点：** 可选用云端大模型（如 DeepSeek）给约 10% 样本润色 reasoning 文本。

**数据量目标：** 5000-20000 条样本（交易历史 + 滑窗回测扩增）

### 阶段 2：QLoRA 微调训练流程

**新建** `training/` 目录（独立于 backend）

```
training/
├── configs/
│   └── qwen30b_moe_qlora.yaml   # Unsloth 配置（lr, lora_r, epochs）
├── train_sft.py                 # SFT 阶段：Unsloth QLoRA 单卡
├── train_dpo.py                 # DPO 阶段（可选精调）
├── export_gguf.py               # 导出 GGUF / 合并 LoRA 权重
└── merge_and_quantize.sh        # 合并 LoRA → AWQ 量化 → 部署格式
```

**训练规格：**
- 框架：**Unsloth**（原生支持 Qwen3 MoE，2026 Faster MoE 加速 1.8x）
- QLoRA：rank=16, alpha=32, 4-bit NF4 量化基座
- 显存：**单卡 17.5GB**（一张 2080ti 22G 即可，无需多卡分布式）
- SFT：3-5 epochs，cosine scheduler，lr=2e-4
- DPO（可选第二阶段）：beta=0.1，1-2 epochs

### 阶段 3：本地推理服务部署

见第 5.3 节。部署到 GPU 算力机，通过内网暴露 OpenAI 兼容 API。

### 阶段 4：接入调参闭环（核心集成点）

**新建** `backend/services/local_llm/gate_optimizer_service.py`

这是唯一触碰运行时的文件，功能：

1. **低频触发**（每日收盘后 / 每周，由定时器调用）
2. 收集近 7/14/30 天 `DecisionSnapshot` 聚合统计 + 当前 `runtime_tuning.json` 参数
3. 调用本地微调模型（走现有 `call_llm_api_sync` 统一网关），输出建议参数 + reasoning
4. 对每个建议参数调用 `runtime_governor.submit_intent()`：

```python
from backend.services import runtime_governor as gov
gov.submit_intent(
    key="min_risk_reward",
    value=2.0,
    source="local_llm_optimizer",   # 新来源
    confidence=0.78,                 # 模型自报置信度
    reason="近7天手续费占比35%且平均亏损>盈利，建议收紧盈亏比",
    ttl_sec=36*3600,                 # 36 小时，与 decision_feedback 同级
)
```

5. Governor 自动按优先级与现有规则、OpenCode、manual 仲裁——**模型不会粗暴覆盖**

**注册新 source 优先级**（改 `runtime_governor.py`）：

```python
SOURCE_PRIORITY = {
    "manual": 100,
    "opencode": 80,
    "decision_feedback": 60,
    "local_llm_optimizer": 55,   # 新增：略低于规则反馈，高于 evolution
    "evolution_gc": 50,
    "maturity": 40,
    "default": 30,
}
DEFAULT_TTL_SEC["local_llm_optimizer"] = 36 * 3600
```

### 阶段 5：效果评估与安全护栏

复用现有 A/B 影子基础设施：

1. **影子对比**：本地模型和现有规则同时对同一窗口出参数建议，记录到新表 `gate_optimizer_logs`（含 model_output、rule_output、随后窗口实际 Sharpe），对比谁更优
2. **效果归因**：复用 `decision_feedback_service` 归因逻辑，统计"local_llm_optimizer 提议参数生效后"的交易表现
3. **安全护栏**（全部已有，直接复用）：
   - Governor 优先级仲裁（manual/opencode 可随时覆盖）
   - `v5_gates_rollback.flag` 熔断（`decision_feedback_service.py:355`）
   - `unified_gate` 硬边界保护（min_rr ∈ [1.5, cap]，超界自动截断）
   - paper 模式优先验证

---

## 7. 第六部分：硬件适配方案

### 6×2080ti 22GB（共 132GB）卡分工

```
6 × 2080ti 22GB (共 132GB)
┌─────────────────────────────────────────────┐
│ 卡0: 微调训练 (Qwen3-30B-A3B QLoRA, 17.5GB)  │  ← 每周/每日离线训练
│ 卡1: vLLM 推理 (生产门控优化器)              │  ← 接入 Governor 调参闭环
│ 卡2: vLLM 推理 (影子对比模型, A/B)           │  ← 阶段5 评估
│ 卡3-5: 数据生成/云端辅助标签/其他服务        │  ← 并行数据流水线
└─────────────────────────────────────────────┘
```

**关键优势：** MoE 的 17.5GB 单卡微调让多卡分布式不再必要，6 张卡可并行做训练、推理、数据生成、影子对比——硬件利用率最大化。

---

## 8. 第七部分：文件改动清单

### 新建（独立模块，低侵入）

| 文件 | 用途 | 阶段 |
|---|---|---|
| `backend/services/local_llm/__init__.py` | 模块初始化 | 1 |
| `backend/services/local_llm/dataset_builder.py` | 训练数据集生成 | 1 |
| `backend/services/local_llm/gate_optimizer_service.py` | 调参接入 | 4 |
| `backend/database/models.py`（增 `GateOptimizerLog` 表） | 影子对比日志 | 5 |
| `training/` 整个目录 | 训练流程 | 2-3 |

### 小改（接入点）

| 文件 | 改动 | 阶段 |
|---|---|---|
| `backend/services/runtime_governor.py` | `SOURCE_PRIORITY` + `DEFAULT_TTL_SEC` 加 `local_llm_optimizer` | 4 |
| `backend/services/evolution_scheduler.py`（或类似调度器） | 加每周触发 `gate_optimizer_service` 的定时任务 | 4 |
| 数据库 `llm_configurations` 表 | 加一行本地 GPU 配置 | 3 |
| 可选：Alembic migration | 建 `gate_optimizer_logs` 表 | 5 |

### 完全不改（零影响）

| 文件 | 理由 |
|---|---|
| `backend/services/ai_decision_service.py` | 实时决策链零影响 |
| `backend/services/decision_core/unified_gate.py` | 门控执行逻辑零影响，只读 runtime_tuning.json |
| 交易执行链路 | 完全不触碰 |

---

## 9. 第八部分：实施里程碑

### MVP（2-3 周）：验证链路可行性

- **不微调**，先用现成 Qwen3-30B-A3B 做 prompt-only 参数优化器
- 部署 Ollama 到 GPU 机，配置内网访问
- 交易机加 LLMConfiguration，接通 `gate_optimizer_service`
- 验证"LLM 出参数建议经 Governor 仲裁后是否有用"

### 微调（2-3 周）：积累数据后训练

- 实现 `dataset_builder.py`，从 `DecisionSnapshot` 生成 SFT 数据集
- Unsloth QLoRA 训练 Qwen3-30B-A3B
- 对比微调前后效果

### 精调与评估（1-2 周）：影子 A/B

- 阶段 5 影子 A/B 对比
- 可选 DPO 精调
- 确认收益后提高 source 优先级

---

## 10. 第九部分：风险评估与安全护栏

### 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|---|---|---|---|
| 微调模型输出不合理参数 | 中 | 低 | Governor 仲裁 + unified_gate 硬边界截断 |
| GPU 机器宕机 | 中 | 低 | TTL 自动回退到现有规则，交易不受影响 |
| 训练数据不足导致过拟合 | 中 | 中 | 滑窗回测扩增 + 影子 A/B 验证 + MVP 先 prompt-only |
| 内网网络抖动 | 低 | 低 | 调用失败时跳过本轮，下次重试 |
| MoE 微调配置复杂 | 低 | 中 | Unsloth 2026 MoE 文档覆盖，单卡简化 |

### 安全护栏（全部已有，直接复用）

1. **Governor 优先级仲裁**：manual(100) / opencode(80) 可随时覆盖 local_llm_optimizer(55)
2. **熔断机制**：`data/v5_gates_rollback.flag` 存在时撤销所有反馈意图（`decision_feedback_service.py:355`）
3. **硬边界保护**：`unified_gate` 对参数有上下限（如 min_rr ∈ [1.5, cap]），超界自动截断
4. **paper 模式优先**：现有规则对 paper 模式跳过激进调整，本地模型同样遵循
5. **TTL 过期**：`local_llm_optimizer` 意图 36 小时后自动失效，防止陈旧建议长期生效

---

## 附录 A：关键代码接口（file:line）

### LLM 调用接口（零改动接入点）

| 位置 | 说明 |
|---|---|
| `backend/services/llm_config_service.py:882` | `base_url` 拼接 |
| `backend/services/llm_config_service.py:929` | `POST {base_url}/chat/completions` |
| `backend/services/llm_config_service.py:660` | `_get_httpx_sync_client`（HTTP 默认无 SSL） |
| `backend/database/models.py:111` | `LLMConfiguration` 模型（base_url/api_key/model 自由字段） |

### 调参闭环接口（核心集成点）

| 位置 | 说明 |
|---|---|
| `backend/services/runtime_governor.py:65` | `SOURCE_PRIORITY`（需加 local_llm_optimizer） |
| `backend/services/runtime_governor.py:75` | `DEFAULT_TTL_SEC`（需加 local_llm_optimizer） |
| `backend/services/runtime_governor.py:26` | `submit_intent(key, value, source, confidence, reason, ttl_sec)` |
| `backend/services/decision_feedback_service.py:347` | `apply_gate_adjustments`（现有 4 条硬编码规则，对照参考） |

### 数据资产（训练数据源）

| 表 | 模型位置 | 用途 |
|---|---|---|
| `decision_snapshots` | `models.py:2777` | SFT 输入（market_snapshot_json）+ 输出（action/direction）+ 标签（pnl） |
| `decision_retrospectives` | `models.py:445` | DPO 偏好信号（was_correct + lesson_learned） |
| `ai_decision_logs` | `models.py:394` | 完整 prompt_snapshot + decision_snapshot + realized_pnl |
| `strategy_memories` | `models.py:665` | 聚合教训（key_lessons） |
| `trade_memory_records` | `models.py:2587` | 逐笔记忆（signal_source="llm" 可过滤） |

### 分层记忆（已有，FinMem 启发）

| 位置 | 说明 |
|---|---|
| `backend/services/trade_memory_context.py:9-13` | FinMem 理论依据注释 |
| `backend/services/trade_memory_context.py:27-30` | TTL：浅层 30 天 / 深层 365 天 |
| `backend/services/experience_retriever.py:46` | 决策前 RAG 检索自身历史 |

---

## 附录 B：参考资料

### 成熟量化系统
- [Freqtrade GitHub](https://github.com/freqtrade/freqtrade) · [Hummingbot 官网](https://hummingbot.org/) · [XEMM 跨所做市](https://hummingbot.org/strategies/v1-strategies/cross-exchange-market-making/)

### 本地化 LLM 自训练
- [FinGPT GitHub](https://github.com/ai4finance-foundation/fingpt) · [FinLoRA 金融微调基准](https://arxiv.org/html/2505.19819v1) · [LoRA/QLoRA 决策框架](https://blog.gopenai.com/choosing-the-right-technique-for-fine-tuning-llms-lora-qlora-or-dpo-18dab048c738) · [SFT/DPO/RLHF 指南](https://www.sundeepteki.org/advice/the-complete-guide-to-post-training-llms-how-sft-rlhf-dpo-and-grpo-shape-llm) · [Apple DPO 泛化警告](https://machinelearning.apple.com/research/reward-generalization)

### LLM 交易智能体
- [TradingAgents 论文](https://arxiv.org/abs/2412.20138) · [TradingAgents GitHub](https://github.com/tauricresearch/tradingagents) · [FinMem 论文](https://arxiv.org/abs/2311.13743) · [FinMem GitHub](https://github.com/pipiku915/finmem-llm-stocktrading) · [FinRL GitHub](https://github.com/AI4Finance-Foundation/FinRL)

### 本地模型选型（2026-06）
- [Qwen3 GitHub](https://github.com/qwenLM/qwen3) · [Qwen3.5/3.6 vLLM 部署](https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html) · [Qwen3 消费级硬件实测](https://www.reddit.com/r/LocalLLaMA/comments/1kdsp4z/) · [arXiv 消费级硬件基准](https://arxiv.org/pdf/2512.23029) · [开源 LLM 对比 2026](https://lushbinary.com/blog/qwen-3-6-vs-gemma-4-llama-4-glm-5-1-deepseek-v4-open-source-comparison/) · [DeepSeek V4 本地不可行讨论](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/discussions/12)

### 微调工具
- [Unsloth Qwen3 微调文档](https://unsloth.ai/docs/models/tutorials/qwen3-how-to-run-and-fine-tune) · [Unsloth MoE 加速](https://unsloth.ai/docs/basics/faster-moe) · [单卡 3090 微调讨论](https://github.com/unslothai/unsloth/discussions/3163)

### DRL 局限
- [DRL 加密货币综述](https://www.emergentmind.com/topics/deep-reinforcement-learning-drl-for-cryptocurrency-trading)

---

> **文档结束**
> 本文档基于 2026-06-24 的全面调研整理而成，所有技术选型和架构决策均有代码库实证或公开资料支撑。实施时建议按"实施里程碑"的 MVP → 微调 → 精调评估 三步走，最小风险逐步验证。
