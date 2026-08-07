# Hyper-Alpha-Arena V4: 多智能体 QAA 架构设计

> 版本: Draft 1.7 | 日期: 2026-05-22 | 状态: V4 实施完成 — Phase 0-3 + Prompt P0-P3 全部交付
> v1.4: 提示词工程分析 — 现有 Prompt 审计、竞品最佳实践、差距分析、打磨规划
> v1.5: Phase 0 (超时修复) + Phase 1A-1E (QAA 框架) 实施完成
> v1.6: Phase 2 (Agent 深度迁移) + Prompt P0-P3 (Schema/Compressor/XML/Few-shot/注入防护) 实施完成
> v1.7: Phase 3 (三层状态架构 + 反思闭环 + LLM Fallback + 漂移监控 + 健康监控) 实施完成

---

## 1. 当前架构诊断

### 1.1 规模统计
- 服务文件: 120+ 个 (.py) | services/ 子包: 17 个 | 总代码行: ~124,000 行
- 核心编排器: full_auto_trading_service.py (9,609 行)
- AI 决策服务: ai_decision_service.py (4,414 行)
- 数据库模型: models.py (2,900 行, 87 张表)
- 现有消息系统: 4 套并存 (EventBus / LearningBus / MarketEventDispatcher / WSBroadcastHub)

### 1.2 当前组件真实分类 (v1.1 代码审查修正)

原设计将 17 个组件统称为"智能体"，但代码分析表明它们的性质差异极大：

**A. 真正的决策智能体 (5个)** — 有独立决策逻辑, 需注册 Agent Card

| 组件 | 文件 | 行数 | 决策能力 |
|------|------|------|----------|
| MasterController | trading_analysts.py:1382 | 1242 | 综合六路分析师 + LLM 最终决策 + DebateLayer 辩论 |
| MultiTimeframeOrchestrator | multi_timeframe_orchestrator.py | 1277 | 长/中/短三周期独立分析 + 情报注入 + 三层协调 |
| IntelligenceSignalEngine | intelligence_signal_engine.py | 382 | 多维信号计算 → 输出 TradingDirectionSignal |
| TradePlannerAgent | trade_planner_agent.py | 887 | 动态 TP/SL (当前未启用, TRADE_PLANNER_ENABLED=false) |
| GeneticOptimizer | genetic_optimizer.py | 586 | 遗传算法优化策略参数 (离线, 不在主循环) |

**B. 规则化计算工具 (6个)** — 纯计算无决策, 注册为 Tool 而非 Agent

| 组件 | 职责 | 耗时 |
|------|------|------|
| PositionAnalyst | 持仓健康评估 (PnL/方向/止损距离) | <1ms |
| MarketAnalyst | 行情趋势/波动率/支撑阻力 | <1ms |
| IntelAnalyst | 新闻/鲸鱼/衍生品/恐贪分析 | <5ms |
| RiskAnalyst | 账户风险/保证金/回撤 | <1ms |
| StrategyAnalyst | 策略有效性/胜率/连亏 | <1ms |
| KlineAnalyst | K线形态识别 (LLM 增强) | <1s 或 <30s(LLM) |

**C. 门控/日志/聚合组件 (6个)** — 规则检查或数据聚合, 无决策逻辑

| 组件 | 实际性质 |
|------|----------|
| DecisionArbiter | 纯日志工具 (不拦截不改变行为, 默认关闭) |
| DecisionConsistencyGate | 规则门控 (30min 内方向翻转检测 → 强制 HOLD) |
| StrategyCoordinator | 数据聚合层 (串联K线/指标/情报, 非决策者) |
| EntryConfidenceGate | 纯函数 (根据 tier/regime 返回开仓门槛) |
| DeterministicRiskGate | 5条硬风控规则 (无状态) |
| FullAutoTradingService | 主编排器 (串联所有组件的调度中心, 9609行) |

> **设计结论**: 只有 A 类 5 个组件需要 Agent Card 注册。
> B 类作为 Tool 由 Agent 内部直接调用（走 MessageBus 反而增加延迟）。
> C 类作为 Guard/Filter 在 MessageBus 层或调度器中执行。

### 1.3 致命架构问题
健康检查主线程全部串行: _bootstrap_market_summary(120s) -> V3因子(60s) -> 分析师 -> AI决策 -> 执行
-> 任一步骤超时 -> 整个循环卡死
-> 实际观察: 1799次超时 / 0次成功开始

核心问题:
1. 固定流程: 所有步骤按固定顺序串行
2. 无超时熔断: 一个步骤卡死全部卡死
3. 无动态路由: 无法根据情况跳过/替换/并行
4. 数据库瓶颈: 87张表在单一SQLite文件, 15+后台线程竞争写入 (v1.1 新增)

### 1.4 现有消息系统盘点 (v1.1 新增)

项目已有 4 套并存的消息机制, V4 架构必须与之协调:

| 系统 | 实现 | 事件类型 | 不足 |
|------|------|----------|------|
| EventBus | asyncio.PriorityQueue | 20+ 种 (价格/风险/信号/决策/订单) | 无 request/response, 无超时, 无熔断 |
| LearningBus | sync threading.Lock + 计数器 | 交易结果 → 学习链路触发 | 纯同步, 无并行 |
| MarketEventDispatcher | threading.Lock + handler列表 | 价格更新广播 | 功能最简 |
| WSBroadcastHub | topic + WebSocket | DRL/Kelly/进化/状态 | 仅推送, 无请求 |

> **设计决策**: 不新建第 5 套消息系统。基于现有 EventBus 扩展 Agent 调用语义 (request/response + 超时 + 熔断), 见 3.6 节。

---

## 2. 协议研究与 QAA 设计 (v1.2 深度扩展)

### 2.1 行业协议全景 (2025-2026)

| 协议 | 创建者 | 定位 | 通信模型 | 传输层 | 成熟度 |
|------|--------|------|----------|--------|--------|
| **MCP** | Anthropic | 模型<->工具 | Client-Server | stdio / Streamable HTTP | 生产可用 (v1.x) |
| **A2A** | Google → Linux Foundation | Agent<->Agent | Peer-to-Peer | HTTP + JSON-RPC / gRPC | 早期 (v1.0.3) |
| **ACP** | IBM (并入 A2A) | 多模态通信 | Brokered C-S | REST + SSE | 规范阶段 |
| **ANP** | 开源社区 | 去中心化信任 | P2P (DID) | W3C DID | 实验性 |

**层次关系**:

```
┌────────────────────────────────────────────────────────────┐
│  A2A ── Agent 间协作层 (发现、任务委派、多Agent工作流)       │
├────────────────────────────────────────────────────────────┤
│  MCP ── Agent-to-Tool 连接层 (工具调用、资源访问、API连接)   │
├────────────────────────────────────────────────────────────┤
│  ADK ── Agent 开发层 (构建框架、组件库、脚手架)              │
└────────────────────────────────────────────────────────────┘
```

### 2.2 MCP 技术解析 — 我们吸收什么

**MCP 核心架构**: Host-Client-Server 三层, JSON-RPC 2.0, 双向通信。

**值得吸收的设计**:

| MCP 概念 | QAA 借鉴 | 说明 |
|----------|----------|------|
| `tools/list` + `tools/call` | ToolDef 注册 + 调用 | JSON Schema 描述工具输入输出, 支持分页发现 |
| Tool `annotations` | `readOnlyHint`, `destructiveHint`, `idempotentHint` | 金融系统必须区分只读/破坏性/幂等操作 |
| `inputSchema` (JSON Schema) | Pydantic 模型自动导出 Schema | 确保类型安全 |
| `resources/subscribe` | Agent 输出订阅 | 资源变更推送模式 |
| Lifecycle: `initialize → initialized → operation → shutdown` | Agent 生命周期管理 | 能力协商 + 版本协商 |
| `sampling/createMessage` | Agent 可请求 LLM 辅助 | Server 向 Client 请求 LLM 采样 |

**不采用的部分**:

| MCP 概念 | 原因 |
|----------|------|
| stdio 传输 | 单进程内通信不需要子进程管道 |
| Streamable HTTP 传输 | Agent 在同一 Python 进程内, 不需要网络序列化 |
| Host-Client-Server 三层 | QAA 是扁平的 Agent 网, 不是嵌套关系 |
| OAuth 2.1 认证 | 内部通信不需要 OAuth |

### 2.3 A2A 技术解析 — 我们吸收什么

**A2A 核心架构**: HTTP + JSON-RPC 2.0, Agent Card 发现, Task 状态机, SSE 流式。

**值得吸收的设计**:

| A2A 概念 | QAA 借鉴 | 说明 |
|----------|----------|------|
| **Agent Card** (`.well-known/agent-card.json`) | `AgentCard` Pydantic 模型 | Agent 能力声明: name, skills, capabilities, authentication |
| **Task 状态机** (`submitted → working → completed/failed/canceled`) | `AgentTask` 状态机 | 7 种状态, `input-required` 支持人机交互 |
| **Part 类型体系** (TextPart / DataPart / FilePart) | 消息内容多态 | 结构化数据 + 文本 + 文件的统一表达 |
| **Skill 定义** (id, name, description, tags, examples) | `Capability` 定义 | Agent 的可调用能力声明 |
| **SSE 流式** (`tasks/sendSubscribe`) | 流式 Agent 输出 | 长时任务的实时进度推送 |
| **sessionId 关联** | 多 Task 会话关联 | 同一会话的多个任务可串联 |

**不采用的部分**:

| A2A 概念 | 原因 |
|----------|------|
| HTTP 传输 | Agent 在同一进程, 直接函数调用, 不走网络 |
| Agent Card URL 发现 | 内部注册表, 不需要 HTTP 端点发现 |
| Push Notification (webhook) | 进程内用 asyncio callback 即可 |
| gRPC 传输 | 单机不需要 RPC |

### 2.4 行业最佳实践参考

**TradingAgents (71k stars, LangGraph)** — 金融多 Agent 标杆:

| 实践 | QAA 应用 |
|------|----------|
| 五阶段流水线 (Analyst → Researcher → Trader → Risk → Fund Manager) | 规则路由可配置阶段 |
| Bull/Bear 辩论 (DebateLayer) | 已有实现, 保留 |
| 共享状态 TypedDict + Checkpoint (SQLite) | Agent 状态持久化参考 |
| 双模型策略 (quick_think / deep_think) | Agent Card 中声明 LLM 级别 |
| Decision Logs (Markdown, 始终开启) | 审计追踪实现参考 |

