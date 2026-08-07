# 编排器 (Orchestrator) vs 总控 (MasterController) — 指挥链说明

## 角色

| 组件 | 职责 | 输出 |
|------|------|------|
| **编排器** `mt_orchestrator` | 多周期方向评估（长/中/短），检测方向矛盾 | 每个symbol: `frozen` / `wait` / `enter` / `reduce` / `exit` |
| **总控** `MasterController` | 综合六路分析师报告 + LLM 决策 | 每个symbol: `hold` / `buy` / `sell` / `pyramid` / `dca` / `reduce` / `close` |

## 两种门控模式

### 模式 A：软注入（默认，ORCHESTRATOR_HARD_GATE=false）

- 编排器结果注入到 `market_summary[sym]["orchestrator"]`，作为 prompt 的一个数据源
- 总控 LLM 看到编排器建议后自行判断，可以选择忽略
- 编排器 `frozen` / `wait` 仅影响**策略创建/暂停**（`_run_health_check` 中），不影响总控对现有仓位的决策

### 模式 B：硬门控（ORCHESTRATOR_HARD_GATE=true）

- 在模式 A 的基础上，当编排器输出 `frozen` 或 `wait` 时：
  - `_execute_master_decisions` 中 **硬拦截** buy / sell 开仓操作
  - 记录 `orchestrator_gate_block` 事件
  - close / reduce / hold 不受影响（仓位管理不受编排器限制）
- 适用于：市场极端波动时由编排器强制阻止新仓

## 配置

```bash
# 启用硬门控
ORCHESTRATOR_HARD_GATE=true

# 关闭（默认）
ORCHESTRATOR_HARD_GATE=false
```

## 编排器对策略生命周期的影响（始终生效）

无论硬门控是否开启，编排器始终影响：
1. **策略创建**：方向矛盾的 symbol 不会创建新策略
2. **策略暂停**：`frozen` 状态的策略会被暂停
3. **策略恢复**：方向一致后自动恢复

## 总控决策优先级

1. **硬风控**（risk_score > 80）→ 拦截所有开仓
2. **编排器硬门控**（如果启用）→ 拦截 frozen/wait 的开仓
3. **敞口限制**（同向 > 40%）→ 拦截开仓
4. **置信度门槛**（< entry_threshold）→ 拦截开仓
5. **总控 LLM 判断** → 最终决策

## Scalp 执行车道（ScalpExecutionLane）

- **short 槽 / scalp / intraday 新开仓**：仅由 `ScalpExecutionLane`（`_run_scalp_independent` + `ScalpExecutionGate`）负责
- **Master 路径**：`SCALP_MASTER_HARD_BLOCK=true` 时对 scalp/intraday 的 buy/sell 硬跳过
- **编排器**：OrchBG 每 10min 写 `ScalpAdvisoryCache`，供规则门只读；不阻塞 tick
- 详见 [`SCALP_EXECUTION_LANE.md`](SCALP_EXECUTION_LANE.md)
