# Rebate 套利 QAA Agent 规格

> 域 ID: `rebate_arb` | 插件: `qaa/domains/rebate_arb/plugin.py`  
> 执行权威: `ExecutionAuthority.route_qaa_rebate_executor`（source=qaa）

## Agent 拓扑（10 Agent）

| agent_id | LLM | 职责 |
|----------|-----|------|
| rebate_incentive_collector | NONE | 激励/campaign 采集 |
| rebate_scanner | NONE | S1–S8 扫描 |
| rebate_wash_guard | NONE | 刷量检测 |
| rebate_risk | NONE | 10 条风控 |
| rebate_strategy_coordinator | NONE | 多策略评分、互斥组、选队列 |
| rebate_strategy_analyst | DEEP | 分策略 AI（S8 方向/选币、S5 费率过滤） |
| rebate_execution_planner | QUICK | 仓位/杠杆/execute_now |
| rebate_decider | QUICK | 兼容 alias，委托 coordinator |
| rebate_executor | NONE | 8 策略 action → ExecutionAuthority |
| rebate_monitor | NONE | 仓位/积分/hold_phase |

## S1–S8 → executor action

| 策略 | action |
|------|--------|
| S1 | execute_maker_hedge |
| S2 | execute_vip_sprint |
| S3 | execute_points_mining |
| S4 | execute_campaign |
| S5 | execute_funding_points |
| S6 | execute_cross_fee |
| S7 | （禁止 execute） |
| S8 | execute_asterdex_rh |

## Router market_state

- `idle` / `collecting` / `opportunity` / `directional_opportunity` / `hedge_opportunity` / `volume_program` / `active` / `vip_sprint` / `alert`

## QAARequest payload（rebate 域）

```python
{
  "opportunities": [...],
  "enabled_strategies": ["S3", "S8"],
  "trader_profile_id": 1,
  "account_equity": 300.0,
  "auto_execute": True,
}
```

## reduce_outputs 决策

```python
{
  "action": "execute|hold|close|executed",
  "strategy_id": "S8",
  "executor_action": "execute_asterdex_rh",
  "size_usd": 45.0,
}
```

## Tick 路径

```
run_qaa_rebate_tick()
  → TickOrchestrator.run_tick(domain="rebate_arb")
  → RebateArbRouter.route()
  → handlers → rebate_executor → ExecutionAuthority
```

fallback: `QAA_V3_ENABLED=false` → `ExecutionAuthority.run_rebate_tick` 直连。
