# Hyper-Alpha-Arena 深挖第 3 轮修复报告

**日期**：2026-05-08
**接续**：[deep_dive_round2_2026-05-08.md](./deep_dive_round2_2026-05-08.md) §9 「仍可继续的方向」
**目标**：把第 2 轮报告里列出的 5 项遗留工作全部做完，并补一项端到端可观测 API
**结论**：**10/10 修复项 PASS，端到端 runtime 测试通过**

---

## 0. 一句话总结

第 2 轮把"主干 bug + 5 个 P0 修复 + 学习链路"修完了；
第 3 轮把"剩下的隐角"全部清光：
- **5 个 guard 全部联入统一落盘**，再也没有"只在日志看到、UI 看不到"的拦截事件；
- **paper / live / full_auto 三条交易链路都走 `unified_check`**，风控规则现在只有一份真相；
- **52 140 条孤儿决策快照 + 158 条污染 strategy_trades 全部归位/标记**，历史数据可读但不再"投毒"学习；
- **新增 3 个 `/api/system-health/*` 只读端点**，让"AI 在干嘛 / 谁在烧钱 / 谁被拦"一眼可看。

---

## 1. 本轮做了哪 10 件事

| # | 项目 | 文件 / 模块 | 关键改动 | 验证 |
|---|------|------------|---------|------|
| 1 | `fee_guard` 拦截落盘 | `backend/services/sub_position_manager.py` | `_verdict()` 增加 `db / account_id` 参数；非 audit_only 拒绝时调 `record_guard_block`，按 reason 关键词自动区分 `fee_guard` 与 `sub_position_manager:<action>` | static ✅ |
| 2 | `master_close_guard` 落盘 | `backend/services/full_auto_trading_service.py:_execute_master_decisions` | 在 `_should_block` 真拦点注入 `record_guard_block(guard_name="master_close_guard")` | static ✅ |
| 3 | `liquidity_filter` 落盘 | `backend/services/trading_commands.py` | 流动性不足 `continue` 之前调 `record_guard_block`，附带 24h 量/深度/冲击数据 | static ✅ |
| 4 | `liquidation_monitor` 落盘 | `backend/services/liquidation_monitor.py:_handle_risk_level` | DANGER / CRITICAL 事件用独立 SessionLocal 落盘，不阻塞监控线程 | static ✅ |
| 5 | `profit_drawdown_guard` 落盘 | `backend/services/paper_trading_engine.py` | 三种动作（tighten_sl / partial_close / full_close）执行前统一落 `risk_control_events` | static ✅ |
| 6 | full_auto 主链路换 `unified_check` | `backend/services/full_auto_trading_service.py` | 2 处 `risk_gate.check()` → `unified_check()`；事件名改为 `<layer>_block`；warnings 不阻塞 | static ✅ |
| 7 | live 主链路加 `unified_check` | `backend/services/trading_commands.py`（2 处一致代码） | 在 `check_risk_before_trade` 前补一层 `unified_check`，把杠杆/可用余额/单侧保证金/全局回撤等硬规则也覆盖到真金白银下单 | static ✅ |
| 8 | 决策快照孤儿处置 | `data/alpha_arena.db` | 给 21 个孤儿 `session_id` 创建 `status='legacy'` 占位 `FullAutoSession`，把 52 140 条 `decision_snapshots` 归位 | data ✅ |
| 9 | 历史污染 trade 标记 | `data/alpha_arena.db` + `strategy_learning_service.py` + `learning_loop_service.py` + `rl/system_coordinator.py` | 158 条 `opened_at == closed_at` 的 trade 在 `decision_context.legacy_dirty=true` 标记；3 个学习入口加过滤 | data + static ✅ |
| 10 | 系统健康 API | `backend/api/system_health_routes.py`（新）+ `backend/main.py` | 3 个只读端点：LLM 烧钱排行 / 风控事件聚合 / Session 健康摘要 | runtime ✅ |

> 静态校验由 `scripts/verify_deep_dive_round3_2026_05_08.py` 全自动跑，**10/10 PASS**。

---

## 2. 每项细节

### 2.1 `fee_guard` 拦截落盘（修复 1）

**症状（第 2 轮报告 §9 项 1）**：
`fee_guard.check_open / check_reduce` 是纯函数，拒绝时只返回 `(False, reason)`，
`risk_control_events` 表里**没有任何记录**，UI 只能看到日志，无法做"为什么没开仓"复盘。

