# Agent 架构重设计 — 可执行技术方案

> **状态**：部分已实施；Swing 集成与证据链/Hermes 闭环见升级版文档  
> **日期**：2026-06-18（架构初稿）  
> **升级版设计（2026-06-27）**：[MID_LONG_AGENT_UPGRADE_DESIGN_2026-06-27.md](./MID_LONG_AGENT_UPGRADE_DESIGN_2026-06-27.md) — 含问题诊断、盈利评估、证据链、Hermes 正向闭环、分阶段路线图  
> **原则**：短线因子驱动（0ms LLM）、中线 AI 波段（快模型）、趋势 AI 深度（TrendAgent 已上线；SwingAgent 待 Phase 0–2 升级）

**本文档 superseded 范围**：TrendAgent「待执行」描述（已实现）；Swing/Hermes/证据链部分请以升级版文档为准。

---

## 1. 架构总览

```
                     tick 触发（90s 间隔）
                          │
                 ┌────────┴────────┐
                 │  market_data 采集 │
                 └────────┬────────┘
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
         短线层(scalp)  中线层(swing)  趋势层(trend)
         因子信号引擎   SwingAgent    TrendAgent ✅
         (<100ms)      (快LLM 5-15s) (深LLM 15-40s)
              │           │           │
              ▼           ▼           ▼
         FeeGuard      V5Gate       V5Gate
              │           │           │
              └─────┬─────┴─────┬─────┘
                    ▼           ▼
              paper_engine.place_order
              (统一撮合 + 硬SL/TP监控)
```

三层独立：各层有独立的信号源、Agent、prompt、模型、资金预算。层间不互相阻塞。

---

## 2. 现有基础设施（复用，不重写）

| 现有模块 | 文件 | 角色 |
|---|---|---|
| 因子引擎（20+文件） | `factor_engine/` | 短线层信号来源，已有8类因子 |
| 订单流数据 | `market_flow_*.py` | 因子引擎微观结构输入 |
| 衍生品数据 | `derivatives_analytics_service.py` | 资金费率/OI因子输入 |
| TrendAgent | `trend_agent.py` ✅ | 趋势层核心，已实现 |
| V5 unified_gate | `decision_core/unified_gate.py` | 所有层硬安全网 |
| PositionExitOrchestrator | `position_exit_orchestrator.py` | 硬规则平仓 |
| FeeGuard | `fee_guard.py` | 短线手续费守卫 |
| NatureStagedTp | `nature_staged_tp.py` | 趋势分批止盈 |

因子引擎不需从零写——现有 `factor_engine/` 已有 8 类因子、计算器、注册表、权重管理。短线层只需接入现有输出 + 加决策层。

---

## 3. 新建模块

### 3.1 短线因子路由器 `backend/services/scalp_factor_router.py`

替代 DirectionAgent 对 scalp/intraday 的处理。从因子引擎取信号，纯规则决策。

```python
class ScalpFactorRouter:
    def evaluate(self, symbol: str, market_data: dict) -> ScalpSignal:
        # 1. 从现有 factor_signal_generator 取复合因子信号
        # 2. 加微观结构过滤（CVD/taker imbalance）
        # 3. FeeGuard 手续费守卫
        # 4. 按阈值决策（不调 LLM）
        #    score >= 70 → 直通执行
        #    score 50-70 → 可选 LLM 确认（SCALP_USE_LLM_CONFIRM 开关）
        #    score < 50  → hold
```

配置（settings.py 新增）：
```python
SCALP_FACTOR_EXECUTE_THRESHOLD = 70   # 直通阈值
SCALP_FACTOR_CONFIRM_THRESHOLD = 50   # 确认阈值
SCALP_USE_LLM_CONFIRM = False         # 是否用 LLM 确认模糊区间
SCALP_DAILY_MAX_TRADES = 15           # 日上限
```

### 3.2 中线波段 Agent `backend/services/swing_agent.py`

替代 DirectionAgent 对 swing 的处理。独立 prompt，1h/4h 尺度，快 LLM。

```python
class SwingAgent:
    def analyze(self, *, symbol, reports, market_envs, account_id) -> SwingDecision:
        # 独立 prompt（1h/4h K线，等回调，盈亏比≥2:1）
        # 快模型（DeepSeek-V3 / GPT-4o），5-15s 延迟
```

prompt 核心（独立 system prompt）：
- 只关注 1h/4h，不看 5m 噪声，不看 1d 长线
- 只在回调到支撑位 + 趋势顺势时入场
- 盈亏比 ≥ 2:1，持仓 2-8h，不过夜

### 3.3 资金分层管理 `backend/services/layer_budget_manager.py`

三层独立预算，防层间挤兑。

