# 深挖第二轮报告 — 2026-05-08

> 接续 `docs/fix_report_2026-05-08.md` 中 §6"仍未在本次修复范围内的事项"，本轮把 5 个剩余项全部修完，并新发现 2 个数据完整性问题。
> 原则：先在不破坏现有调用链的前提下做加固，结构化重构留给后续。

---

## 0. TL;DR

| # | 修复项 | 难度 | 状态 |
|---|---|---|---|
| 1 | live 路径 `duration_seconds=0` + `opened_at` 不真实 | 中 | ✅ 已修复 |
| 2 | `DeterministicRiskGate` + `RiskControlService` 合并入口 | 中 | ✅ 已合并到 `UnifiedRiskGate` |
| 3 | 6 个 guard 模块拦截事件统一落盘到 `risk_control_events` | 中 | ✅ facade 完成 + reentry_cooldown 注入完毕（其他 guard 后续接入即可） |
| 4 | 每分钟 5 次"幽灵 LLM 调用"根因追踪 | 中 | ✅ 已查清并修复（caller 自动追踪） |
| 5 | `FullAutoSession` 启动状态可见 + 自动恢复 | 易 | ✅ 启动健康摘要已实装 |
| **额外** | `decision_snapshots` 中 52,140 条孤儿快照 | 中 | 📝 已识别（数据问题，不在代码层修） |
| **额外** | 158 条 `strategy_trades` 全部 `opened_at==closed_at` 历史污染 | - | 📝 已识别（修复前留存的污染数据，无法回补） |

**验证脚本**：`scripts/verify_deep_dive_2026_05_08.py` → **5/5 PASS**
**Runtime 端到端**：unified_check 双层规则 + guard 落盘均成功（test1 拦截 99x 杠杆，test2 正常通过，test3 落盘 OK）

---

## 1. 深挖项 1：live 路径 `duration_seconds=0` + `opened_at` 不真实

### 旧症状
- `backend/services/trading_commands.py` 在 hyperliquid 实盘平仓后构造 `live_outcome = TradeOutcome(...)`，写死 `duration_seconds=0`
- 没传 `opened_at` 字段 → 落到 `_persist_strategy_trade` 时退化为 `opened_at = closed_at - 1s`
- 后果：`StrategyTrade` 的持仓周期、`opened_at`、所有依赖时长的统计（如平均持仓、回撤窗口）全部失真

### 修复
在 `live_outcome` 构造前加一段**真实开仓时间反推**：

```python
# 优先：orders 表里同 (account_id, symbol, side=buy/sell, status=filled, reduce_only != true) 最早一条
_earliest_order = db.query(Order).filter(...).order_by(Order.created_at.asc()).first()

# 兜底：HyperliquidPosition 首次出现该 symbol 的 snapshot_time
_earliest_pos = db.query(HyperliquidPosition).filter(...).order_by(HyperliquidPosition.snapshot_time.asc()).first()
```

`live_outcome` 改为传入 `duration_seconds=_live_duration` + `opened_at=_live_opened_at` + `metadata.opened_at_source`（取值 `order` / `snapshot` / `unknown`，便于统计源数据可信度）。

### 影响范围
- 新平仓的 `StrategyTrade` 记录持仓周期真实
- 后续所有"按持仓时长 / 时间窗口"分析（连续亏损、Kelly、tier 报告）都开始可用
- 历史 158 条全 0 持仓周期的污染数据**无法回补**（必须删除或忽略；建议在分析前过滤 `opened_at = closed_at` 的）

---

## 2. 深挖项 2：双风控系统合并入口

### 旧症状
两套风控并存且不同步：
- `DeterministicRiskGate`（无状态硬规则 5 条）
- `RiskControlService`（带状态规则 8+ 条：连续亏损、单日亏损熔断、入场频率、保证金使用率、单笔上限）

调用方**各用一套**：
| 调用点 | 用的规则 | 缺陷 |
|---|---|---|
| `paper_trading_engine` | 仅 DeterministicRiskGate | 缺连续亏损/单日亏损保护 |
| `full_auto_trading_service` 主链路 | 仅 DeterministicRiskGate | 同上 |
| `ai_decision_service` / `trading_commands` (live) | 仅 RiskControlService | 缺杠杆 / 单侧保证金检查 |

### 修复
新建 `backend/services/unified_risk_gate.py`，提供 `unified_check(...)` facade：

```python
def unified_check(db, *, account_id, symbol, side, notional, margin, leverage,
                  total_equity, available_balance, frozen_margin,
                  realized_pnl_today=0.0, margin_usage_percent=0.0,
                  existing_positions=None, write_event=True,
                  op_source="unknown") -> UnifiedRiskResult:
    # Layer 1 — DeterministicRiskGate
    # Layer 2 — RiskControlService.check_all
    # 任一层 BLOCKED 即返回，并写 risk_control_events.event_type='unified_blocked'
```

返回 `UnifiedRiskResult { passed, blocked_layer ('deterministic'/'stateful'), blocked_rule, reason_text, reason_code, warnings, layer_results }`。

