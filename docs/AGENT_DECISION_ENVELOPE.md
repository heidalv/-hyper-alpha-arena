# AgentDecisionEnvelope

贯穿中线/长线 Agent 开→平仓归因。

## Schema

| 字段 | 说明 |
|------|------|
| `agent_source` | `swing_agent` \| `trend_agent` |
| `lane_decision_id` | UUID |
| `alignment_score` | 0–15（QuantBrief） |
| `cited_fact_ids` | 引用 fact 列表 |
| `evidence_available_ratio` | 证据可用率 |
| `structure_sl_price` / `structure_tp_price` | 结构价 |
| `sl_pct` / `tp_pct` | 百分比 |
| `sl_source` | 如 `midlong_structure_swing_agent` |
| `quant_brief` | MidLongQuantBrief 快照 |
| `orch_snapshot_ts` | 编排器快照时间 |
| `thesis_id` | MLTO 研判账本 ID（mid/long 开仓必填） |
| `hub_composite` / `hub_adjusted` / `consistency` | Decision Hub 数学融合结果 |
| `open_readiness` | 开单就绪度 0–100 |
| `memory_event_ids` | 引用记忆事件 ID 列表 |
| `tranche_stage` | 分批建仓阶段 0–3 |
| `debate_log_id` | 灰区辩论日志 ID（可选） |

## MLTO 贯穿

中线/长线启用 `MIDLONG_THESIS_LEDGER_ENABLED` 时，Execute 通过 `_execute_mlto_lane` 写入上述 MLTO 字段；平仓后 `TradeOutcome.metadata` 携带 `thesis_id` 供 OWM 学习。

详见 [MLTO_ARCHITECTURE.md](./MLTO_ARCHITECTURE.md)。

## 写入点

1. Execute 阶段 `_build_midlong_agent_envelope` → `dec["_agent_envelope"]`
2. `AIDecisionLog.decision_snapshot.agent_source`
3. `paper_engine.place_order(position_metadata=...)`
4. 平仓 `TradeOutcome.metadata` → `unified_learning` / Hermes wisdom

## 模块

- `backend/services/agent_decision_envelope.py`
- `backend/services/mid_long_quant_brief.py`
- `backend/services/mid_long_structure_stop.py`
- `backend/services/position_exit_state.py`（`merge_exit_state`）