```python
class LayerBudgetManager:
    LAYER_ALLOCATIONS = {"scalp": 0.20, "swing": 0.40, "trend": 0.40}

    def get_layer_budget(self, layer, total_equity) -> float:
        # 总权益 × 分配比例 - 该层已用保证金

    def can_open(self, layer, required_margin, total_equity) -> bool:
        # 该层预算是否够开这仓
```

---

## 4. 改造现有模块

### 4.0 QAA 兼容性（关键约束）

当前系统 `QAA_MODE=qaa` + `QAA_V3_ENABLED=true`，实际走 `_run_qaa_v3_tick`（TickOrchestrator 驱动）。QAA v3 有 6 个 agent handler 通过 event_bus 注册，TickOrchestrator 按顺序调度：

```
QAA TickOrchestrator（不动）
  ├─ market_data handler     （数据采集，不变）
  ├─ risk_control handler    （风控，不变）
  ├─ factor_engine handler   （因子计算，不变——短线层复用输出）
  ├─ intel_signal handler    （情报，不变）
  ├─ mt_orchestrator handler （编排器，不变）
  └─ master_controller handler（最终决策）← 三层路由在这里分流
       ├─ scalp/intraday → ScalpFactorRouter（用 factor_engine handler 的输出）
       ├─ swing          → SwingAgent
       └─ trend_follow   → TrendAgent（已有）
```

**三层路由不改 QAA 的调度结构**——QAA 的 Phase A/B/C 三阶段 session、TickOrchestrator、event_bus、agent 注册全部不动。三层路由只是 master_controller handler（行13364）内部的决策逻辑分流。

同时，三层输出最终都汇入**同一套仓位管理**（paper_engine / sub_position_manager / CORRELATION_BUCKETS / 同向合并+杠杆统一），遵守现有同币种多周期仓位策略。

### 4.1 三层路由接入（两个路径都要改）

**路径 A：QAA v3 模式**（当前生效）— master_controller handler 内部（行13364附近）
**路径 B：非 QAA 模式**（回退路径）— `_execute_master_decisions` 行6486附近

两条路径都加同样的 nature 分流逻辑。

```python
_dec_nature_raw = (dec.get("trade_nature") or "").lower()

if action in ("buy", "sell"):
    if _dec_nature_raw in ("scalp", "intraday"):
        # → ScalpFactorRouter（纯因子，0ms LLM）
    elif _dec_nature_raw in ("swing",):
        # → SwingAgent（快 LLM，独立 prompt）
    elif trend_agent.is_trend_nature(_dec_nature_raw):
        # → TrendAgent（已有，不动）
```

### 4.2 `full_auto_trading_service.py` place_order 前 — 层预算检查

```python
_layer = {"scalp":"scalp","intraday":"scalp","swing":"swing",
          "trend_follow":"trend","position":"trend"}.get(_dec_nature_raw,"swing")
if not layer_budget_manager.can_open(_layer, margin, equity):
    action = "hold"  # 该层预算不足
```

### 4.3 `settings.py` — 7 个新配置项

### 4.4 废弃

| 模块 | 处置 |
|---|---|
| MasterController（1500+行死代码） | 步骤4删 |
| DirectionAgent 对 scalp/swing 的处理 | 步骤1-2验证后废 |

---

## 5. 文件清单

| 操作 | 文件 | 行数 |
|---|---|---|
| 新建 | `backend/services/scalp_factor_router.py` | ~200 |
| 新建 | `backend/services/swing_agent.py` | ~250 |
| 新建 | `backend/services/layer_budget_manager.py` | ~100 |
| 改造 | `backend/services/full_auto_trading_service.py` 行6486+place_order前 | +~60 |
| 改造 | `backend/config/settings.py` | +7项 |
| 新建 | `tests/backend/unit/test_layer_architecture.py` | ~150 |

---

## 6. 实施顺序

| 步骤 | 内容 | 验证标准 |
|---|---|---|
| 1 | scalp_factor_router + 接入 | scalp 延迟<100ms，不调 DirectionAgent |
| 2 | swing_agent + 接入 | swing 独立 prompt，盈亏比≥2:1 |
| 3 | layer_budget_manager + 开仓前检查 | 三层资金独立 |
| 4 | 废弃 MasterController + DirectionAgent 清理 | -1500行，无回归 |
| 5 | 全量测试 + 模拟盘24h | LLM调用降60%，短线延迟降 |

---

## 7. 安全机制（全部保留不变）

V5Gate / 硬SL-TP监控 / FeeGuard / NatureStagedTp / CORRELATION_BUCKETS / 单笔最大亏损 —— 所有层共用，不改。新增 layer_budget_manager 为层间隔离。

---

## 8. 待确认问题

1. 因子引擎更新频率是否够快（需<1min）？不够则短线回退到因子+可选LLM。
2. SwingAgent 是否需要持仓复查？建议初期不加，靠TP/SL。
3. 因子权重滚动学习？初期固定权重，7天后决定。
4. DirectionAgent 废弃时机？步骤1-2验证后再废。