**ai-hedge-fund (58k stars, FastAPI)**:

| 实践 | QAA 应用 |
|------|----------|
| 19 个角色 Agent 独立分析 | Agent 独立自治原则 |
| Pydantic 结构化信号 (signal + confidence + reasoning) | Agent 响应格式规范 |
| 层级漏斗聚合 (全部信号 → Risk Manager → Portfolio Manager) | Guard 链设计参考 |

**AWS 金融服务 Agent 最佳实践**:

| 实践 | QAA 应用 |
|------|----------|
| 延迟关键路径走同步, 研究路径走异步 | 混合通信模式 |
| 风控硬规则独立于 LLM | DeterministicRiskGate |
| 审计追踪 (输入快照 + 推理链 + 信号来源) | AgentTask 历史记录 |

### 2.5 QAA 设计决策

**为什么不直接用 MCP / A2A / LangGraph?**

| 维度 | MCP | A2A | LangGraph | QAA (自建) |
|------|-----|-----|-----------|-----------|
| Agent 间通信 | 不支持 | 支持 | 支持 | 支持 |
| 工具注册 | 优秀 | 无 | 一般 | 吸收 MCP |
| 延迟开销 | HTTP 序列化 | HTTP 序列化 | 图遍历开销 | 函数调用 (<1us) |
| 现有代码兼容 | 需全部重写 | 需全部重写 | 需全部重写 | Adapter 模式渐进迁移 |
| 风控硬门控 | 需适配 | 需适配 | 需适配 | 原生设计 |
| 审计追踪 | 需外挂 | 部分支持 | Checkpoint | 原生设计 |
| 数据库瓶颈感知 | 无 | 无 | 无 | 原生设计 |

**QAA 的定位**: 吸收 MCP 的 Tool 契约 + A2A 的 Agent Card + TradingAgents 的辩论审计,
在 **同一 Python 进程内** 用函数调用 + EventBus 实现零网络开销的多 Agent 协作。

> 未来可选: 对外暴露 MCP Server (给 AI IDE 调试), 或 A2A 端点 (给远程 Agent 协作)。
> 但核心交易循环保持进程内自建。

---

## 3. V4 多智能体架构设计

### 3.1 核心原则
1. **规则路由优先, LLM 辅助** — 90% tick 由规则引擎确定调用链 (毫秒级), 异常场景才 fallback 到 LLM 规划 (v1.1 修正)
2. 子Agent独立自治 — 各自责任务/记忆/状态
3. 协议通信解耦 — 基于现有 EventBus 扩展, 不新建消息系统
4. **每步超时熔断** — 每个 Agent/Tool 调用都有独立超时 + 明确 fallback 值 (v1.1 新增)
5. 兼容现有代码 — 不改内部逻辑, 只改调用方式

### 3.2 架构总览 (v1.3 五阶段决策流水线)

借鉴 TradingAgents 五阶段流水线 + ai-hedge-fund 层级漏斗聚合,

```
┌─────────────────────────────────────────────────────────────────────┐
│                 Master Coordinator (调度中心)                        │
│  RuleRouter (规则路由, 毫秒级) + LLM Planner (fallback, 30-60s)     │
│  Tick 循环: 感知 → 路由 → 委派 → 综合 → 门控 → 执行 → 反思          │
└────────────────────────────┬────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│              Message Bus (基于 EventBus 扩展)                        │
│  request/response + 超时/熔断/重试 + 并行调度                        │
│  Guards: ConsistencyGate / DeterministicRiskGate / EntryConfidence  │
└───┬──────────┬──────────┬──────────┬──────────┬──────────┬─────────┘
    │          │          │          │          │          │
┌───▼───┐ ┌───▼───┐ ┌────▼───┐ ┌───▼────┐ ┌───▼────┐ ┌───▼────┐
│ 阶段1 │ │ 阶段2 │ │ 阶段3  │ │ 阶段4  │ │ 阶段5  │ │ 离线   │
│ 感知  │ │ 分析  │ │ 辩论   │ │ 决策   │ │ 执行   │ │        │
│       │ │       │ │        │ │        │ │        │ │        │
│ 市场  │ │ 因子  │ │ 编排器 │ │ Master │ │ 交易   │ │ 遗传   │
│ 数据  │ │ 引擎  │ │  (MT)  │ │Control │ │ 执行   │ │ 优化   │
│ Agent │ │ Agent │ │ Agent  │ │ Agent  │ │ Agent  │ │ Agent  │
│(只读) │ │(计算) │ │(协调)  │ │(LLM)   │ │(写)    │ │(离线)  │
└───────┘ └───────┘ └────────┘ └────────┘ └────────┘ └────────┘
    │          │          │          │          │
┌───▼──────────▼──────────▼──────────▼──────────▼────────────────────┐
│                     Tools (直接调用, 不走 MessageBus)                 │
│  6x Analyst | StrategyCoordinator | IntelSignal | FactorCalculator  │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────────┐
│                    三层状态架构 (v1.3 新增)                           │
│  Layer A (确定性): 持仓/余额/风控限额 — LLM 只读, 仅由执行层写入     │
│  Layer B (生成式): LLM 上下文窗口 — 瞬态, 受驱逐影响                 │
│  Layer C (检索增强): ChromaDB 情景记忆 — 带时间戳, 结果禁令保护      │
├─────────────────────────────────────────────────────────────────────┤
│                    数据库分层                                        │
│  alpha_arena.db (交易核心) | alpha_market.db (市场)                  │
│  alpha_analytics.db (审计日志) | alpha_snapshots.db (快照)           │
└─────────────────────────────────────────────────────────────────────┘
```

**五阶段映射到现有组件**:

| 阶段 | 竞品来源 | 对应 Agent | 耗时预算 | 产出 |
|------|---------|-----------|---------|------|
| 1.感知 | TradingAgents 数据获取 | MarketDataAgent | <10s | MarketSnapshot |
| 2.分析 | TradingAgents Analyst Team (并行) | FactorEngine + 6x Analyst (并行) | <20s | 因子/技术/情报报告 |
| 3.辩论 | TradingAgents Bull/Bear Researcher | MTOrchestrator + IntelligenceSignal | <15s | 多周期协调 + 信号 |
| 4.决策 | ai-hedge-fund PM 裁决 | MasterController (LLM) | <30s | 交易决策 (BUY/SELL/HOLD) |
| 5.执行 | 两者共有 | TradeExecution + RiskControl Gate | <5s | 订单/持仓变更 |
| 反思 | TradingAgents 反思机制 | LearningBus (异步, 不在主循环) | 异步 | 经验/策略调整 |

### 3.3 Agent Card 定义 (v1.3 增加双模型策略)

每个决策智能体注册能力卡片, **新增 LLM 级别声明** (借鉴 TradingAgents 双模型策略):

```python
class LLMLevel(str, Enum):
    """LLM 调用级别 (借鉴 TradingAgents quick_think / deep_think)"""
    NONE = "none"           # 纯规则, 无 LLM (如 RiskControlAgent)
    QUICK = "quick"         # 快速推理, 低延迟 (<5s): 分类、打分、简单判断
    DEEP = "deep"           # 深度推理, 高质量 (<60s): 综合决策、辩论、策略分析

class AgentCard(BaseModel):
    agent_id: str
    display_name: str
    capabilities: list[Capability]
    tools: list[ToolDef]

    # --- v1.3 新增: 双模型策略 ---
    llm_level: LLMLevel = LLMLevel.NONE
    # quick: 轻量模型 (DeepSeek-V3, Gemini Flash), temperature=0
    # deep: 重量模型 (Claude Sonnet, GPT-4o), temperature=0 + seed
    # none: 纯规则引擎, 不调 LLM, 延迟 <1ms

    # --- 超时熔断策略 ---
    max_timeout_sec: float
    timeout_strategy: Literal["skip", "cached", "hold", "retry"]
    fallback_value: Any
    max_retries: int = 0
    circuit_breaker: CircuitBreakerConfig
```

**各 Agent 双模型策略**:

| Agent | llm_level | 模型选择 | 原因 |
|-------|-----------|---------|------|
| MarketDataAgent | NONE | - | 纯数据获取, 无 LLM |
| FactorEngineAgent | QUICK | DeepSeek-V3 | 因子计算 + 体制分类, 快速推理 |
| MTOrchestratorAgent | QUICK | DeepSeek-V3 | 多周期协调, 需要快速判断 |
| IntelligenceSignalEngine | QUICK | DeepSeek-V3 | 信号计算, 快速推理 |
| MasterController | DEEP | Claude/GPT-4o | 最终决策, 需要深度推理 + 辩论 |
| RiskControlAgent | NONE | - | 纯硬规则, 无 LLM (确定性保障) |
| TradeExecutionAgent | NONE | - | 订单执行, 无 LLM |

### 3.4 Master Coordinator 工作流程 (v1.3 决策委员会模式)

借鉴 TradingAgents 五阶段 + ai-hedge-fund 层级漏斗, 设计六步决策循环:

循环(60-90s, 总延迟预算 ≤90s):
  1. **[感知]** MarketDataAgent 获取价格/K线 (超时 10s, cached fallback)
  2. **[路由]** RuleRouter 规则引擎确定调用计划 (毫秒级):
     - 市场平稳 → 仅基础分析 + 风控
     - 波动率异常 → 深度因子 + MT编排器 + 情报信号 + 风控
     - 有重大新闻 → 情报信号 + 快速因子 + 辩论 + 风控
     - 规则无法判断 → **fallback: LLM 规划调用链** (额外 30-60s)
  3. **[分析+辩论]** 并行委派 + 结构化辩论 (借鉴 TradingAgents Bull/Bear):
     - 因子引擎 + 6x Analyst 并行计算 (各 <1ms)
     - MTOrchestrator 多周期协调 (QUICK LLM)
     - IntelligenceSignal 情报注入
     - **DebateLayer**: 从分析师报告中提取多空论点, 结构化辩论
  4. **[决策]** MasterController (DEEP LLM) 综合决策:
     - 接收所有信号 (借鉴 ai-hedge-fund Pydantic 结构化信号)
     - 每个信号: `signal: bullish|bearish|neutral + confidence: 0-1 + reasoning: str`
     - LLM 综合所有信号 + 辩论结果, 输出最终决策
  5. **[门控]** Guard 链串行执行 (借鉴 TradingAgents Risk Manager 层级):
     - ConsistencyGate: 30min 内方向翻转检测
     - DeterministicRiskGate: 5条硬风控规则 (确定性, 非 LLM)
     - Layer A 状态验证: 持仓/余额一致性检查
  6. **[执行+反思]**
     - TradeExecutionAgent 下单 (带幂等性)
     - 异步: LearningBus 反馈闭环 (借鉴 TradingAgents 反思机制)

