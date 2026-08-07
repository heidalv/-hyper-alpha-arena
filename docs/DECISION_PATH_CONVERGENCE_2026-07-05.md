# 决策路径收敛（P0）— 2026-07-05

## 背景

三周期架构下，mid/long 同时存在两条执行路径：

| 路径 | 触发 | 问题 |
|------|------|------|
| **Master 主循环** | `_execute_master_decisions` → Swing/Trend LLM | 与独立循环重复调 LLM、可能双开 |
| **MidLongAgent 独立循环** | `_run_midlong_independent` → `_maintain_mlto_theses_for_session(light_context=True)` | 设计上的唯一 mid/long 执行入口 |

短线已有对称方案：`SCALP_MASTER_HARD_BLOCK=true` 时 Master 不碰 scalp 新开，由 `ScalpExecutionLane` 独占。

**P0 目标：** 为 mid/long 建立同样的「Master 委托 + 独立循环独占新开」，**不加新门**，只去掉重复路径。

---

## 实现（已落地）

### 1. 配置项

| 变量 | 默认 | 说明 |
|------|------|------|
| `MIDLONG_AGENT_INDEPENDENT_SCHEDULER` | true | 独立 mid/long 调度循环 |
| `MIDLONG_MASTER_DELEGATE` | true | Master 路径跳过 mid/long **新开**（buy/sell/pyramid/dca） |

关闭委托（回滚）：`MIDLONG_MASTER_DELEGATE=false` — Master 恢复 mid/long 新开，但可能与独立循环重复。

### 2. 代码改动（`full_auto_trading_service.py`）

```
Master 决策循环
  ├─ MidLongExecutionLane（新增）
  │    MIDLONG_MASTER_DELEGATE && swing/trend nature
  │    && action ∈ {buy,sell,pyramid,dca} → continue（跳过）
  ├─ ScalpExecutionLane（已有）
  │    SCALP_MASTER_HARD_BLOCK && scalp → continue
  ├─ _skip_agent_llm（增强）
  │    独立调度开启时 mid/long 不再在主循环调 Swing/Trend LLM
  └─ 主循环末尾 _maintain_mlto_theses_for_session
       run_mid=False, run_long=False（独立调度开启时）
```

**平仓/减仓不受影响：** 委托块仅拦截 `buy/sell/pyramid/dca`，close/reduce 仍走 Master 或持仓管理路径。

### 3. 指挥链（收敛后）

| 层级 | Short | Mid | Long |
|------|-------|-----|------|
| 规则/编排 | OrchBG → ScalpAdvisory | OrchBG slot → mid_bias | OrchBG → long_bias |
| AI 新开 | ScalpExecutionLane | **MidLongAgent 独立** | **MidLongAgent 独立** |
| Master | hard_block 新开 | **delegate 新开** | **delegate 新开** |
| 执行 | paper_engine / V5 | evaluate_midlong_open → paper | 同上 |
| Thesis | — | MLTO（独立循环内 light_context） | MLTO |

---

## 验证

### 静态自检

```bash
cd 001Alpha/Hyper-Alpha-Arena
python scripts/verify_three_cycle_strategy.py --no-live
```

期望新增 PASS：`Master 委托 mid/long 新开给独立循环`。

### 运行时 grep

```bash
# 独立循环在跑
grep "MidLongAgent独立" logs/backend.log | tail -20

# Master 委托跳过（debug 级）
grep "MidLongLane" logs/backend.log | tail -20

# 不应在同一 tick 同一 symbol 出现 Master 路径 Swing 开单 + 独立路径开单
grep -E "SwingAgent独立|TrendAgent独立" logs/backend.log | tail -30
```

### 72h 验收（Paper）

| 指标 | 标准 |
|------|------|
| mid 成交笔数 | ≥ 3 |
| long 成交笔数 | ≥ 1 |
| 双开重复 | 同 symbol+tier 同分钟无两笔独立+Master 新开 |

---

## P1：Live 宪法级风控（已落地）

Paper 继续「减门、block→scale、Probe 试单」；**Live 模式**在现有 `risk_control_service` 上补全硬拦截，不新增业务门。

### 配置

| 变量 | 默认 | 说明 |
|------|------|------|
| `LIVE_CONSTITUTIONAL_RISK_ENABLED` | true | Live 开单前 + 会话 tick 强制宪法风控 |

### 已接线

| 检查点 | 文件 | 行为 |
|--------|------|------|
| 开单前 | `_execute_live_trade` | `_live_constitutional_pre_trade_check` → `check_risk_before_trade` |
| 独立 Agent Live | `_try_execute_independent_agent_open` | `mode=live` 无 PaperAgentProbe；V5 通过后宪法风控再 `_execute_live_trade` |
| 会话 tick | 主循环 §4.6 | Live 走 `_check_live_constitutional_session_risk`（日亏熔断 → defensive） |
| Paper | 不变 | `_paper_loss_locks_disabled` / PaperAgentProbe 仍有效 |

### Live 部署建议

```env
TRADING_MODE=live
LIVE_CONSTITUTIONAL_RISK_ENABLED=true
MIDLONG_MASTER_DELEGATE=true
SCALP_MASTER_HARD_BLOCK=true
```

### 验证 grep

```bash
grep "LiveConstitutional\|Live宪法" logs/backend.log | tail -20
python scripts/verify_three_cycle_strategy.py --no-live
```

---

## P1（历史设计稿，供对照）

### 已有能力

- `check_risk_before_trade()` — 日亏熔断、单币限额、总仓位、保证金
- `check_strategy_consecutive_losses()` — 策略连亏（已在 full_auto ~L14634 部分接入）

### 待接线（Live only）— 已由上方「已接线」替代

| 检查点 | 文件 | 动作 |
|--------|------|------|
| ~~开单前~~ | ~~`_execute_live_trade`~~ | ✅ 已实现 |
| ~~Paper 探针~~ | ~~`_try_execute_independent_agent_open`~~ | ✅ Live 不走 Probe |
| ~~日亏熔断~~ | ~~session tick~~ | ✅ `_check_live_constitutional_session_risk` |

### 环境变量建议（Live 部署时）

```env
TRADING_MODE=live
AGENT_FACT_GUARD_PAPER_ENFORCE=false   # Live 用 enforce 版 FactGuard（若已拆分）
MIDLONG_MASTER_DELEGATE=true
SCALP_MASTER_HARD_BLOCK=true
```

---

## 与主流 AI 交易系统对齐说明

| 维度 | 收敛前 | 收敛后 |
|------|--------|--------|
| 决策链宽度 | Master + 双 mid/long 路径 | 每 tier 单入口 |
| LLM 成本 | mid/long 可能双调 | 独立循环单次 |
| 可观测性 | 难归因开单来源 | `[MidLongLane]` / `[MidLongAgent独立]` 分离 |
| 风控 | Paper 减门 / Live 未统一 | P1 文档化 Live 宪法 |

---

## 相关文档

- [SCALP_EXECUTION_LANE.md](./SCALP_EXECUTION_LANE.md)
- [MID_LONG_STRATEGY_DESIGN_AND_FEASIBILITY_2026-07-04.md](./MID_LONG_STRATEGY_DESIGN_AND_FEASIBILITY_2026-07-04.md)
- [ORCHESTRATOR_VS_MASTER.md](./ORCHESTRATOR_VS_MASTER.md)
