# Mid/Long Execution Lane

> **2026-07-04 默认路径** — 详见 [MID_LONG_STRATEGY_DESIGN_AND_FEASIBILITY_2026-07-04.md](./MID_LONG_STRATEGY_DESIGN_AND_FEASIBILITY_2026-07-04.md)

## 默认链路（Agent Fast Lane）

```
MidLongAgent独立循环 (45s) / 主循环 Execute
    → SwingAgent.analyze() / TrendAgent.analyze_direction()
    → MTF 共振融合 + Regime 前置
    → evaluate_midlong_open()  【单点组合门控】
        DCP + V5 + Regime size + Orch soft + MC tail + 周限额
    → _execute_paper_trade (size_multiplier 缩仓)
    → PEO / trend_review
    → unified_learning
```

MLTO 仅 thesis 面板/学习，`MIDLONG_MLTO_CONTROLS_EXEC=false` 时不控开单。

## Legacy 链路（可选）

仅 `MIDLONG_MLTO_CONTROLS_EXEC=true` 时：

```
Fix18 stub → _execute_mlto_lane → Hub/open_gate → V5 → place_order
```

## Feature Flags（2026-07-04）

| Flag | 默认 | 说明 |
|------|------|------|
| `MIDLONG_MLTO_CONTROLS_EXEC` | **false** | Agent 直控 |
| `MIDLONG_THESIS_OPEN_GATE` | **false** | 跳过 Hub readiness 门 |
| `MIDLONG_AI_MANDATORY` | **true** | 每 tick 必跑 Swing/Trend LLM |
| `MIDLONG_AGENT_INDEPENDENT_SCHEDULER` | **true** | 与 Scalp 同级独立循环 |
| `ORCHESTRATOR_HARD_GATE` | **false** | wait/frozen → 缩仓不 block |
| `AGENT_FACT_GUARD_PAPER_ENFORCE` | **false** | shadow 审计 |
| `MIDLONG_QUANT_BRIEF_HARD_GATE` | **false** | QuantBrief 软证据 |
| `MIDLONG_PERSISTENCE_TICKS` | **1** | 1-tick 即放行 |
| `TIER_MID_AI_TICK_SEC` | **45** | 中线 tick |
| `TIER_LONG_AI_TICK_SEC` | **90** | 长线 tick |
| `TREND_MAX_OPENS_PER_WEEK` | **2** | 长线周开单上限 |
| `MIDLONG_MONTE_CARLO_ENABLED` | **true** | MC tail 缩仓 |
| `LAYER_BUDGET_SCALP/SWING/TREND` | **0.40/0.45/0.15** | 三层资金 |

## 日志关键字

| 关键字 | 含义 |
|--------|------|
| `[MidLongAgent独立]` | 独立循环 tick |
| `[SwingAgent独立]` / `[TrendAgent独立]` | Agent 分析 |
| `[AgentFastLane]` | 主循环 Fast Lane 开单 |
| `[V5Gate] PASS/BLOCK` | 组合门控结果 |
| `[MonteCarlo]` | tail 缩仓 |
| `[LongWeeklyCap]` | 长线周限额 |

## 验收

```bash
backend\.venv\Scripts\python.exe scripts/verify_three_cycle_strategy.py --no-live
backend\.venv\Scripts\python.exe scripts/phase0_tier_stats.py
```