**v1.3 关键升级**:
- 分析师从"串行计算"升级为"并行 + 结构化辩论"
- 信号格式统一为 Pydantic 结构化 (signal + confidence + reasoning)
- Guard 链增加 Layer A 确定性状态验证 (借鉴学术论文三层状态架构)

### 3.5 规则路由引擎 (v1.1 替代原"LLM 路由")

```python
class RuleRouter:
    """基于市场状态的规则路由, 替代每 tick LLM 规划"""

    def route(self, market_snapshot: MarketSnapshot) -> list[AgentCall]:
        calls = []

        # 必须调用 (priority=0)
        calls.append(AgentCall("market_data", "get_snapshot", timeout=10))
        calls.append(AgentCall("risk_control", "check", timeout=5))

        # 条件调用 (priority=1)
        if market_snapshot.volatility_regime in ("HIGH", "EXTREME"):
            calls.append(AgentCall("factor_engine", "compute_full", timeout=20))
            calls.append(AgentCall("mt_orchestrator", "evaluate", timeout=30))

        if market_snapshot.has_major_news:
            calls.append(AgentCall("intelligence", "get_signals", timeout=10))

        # 可选调用 (priority=2, 时间允许时)
        if market_snapshot.volatility_regime == "NORMAL":
            calls.append(AgentCall("factor_engine", "compute_basic", timeout=15))

        return calls
```

规则路由的优势:
- 延迟: <1ms (vs LLM 规划 30-60s)
- 成本: 0 (vs LLM token 费用)
- 确定性: 规则可审计可回测 (vs LLM 输出不确定)
- 可扩展: 新增规则不影响现有规则

### 3.6 MessageBus 设计 (v1.1: 基于现有 EventBus 扩展)

**不新建第 5 套消息系统**, 而是给现有 EventBus 添加 Agent 调用语义:

```python
# 现有 EventBus (event_bus.py) 已具备:
# - asyncio.PriorityQueue 发布订阅
# - 20+ 事件类型
# - 通配符订阅、背压保护
# 缺失: request/response、超时、熔断、并行调度

# 扩展方案:
class EventBus:
    # ... 现有代码不变 ...

    async def call_agent(self, agent_id: str, action: str,
                         payload: dict, timeout_ms: float,
                         priority: int = 0) -> AgentResponse:
        """新增: 同步请求-响应模式, 带超时和熔断"""
        ...

    async def call_agents_parallel(self, calls: list[AgentCall],
                                   global_timeout_ms: float) -> list[AgentResponse]:
        """新增: 并行调用多个 Agent, 各自独立超时"""
        ...
```

### 3.7 数据库分层策略 (v1.1 新增, 原文档完全缺失)

当前 87 张表在单一 `alpha_arena.db` (SQLite WAL), 15+ 后台线程竞争写入。
多 Agent 并行执行会增加写入压力, 必须分层:

| 层 | 数据库 | 表 | 写入频率 | 策略 |
|----|--------|-----|----------|------|
| 交易核心 | alpha_arena.db | Order, Position, Trade, Account, AIStrategy | 每秒数次 | WAL, busy_timeout=120s |
| 市场数据 | alpha_market.db | CryptoKline, CryptoPrice, PriceSample | 每秒数次 | WAL, 独立连接池 |
| 分析日志 | alpha_analytics.db | AIDecisionLog, DecisionSnapshot, FactorQualityReport | 每分钟数次 | WAL, 异步写入队列 |
| 快照 | alpha_snapshots.db | (已有独立文件) | 每 5 分钟 | 现有 |
| 短期记忆 | 内存字典 + SQLite | 最近决策缓存 | 每秒 | 纯内存, 无 DB |

```python
# 写入队列: 非关键写入异步化
class AsyncLogWriter:
    """分析日志异步写入, 不阻塞交易主循环"""
    def __init__(self):
        self._queue: Queue = Queue(maxsize=1000)
        self._thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._thread.start()

    def enqueue(self, db_session_factory, record):
        self._queue.put((db_session_factory, record))

    def _flush_loop(self):
        while True:
            batch = self._drain_batch(max_size=50, timeout_s=5)
            # 单次事务写入 batch
            ...
```

### 3.8 竞品借鉴: 关键设计模式 (v1.3 新增)

以下是从行业竞品中提炼的核心设计模式, 逐一映射到 QAA 的实现:

#### 3.8.1 结构化信号传递 (借鉴 ai-hedge-fund)

所有 Agent 的输出统一为 Pydantic 结构化信号, 杜绝"自然语言模糊传递":

```python
class AgentSignal(BaseModel):
    """统一信号格式 — 所有 Agent 必须输出此格式"""
    agent_id: str
    symbol: str
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str                              # 人类可读的决策理由
    timeframe: str | None = None                # "1h", "4h", "1d"
    source_data_hash: str | None = None         # 输入数据哈希 (审计)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
```

**已有对应**: trading_analysts.py 的 `AnalystReport` 已有类似结构, 需统一为 `AgentSignal`。

#### 3.8.2 结构化辩论机制 (借鉴 TradingAgents)

当前 DebateLayer (trading_analysts.py:1277-1381) 已实现多空辩论, 保留并增强:

```
DebateLayer 工作流 (保留现有实现):
  1. 从 6 个 AnalystReport 中提取 Bull 论点 (支撑上涨的指标)
  2. 从 6 个 AnalystReport 中提取 Bear 论点 (支撑下跌的指标)
  3. 构建辩论 prompt 注入 MasterController 的 LLM 调用
  4. LLM 综合辩论结果做最终裁决
```

**TradingAgents 的关键发现**: 辩论模式比投票模式产生更优决策
(Sharpe Ratio 在 AAPL 上从 1.64 提升到 8.21)。
这验证了当前 DebateLayer 的设计方向是正确的。

#### 3.8.3 双模型策略 (借鉴 TradingAgents)

```python
# 借鉴 TradingAgents 的 quick_think_llm / deep_think_llm
LLM_CONFIG = {
    "quick": {
        "model": "deepseek-chat",     # 或 gemini-2.0-flash
        "temperature": 0,
        "max_tokens": 1024,
        "timeout": 10,                 # 严格超时
        "cost_per_1k_tokens": 0.001,   # 极低成本
    },
    "deep": {
        "model": "claude-sonnet-4",    # 或 gpt-4o
        "temperature": 0,
        "seed": 42,                    # 固定种子, 可复现
        "max_tokens": 4096,
        "timeout": 60,
        "cost_per_1k_tokens": 0.015,
    }
}
```

**成本控制**: 90% 的 tick 只用 quick 模型 (因子分类、信号生成),
仅 MasterController 最终决策使用 deep 模型。
预计每 tick 成本从 ~$0.05 降至 ~$0.01。

#### 3.8.4 反思反馈闭环 (借鉴 TradingAgents)

TradingAgents 在后续运行时获取已实现收益 (realised return),
生成反思注入 Portfolio Manager 的 prompt。QAA 的实现:

```python
class ReflectionEngine:
    """反思引擎 — 每笔交易结束后异步评估决策质量"""

    def generate_reflection(self, decision: AuditEntry, outcome: TradeOutcome):
        """生成反思文本, 注入下次 MasterController 的 prompt"""
        if outcome.realized_pnl > 0:
            return f"Previous {decision.action} was CORRECT. "
                   f"Reason: {decision.reasoning}. Result: +{outcome.realized_pnl:.2%}"
        else:
            return f"Previous {decision.action} was WRONG. "
                   f"Expected: {decision.reasoning}. Actual: {outcome.realized_pnl:.2%}. "
                   f"Consider: {outcome.market_condition_at_exit}"

# 异步注入: 不在主循环内, 由 LearningBus 触发
# 已有对应: learning_bus.py 的 strategy_learning_service.run_periodic_review
```

#### 3.8.5 三层状态架构 (借鉴学术论文 "Agentic Trading")

```
┌──────────────────────────────────────────────────────────┐
│  Layer A: 确定性状态 (审计真相)                            │
│  ── 持仓、余额、风控限额、订单状态                          │
│  ── 对 LLM 只读, 仅由 TradeExecutionAgent 写入             │
│  ── 不受幻觉/遗忘影响                                      │
│  ── 存储在 alpha_arena.db (Layer A 专属表)                 │
├──────────────────────────────────────────────────────────┤
│  Layer B: 生成式上下文 (LLM 活动状态)                      │
│  ── LLM 的上下文窗口 (最近 N 轮决策)                       │
│  ── 瞬态, 受驱逐影响                                      │
│  ── 存储在内存 LRU 缓存                                    │
├──────────────────────────────────────────────────────────┤
│  Layer C: 检索增强记忆 (情景记忆)                           │
│  ── 带时间戳的历史情景                                     │
│  ── 结果禁令: outcome 字段延迟 k 个 tick 后才可检索         │
│  ── 时间衰减: 优先检索近期数据                              │
│  ── 存储在 ChromaDB (向量检索) + SQLite (元数据)           │
└──────────────────────────────────────────────────────────┘
```

**关键规则**:
- **Layer A 覆盖一切**: 当 LLM (Layer B) 的建议与 Layer A 冲突时, Layer A 优先
  (例: LLM 建议 BUY, 但 Layer A 显示余额不足 → 拒绝)
- **结果禁令防护前瞻偏差**: Agent 检索情景记忆时, 时间 t 的情景其 `outcome` 字段
  直到 `t_now >= t + k` 后才可访问, 防止"用未来信息做决策"
- **确定性状态独立于 LLM**: RiskControlAgent 仅读取 Layer A, 不受 LLM 影响

#### 3.8.6 延迟预算管理 (借鉴 Axon Trade)

