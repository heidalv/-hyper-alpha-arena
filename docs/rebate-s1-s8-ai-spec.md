# S1–S8 AI 策略规格

| ID | 目标 | AI | 方向规则 | 执行模式 | Paper |
|----|------|-----|----------|----------|-------|
| S1 | Maker 返佣对冲 | 规则 EV | fixed_hedge | hedge | 可 |
| S2 | VIP 冲刺 | 可选 DEEP | volume_target | volume_program | 可 |
| S3 | HL 积分 | 无 | fixed_roundtrip | maker_roundtrip | 可 |
| S4 | 活动套利 | 可选 DEEP | campaign_rules | volume_program | 可 |
| S5 | 费率+积分 | 规则+可选 DEEP | funding_rate | directional | 可 |
| S6 | 跨所费率差 | 规则 EV | fixed_hedge | hedge | 可 |
| S7 | Alpha 监控 | 无 | none | monitor_only | 禁止 |
| S8 | Rh 积分 | 必须 DEEP+QUICK | ai_signal | directional | 可 |

## QAA Agent 链

- S1/S6: coordinator → risk → executor
- S2/S4: coordinator → wash_guard → executor
- S3: coordinator → executor → monitor(close)
- S5: coordinator → analyst → executor
- S8: coordinator → analyst(L0–L2) → planner → executor
- S7: monitor → rule_sync_gate

## Paper 验收（300U）

- 绑定交易员 + 双模型 + 策略授权
- validate-start 逐项检查
- 2 周：无静默开单、S8 逆势 skip 可审计
