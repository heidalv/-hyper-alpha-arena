# Rebate 返利/积分套利自动化设计

> 对应实现：`backend/services/rebate_arb/`  
> 配置：`backend/config/rebate_arb_config.yaml`  
> API：`/api/rebate/*`  
> 前端：Arbitrage Hub → Points / Config Tab

## 1. 系统定位

Rebate 套利与 V3 统计套利（资金费率/跨所价差）并行运行，目标函数不同：

| 维度 | V3 统计套利 | Rebate 套利 |
|------|------------|-------------|
| 收益来源 | 资金费率、价差收敛 | 返佣、积分、Rh 激励 |
| 策略 | funding / cross / basis | S1–S8 |
| 资金池 | `funding_rate_arb` + `cross_exchange_spread` | `rebate_points_arb` |
| 自动执行 | FullAuto tick（需 `arb_enabled` + `FUNDING_ARB_ENABLED`） | tick 扫描；开仓需 `engine.auto_execute=true` |

两套系统通过 `GlobalCapitalCoordinator` 共享全局资金池，防止超额分配。

## 2. 策略矩阵 S1–S8

| ID | 名称 | 默认 | 说明 |
|----|------|------|------|
| S1 | Maker 对冲 | **关** | Asterdex Maker + Binance 对冲；纯费率差通常为负 EV |
| S2 | VIP 冲刺 | 关 | 需 >50k U |
| S3 | 积分挖矿 | 开 | 核心积分策略 |
| S4 | 活动套利 | 关 | 需大资金 |
| S5 | 资金费率+积分 | 关 | 需稳定持仓 |
| S6 | 跨所费率差 | 开 | 与 S8 组合 |
| S7 | Binance Alpha | 关 | monitor_only，API/规则变更 |
| S8 | Asterdex Rh | 开 | P0 最高优先级 |

## 3. 运行流程

```
FullAuto 90s tick (arb_enabled=true)
  └─ _run_rebate_arb_tick
       ├─ capital_coordinator.update_equity
       ├─ 采集各所 incentive_data + funding_rates
       ├─ rebate_arb_engine.scan_all_strategies → evaluations
       ├─ [auto_execute=true] execute_strategy(最优 viable)
       ├─ position_monitor.check_exits → close_position
       └─ S8 check_and_advance_hold_phases
```

## 4. 风控（10 条）

见 `backend/services/rebate_arb/risk_gate.py`：

- 日/周刷量上限
- wash trade 检测（`wash_trade_avoider.py`）
- 单所敞口 / 总 rebate 敞口
- 期望价值比
- 活动截止预警
- 日亏损熔断
- 费率突变告警

## 5. 数据库表

| 表 | 用途 |
|----|------|
| `rebate_positions` | 仓位生命周期 |
| `rebate_orders` | 订单记录 |
| `rebate_incentive_snapshots` | 激励数据快照 |
| `rebate_performance_logs` | 绩效日志 |

## 6. 配置说明（300U 小资金方案）

```yaml
engine:
  paper_mode: true
  auto_execute: false    # 自动开仓开关，默认关
  max_position_usd: 300

capital_allocation:
  funding_rate_arb: 0.10
  cross_exchange_spread: 0.20
  rebate_points_arb: 0.60
  emergency_reserve: 0.10
```

环境变量覆盖：`REBATE_ARB_<SECTION>_<KEY>`

## 7. 手动/API 触发

```bash
# 扫描
GET /api/rebate/opportunities

# 执行
POST /api/rebate/execute
{"strategy_type": "S8", "size_usd": 100, "symbol": "ETH"}

# 平仓
POST /api/rebate/close/{position_id}
```

## 8. 与 V3 套利的关系

- 共享 `ExchangeManager` 和各所 adapter
- 共享 `GlobalCapitalCoordinator` 资金互斥
- **不共享**仓位表（V3 用 `arbitrage_positions`，Rebate 用 `rebate_positions`）
- FullAuto 同一 tick 内并行运行，互不阻塞

## 9. 上线检查清单

- [ ] `paper_mode: true` 跑通 2 周
- [ ] 各所 API 凭证配置完成
- [ ] S1 仅在 Rh/积分补偿验证为正 EV 后启用
- [ ] `auto_execute` 先 false，手动验证后再开
- [ ] 监控 Rebate Hub 仓位 / 积分 / 事件日志