每个 tick 的 90s 预算分配:

| 阶段 | Agent | 预算 | 超时策略 |
|------|-------|------|----------|
| 1.感知 | MarketData | 10s | cached |
| 2.路由 | RuleRouter | <1ms | N/A |
| 3a.分析(并行) | FactorEngine + 6x Analyst | 20s | cached |
| 3b.辩论 | MTOrchestrator + IntelSignal | 15s | skip |
| 4.决策 | MasterController (DEEP LLM) | 30s | hold |
| 5.门控 | Guards (确定性) | <1s | hold |
| 6.执行 | TradeExecution | 5s | retry(1) |
| 7.审计 | AsyncLogWriter | 异步 | N/A |
| **合计** | | **~80s** | **<90s 兜底** |

**漂移监控**: 每日自动计算各阶段 P99 耗时, 超过预算 120% 时告警。

---

## 4. QAA 协议完整规范 (v1.2 深度设计)

### 4.1 协议分层架构

```
┌────────────────────────────────────────────────────────────────┐
│  L4 审计层 — AuditTrail (每步不可变记录)                        │
├────────────────────────────────────────────────────────────────┤
│  L3 应用层 — AgentCard / ToolDef / GuardDef / RuleRouter       │
├────────────────────────────────────────────────────────────────┤
│  L2 通信层 — MessageBus (EventBus 扩展)                         │
│    request/response | publish/subscribe | parallel dispatch     │
├────────────────────────────────────────────────────────────────┤
│  L1 传输层 — 进程内函数调用 + asyncio (无网络序列化)              │
└────────────────────────────────────────────────────────────────┘
```

### 4.2 核心数据模型

```python
from pydantic import BaseModel, Field
from typing import Any, Literal
from enum import Enum
from datetime import datetime
import uuid

# ==================== 消息 ====================

class QAAStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CIRCUIT_OPEN = "circuit_open"
    CANCELED = "canceled"

class QAARequest(BaseModel):
    """QAA 请求消息"""
    msg_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    from_agent: str                          # 发送方 agent_id
    to_agent: str                            # 接收方 agent_id 或 "broadcast"
    action: str                              # 要调用的能力名称
    payload: dict[str, Any]                  # JSON Schema 验证的参数
    timeout_ms: float                        # 该请求的超时 (毫秒)
    priority: int = 0                        # 0=必须, 1=重要, 2=可选
    session_id: str | None = None            # 关联交易会话

class QAAResponse(BaseModel):
    """QAA 响应消息"""
    msg_id: str                              # 对应 request.msg_id
    correlation_id: str                      # request.msg_id
    status: QAAStatus
    data: Any = None                         # 结果数据 (Pydantic 模型序列化)
    error: str | None = None                 # 错误信息
    elapsed_ms: float = 0                    # 实际耗时
    fallback_used: bool = False              # 是否使用了 fallback 值
    agent_version: str = ""                  # Agent 版本号 (审计用)

# ==================== Agent Card ====================

class Capability(BaseModel):
    """Agent 可调用能力 (借鉴 A2A Skill + MCP Tool)"""
    name: str                                # 能力唯一名称
    description: str                         # 人类可读描述
    input_schema: dict                       # JSON Schema (从 Pydantic 自动导出)
    output_schema: dict                      # JSON Schema
    cost_estimate_ms: float                  # 预估耗时 (毫秒)
    is_llm_required: bool = False            # 是否需要 LLM 调用
    is_destructive: bool = False             # 是否有副作用 (借鉴 MCP annotations)
    is_idempotent: bool = True               # 是否幂等 (重试安全)

class ToolDef(BaseModel):
    """Agent 内部可用的计算工具 (借鉴 MCP Tool)"""
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    cost_estimate_ms: float
    read_only: bool = True                   # 借鉴 MCP readOnlyHint

class GuardDef(BaseModel):
    """门控规则定义"""
    name: str
    description: str
    priority: int = 0                        # 执行优先级 (0 最先)
    is_blocking: bool = True                 # True=拦截, False=仅记录

class CircuitBreakerConfig(BaseModel):
    """熔断器配置 (借鉴 Circuit Breaker Pattern)"""
    failure_threshold: int = 3               # 连续 N 次失败触发熔断
    recovery_timeout_sec: float = 300        # 熔断恢复时间
    half_open_max_calls: int = 1             # 半开状态试探次数
    fallback_agent: str | None = None        # 熔断后的替代 Agent

class AgentCard(BaseModel):
    """Agent 注册卡片 (借鉴 A2A AgentCard)"""
    agent_id: str                            # 唯一标识
    display_name: str
    version: str = "1.0.0"
    description: str
    capabilities: list[Capability]           # 可调用能力
    tools: list[ToolDef]                     # 内部工具
    guards: list[GuardDef]                   # 门控规则

    # 超时熔断策略
    max_timeout_sec: float = 30              # 全局超时上限
    timeout_strategy: Literal["skip", "cached", "hold", "retry"] = "skip"
    fallback_value: Any = None               # 超时后的默认返回值
    max_retries: int = 0
    circuit_breaker: CircuitBreakerConfig

    # 元数据
    dependencies: list[str] = []             # 依赖的其他 agent_id
    tags: list[str] = []                     # 分类标签
```

### 4.3 Agent Task 状态机 (借鉴 A2A Task Lifecycle)

```
                 ┌──────────┐
                 │ CREATED  │ ──── submit() ────► SUBMITTED
                 └──────────┘                         │
                                                      ▼
                 ┌──────────┐ ◄── resume() ──  WORKING ────► (执行中)
                 │ SUSPENDED│      (等待输入)     │  │  │
                 └──────────┘                     │  │  │
                                      成功 ───────┘  │  │
                                       ▼            │  │
                                  COMPLETED         │  │
                                                       │  │
                                    超时 ──────────────┘  │
                                       ▼                  │
                                  TIMEOUT ──► (熔断判定)   │
                                                        │  │
                                    失败 ───────────────┘  │
                                       ▼                    │
                                     FAILED ──► (重试/熔断) │
                                                             │
                                    取消 ◄───────────────────┘
                                       ▼
                                    CANCELED
```

```python
class AgentTaskState(str, Enum):
    CREATED = "created"
    SUBMITTED = "submitted"
    WORKING = "working"
    SUSPENDED = "suspended"      # 需要额外输入 (借鉴 A2A input-required)
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELED = "canceled"

class AgentTask(BaseModel):
    """Agent 任务实例 (借鉴 A2A Task)"""
    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    agent_id: str
    session_id: str | None = None
    state: AgentTaskState = AgentTaskState.CREATED
    request: QAARequest
    response: QAAResponse | None = None
    history: list[dict] = []                # 状态变更历史 (审计用)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = {}
```

### 4.4 消息通信模式

**模式 A: 同步请求-响应 (延迟关键路径)**

```python
# 风控检查、订单验证 — 必须同步等待结果
response = await bus.call_agent(
    agent_id="risk_control",
    action="check_position_limit",
    payload={"symbol": "BTC", "side": "BUY", "size": 0.5},
    timeout_ms=5000,
    priority=0,
)
if response.status == QAAStatus.SUCCESS and not response.data["allowed"]:
    return  # 风控拦截
```

**模式 B: 并行调度 (多 Agent 同时执行)**

```python
# 因子计算 + 情报信号 + 风控评估 — 并行执行
results = await bus.call_agents_parallel(
    calls=[
        AgentCall("factor_engine", "compute_full", {"symbols": ["BTC"]}, timeout_ms=20000),
        AgentCall("intelligence", "get_signals", {"symbols": ["BTC"]}, timeout_ms=10000),
        AgentCall("risk_control", "portfolio_check", {}, timeout_ms=5000),
    ],
    global_timeout_ms=25000,  # 总超时兜底
)
# results: list[QAAResponse], 各自独立超时
```

**模式 C: 事件广播 (发布-订阅)**

```python
# 市场数据更新 → 所有订阅者收到通知 (已有 EventBus 实现)
bus.publish("price_update", {"symbol": "BTC", "price": 67500.0})
```

### 4.5 超时熔断机制

```python
class CircuitBreaker:
    """熔断器实现 (三态: CLOSED → OPEN → HALF_OPEN)"""

    def __init__(self, config: CircuitBreakerConfig):
        self._state = "closed"
        self._failure_count = 0
        self._last_failure_time: float = 0
        self._config = config

    def can_execute(self) -> bool:
        if self._state == "closed":
            return True
        if self._state == "open":
            # 冷却期结束 → 半开
            if time.time() - self._last_failure_time > self._config.recovery_timeout_sec:
                self._state = "half_open"
                return True
            return False
        if self._state == "half_open":
            return self._failure_count < self._config.half_open_max_calls
        return False

    def record_success(self):
        self._failure_count = 0
        self._state = "closed"

    def record_failure(self):
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= self._config.failure_threshold:
            self._state = "open"
            logger.warning(f"Circuit breaker OPEN for agent")
```

**各 Agent 熔断策略**:

| Agent | 熔断阈值 | 恢复时间 | 熔断后行为 | fallback_agent |
|-------|---------|---------|-----------|----------------|
| MarketDataAgent | 5 次超时 | 60s | 用缓存快照 | 无 |
| FactorEngineAgent | 3 次失败 | 300s | 用上次因子 | 无 |
| RiskControlAgent | 2 次失败 | 30s | 强制 HOLD | 无 (安全第一) |
| TradeExecutionAgent | 3 次失败 | 120s | 禁止开仓 | 无 |
| MTOrchestratorAgent | 5 次超时 | 300s | 跳过编排 | 无 |
| IntelligenceSignalEngine | 5 次失败 | 600s | 跳过情报 | 无 |

### 4.6 审计追踪 (不可变决策日志)

```python
class AuditEntry(BaseModel):
    """审计记录 — 每步决策不可篡改"""
    timestamp: datetime
    tick_id: str                             # 交易循环标识
    agent_id: str
    action: str
    input_snapshot: dict                     # 输入数据的精确版本 (哈希)
    output_data: Any                         # Agent 输出
    status: QAAStatus
    elapsed_ms: float
    llm_prompt_hash: str | None = None       # LLM prompt 哈希 (可审计)
    llm_response_hash: str | None = None     # LLM 响应哈希
    fallback_used: bool = False
    circuit_breaker_state: str = "closed"
    metadata: dict[str, Any] = {}
```