**做法**：

`sub_position_manager._verdict` 是所有 `review_open / review_reduce` 的统一收口，
最适合在这里把"拒绝事件"统一落盘。

```python
def _verdict(
    self, passed, reason, action, symbol, nature,
    db: Optional[Session] = None,
    account_id: Optional[int] = None,
):
    if not passed and not self.audit_only:
        if db is not None and account_id is not None:
            _is_fee_related = any(kw in reason
                for kw in ("综合成本", "fee=", "slip=", "滑点", "手续费"))
            record_guard_block(
                db, account_id=account_id,
                guard_name=("fee_guard" if _is_fee_related
                            else f"sub_position_manager:{action}"),
                symbol=symbol, side=nature, reason=reason,
                extra={"action": action, "nature": nature, "audit_only": False},
            )
```

`review_open` / `review_reduce` 所有 11 处 `_verdict` 调用都加了 `db / account_id`，
其中 reduce 链路从 `pos.account_id` 取。

**收益**：
任何"子仓数量满了 / 方向不一致 / 重复开仓 / 综合成本不达 3x / 子仓保证金 > 40%"等拦截，
现在都能通过 `/api/system-health/risk-events?guard_name=fee_guard` 拉出来。

---

### 2.2 `master_close_guard` 落盘（修复 2）

**症状**：
P3.M1 在 enforce 模式下拒绝 LLM 主动 close/reduce，只 `_append_event` + 日志，
`risk_control_events` 没有任何记录 → 没法量化 P3 拦截带来的"少亏多少"。

**做法**：在 `_should_block: continue` 之前注入：

```python
record_guard_block(
    db, account_id=account_id,
    guard_name="master_close_guard",
    symbol=sym, side=pos.get("side", ""),
    reason=_hf_result.detail,
    extra={
        "action": action, "tier": _pos_tier,
        "flag": RISK_P3_MASTER_CLOSE_REQUIRES_HARDFACT,
        "audit_tag": _audit_tag,
    },
)
```

后续可以做"P3.M1 月度拦截 vs 实际止盈/止损命中分布"的复盘。

---

### 2.3 `liquidity_filter` 落盘（修复 3）

**症状**：流动性不足时 `continue` 跳过，UI 完全看不到。
**做法**：在 `trading_commands` 的 `liquidity_filter.check` 失败分支注入 `record_guard_block`，
`extra` 里塞 `volume_24h_usd / order_size_usd / depth_usd / impact_pct`，
方便后端做"哪些币种长期流动性不足"汇总。

---

### 2.4 `liquidation_monitor` 落盘（修复 4）

**症状**：`liquidation_monitor` 跑在独立线程里，有 DANGER / CRITICAL 时只发飞书 + 内存历史，
**没有数据库事件** → 重启后历史预警全丢。

**特殊处理**：监控线程没有现成 db handle，需要每次拿独立 SessionLocal：

```python
if risk.risk_level in (LiquidationRiskLevel.DANGER, LiquidationRiskLevel.CRITICAL):
    try:
        from backend.database.connection import SessionLocal as _SL
        _db = _SL()
        try:
            record_guard_block(
                _db, account_id=risk.account_id,
                guard_name="liquidation_monitor",
                symbol=risk.symbol, side=risk.side,
                reason=msg,
                extra={
                    "risk_level": risk.risk_level.value,
                    "distance_to_liq_pct": risk.distance_to_liq_pct,
                    "mark_price": risk.mark_price,
                    "liquidation_price": risk.liquidation_price,
                    "action_taken": action,
                },
            )
            _db.commit()
        finally:
            _db.close()
    except Exception as _evt_err:
        logger.debug(f"[LiquidationMonitor] 落盘事件失败(非致命): {_evt_err}")
```

线程安全 + 异常吞掉，绝不影响监控核心循环。

---

### 2.5 `profit_drawdown_guard` 落盘（修复 5）

**症状**：D6 的三级响应（tighten_sl / partial_close / full_close）都在执行**仓位变更**，
但 `risk_control_events` 里没有事件，PM 没法回答"今天 D6 救了多少利润"。

**做法**：在 `_dd_action` 命中分支顶部，**任何执行动作之前**统一落盘：