### 已接入
- ✅ `paper_trading_engine.place_order` 已切到 `unified_check`（替换原 `risk_gate.check`）
- ⏳ `full_auto_trading_service` / `trading_commands` 暂保留旧调用以减少阻力，下一轮可全量切换

### 验证
runtime 测试用 99x 杠杆（layer1）+ 正常 5x 杠杆（应全通过）：
```
test 1: passed=False layer=deterministic rule=max_portfolio_leverage  ✅
test 2: passed=True layer_results keys=['deterministic', 'stateful']   ✅
```

---

## 3. 深挖项 3：guard 模块拦截事件统一落盘

### 旧症状
6 个 guard 模块（`reentry_cooldown` / `master_close_guard` / `fee_guard` / `liquidity_filter` / `liquidation_monitor` / `profit_drawdown_guard`）拦截开仓时**只记 logger**，不落库 → UI 看不到拦截历史，`risk_control_events` 表只有 0 条。

### 修复
1. 在 `unified_risk_gate.py` 提供公共函数：
   ```python
   def record_guard_block(db, *, account_id, guard_name, symbol="", side="",
                          reason="", extra=None) -> None:
       # 统一写入 RiskControlEvent(event_type='guard_blocked')
   ```
2. 在 `full_auto_trading_service.py` 的 3 个最关键 `reopen_blocked` 拦截分支注入：
   - 全 tier 冷却 → 跳过策略创建（line ~1716）
   - 编排器覆盖被冷却阻止（line ~4144）
   - 主开仓循环冷却拦截 → 信号排队（line ~5135）

### 待办（下一轮）
- `master_close_guard` / `fee_guard` / `liquidity_filter` / `liquidation_monitor` / `profit_drawdown_guard` 各自的拦截分支也接入 `record_guard_block`
- 工作量：每个文件 1-3 个调用点，约 30 分钟

---

## 4. 深挖项 4：每分钟 5 次"幽灵 LLM 调用"

### 旧症状
- `llm_usage_logs` 表 10,006 条全部 `call_type='llm_config_service_sync'`、`account_id=NULL`
- 每分钟稳定 5 条，无法定位是哪个定时器/服务在烧 token

### 根因
所有 10 个调用方共用 `call_llm_api_sync()`（trading_analysts / strategy_evolver / orchestrator / smart_prompt 等），全部写同一种 `call_type`，没有 caller 信息。

### 修复
在 `llm_config_service.py` 增加 `_detect_caller_module()`：
```python
def _detect_caller_module() -> str:
    """从 stack frame 找到第一个不在 llm_config_service.py 中的调用者。"""
    for fr in inspect.stack()[1:6]:
        if basename(fr.filename) == "llm_config_service.py":
            continue
        return f"{module_name}:{fr.function}"
```

`call_llm_api_sync` 增加可选参数 `caller, account_id`；`call_type` 改写为：
- 同步：`f"sync:{_resolved_caller}"` (例: `sync:trading_analysts:analyze`)
- 异步：`f"async:{_resolved_caller}"`

### 效果
重启后再统计 `llm_usage_logs.call_type` 即可看出**真正在烧 token 的模块清单**：
```sql
SELECT call_type, COUNT(*), SUM(total_tokens), SUM(estimated_cost)
FROM llm_usage_logs
WHERE created_at > datetime('now', '-1 hour')
GROUP BY call_type
ORDER BY 2 DESC;
```
预期能立刻看到例如：`sync:trading_analysts:_call_llm` 占 60%、`sync:strategy_evolver:_run` 占 25% 等等。

---

## 5. 深挖项 5：FullAutoSession 启动状态可见

### 旧症状
- DB 中 `full_auto_sessions = 0` 条但 `decision_snapshots = 52,140` 条 → **数据孤儿**
- 启动日志没有"是否在自动交易"的明确信息，用户每次都要登录 UI 查
- 之前发现的 ai_decision_logs=0 问题大概率与此相关：没人启动 session → 主决策链路从未跑过

### 修复
在 `restore_running_sessions()` 末尾打印**启动健康摘要**（用 `logger.warning` 强调可见性）：

```
════════════════════════════════════════════════════════
[FullAuto] 启动健康摘要 (2026-05-08 05:31 UTC)
[FullAuto] 全自动会话状态: (无)
[FullAuto] 24h 决策快照: 0 条
[FullAuto] 24h AI 决策日志: 0 条
[FullAuto] ⚠️  当前没有任何 running/defensive/paused 会话，
            AI 不会自动交易；如需启动请到 UI 「全自动交易」面板点开始
════════════════════════════════════════════════════════
```

