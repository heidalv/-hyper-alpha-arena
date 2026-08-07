# 跨策略协调规格

实现: `rebate_strategy_coordinator` Agent + `strategy_coordinator.py`

## 互斥组

| 组 | 策略 | 规则 |
|----|------|------|
| hedge_mutex | S1, S6 | 同 tick 只选一个 |
| directional_mutex | S5, S8 | Asterdex/HL 方向仓与 hedge 互斥 |
| volume_program | S2, S4 | wash_score 高时暂停 |

## 多目标评分

```
score = 0.5*monthly_ev_norm + 0.2*points_urgency + 0.2*wash_headroom - 0.1*direction_risk
```

## 资金子池

`rebate_points_arb` 内按 YAML 可配 S1–S8 子配额（默认均分）。