**审计原则 (来自 "Agentic Trading" 学术论文最佳实践)**:

| 原则 | 实现 |
|------|------|
| 输入快照 | 每次决策前记录所有输入数据的精确版本 (含哈希) |
| 确定性优先 | LLM temperature=0 + 固定 seed (可复现) |
| 结果禁令 | 情景记忆的 outcome 字段直到 t+k 后才可检索 (防前瞻偏差) |
| 延迟预算 | 每个 tick 总延迟上限 90s, 每步有独立预算 |
| 不可变日志 | 审计记录 append-only, 不允许修改或删除 |

### 4.7 Agent 生命周期管理

```python
class AgentLifecycle:
    """Agent 生命周期管理 (借鉴 MCP initialize + A2A Task 状态机)"""

    async def initialize(self, card: AgentCard):
        """阶段一: 初始化 — 注册能力、版本协商"""
        self.card = card
        self.circuit_breaker = CircuitBreaker(card.circuit_breaker)
        self.state = "idle"
        await self._register_tools(card.tools)
        await self._register_guards(card.guards)

    async def handle_request(self, request: QAARequest) -> QAAResponse:
        """阶段二: 操作 — 处理请求"""
        if not self.circuit_breaker.can_execute():
            return QAAResponse(
                msg_id=uuid.uuid4().hex[:16],
                correlation_id=request.msg_id,
                status=QAAStatus.CIRCUIT_OPEN,
                fallback_used=True,
                data=self.card.fallback_value,
            )

        task = AgentTask(agent_id=self.card.agent_id, request=request)
        task.state = AgentTaskState.WORKING

        try:
            # 超时包装
            result = await asyncio.wait_for(
                self._execute_capability(request.action, request.payload),
                timeout=request.timeout_ms / 1000,
            )
            task.state = AgentTaskState.COMPLETED
            self.circuit_breaker.record_success()
            return QAAResponse(
                correlation_id=request.msg_id,
                status=QAAStatus.SUCCESS,
                data=result,
            )
        except asyncio.TimeoutError:
            task.state = AgentTaskState.TIMEOUT
            self.circuit_breaker.record_failure()
            return QAAResponse(
                correlation_id=request.msg_id,
                status=QAAStatus.TIMEOUT,
                fallback_used=True,
                data=self.card.fallback_value,
            )
        except Exception as e:
            task.state = AgentTaskState.FAILED
            self.circuit_breaker.record_failure()
            return QAAResponse(
                correlation_id=request.msg_id,
                status=QAAStatus.FAILED,
                error=str(e),
            )
        finally:
            # 写入审计日志 (异步, 不阻塞)
            self._audit_logger.enqueue(task)

    async def shutdown(self):
        """阶段三: 关闭 — 清理资源"""
        self.state = "shutdown"
```

### 4.8 完整调用流程示例

```
Tick #42 开始 (session: fa_c8f0899147)
│
├── [感知] bus.call_agent("market_data", "get_snapshot", timeout=10s)
│   └── Response: {status: SUCCESS, data: {BTC: 67500, ETH: 3850, ...}}
│
├── [路由] RuleRouter.route(market_snapshot)
│   └── 决策: volatility_regime=HIGH → 调用因子+编排器+风控
│
├── [并行] bus.call_agents_parallel([
│       ("factor_engine", "compute_full", timeout=20s),
│       ("mt_orchestrator", "evaluate", timeout=30s),
│       ("risk_control", "portfolio_check", timeout=5s),
│   ], global_timeout=35s)
│   ├── factor_engine: SUCCESS (18s), data: {adaptive_score: 0.72}
│   ├── mt_orchestrator: SUCCESS (12s), data: {long: BULLISH, short: NEUTRAL}
│   └── risk_control: SUCCESS (0.3s), data: {risk_score: 35, allowed: true}
│
├── [综合] bus.call_agent("master_controller", "synthesize", timeout=60s)
│   └── MasterController 调用 LLM 综合决策
│       ├── Tools 内部调用: 6x Analyst (毫秒级)
│       ├── DebateLayer: Bull vs Bear 辩论
│       └── Response: {action: BUY, symbol: BTC, confidence: 0.78}
│
├── [门控] Guard 链串行执行
│   ├── ConsistencyGate: PASS (30min 内无方向翻转)
│   └── DeterministicRiskGate: PASS (敞口未超限)
│
├── [执行] bus.call_agent("trade_execution", "place_order", timeout=10s)
│   └── Response: {status: SUCCESS, data: {order_id: "xxx", filled: true}}
│
└── [审计] AsyncLogWriter.enqueue(all_audit_entries)
    └── Tick #42 完成 (总耗时: 52s)
```

---

## 5. 记忆系统 (v1.3 三层状态架构)

借鉴学术论文 "Agentic Trading" 三层状态架构, 将记忆系统升级为 **确定性优先** 设计:

### 5.1 三层状态架构

| 层级 | 存储 | TTL | 内容 | LLM 权限 | 冲突优先级 |
|------|------|-----|------|---------|-----------|
| **Layer A: 确定性状态** | alpha_arena.db | 实时 | 持仓、余额、风控限额、订单 | **只读** | **最高 (覆盖一切)** |
| **Layer B: 生成式上下文** | 内存 LRU (最近 N 轮) | 会话 | LLM 上下文窗口、最近决策 | 读写 | 中 |
| **Layer C: 检索增强记忆** | ChromaDB + SQLite | 永久 | 历史情景、策略参数、成功/失败模式 | 只读 (带禁令) | 低 |

### 5.2 Layer A: 确定性状态 (审计真相)

```python
class DeterministicState:
    """Layer A — 仅由 TradeExecutionAgent 写入, 所有 Agent 只读"""

    def get_positions(self, account_id: str) -> list[Position]:
        """返回当前持仓 — 不受 LLM 幻觉影响"""

    def get_balance(self, account_id: str) -> float:
        """返回真实余额 — LLM 无法修改"""

    def check_risk_limits(self, account_id: str) -> RiskCheckResult:
        """返回风控状态 — 独立于 LLM 判断"""
```

**关键规则**: 当 MasterController (LLM) 建议 BUY 但 Layer A 显示余额不足时,
**Layer A 的状态为真**, 交易被拒绝。LLM 的判断不覆盖确定性事实。

### 5.3 Layer C: 检索增强记忆 (带结果禁令)

```python
class EpisodicMemory:
    """Layer C — 情景记忆, 带结果禁令防护前瞻偏差"""

    def store(self, episode: Episode):
        """存储交易情景 (决策时存储, outcome 延迟填充)"""

    def retrieve(self, query: str, current_tick: int) -> list[Episode]:
        """检索相关情景 — 结果禁令: outcome 字段仅在 t_now >= t + k 后可访问"""
        results = self._vector_search(query)
        for ep in results:
            if current_tick < ep.tick_id + self._outcome_delay_k:
                ep.outcome = None  # 禁令: 隐藏未来结果
        return results
```

### 5.4 Phase 存储方案

| 层级 | Phase 1 存储 | Phase 3+ 存储 |
|------|-------------|---------------|
| Layer A | alpha_arena.db (现有) | 同左 |
| Layer B | 内存字典 + LRU (max=100 条) | 同左 |
| Layer C | ChromaDB (已集成) + SQLite 元数据 | Redis 向量索引 (可选) |

> **Phase 1 不引入 Redis**。单机部署下 SQLite + ChromaDB 已足够。
> Redis 仅在确认单机性能瓶颈后考虑。

---

## 6. 提示词工程分析与打磨规划 (v1.4 新增)

### 6.1 现有系统提示词审计

系统中有 17 个 LLM 提示词位置，以下审计 4 个核心 Prompt 的设计质量：

#### 6.1.1 成熟度评估

| Prompt | 文件位置 | 规模 | 成熟度 | 核心优势 | 核心问题 |
|--------|---------|------|--------|---------|---------|
| **MasterController.synthesize()** | trading_analysts.py:1660-1921 | ~5000 token | **最高** | 7 个场景示例、铁律机制、防守模式、数据驱动约束 | 过长 (逼近注意力极限)、缺开仓场景示例、段落间有重复约束 |
| **KlineAnalyst** | trading_analysts.py:1096-1126 | ~500 token | **中等** | 简洁高效、`response_format` 强制 JSON | 无 Few-shot、无 CoT、约束最少、缺负面示例 |
| **OUTPUT_FORMAT_COMPLETE** | ai_decision_service.py:439-527 | ~2000 token | **中高** | 3 个完整 Few-shot (buy/sell/hold)、字段约束严格 | 示例价格过时、与 MasterController 概念不一致 |
| **SIGNAL_SYSTEM_PROMPT** | ai_signal_generation_service.py:26-139 | ~2000 token | **高** | 3 步工作流、Function Calling、3 种输出格式 | 缺完整交互流程示例、阈值设定指导不足 |

**其他辅助 Prompt** (未详细审计):

| Prompt | 文件 | 角色 |
|--------|------|------|
| Strategy Evolver | strategy_evolver.py | 策略进化 LLM |
| Prompt Generation | ai_prompt_generation_service.py | 提示词自动生成 |
| AI Coin Selector | auto_coin_selector.py | AI 币种筛选 |
| Defensive Mode | full_auto_trading_service.py:8750 | 防守模式决策 |
| News Intelligence | news_intelligence_service.py | 新闻分析 |
| Whale Tracker | whale_tracker_service.py | 鲸鱼追踪 |
| Trade Journal | ai_trade_journal_service.py | 交易日志 |
| Strategy Optimizer | strategy_optimizer_service.py | 策略优化 |
| Hypothesis Engine | strategy_hypothesis_engine.py | 假设生成 (仅英文) |
| Factor Discovery | ai_factor_discovery_service.py | 因子发现 (含代码生成) |

#### 6.1.2 MasterController.synthesize() 深度分析

这是系统最核心也最长的提示词 (~5000 token), 由 `f-string` 动态拼接:

**动态注入段落**:
- `{report_text}`: 6 路分析师报告
- `{debate_text}`: 多空辩论摘要 (DebateLayer 输出)
- `{_tier_context_text}`: Tier 上下文 (~40 行)
- `{_hold_timeout_text}`: 持仓超时提醒
- `{_recent_lessons_text}`: 交易教训 (反思闭环)

