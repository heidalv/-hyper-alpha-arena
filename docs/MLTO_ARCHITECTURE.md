# MLTO 架构说明

> **执行模型（2026-07-04）**：默认 `MIDLONG_MLTO_CONTROLS_EXEC=false` — MLTO **不控开单**，仅 thesis 面板 + 学习。开单由 SwingAgent / TrendAgent 独立 LLM 直控。详见 [MID_LONG_STRATEGY_DESIGN_AND_FEASIBILITY_2026-07-04.md](./MID_LONG_STRATEGY_DESIGN_AND_FEASIBILITY_2026-07-04.md)。

**MidLong Thesis Orchestrator（MLTO）** 将中线/长线决策从「单次 LLM 开单」升级为「每 tick 更新 thesis + 数学层开单门控」（**可选模式**，需显式开启 `MIDLONG_MLTO_CONTROLS_EXEC=true`）。

## 设计原则

| 原则 | 说明 |
|------|------|
| LLM 只更新 thesis | Swing/Trend Agent 以 `thesis_update` 模式输出 direction / conviction / summary，不直接 buy/sell（MLTO 控 exec 时） |
| 开单由 Decision Hub 决定 | 量化信号 + OWM 权重 + 一致性惩罚 → `open_readiness`（**仅 MLTO 控 exec 时**） |
| Agent 直控（默认） | `MIDLONG_MLTO_CONTROLS_EXEC=false` 时 Swing/Trend 独立 analyze → V5 → place_order |
| QuantBrief 软证据 | `MIDLONG_QUANT_BRIEF_HARD_GATE=false`（默认），alignment 写入 ingest，不再跳过 LLM |
| thesis_id 贯穿执行 | envelope → place_order → TradeOutcome → unified_learning |

## 数据流

```
OrchBG / QuantBrief / PreScreener / analyst_reports
    → evidence_ingest.ingest_tick
    → layered_memory (FinMem γ 检索)
    → qual_layer.update_thesis (Swing/Trend LLM)
    → thesis_store.apply_llm_update
    → quant_layer + debate_layer (灰区)
    → decision_hub.fuse_signals
    → open_gate.allow + tranche_gate
    → Execute place_order
    → 平仓 → learning_bridge.record_outcome (OWM + postmortem)
```

## 数据库表（Analytics DB）

| 表 | 用途 |
|----|------|
| `mlto_thesis` | 主账本 session+symbol+tier |
| `mlto_memory_event` | 分层记忆事件 |
| `mlto_thesis_event` | Audit 时间线 |
| `mlto_signal_weight` | OWM 证据源权重 |
| `mlto_debate_log` | 灰区辩论记录 |

## Feature Flags

| Flag | 默认（2026-07-04） | 说明 |
|------|-------------------|------|
| `MIDLONG_MLTO_CONTROLS_EXEC` | **false** | false=Agent 直控；true=Execute 走 `_execute_mlto_lane` |
| `MIDLONG_THESIS_LEDGER_ENABLED` | true | thesis 更新 + 面板（可与 exec=false 并存） |
| `MIDLONG_QUANT_BRIEF_HARD_GATE` | **false** | true=旧行为：alignment 低则跳过 LLM |
| `MIDLONG_THESIS_OPEN_GATE` | **false** | true=open_readiness / stable / reviews 门控 |
| `MIDLONG_THESIS_DEBATE_ENABLED` | true | 灰区 Bull/Bear 辩论 |
| `MIDLONG_TRANCHE_ENTRY_ENABLED` | true | 分批建仓 |
| `MIDLONG_THESIS_REGIME_RESET` | true | regime 变化时 thesis 降权重置 |
| `MIDLONG_OPEN_READINESS_MIN_MID` | **25**（paper 均衡） | 中线最低就绪度（open_gate 启用时） |
| `MIDLONG_OPEN_READINESS_MIN_LONG` | **28**（paper 均衡） | 长线最低就绪度（open_gate 启用时） |
| `MIDLONG_THESIS_MIN_REVIEWS` | 3 | 最少复核 tick 数 |

## API

- `GET /api/mlto/sessions/{session_id}/thesis/summary` — 全会话 thesis 列表 + 学习指标
- `GET /api/mlto/sessions/{session_id}/thesis?symbol=&tier=` — 单币详情 + 记忆时间线

## 前端

- `MidLongThesisPanel.tsx` — FullAuto 面板内展示研判账本与证据链
- 决策日志：`[MLTO]` 前缀替代「跳过 Trend LLM」

## 验收

```bash
backend\.venv\Scripts\python.exe scripts/verify_midlong_thesis_chain.py
backend\.venv\Scripts\python.exe scripts/mlto_design_audit.py
backend\.venv\Scripts\python.exe scripts/mid_long_agent_acceptance_check.py
backend\.venv\Scripts\python.exe -m pytest tests/backend/unit/test_mlto_chain.py -q
backend\.venv\Scripts\python.exe scripts/verify_midlong_chain.py
```

## 回滚

1. `MIDLONG_THESIS_LEDGER_ENABLED=false` — 恢复 legacy Swing/Trend 单次 LLM
2. `MIDLONG_QUANT_BRIEF_HARD_GATE=true` — 恢复 alignment 硬 gate（不推荐）

## 参考对标

- FinMem：分层记忆 + γ 检索
- ThesisAgent：thesis + Deterministic Hub
- TradingAgents：灰区辩论
- zjzJoez：audit 链 + 学习闭环