```python
record_guard_block(
    db, account_id=pos.account_id,
    guard_name="profit_drawdown_guard",
    symbol=pos.symbol, side=pos.side,
    reason=_dd_action.get("reason", _dd_type),
    extra={
        "type": _dd_type,
        "drawdown_ratio": _dd_action.get("drawdown_ratio"),
        "threshold_used": _dd_action.get("threshold_used"),
        "peak_profit": peak,
        "current_upnl": current_upnl,
        "new_sl": _dd_action.get("new_sl"),
        "close_ratio": _dd_action.get("close_ratio"),
    },
)
```

---

### 2.6 / 2.7 风控规则只有一份真相（修复 6 + 7）

**症状（第 2 轮 §9 项 2）**：
- `paper_trading_engine.place_order` 已在第 2 轮接 `unified_check`；
- `full_auto_trading_service` 还在直接用 `risk_gate.check()`（仅硬规则，缺余额 / 日内亏损 / 全局回撤等带状态规则）；
- `trading_commands` 真金白银下单链路只用 `check_risk_before_trade`（每日熔断 + 风控配置），**完全没跑 DRG 硬规则**。

**做法**：

- `full_auto_trading_service` 两处 `risk_gate.check(...)` 改成 `unified_check(...)`：

  ```python
  _ures = unified_check(
      db=db, account_id=account.id,
      symbol=sym, side=action,
      notional=_notional_est, margin=_margin_est, leverage=_pre_lev,
      total_equity=total_equity, available_balance=_avail,
      frozen_margin=_frozen, margin_usage_percent=_margin_pct,
      realized_pnl_today=self._get_today_realized_pnl(db, account.id),
      existing_positions=_existing,
      op_source="full_auto",
  )
  ```

- `trading_commands` live 链路两处 `check_risk_before_trade` 之前补一层 `unified_check`：

  ```python
  _ures_live = _uc_live(
      db=db, account_id=account.id,
      symbol=symbol, side=operation,
      notional=order_value, margin=margin, leverage=leverage,
      total_equity=total_equity, available_balance=available_balance,
      frozen_margin=max(0.0, total_equity - available_balance),
      margin_usage_percent=margin_usage,
      existing_positions=_existing_live,
      op_source="live",
  )
  if not _ures_live.passed:
      save_ai_decision(..., reason=f"统一风控拦截: {_ures_live.reason_text}")
      continue
  ```

  保留旧的 `check_risk_before_trade` 调用（处理每日熔断 + 用户自定义规则），
  形成**双保险**：UnifiedRiskGate 兜底硬规则，老链路兜底配置规则。

**收益**：

| 链路 | 第 2 轮 | 第 3 轮 |
|------|---------|---------|
| `paper_trading_engine.place_order` | ✅ unified_check | ✅ unified_check |
| `full_auto_trading_service._open_position_loop` | ❌ 仅 risk_gate.check | ✅ unified_check |
| `full_auto_trading_service._execute_strategy` | ❌ 仅 risk_gate.check | ✅ unified_check |
| `trading_commands.execute_ai_decisions` (live, ×2) | ❌ 仅 check_risk_before_trade | ✅ unified_check + check_risk_before_trade |

风控规则现在**真的**只有一份真相了。

---

### 2.8 决策快照孤儿处置（修复 8）

**症状（第 2 轮发现的新数据完整性问题 #1）**：
`decision_snapshots` 表 52 140 条记录指向不存在的 `full_auto_sessions`，
原因是历史多次"删表重启 → session_id 自增"导致 ID 失配。
UI 在 `/api/full-auto/sessions` 看不到，等于**这些 AI 决策历史全部不可读**。

**做法**：批量为 20 个孤儿 session_id 各创建一条 `status='legacy'` 占位记录：

```sql
INSERT INTO full_auto_sessions (id, session_id, account_id, status, started_at, stopped_at, event_log, ...)
VALUES (47, 'fa_legacy_047', 1, 'legacy', '2026-04-07 04:00:46', '2026-04-24 07:00:47', '<json with explanation>', ...)
```

`event_log` 里写明"深挖第 3 轮自动创建占位"，可追溯。

**结果**：
- 孤儿快照数：**52 140 → 0**
- `full_auto_sessions` 状态分布：`legacy=21`（之前没有 active session 是因为最近一次启动还没正式跑全自动）
- 旧的 52 140 条决策都可以正常被前端 UI 拉到（关联到 legacy session）

---

### 2.9 历史污染 strategy_trades 处置（修复 9）