**静态结构段落**:
- 决策原则 (~60 行): 开仓硬性前提 (6 条)
- 模板信号采信门槛 (~20 行): 4 级梯度 (无信号/仅供参考/可采信/强信号)
- Reduce/Close 铁律 (~60 行): 含数据事实 + 5 条铁律 + 自检清单
- 动态交易规划表 (~15 行): 当前持仓 + 操作空间
- TP/SL 管理 (~40 行): 止盈止损规则
- 7 个典型场景: 盈利锁利/量能萎缩/RSI 超买/微亏/高波动/无 SL
- 波动率认知 (~10 行): 按币种市值分级
- JSON 输出格式 (~20 行): 完整 Schema 示例

**优势 (超越竞品)**:
- 铁律机制: "亏损 > 8% 绝对禁止补仓" 等数据驱动约束, 竞品无此设计
- 场景示例: 7 个真实场景覆盖主要操作模式
- 防守模式: 分层操作权限约束 (竞品无此概念)
- 自检清单: 三问自检的显式推理链

**问题**:
- 所有场景都是 "hold + adjust_sl" 模式, 缺少 buy/sell 开仓完整示例
- 动态注入段落的存在与否不可预知, 导致 prompt 结构不稳定
- 多处重复约束 ("hold 是默认" 出现多次)
- Prompt 注入后整体可能达 6000-8000 token, 逼近 LLM 有效注意力极限

### 6.2 竞品提示词最佳实践

#### 6.2.1 Schema-as-Prompt 模式 (TradingAgents)

TradingAgents 最精妙的设计: **Pydantic Field description 即 LLM 输出指令**

```python
# TradingAgents: tradingagents/agents/schemas.py
class PortfolioDecision(BaseModel):
    rating: PortfolioRating = Field(
        description=(
            "The final position rating. Exactly one of Buy / Overweight / Hold / "
            "Underweight / Sell, picked based on the analysts' debate."
        ),
    )
    executive_summary: str = Field(
        description=(
            "A concise action plan covering entry strategy, position sizing, "
            "key risk levels, and time horizon. Two to four sentences."
        ),
    )
    investment_thesis: str = Field(
        description=(
            "Detailed reasoning anchored in specific evidence from the analysts' "
            "debate. If prior lessons are referenced in the prompt context, "
            "incorporate them; otherwise rely solely on the current analysis."
        ),
    )
```

**核心优势**:
- `Enum` 约束输出值域: `PortfolioRating(str, Enum)` 确保只输出 5 个枚举值之一
- `Field(description=...)` 即 prompt: prompt body 只需提供上下文, 无需在文本中描述输出格式
- 优雅降级: 结构化输出失败时回退到 free-text, `render` 函数保持下游格式不变

```python
# TradingAgents: 降级策略
def invoke_structured_or_freetext(structured_llm, plain_llm, prompt, render):
    if structured_llm is not None:
        try:
            result = structured_llm.invoke(prompt)
            return render(result)  # Pydantic → markdown
        except Exception:
            pass
    response = plain_llm.invoke(prompt)
    return response.content  # free-text fallback
```

**对比我们的系统**: 完全依赖文本指令 "请返回 JSON", 仅 KlineAnalyst 使用了 `response_format`。
输出格式可靠性低于竞品。

#### 6.2.2 结构化信号传递

**ai-hedge-fund 的统一信号格式**:

```python
# 所有 Agent 输出统一为三元组
{
    "signal": "bullish" | "bearish" | "neutral",
    "confidence": 0-100,           # 统一整数范围
    "reasoning": { ... }           # 结构化分析明细
}
```

**TradingAgents 的三级汇聚架构**:

```
Analysts → 自然语言报告
    ↓
Research Manager → ResearchPlan (5-tier rating: Buy/Overweight/Hold/Underweight/Sell)
    ↓
Trader → TraderProposal (action + entry_price + stop_loss + position_sizing)
    ↓
Portfolio Manager → PortfolioDecision (rating + thesis + price_target)
```

**TradingAgents 的信号提取** — 确定性正则解析, 非二次 LLM:
```python
_RATING_LABEL_RE = re.compile(r"rating.*?[:\-][\s*]*(\w+)", re.IGNORECASE)
```

#### 6.2.3 Prompt 四层分层架构 (TradingAgents)

```
Layer 0: 通用协作层 (所有 analyst 共享)
  "You are a helpful AI assistant, collaborating with other assistants..."
Layer 1: Agent 角色层 (每个 agent 独特)
  "You are a trading assistant tasked with analyzing financial markets..."
Layer 2: 上下文注入层
  prompt = prompt.partial(current_date=current_date, instrument_context=...)
Layer 3: 数据注入层 (XML 标签包裹)
  <start_of_news>{news_block}</end_of_news>
  <start_of_stocktwits>{stocktwits_block}</end_of_stocktwits>
  <start_of_reddit>{reddit_block}</end_of_reddit>
```

**Portfolio Manager 的分层设计尤为精巧**:

```python
prompt = f"""As the Portfolio Manager, synthesize...
{instrument_context}                              # Layer 2: 上下文
---Rating Scale---                                 # Layer 1: 角色
- Research Manager's plan: {research_plan}         # Layer 3: 中间结果
- Trader's proposal: {trader_plan}                 # Layer 3: 中间结果
- Lessons from prior decisions: {past_context}     # Layer 3: 记忆
Risk Debate History: {history}                     # Layer 3: 完整辩论
"""
```

**ai-hedge-fund 的 PM 极简 Prompt** — 仅 ~200 token:

```python
"system": "You are a portfolio manager.
Inputs per ticker: analyst signals and allowed actions with max qty.
Pick one allowed action per ticker and a quantity <= the max.
Keep reasoning very concise (max 100 chars). Return JSON only."

"human": "Signals:\n{signals}\nAllowed:\n{allowed}\nFormat:\n{json_template}"
```

对比: 我们的 MasterController prompt 达 ~5000 token, 是竞品 PM 的 25 倍。

#### 6.2.4 确定性预填充策略 (ai-hedge-fund)

```python
# 无可执行动作的 ticker 预填为 hold, 不浪费 LLM token
if set(allowed_actions.keys()) == {"hold"}:
    prefilled_decisions[ticker] = PortfolioDecision(
        action="hold", quantity=0, confidence=0, reasoning="No allowed actions"
    )
```

```python
# 信号压缩: 完整分析 → {sig, conf} 对
ticker_signals = {}
for agent, signals in analyst_signals.items():
    sig = signals[ticker].get("signal")
    conf = signals[ticker].get("confidence")
    if sig is not None and conf is not None:
        ticker_signals[agent] = {"sig": sig, "conf": conf}
```

**效果**: ai-hedge-fund 的 PM prompt 仅 ~200 token, 因为他消费的是压缩后的信号,
不是完整的分析师报告文本。

#### 6.2.5 XML 标签数据分隔 (TradingAgents)

TradingAgents 使用 XML 风格标签包裹注入数据, 帮助 LLM 区分指令与数据:

```python
<start_of_news>
{news_block}
<end_of_news>

<start_of_stocktwits>
{stocktwits_block}
</end_of_stocktwits>
```

这不仅是格式化, 更是 **Prompt 注入防护**: 当注入的新闻文本包含类似指令的内容时,
XML 标签帮助 LLM 识别数据边界, 降低误读风险。

#### 6.2.6 反思学习机制 (TradingAgents)

```python
# 交易后反思 prompt (2-4 句话, 强制结构化)
"You are a trading analyst reviewing your own past decision now that the outcome is known.
Write exactly 2-4 sentences covering:
1. Was the directional call correct? (cite the alpha figure)
2. Which part of the investment thesis held or failed?
3. One concrete lesson to apply to the next similar analysis."
```

反思结果注入未来决策:
```python
past_context = state.get("past_context", "")
lessons_line = f"- Lessons from prior decisions and outcomes:\n{past_context}\n"
```

**对比我们的系统**: 已有类似机制 (`_recent_lessons_text`), 但反思生成没有强制结构化。

#### 6.2.7 竞品提示词设计对比总结

| 维度 | TradingAgents | ai-hedge-fund | Hyper-Alpha-Arena (现有) |
|------|--------------|---------------|------------------------|
| 输出约束方式 | `with_structured_output(Schema)` | `with_structured_output(Model)` | 文本指令 "请返回 JSON" |
| 评级体系 | 5-tier (Buy/Overweight/Hold/Underweight/Sell) | 3-tier (bullish/neutral/bearish) | 混用 (0.0-1.0 / 0-100) |
| PM Prompt 长度 | ~500 token | ~200 token | ~5000 token |
| 信号压缩 | 不压缩 (完整报告) | PM 前压缩为 {sig, conf} | 无压缩 |
| 数据分隔 | XML 标签 | 无明确分隔 | Markdown + emoji |
| Few-shot | 无 (Schema 驱动) | JSON 模板 | 7 个场景 (MC) / 3 个示例 (decision) |
| 反思学习 | 强制 2-4 句结构化 | 无 | 有但非强制结构化 |
| 风控 Prompt | 辩论式 (3 方) | 算法化 (无 LLM) | 硬规则 + 铁律 |
| Prompt 注入防护 | XML 标签边界 | 无 | 无 |

### 6.3 差距分析

基于 6.1 (现有审计) 和 6.2 (竞品实践) 的对比, 识别出 7 个关键差距:

#### P0 — 必须修复 (影响输出可靠性和系统一致性)

| # | 差距 | 现状 | 竞品做法 | 预期收益 |
|---|------|------|---------|---------|
| G1 | **缺少 Schema-as-Prompt** | 文本指令 "请返回 JSON", 仅 KlineAnalyst 用 `response_format` | Pydantic `with_structured_output` + Enum 约束 | 输出可靠性提升, JSON 解析失败率降低 50%+ |
| G2 | **信号传递格式不统一** | 置信度 0.0-1.0 和 0-100 混用; 操作集不统一; 止盈止损价格/百分比混用 | ai-hedge-fund 统一 0-100; TradingAgents Enum 约束 | 消除跨系统不一致, 降低维护复杂度 |

#### P1 — 重要改进 (影响效率和安全性)

