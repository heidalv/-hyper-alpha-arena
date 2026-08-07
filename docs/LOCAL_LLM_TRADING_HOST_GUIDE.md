# 本地 LLM 交易机（本机）接入指南

> **适用机器**：运行 Hyper-Alpha-Arena 的交易机（Windows，无 GPU）
> **本机职责**：生成训练数据、配置本地模型接入、实现调参闭环接入、效果评估。
> **本机不做模型训练、不部署推理服务。** 推理和训练由 GPU 算力机负责。
> **配套文档**：GPU 机配置见 [`LOCAL_LLM_GPU_HOST_GUIDE.md`](./LOCAL_LLM_GPU_HOST_GUIDE.md)；整体设计与调研见 [`LOCAL_LLM_SELF_TRAINING_DESIGN.md`](./LOCAL_LLM_SELF_TRAINING_DESIGN.md)

---

## 目录

- [0. 职责边界（先读）](#0-职责边界先读)
- [1. 前提确认](#1-前提确认)
- [2. 接入 GPU 机推理服务（零代码改动）](#2-接入-gpu-机推理服务零代码改动)
- [3. 训练数据集生成](#3-训练数据集生成)
- [4. 调参闭环接入](#4-调参闭环接入)
- [5. 效果评估（影子 A/B）](#5-效果评估影子-ab)
- [6. 代码改动清单](#6-代码改动清单)
- [7. 验收测试](#7-验收测试)
- [8. 常见问题](#8-常见问题)

---

## 0. 职责边界（先读）

本机（交易机）只做四件事：

| 任务 | 说明 | 频率 |
|---|---|---|
| **配置接入** | 在数据库加一条本地 GPU 模型的 LLMConfiguration | 一次性 |
| **生成训练数据** | 读 `DecisionSnapshot` + 回测引擎，生成 SFT JSONL，发给 GPU 机 | 每周 |
| **调参闭环** | 调 GPU 机 API 拿参数建议，经 `RuntimeGovernor` 仲裁写入 | 每日/每周 |
| **效果评估** | 影子 A/B，对比本地模型 vs 现有规则的参数建议效果 | 持续 |

**数据流向：**

```
本机（交易机）                         GPU 机
─────────────                         ──────
交易产生 DecisionSnapshot
    ↓
dataset_builder 生成 JSONL ──发送──>  Unsloth 训练 → 新权重加载到推理服务
                                          ↑
gate_optimizer_service                  │
  调 http://GPU_IP:8000/v1/chat/completions
  拿到参数建议                          │
    ↓                                   │
runtime_governor.submit_intent(         │
  source="local_llm_optimizer")         │
    ↓
unified_gate 读取 runtime_tuning.json
    ↓
影响下一笔交易 → 新 DecisionSnapshot（闭环）
```

**不做的事：** 不跑 Unsloth/vLLM/Ollama，不做模型训练，不持有 GPU 权重。

---

## 1. 前提确认

### 1.1 GPU 机已就绪

从 GPU 机操作员处拿到两个信息（详见 GPU 主机指南）：

- **推理服务地址**：`http://<GPU机内网IP>:8000/v1`（vLLM）或 `http://<GPU机内网IP>:11434/v1`（Ollama）
- **API Key**：如 `local-secret-2026`

### 1.2 本机网络连通性验证

```powershell
# 在本机执行，验证能访问 GPU 机
curl http://<GPU机内网IP>:8000/v1/models
# 应返回 JSON，含 Qwen3-30B-A3B 模型名
```

若超时：① 确认两机同内网可互 ping；② GPU 机防火墙放行 8000（见 GPU 指南第 2.3 节）。

### 1.3 数据资产就绪

本方案依赖以下数据库表（已在 Hyper-Alpha-Arena 中存在）：

| 表 | 模型定义 | 用途 |
|---|---|---|
| `decision_snapshots` | `backend/database/models.py:2777` | 训练数据核心（输入+输出+结果） |
| `decision_retrospectives` | `models.py:445` | DPO 偏好信号（对错标签+教训） |
| `ai_decision_logs` | `models.py:394` | 备用 SFT 源（prompt+输出+pnl） |
| `strategy_memories` | `models.py:665` | 聚合教训文本 |

---

## 2. 接入 GPU 机推理服务（零代码改动）

系统的 LLM 调用已是 OpenAI 兼容架构，`base_url` 完全由数据库决定——**改一行配置即可接入，代码零改动**。

### 2.1 接入原理（为什么零改动）

- `call_llm_api_sync`（`llm_config_service.py:929`）走标准 `POST {base_url}/chat/completions`
- `base_url` 由 `LLMConfiguration.base_url` 字段决定（自由字符串）
- httpx 对 `http://` 地址默认不启用 SSL 校验，内网明文直接通（`llm_config_service.py:660`）
- `provider` 字段无枚举约束，填 `vllm`/`ollama`/`local` 即可

### 2.2 添加 LLMConfiguration

**方式 A：通过前端 LLM 配置页面**（推荐，若有管理界面）

在 LLM 配置页新增：
- 名称：`本地GPU-门控优化器`
- Provider：`vllm`（或 `ollama`）
- Model：`Qwen3-30B-A3B-Instruct`（或 GPU 机上的模型名）
- Base URL：`http://<GPU机内网IP>:8000/v1`
- API Key：`local-secret-2026`
- 是否默认：否（不覆盖云端主力 LLM）

**方式 B：直接 SQL**（针对 `alpha_arena` 库）

```sql
INSERT INTO llm_configurations
(name, provider, model, base_url, api_key, is_default, is_active, created_at, updated_at)
VALUES
('本地GPU-门控优化器', 'vllm', 'Qwen3-30B-A3B-Instruct',
 'http://192.168.1.100:8000/v1', 'local-secret-2026',
 'false', 'true', NOW(), NOW());
-- 记下返回的 id，例如 99，后续 gate_optimizer_service 用它
```

### 2.3 验证调用链

写个临时脚本调一次本地模型，确认整条链路通：

```python
# 临时验证脚本（跑完可删）
from backend.services.llm_config_service import get_llm_config, call_llm_api_sync

config = get_llm_config(99)  # 换成你的 LLMConfiguration id
resp = call_llm_api_sync(
    config=config,
    messages=[
        {"role": "system", "content": "你是门控参数优化器，输出JSON。"},
        {"role": "user", "content": 'trending市场，7天胜率42%，手续费占比35%。当前min_risk_reward=1.8。建议参数？输出{"min_risk_reward":...}'},
    ],
    temperature=0.2,
    max_tokens=500,
    response_format={"type": "json_object"},
)
print(resp)
```

应返回含 `min_risk_reward` 等字段的 JSON。若失败，检查第 1.2 节连通性。

---

## 3. 训练数据集生成

### 3.1 新建模块

**新建** `backend/services/local_llm/dataset_builder.py`

职责：读 `DecisionSnapshot` → 聚合统计 → 用回测引擎算最优参数标签 → 输出 SFT JSONL。

### 3.2 数据样本映射

```
DecisionSnapshot (market_snapshot_json + regime + pnl_pct)
  → 输入: "在 {regime} 市场环境下，过去7天统计：胜率X%，平均盈Y%，平均亏Z%，
          手续费占比W%，样本N笔。当前门控参数为 {当前gates}。"
  → 目标输出: {"min_risk_reward": 2.0, "scalp_min_confidence": 70,
              "max_daily_trades": 7, "reasoning": "..."}
```

### 3.3 关键设计：标签从哪来

- 按 `(regime, 时间窗口)` 聚合 `DecisionSnapshot`，统计胜率/平均盈亏/手续费占比
- 窗口末端的**最优参数标签**用本机现有的回测引擎（`backtest_engine/`）做网格搜索确定：哪组参数在该窗口回测 Sharpe 最高，就用它当标签
- 这样生成 `(市场状态特征, 历史表现) → 最优参数` 映射——模型学"什么状态该用什么参数"

### 3.4 复用现有代码

| 需要 | 复用 |
|---|---|
| 读决策历史 | `DecisionSnapshot`（`models.py:2777`）、`AIDecisionLog`（`models.py:394`） |
| 参数回测算标签 | `backend/services/backtest_engine/` + `walk_forward_validator.py` |
| regime 判定 | `backend/services/decision_core/regime_agent.py` |
| 现有调参规则参考 | `decision_feedback_service.py:347`（4 条硬编码规则的逻辑可借鉴） |
| 交易历史过滤 | `TradeMemoryRecord.signal_source="llm"`（`models.py:2587`） |

### 3.5 输出与发送

```python
# dataset_builder.py 核心输出
def build_dataset(days: int = 90, out_path: str = "training/data/train.jsonl"):
    # 1. 查近 days 天 DecisionSnapshot，按 regime+7天窗聚合
    # 2. 每个窗口：回测网格搜索找最优参数 → 当标签
    # 3. 写 JSONL（messages 格式，见 GPU 指南 5.1）
    ...
```

**云端辅助（可选）：** 约 10% 样本用云端大模型（如 DeepSeek）润色 reasoning 文本，提升数据质量。

**发送给 GPU 机：** 生成后通过内网共享/拷贝 `train.jsonl` 到 GPU 机的 `training/data/` 目录（见 GPU 指南 5.1）。

---

## 4. 调参闭环接入

### 4.1 新建模块

**新建** `backend/services/local_llm/gate_optimizer_service.py`

这是唯一触碰运行时的文件，功能：
1. 低频触发（每日收盘后/每周，由定时器调用）
2. 收集近 7/14/30 天 `DecisionSnapshot` 聚合统计 + 当前 `runtime_tuning.json` 参数
3. 调用本地模型（走 `call_llm_api_sync`），拿建议参数 + reasoning
4. 对每个建议参数调 `runtime_governor.submit_intent()`

### 4.2 核心代码框架

```python
# gate_optimizer_service.py
from backend.services import runtime_governor as gov
from backend.services.llm_config_service import get_llm_config, call_llm_api_sync

LOCAL_LLM_CONFIG_ID = 99  # 第 2.2 节创建的 LLMConfiguration id

def run_gate_optimization():
    # 1. 聚合近 7 天统计（复用 dataset_builder 的聚合逻辑）
    stats = _aggregate_recent(days=7)

    # 2. 调本地模型
    config = get_llm_config(LOCAL_LLM_CONFIG_ID)
    resp = call_llm_api_sync(
        config=config,
        messages=_build_prompt(stats, current_gates()),
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    suggestions = _parse_suggestions(resp)  # {param: (value, confidence, reason)}

    # 3. 经 Governor 仲裁提交（模型不会粗暴覆盖）
    for key, (value, conf, reason) in suggestions.items():
        gov.submit_intent(
            key=key,
            value=value,
            source="local_llm_optimizer",   # 新来源
            confidence=conf,
            reason=reason,
            ttl_sec=36 * 3600,              # 36 小时，与 decision_feedback 同级
        )
```

### 4.3 注册新 source 优先级

**改** `backend/services/runtime_governor.py`：

```python
SOURCE_PRIORITY = {
    "manual": 100,
    "opencode": 80,
    "decision_feedback": 60,
    "local_llm_optimizer": 55,   # ← 新增：略低于规则反馈，高于 evolution
    "evolution_gc": 50,
    "maturity": 40,
    "default": 30,
}
DEFAULT_TTL_SEC["local_llm_optimizer"] = 36 * 3600   # ← 新增
```

优先级 55 的含义：manual/opencode 可随时覆盖它，decision_feedback 的规则也优先于它；它高于 evolution。**多重保护，模型出错也不会失控。**

### 4.4 接定时任务

**改** 现有调度器（如 `evolution_scheduler.py` 或类似的 APScheduler 注册处），加一个低频任务：

```python
# 每天收盘后（如 UTC 00:00）触发一次
scheduler.add_job(run_gate_optimization, 'cron', hour=0, minute=30)
```

---

## 5. 效果评估（影子 A/B）

### 5.1 目标

让本地模型和现有规则**同时**对同一窗口出参数建议，对比谁的参数在随后窗口表现更好，用数据决定是否提升 local_llm_optimizer 的优先级。

### 5.2 新建日志表

**改** `backend/database/models.py`，加：

```python
class GateOptimizerLog(Base):
    __tablename__ = "gate_optimizer_logs"
    id = Column(Integer, primary_key=True)
    run_at = Column(DateTime, default=datetime.utcnow)
    window_start = Column(DateTime)
    window_end = Column(DateTime)
    regime = Column(String)
    market_stats = Column(JSON)         # 输入统计
    model_output = Column(JSON)         # 本地模型建议
    rule_output = Column(JSON)          # 现有规则建议（对照）
    applied_source = Column(String)     # 最终生效的是哪个来源
    # 事后回填：
    realized_sharpe_model = Column(Float, nullable=True)
    realized_sharpe_rule = Column(Float, nullable=True)
    winner = Column(String, nullable=True)   # "model" / "rule" / "tie"
```

加 Alembic migration 建表。

### 5.3 对比流程

1. 每次运行时，本地模型和 `decision_feedback` 规则都各自出建议，都记到 `gate_optimizer_logs`
2. Governor 按优先级仲裁（通常规则 60 > 模型 55，规则生效，模型影子记录）
3. 一段时间后（如 2 周），回填两个建议在随后窗口的实际 Sharpe
4. 统计模型胜率，若稳定优于规则，再考虑提高 source 优先级

---

## 6. 代码改动清单

### 新建

| 文件 | 用途 |
|---|---|
| `backend/services/local_llm/__init__.py` | 模块初始化 |
| `backend/services/local_llm/dataset_builder.py` | 训练数据生成（第 3 节） |
| `backend/services/local_llm/gate_optimizer_service.py` | 调参接入（第 4 节） |

### 小改

| 文件 | 改动 |
|---|---|
| `backend/services/runtime_governor.py` | `SOURCE_PRIORITY` + `DEFAULT_TTL_SEC` 加 `local_llm_optimizer` |
| `backend/database/models.py` | 加 `GateOptimizerLog` 表 |
| 调度器（如 `evolution_scheduler.py`） | 加每日触发 `run_gate_optimization` |
| Alembic migration | 建 `gate_optimizer_logs` 表 |
| 数据库 `llm_configurations` | 加一行 GPU 机配置（第 2.2 节） |

### 完全不改

| 文件 | 理由 |
|---|---|
| `backend/services/ai_decision_service.py` | 实时决策链零影响 |
| `backend/services/decision_core/unified_gate.py` | 只读 runtime_tuning.json |
| 交易执行链路 | 完全不触碰 |

---

## 7. 验收测试

按顺序验证，每步通过再做下一步：

1. **连通性**：第 1.2 节 `curl http://GPU_IP:8000/v1/models` 返回模型列表
2. **LLM 配置**：第 2.3 节临时脚本返回合理 JSON 参数建议
3. **数据集**：`dataset_builder.py` 生成 ≥1000 条 JSONL，抽查格式正确
4. **Governor 注册**：手动 `submit_intent(source="local_llm_optimizer", ...)` 能写入并按优先级仲裁
5. **闭环触发**：定时任务跑通，`runtime_tuning.json` 出现 local_llm_optimizer 的参数
6. **影子日志**：`gate_optimizer_logs` 表有记录，model_output 和 rule_output 都非空
7. **故障隔离**：关掉 GPU 机，验证交易不受影响、Governor 的 TTL 让意图过期回退

---

## 8. 常见问题

| 问题 | 解决 |
|---|---|
| 调用 GPU 机超时 | 检查内网连通、防火墙；调用失败会自动跳过本轮，不影响交易 |
| 本地模型返回的参数不合理 | Governor 仲裁 + unified_gate 硬边界截断，不会失控 |
| 训练数据太少 | 先用 prompt-only（不微调）跑 MVP，积累数据后再训 |
| regime 统计不准 | 复用 `decision_core/regime_agent.py` 的判定逻辑 |
| reasoning 文本质量差 | 用云端大模型给 10% 样本润色（云端辅助） |
| 不确定何时提升优先级 | 先跑 2 周影子 A/B，数据说话，胜率稳定再升 55→60 |

---

## 速查：本机需要向 GPU 机提供的

实施时，本机要给 GPU 机提供：

1. **训练数据 JSONL**：由 `dataset_builder.py` 生成，放到约定的 `training/data/` 位置
2. **数据格式约定**：见 GPU 主机指南 5.1 节的 messages JSONL 格式

> 推理服务和训练流程由 GPU 机负责，本机只管"喂数据 + 调 API + 接 Governor"。

---

> **配套**：GPU 机如何部署推理与训练 → [`LOCAL_LLM_GPU_HOST_GUIDE.md`](./LOCAL_LLM_GPU_HOST_GUIDE.md)