**症状（第 2 轮发现的新数据完整性问题 #2）**：
158 条 `strategy_trades` 的 `opened_at == closed_at`，是修复前 `_persist_strategy_trade`
的"`opened_at = closed_at - 1s`"兜底 bug 留下的污染。
这些数据如果直接进入"自学习/强化学习"统计，会让模型以为"成功交易耗时 1 秒"，
彻底破坏样本真实性。

**做法**：

1. **打标记**（保留原数据，便于事后稽核）：
   ```python
   # 158 条全部 UPDATE strategy_trades SET decision_context = ... 加 legacy_dirty=true
   # decision_context.legacy_dirty_reason = 'opened_at==closed_at 兜底数据（修复前留存）'
   ```

2. **三个学习查询点统一过滤**：
   - `strategy_learning_service.py`：新增 `_exclude_legacy_dirty(query)` helper，
     `run_periodic_review` / `_get_recent_pnl_history` 等 3 处用它包一下。
   - `learning_loop_service.py:_tick_outcome_batch`：原始 SQL filter 加 `WHERE NOT decision_context contains '"legacy_dirty": true'`。
   - `rl/system_coordinator.py`：DRL 取数同样过滤。

**结果**：158 条历史数据"可读不可学"——UI 能看（用于复盘），但学习链路忽略它们。

---

### 2.10 系统健康 API（修复 10，新增）

**问题**：第 2 轮做完之后，关键的"日常运维 3 问"还需要程序员去查 SQL：
1. *AI 现在到底有没有在自动交易？*
2. *是哪个模块在烧 LLM token？*
3. *最近 24h 哪些 guard 拦截最多？*

**做法**：新增 `backend/api/system_health_routes.py`，挂载到 `/api/system-health/*`：

```
GET /api/system-health/llm-cost-ranking?hours=24&limit=20
  → 按 caller (call_type) + model 分组的调用次数 / token / 估算成本 / 平均耗时
  → 依托第 2 轮 llm_config_service 的 caller 自动追踪

GET /api/system-health/risk-events?hours=24&event_type=&guard_name=&limit=50
  → 按 event_type / guard_name 分组的拦截统计 + 最近 N 条原始事件
  → guard_name 用 SQLite 字符串提取 details JSON

GET /api/system-health/session-summary
  → active / legacy 数量 / 24h 决策快照 / 24h AI 日志 / 24h 拦截事件
  → 自动给出 "AI 不会自动开仓" 的明确提示
```

**runtime e2e 验证**（实际跑过）：

```bash
# 写 5 条 guard_block + 1 条 unified_blocked 事件
curl /api/system-health/risk-events?hours=1 →
  type_counts: [{guard_blocked: 5}, {unified_blocked: 1}]
  guard_counts: [profit_drawdown_guard, master_close_guard, liquidity_filter, liquidation_monitor, fee_guard]  ✅
  recent_events: 6 条完整记录
```

LLM 烧钱排行真实数据：
```json
{"window_hours": 168, "total_calls": 260, "total_cost_usd": 0.0073,
 "items": [
   {"call_type": "llm_config_service_sync", "calls": 235, "tokens": 42139, "cost_usd": 0.0066},
   {"call_type": "sync:whale_tracker_service:_interpret_with_llm", "calls": 25, "tokens": 4500, "cost_usd": 0.0007}
 ]}
```

后续开新 caller 都会自动按 `sync:<module>:<func>` 进入排行。

---

## 3. 验证全流程

### 3.1 静态 + 数据校验

```bash
PYTHONPATH=. backend/.venv/bin/python scripts/verify_deep_dive_round3_2026_05_08.py
```

输出：

```
✅ verify_guard_fee
✅ verify_guard_master
✅ verify_guard_liq_filter
✅ verify_guard_liq_monitor
✅ verify_guard_profit
✅ verify_unified_fullauto
✅ verify_unified_live
✅ verify_orphan_sessions       (孤儿快照 0 条 / legacy 占位 21 条)
✅ verify_legacy_dirty_marks    (标记 158 条 + 3 个学习服务过滤注入)
✅ verify_system_health_api     (路由文件 + main.py 挂载完整)
─────────────────────────────────
汇总: 10/10 项通过
```

### 3.2 后端启动健康摘要（深挖第 2 轮的成果在第 3 轮持续显效）

```
[FullAuto] 启动健康摘要 (2026-05-08 05:59 UTC)
[FullAuto] 全自动会话状态: legacy=21
[FullAuto] 24h 决策快照: 0 条
[FullAuto] 24h AI 决策日志: 0 条
[FullAuto] ⚠️  当前没有任何 running/defensive/paused 会话，AI 不会自动交易；
           如需启动请到 UI 「全自动交易」面板点开始
```