| # | 差距 | 现状 | 竞品做法 | 预期收益 |
|---|------|------|---------|---------|
| G3 | **缺少信号压缩** | MC 直接消费 5 路完整报告 (~5000 token) | ai-hedge-fund PM 消费压缩后 {sig, conf} (~200 token) | MC prompt 缩短 30-50%, 有效注意力提升 |
| G4 | **缺少 XML 数据分隔** | Markdown 标题 + emoji, 无明确数据边界 | `<start_of_news>...</end_of_news>` | 降低 prompt injection 风险 |
| G5 | **缺少确定性预填充** | 所有持仓都走完整 LLM 分析 | 无动作 ticker 预填 hold | 每次决策节省 ~500-1000 token |

#### P2 — 质量提升 (影响分析质量)

| # | 差距 | 现状 | 竞品做法 | 预期收益 |
|---|------|------|---------|---------|
| G6 | **KlineAnalyst 缺 Few-shot + CoT** | 无示例、无推理引导 | TradingAgents Schema 驱动 + ai-hedge-fund JSON 模板 | 提升形态识别一致性 |
| G7 | **缺少负面示例** | 所有 prompt 只展示正确做法 | MasterController 铁律有反例, 但其他 prompt 无 | 减少常见错误输出 |

#### P3 — 可选增强

| # | 差距 | 现状 | 竞品做法 | 预期收益 |
|---|------|------|---------|---------|
| G8 | **Prompt 注入安全防护** | 多段动态注入无边界标记 | XML 标签 + 指令/数据边界 | 系统安全性提升 |

### 6.4 跨系统一致性问题

**现有系统的两个决策子系统存在概念模型冲突**:

| 维度 | ai_decision_service | MasterController (trading_analysts) |
|------|---------------------|-------------------------------------|
| **置信度范围** | 0.0-1.0 浮点 | 0-100 整数 |
| **操作类型** | buy/sell/hold/close | hold/buy/sell/close/reduce/pyramid/dca |
| **止盈止损** | take_profit_price / stop_loss_price (价格) | adjust_tp / adjust_sl (价格) + stop_loss_pct / take_profit_pct (百分比) |
| **风控字段** | risk_scenario | trade_nature + risk_reward_ratio |
| **角色定位** | 审核者 | 首席交易官 (CTO) |
| **输出字段数** | 12 | 12 (但含义不同) |
| **Prompt 发送方式** | `role: "user"` | `role: "system"` |
| **JSON 强制** | 文本指令 | 文本指令 (仅 KlineAnalyst 用 `response_format`) |

**影响**: 两套系统的 Prompt 在概念层面不一致, 导致:
1. LLM 在不同 prompt 间切换时产生认知混乱
2. 审计日志中的决策数据格式不统一, 回测困难
3. 新开发者的认知负担高 (需要理解两套概念模型)

### 6.5 打磨策略

#### 6.5.1 核心原则

1. **不重写, 逐步升级** — 保留现有优势 (铁律机制、场景示例、防守模式), 仅修复差距
2. **Schema 驱动** — 用 Pydantic Schema 定义统一概念模型, 所有 Prompt 输出绑定到 Schema
3. **压缩在先** — 在 MasterController 之前增加信号压缩层, 减少 PM 的 token 消耗
4. **确定性优先** — 竞品 ai-hedge-fund 证明: 确定性代码比 LLM prompt 更可靠地做风控

#### 6.5.2 统一概念模型

定义跨系统共享的 Pydantic Schema, 消除 6.4 节中的不一致:

```python
# ===== 统一 Schema (所有 Prompt 共享) =====

class TradingAction(str, Enum):
    """统一操作类型 — 取代两套混用"""
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CLOSE = "close"
    REDUCE = "reduce"         # 减仓
    PYRAMID = "pyramid"       # 加仓 (盈利加仓)

class ConfidenceScale(int, Enum):
    """统一置信度 — 0-100 整数, 取代 0.0-1.0 浮点"""
    NONE = 0
    LOW = 25
    MEDIUM = 50
    HIGH = 75
    VERY_HIGH = 100

class StopTakeLevel(BaseModel):
    """统一止盈止损 — 同时记录价格和百分比"""
    price: float | None = Field(description="目标价格")
    pct: float = Field(description="距离当前价的百分比, 如 2.5 表示 2.5%")

class TradingDecision(BaseModel):
    """统一决策输出 — 所有 Prompt 最终输出此格式"""
    symbol: str = Field(description="交易对, 如 BTC")
    action: TradingAction = Field(
        description=(
            "交易动作。HOLD 为默认, 仅在高置信度 (>65) 且有明确信号时才开仓。"
            "REDUCE 用于减仓, PYRAMID 用于盈利加仓。"
        ),
    )
    confidence: int = Field(
        ge=0, le=100,
        description="决策置信度 0-100。< 60 自动降级为 HOLD。",
    )
    reasoning: str = Field(
        description=(
            "决策理由, 必须包含: (1) 正反论点权衡 (2) 关键数据引用 "
            "(3) 与上一次决策的一致性说明。3-5 句话。"
        ),
    )
    # 以下字段仅 action=BUY/SELL/PYRAMID 时必填
    trade_nature: str | None = Field(
        default=None,
        description="交易性质: scalping/day/swing/position",
    )
    stop_loss: StopTakeLevel | None = Field(default=None)
    take_profit: StopTakeLevel | None = Field(default=None)
    risk_reward_ratio: float | None = Field(
        default=None, ge=1.0,
        description="盈亏比, 必须 >= 1.5 才允许开仓",
    )
    # 以下字段仅 action=HOLD/REDUCE 时有意义
    adjust_sl: float | None = Field(
        default=None,
        description="调整止损到新价格 (仅 HOLD/REDUCE)",
    )
    adjust_tp: float | None = Field(
        default=None,
        description="调整止盈到新价格 (仅 HOLD/REDUCE)",
    )
```

#### 6.5.3 Schema-as-Prompt 实施路径

**Step 1**: 定义 Schema → 替代文本指令中的 JSON 格式描述

```python
# Before (ai_decision_service.py):
# OUTPUT_FORMAT_COMPLETE = '''请返回 JSON: {"action": "buy", "confidence": 0.8, ...}'''

# After:
llm = ChatOpenAI(model="claude-sonnet-4", temperature=0)
structured_llm = llm.with_structured_output(TradingDecision)

decision = structured_llm.invoke(prompt)  # 直接返回 TradingDecision 实例
# 无需 JSON 解析, 无需容错, 类型安全
```

**Step 2**: Prompt body 精简 — 只保留上下文和决策指导, 输出格式由 Schema 控制

```python
# Before: prompt 中 ~20 行描述 JSON 格式
# After: prompt 中 0 行描述 JSON 格式, Schema 的 Field description 承担此职责
```

**Step 3**: 降级策略 — `with_structured_output` 失败时回退到 free-text + JSON 解析

```python
def safe_invoke(llm, structured_llm, prompt, schema_class):
    try:
        return structured_llm.invoke(prompt)
    except Exception:
        response = llm.invoke(prompt)
        return schema_class.model_validate_json(
            extract_json(response.content)  # 正则提取 JSON
        )
```

#### 6.5.4 信号压缩层

在 MasterController 之前增加 `SignalCompressor`, 将 5 路分析师报告压缩为结构化信号:

```python
class SignalCompressor:
    """将分析师报告压缩为结构化信号, 减少 PM 的 token 消耗"""

    def compress(self, reports: list[AnalystReport]) -> dict[str, CompressedSignal]:
        """每个 symbol 输出一个压缩信号"""
        result = {}
        for report in reports:
            result[report.symbol] = CompressedSignal(
                signal=report.direction,         # bullish/bearish/neutral
                confidence=report.confidence,     # 0-100
                key_drivers=report.key_factors[:3],  # 最多 3 个关键因素
                risk_flags=report.risk_flags[:2],    # 最多 2 个风险标记
            )
        return result

class CompressedSignal(BaseModel):
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: int = Field(ge=0, le=100)
    key_drivers: list[str] = Field(max_length=3)
    risk_flags: list[str] = Field(max_length=2)
```

**效果预估**: MasterController prompt 从 ~5000 token 缩短至 ~2500 token,
同时保留关键决策信息。

#### 6.5.5 XML 数据分隔改造

将所有动态注入数据用 XML 标签包裹:

```python
# Before:
f"## 分析师报告\n{report_text}\n## 辩论摘要\n{debate_text}"

# After:
f"<analyst_reports>\n{report_text}\n</analyst_reports>\n"
f"<debate_summary>\n{debate_text}\n</debate_summary>\n"
f"<tier_context>\n{tier_context_text}\n</tier_context>\n"
f"<recent_lessons>\n{lessons_text}\n</recent_lessons>"
```

#### 6.5.6 打磨优先级与预期收益

| 优先级 | 打磨项 | 涉及文件 | 预期收益 |
|--------|--------|---------|---------|
| **P0** | 统一 Schema: `TradingDecision` + `ConfidenceScale` | ai_decision_service.py, trading_analysts.py | 消除跨系统不一致 |
| **P0** | Schema-as-Prompt: `with_structured_output` | ai_decision_service.py, trading_analysts.py | 输出可靠性提升 50%+ |
| **P1** | 信号压缩: `SignalCompressor` | trading_analysts.py (新增) | MC prompt 缩短 30-50% |
| **P1** | XML 数据分隔 | trading_analysts.py | Prompt injection 防护 |
| **P1** | 确定性预填充 | trading_analysts.py | 每次节省 ~500-1000 token |
| **P2** | KlineAnalyst Few-shot + CoT | trading_analysts.py | 分析质量提升 |
| **P2** | 负面示例 ("不要这样做") | trading_analysts.py | 减少常见错误 |
| **P3** | Prompt 注入安全防护 | 全局 | 系统安全性 |

#### 6.5.7 保留现有优势

以下设计**已经超越竞品**, 不做改动:

| 现有优势 | 竞品状态 | 保留理由 |
|---------|---------|---------|
| 铁律机制 (5 条数据驱动约束) | 竞品无此设计 | 直接可审计, 确定性保障 |
| 7 个典型场景示例 | TradingAgents 无 Few-shot | 提供具体决策参照 |
| 防守模式 (分层操作权限) | 竞品无此概念 | 风险分级管理 |
| 自检清单 (三问自检) | 竞品无此设计 | 显式推理链 |
| DebateLayer (纯规则化辩论) | TradingAgents 用 LLM 辩论 | 零额外 LLM 开销 |
| PromptTemplate 数据库系统 | 竞品无版本管理 | 动态变量注入 + 自动进化 |