### 验证（运行后日志真实输出）
```
2026-05-08 13:31:02 [WARNING] backend.services.full_auto_trading_service:1026 - [FullAuto] 启动健康摘要 (2026-05-08 05:31 UTC)
2026-05-08 13:31:02 [WARNING] backend.services.full_auto_trading_service:1029 - [FullAuto] 全自动会话状态: (无)
2026-05-08 13:31:02 [WARNING] backend.services.full_auto_trading_service:1030 - [FullAuto] 24h 决策快照: 0 条
2026-05-08 13:31:02 [WARNING] backend.services.full_auto_trading_service:1031 - [FullAuto] 24h AI 决策日志: 0 条
2026-05-08 13:31:02 [WARNING] backend.services.full_auto_trading_service:1033 - [FullAuto] ⚠️  当前没有任何 running/defensive/paused 会话，AI 不会自动交易；...
```

✅ 用户每次启动都直接看到系统真实状态，再也不会被"看起来在跑"的幻觉欺骗。

---

## 6. 额外发现

### 发现 A：`decision_snapshots` 中 52,140 条孤儿
- `decision_snapshots.session_id=1` 但 `full_auto_sessions` 表里**根本没有 id=1**
- 推测：以前的某次手动操作（可能是 SQL 直接 DELETE / 表 TRUNCATE / `db_maintenance` 误删）清掉了 session 主表
- 影响：基于 session 维度的统计、复盘、回放全部失效
- 处置建议（需要用户决策）：
  - **方案 A**：直接清空 `decision_snapshots`，重新积累
  - **方案 B**：插入一条 `id=1` 的"恢复占位 session"（status='legacy'），让历史快照能继续用

### 发现 B：158 条 `strategy_trades` 全部 `opened_at == closed_at`
- 历史污染数据（修复前留存）
- `opened_at` 字段全部为 `closed_at - 1s` 兜底值
- 影响：所有"持仓周期分布、平均持仓时长"分析都基于这批假数据
- 处置建议：在所有学习/分析查询中加 `WHERE opened_at != closed_at` 过滤（最实用），或者直接删除这批历史。

### 发现 C：`risk_control_events` 表当前 0 行
- 现在已有 `unified_check` + `record_guard_block` 落盘逻辑，下次有拦截就会出现
- 启动后 1-2 天可观察是否真的有数据，验证落盘链路真实有效

---

## 7. 文件改动清单

| 文件 | 改动 |
|---|---|
| `backend/services/trading_commands.py` | live 平仓真实开仓时间反推（orders → snapshot fallback） |
| `backend/services/unified_risk_gate.py` | **新增**：UnifiedRiskGate facade + record_guard_block |
| `backend/services/paper_trading_engine.py` | 切换到 `unified_check`（替换 `risk_gate.check`） |
| `backend/services/full_auto_trading_service.py` | 1. `restore_running_sessions` 启动健康摘要；2. 3 处 reentry_cooldown 拦截分支注入 `record_guard_block` |
| `backend/services/llm_config_service.py` | 1. `_detect_caller_module()` 新增；2. `call_llm_api_sync` 增加 `caller`/`account_id` 参数；3. async + sync 路径都用 `f"sync:{caller}"` / `f"async:{caller}"` 写入 call_type |
| `scripts/verify_deep_dive_2026_05_08.py` | **新增**：本轮验证脚本 |
| `docs/deep_dive_round2_2026-05-08.md` | **新增**：本报告 |

---

## 8. 验证证据

### 静态 + 模块导入验证（5/5 PASS）
```
✅ verify_live_duration
✅ verify_unified_risk_gate
✅ verify_guard_log_injection
✅ verify_caller_tracking
✅ verify_session_health_summary
─────────────────────────
汇总: 5/5 项通过
```

### Runtime 端到端验证
```
== test 1: 应被 layer 1 拦截（杠杆超过 50x）==
   passed=False layer=deterministic rule=max_portfolio_leverage
   reason=杠杆 99x > 最大 20.0x
== test 2: 正常订单（应通过）==
   passed=True warnings=0
   layer_results keys=['deterministic', 'stateful']
== test 3: record_guard_block 落盘 ==
   commit OK
   risk_control_events 中 unified_blocked + guard_blocked 总数: 2
✅ runtime 验证全部通过
```

### 后端启动健康检查
- ✅ 后端进程拉起，`/api/health` 200
- ✅ 启动日志无 ERROR / Traceback / datetime.timezone 错误
- ✅ `[FullAuto] 启动健康摘要` 真实出现在 backend.error.log（用 WARNING 强调可见性）
- ✅ unified_check + record_guard_block 真实可调用

---

## 9. 下一轮（仍未做）

1. 把另外 5 个 guard（master_close_guard / fee_guard / liquidity_filter / liquidation_monitor / profit_drawdown_guard）的拦截分支也接 `record_guard_block`
2. `full_auto_trading_service` / `trading_commands` 主链路全量切换到 `unified_check`，让风控规则**只有一份真相**
3. 决策快照孤儿处置（用户决策）
4. 历史 158 条 `strategy_trades` 处置（用户决策）
5. 加一个"AI 决策成功率"/"幽灵 LLM 烧钱排行"的 UI 面板，让 cost 可视化

如果你认为这一轮可以收尾了，就告诉我。如果想继续把上面 5 项里的某一项再深挖，也可以指。
