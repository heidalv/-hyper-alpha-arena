# S8 短线 + 大方向过滤

实现: `macro_direction_filter.py` + `rebate_strategy_analyst` handler

## 三层门控

| 层 | Tool | 动作 |
|----|------|------|
| L0 | MultiTimeframeOrchestrator | 逆势 conf≥0.3 → skip；同向 +10% confidence |
| L1 | IntelligenceSignalEngine | danger → skip |
| L2 | ShortTermTactician | long_only 禁空；弱信号缩仓 |
| L3 | arb_llm_planner DEEP+QUICK | execute_now 否决 |

## 约束

- Rh 持仓 ≥60min 不变
- 不做 scalp / 中长线 tier 开仓
- 大方向仅过滤，不决定持仓周期