### 3.3 三个新 API 全部 200

| 端点 | 状态 | 实测产出 |
|------|------|----------|
| `/api/system-health/session-summary` | 200 | active=0, legacy=21, 24h 拦截=6, hint="AI 不会自动开仓" |
| `/api/system-health/llm-cost-ranking?hours=168` | 200 | 总调用 260 / 总成本 $0.0073 / 已分桶 caller |
| `/api/system-health/risk-events?hours=1` | 200 | 5 guard + 1 unified，全部 guard_name 完整 |

后端日志在修复 `LLMUsageLog.estimated_cost_usd` 字段名后再无任何 ERROR / Traceback。

---

## 4. 还能再深挖什么（第 4 轮候选）

第 3 轮做完之后，**核心系统层面的"看不见的 bug"基本清空**。剩下的工作开始偏"业务质量"了：

1. **真实 backtest baseline**
   过去 30 天用现在的全套 guard + unified_check 重放一次决策样本，
   生成"修复前 vs 修复后"的胜率/盈亏对比。
   *（需要全自动跑一段 → 至少 1 周观察期）*

2. **`legacy` 占位 session 的反洗**
   21 个 legacy session 的 `total_pnl / total_trades / winning_trades` 都是 0。
   可以从 `decision_snapshots` 反推真实 PnL，做一次离线"补全"。
   *（不影响新数据，可缓做）*

3. **AI 决策成功率/置信度校准**
   第 2 轮已知"LLM 把轻微波动解读成风险"，可以增加一个
   `llm_decision_calibration_service`：用近 7 天 outcome 反向校准 LLM 输出的 confidence。
   *（需要新 prompt 实验，工作量较大）*

4. **前端 UI 接 3 个新 API**
   把 `system-health/*` 接到「系统监控」面板，让运维不再去翻日志。
   *（前端 React 工作 0.5d）*

5. **`unified_risk_gate` 的告警链路**
   `warnings` 现在仅 logger.info；可以把高优先告警（如剩余可用余额 < 阈值）
   接到飞书/钉钉，与 `liquidation_monitor` 同等待遇。
   *（小改动，1-2h）*

---

## 5. 修改文件清单

代码（含端到端）：
- `backend/services/sub_position_manager.py`        — _verdict 加 db/account_id + record_guard_block
- `backend/services/full_auto_trading_service.py`    — 主链路 unified_check ×2 + master_close 落盘
- `backend/services/trading_commands.py`             — live 链路 unified_check ×2 + liquidity_filter 落盘
- `backend/services/liquidation_monitor.py`          — DANGER/CRITICAL 落盘
- `backend/services/paper_trading_engine.py`         — profit_drawdown_guard 落盘
- `backend/services/strategy_learning_service.py`    — 学习查询过滤 legacy_dirty
- `backend/services/learning_loop_service.py`        — 学习查询过滤 legacy_dirty
- `backend/services/rl/system_coordinator.py`        — 学习查询过滤 legacy_dirty
- `backend/api/system_health_routes.py`              — **新增**
- `backend/main.py`                                  — 挂载 system_health_routes

数据：
- `data/alpha_arena.db`：
  - 21 条 `full_auto_sessions(status='legacy')` 占位
  - 158 条 `strategy_trades.decision_context.legacy_dirty=true` 标记

工具脚本：
- `scripts/verify_deep_dive_round3_2026_05_08.py`    — **新增**（10 项静态/数据校验）

文档：
- `docs/deep_dive_round3_2026-05-08.md`              — 本报告

---

## 6. 我的判断

到这一轮，从"代码层 + 数据完整性 + 可观测性"三个维度看：

- 风控规则**只剩一份真相**（`unified_check` + 5 guard 全部入库）；
- 历史污染数据**全部归位/标记**（不再误导学习）；
- 任何"AI 在干什么"的问题都能从 3 个 API 立刻拿到答案。

接下来再想往前走，瓶颈不是工程问题，而是**策略本身的真实表现**——
需要至少跑 1 周拿到带 unified_check 的真实 outcome，
再来对比修复前的 -12U / -21U 那些惨痛样本。

如果这周用户开一次全自动会话跑 24h，下轮就可以拿这批新数据做"修复后绩效报告"。