---

## 7. 兼容升级路径 (v1.1 修正: 增加 Phase 0)

### Phase 0: 快速修复现有超时 (不改架构, 立即可做)

目标: 将 "1799 次超时 / 0 次成功" 变为 "稳定运行", 无需引入任何新组件。

- [ ] 给 `_run_health_check` 的每个子步骤加独立超时线程
- [ ] V3 因子管道: 已有 `_MAX_V3_SECONDS=45`, 增加跳过后续步骤的逻辑
- [ ] LLM 调用: 加严格超时 (当前 ai_decision_service.py 无超时)
- [ ] `_bootstrap_market_summary`: 已注释, 确认缓存模式稳定
- [ ] 给 `_run_unified_loop_safe` 的 join 超时后增加 DB session 清理

```python
def _run_with_timeout(fn, timeout_s, fallback=None):
    """通用超时包装器"""
    result = [fallback]
    def _target():
        result[0] = fn()
    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if t.is_alive():
        logger.warning(f"{fn.__name__} timed out after {timeout_s}s")
        return fallback
    return result[0]
```

预期效果: 每个 tick 从 360s 降至 90s 以内, 无一步卡死全死。

### Phase 1: Agent Card 定义 + 超时熔断框架

- [x] 定义 AgentCard / ToolDef / CircuitBreakerConfig Pydantic 模型
- [x] 给现有 EventBus 添加 call_agent / call_agents_parallel 方法
- [x] 实现超时线程 + 熔断器 + fallback 逻辑
- [x] 实现 RuleRouter (规则路由引擎)
- [x] 改造 _run_unified_loop 为 QAA 调度器
- [x] 保留原有 _run_trading_cycle 作为 fallback (QAA_MODE=legacy)
- [x] AsyncLogWriter 异步日志写入器
- [x] 双模型配置 (quick/deep LLM)
- [x] QAA_MODE 设置开关

### Phase 2: 逐步迁移 Agent (按风险从低到高)

- [x] GeneticOptimizer (离线, 零风险) — handler 注册 + 状态返回
- [x] MarketDataAgent (只读, 低风险) — unified_data_pool 集成
- [x] FactorEngineAgent (计算密集, 中风险) — V3 pipeline 集成
- [x] IntelligenceSignalEngine (信号, 中风险) — compute_trading_signal 集成
- [x] RiskControlAgent (风控, 需极度稳定) — consistency_gate + risk_model 集成
- [x] MTOrchestrator (编排器, 高复杂度) — evaluate_portfolio 集成
- [x] MasterController (最核心, 最后迁移) — signal compressor + XML prompt 集成

### Prompt 打磨 (P0-P3)

- [x] P0: 统一 TradingDecision Schema + ConfidenceScale + StopTakeLevel
- [x] P0: Schema-as-Prompt (safe_invoke 降级策略)
- [x] P1: SignalCompressor 信号压缩器
- [x] P1: XML 数据分隔 + 确定性预填充
- [x] P2: KlineAnalyst Few-shot + 负面示例
- [x] P3: Prompt 注入安全防护 (sanitize_user_input)

### Phase 3: 增强

- [x] 三层状态架构 (Layer A 确定性 + Layer B 生成式 LRU + Layer C 情景记忆+结果禁令)
- [x] 反思闭环 (ReflectionEngine — 交易结果评估 + 教训注入 prompt)
- [x] LLM 规划 fallback (规则路由无法判断时使用 QUICK LLM 规划)
- [x] 延迟漂移监控 (P99 超预算 120% 自动告警)
- [x] Agent 健康监控 API (状态/熔断/延迟/告警聚合端点)
- [ ] Redis Pub/Sub 替代内存 EventBus (仅当确认单机瓶颈时)
- [ ] Agent 健康监控面板 (前端)
- [ ] 长期记忆增强 (ChromaDB 向量检索优化)

兼容: Adapter 模式 + 配置开关 QAA_MODE=legacy|qaa

---

## 8. 预期收益

| 指标 | 当前 | Phase 0 后 | V4 完整 |
|------|------|-----------|---------|
| 单 tick 耗时 | 120-360s (卡死) | <90s | <30s |
| 超时容错 | 一步卡死全死 | 独立超时跳过 | Agent 级熔断 |
| 路由方式 | 固定 21 步串行 | 固定 21 步+超时 | 规则路由+并行 |
| 可扩展性 | 改总控代码 (9609行) | 同左 | 注册 Card 即可 |
| 并行度 | ~0 | 部分并行 (V3/LLM) | 多 Agent 并发 |
| 数据库竞争 | 87 表 1 文件 | 同左 | 4 文件分层 |

---

## 9. 可行性评审结论

### 9.1 评审评分 (v1.4 更新)

| 维度 | Draft 1.0 | v1.1 | v1.3 | v1.4 | 说明 |
|------|-----------|------|------|------|------|
| 问题诊断 | A | A | A | A | 串行无熔断诊断准确 |
| 协议选择 (QAA) | A | A | A | A | 自建方向正确, 深度技术解析 |
| 智能体分类 | D | A | A | A | 5 Agent + 6 Tool + 6 Guard |
| 路由策略 | C | A | A | A | 规则路由 + LLM fallback |
| 消息系统 | C | A | A | A | 基于 EventBus 扩展 |
| 超时熔断 | D | B | A | A | 延迟预算管理 + 漂移监控 |
| 数据库策略 | F | B | B | B | 分层策略 |
| 记忆系统 | C | B | A | A | 三层状态架构 + 结果禁令 |
| 实施路径 | C | A | A | A | Phase 0 + 风险排序迁移 |
| 竞品借鉴 | F | - | A | A | 结构化信号/辩论/双模型/反思闭环/延迟预算 |
| 决策流水线 | F | - | A | A | 五阶段映射到现有组件 |
| **提示词工程** | F | - | - | **B** | v1.4 新增: 审计完成, 差距识别, 打磨策略已规划 |

### 9.2 竞品借鉴映射总表 (v1.4 更新)

| 借鉴来源 | 设计模式 | QAA 实现 | 对应现有代码 |
|----------|---------|----------|-------------|
| TradingAgents | 五阶段流水线 | 3.2 架构总览 | 新设计 |
| TradingAgents | Bull/Bear 辩论 | 3.8.2 DebateLayer 增强 | trading_analysts.py:1277 |
| TradingAgents | 双模型策略 | 3.3 LLMLevel (quick/deep) | ai_decision_service.py |
| TradingAgents | 反思反馈闭环 | 3.8.4 ReflectionEngine | learning_bus.py |
| TradingAgents | Checkpoint Resume | 4.3 AgentTask 状态机 | 新设计 |
| TradingAgents | **Schema-as-Prompt** | 6.5.3 统一 Schema | ai_decision_service.py |
| TradingAgents | **XML 数据分隔** | 6.5.5 XML 改造 | trading_analysts.py |
| ai-hedge-fund | 结构化信号 | 3.8.1 AgentSignal | trading_analysts.py AnalystReport |
| ai-hedge-fund | 层级漏斗聚合 | 3.4 Guard 链 | decision_consistency_gate.py |
| ai-hedge-fund | **信号压缩** | 6.5.4 SignalCompressor | 新设计 |
| ai-hedge-fund | **确定性预填充** | 6.5.6 P1 优先级 | trading_analysts.py |
| 学术论文 | 三层状态架构 | 3.8.5 + 5.1 记忆系统 | 新设计 |
| 学术论文 | 结果禁令 (前瞻偏差防护) | 5.3 EpisodicMemory | 新设计 |
| AWS 金融 | 混合同步/异步通信 | 4.4 消息通信模式 | event_bus.py |
| Axon Trade | 延迟预算管理 | 3.8.6 延迟分配表 | 新设计 |

### 9.3 关键风险

1. **full_auto_trading_service.py 的 9609 行是所有问题的根源** — 任何架构升级都
   受限于这个单文件的复杂度。Phase 0 的超时修复是最小改动最大收益。
2. **LLM 调用仍是最大瓶颈** — 双模型策略 (quick/deep) 可将 90% tick 的 LLM 延迟
   从 30-60s 降至 <5s, 但 MasterController 的 deep 调用仍需 30-60s。
   需要考虑: LLM 调用异步化 (先执行规则策略, LLM 结果到达后更新决策)
3. **SQLite 在高并发写入下的限制** — WAL 模式 + busy_timeout=120s 可以缓解,
   但如果 5 个 Agent 同时写日志, 仍可能出现竞争。数据库分层 + 异步写入队列是必须的。
4. **结果禁令实现复杂度** — Layer C 的情景记忆需要在检索时动态过滤 outcome 字段,
   这要求每个 Episode 有精确的时间戳标记和延迟窗口 (k) 参数。
5. **跨系统 Prompt 一致性迁移风险** — ai_decision_service.py 和 trading_analysts.py
   使用不同的概念模型 (置信度 0.0-1.0 vs 0-100, 操作集不同), 统一 Schema 时需要
   同时修改两处 Prompt 的下游消费者, 回归测试范围较大。

### 9.4 推荐实施优先级

```
1. [紧急] Phase 0: 给现有串行步骤加独立超时 → 系统从不可用变为可用
2. [重要] 双模型策略: Agent Card 增加 LLMLevel → 90% tick 延迟降至 <5s
3. [重要] 数据库拆分: alpha_market.db 独立 → 降低写入竞争
4. [重要] Agent Card + EventBus 扩展 → 架构基础
5. [重要] 结构化信号: 统一 AgentSignal 格式 → 消除模糊传递
6. [重要] Prompt P0: 统一 TradingDecision Schema + Schema-as-Prompt → 输出可靠性
7. [重要] Prompt P1: SignalCompressor + XML 数据分隔 → MC prompt 缩短 30-50%
8. [渐进] 逐个迁移 Agent → 低风险验证
9. [渐进] 反思闭环 + 三层状态架构 → 智能增强
10. [渐进] Prompt P2: KlineAnalyst Few-shot + 负面示例 → 质量提升
11. [按需] Redis / 向量数据库 / Agent 监控 → 性能增强
```
