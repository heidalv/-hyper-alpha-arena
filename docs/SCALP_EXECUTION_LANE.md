# Scalp Execution Lane — 规则快开 + AI 后台参谋 + 分层 5s Veto

## 指挥链

| 层级 | 组件 | Scalp | Mid/Long |
|------|------|-------|----------|
| 规则层 | `mt_orchestrator` | 软约束 → `ScalpAdvisoryCache` | slot_actions → Swing/Trend |
| AI 层 | MasterController | **hard_block**（不开 scalp） | 统筹六路分析师 |
| 执行层 | ScalpExecutionLane | **唯一 scalp 开仓入口** | V5 + paper_engine |
| 后台 | OrchBG | 每 10min 刷新 advisory | long/short bias 供 Master |

**short 槽 = ScalpExecutionLane 自治**，读同一套 orchestrator cache，不再由 Master 热路径驱动。

## 热路径（<1s）

```
ScalpFactorRouter → ScalpExecutionGate → [FlashVeto 35-44] → paper_engine → async review
                         ↑ 只读
                  ScalpAdvisoryCache ← OrchBG + StructureScanner
```

### 分层门槛

| 分数 | 行为 |
|------|------|
| ≥ 45 | 直通，不调 LLM |
| 35–44 | 5s Flash Veto（fail-open） |
| < 35 | 不开仓 |

## 配置（Paper 默认）

| 变量 | 默认 | 说明 |
|------|------|------|
| `SCALP_EXECUTION_LANE_ENABLED` | true | 总开关 |
| `SCALP_DIRECT_THRESHOLD` | 45 | 直通 |
| `SCALP_VETO_BAND_LOW` | 35 | veto 下限 |
| `SCALP_VETO_MODE` | tiered | tiered / off |
| `SCALP_VETO_TIMEOUT_S` | 5 | fail-open 超时 |
| `SCALP_MASTER_HARD_BLOCK` | true | Master 不碰 scalp |
| `ORCH_BG_INTERVAL_SEC` | 600 | 参谋刷新 |
| `SCALP_STRUCTURE_SL_BUFFER_PCT` | 0.008 | swing low 下方缓冲 |

## 模块

- `backend/services/scalp/scalp_advisory_cache.py` — 参谋缓存
- `backend/services/scalp/scalp_structure_scanner.py` — swing/猎杀/regime
- `backend/services/scalp/structure_stop_calculator.py` — 结构 SL
- `backend/services/scalp/scalp_execution_gate.py` — 规则门
- `backend/services/scalp/scalp_flash_veto.py` — 5s tiered veto
- `backend/services/scalp/scalp_agent.py` — 可选慢参谋摘要

## 审计

- 每笔 scalp：`lane_decision_id` = factor → gate → [veto] → paper
- Veto band：`scalp_veto_audit` 表（symbol, score, verdict, latency_ms, source）
- 开单后：`_async_scalp_review`；veto band 开单 2min 内 AI close + 浮亏 → 加速平仓

## 验证

```bash
cd 001Alpha/Hyper-Alpha-Arena
python scripts/verify_scalp_structure_sl.py --symbols BTC JTO ETH
```

日志关键字：
- `[FullAuto][OrchBG] 编排器后台线程启动`
- `[ScalpGate]` / `[ScalpRouter独立] Gate通过`
- Master 路径不应出现 scalp 新开成交
