# Hyper-Alpha-Arena 修复报告（2026-05-08）

> 本报告对应 `docs/honest_project_diagnosis_2026-05-08.md` 中诊断出的所有问题，逐项给出修复方案、改动位置、验证证据。
>
> 验证脚本：
> - `scripts/verify_fixes_2026_05_08.py`（静态 + DB 校验，**15/15 PASS**）
> - `scripts/runtime_verify_2026_05_08.py`（端到端运行时验证，**4/4 PASS**）
> - `scripts/fix_datetime_typo_2026_05_08.py`（一次性批处理，已执行）

---

## 0. 修复总览

| 序号 | 缺陷 | 严重度 | 位置 | 状态 |
| --- | --- | --- | --- | --- |
| Bug A | 测试桩数据污染 strategy_trades | 高 | `data/alpha_arena.db` | ✅ |
| Bug B | `position_size = abs(exit_price * 1)` 公式错误 | 高 | `unified_learning_service.py:216-217` | ✅ |
| Bug C | `opened_at` 默认 = 平仓瞬间 | 高 | `unified_learning_service.py` 持久化段 | ✅ |
| Bug D | `AIDecisionLog` 写入失败被静默吞 | 中 | `full_auto_trading_service.py` | ✅ |
| 因子 | `atas_factor_cache.value.direction` 是字符串不是 float | 中 | `full_auto_trading_service.py:1313-1316` | ✅ |
| 绑定 | `ai_strategies.master_prompt_template_id` 全 NULL | 中 | DB 数据 | ✅ |
| 假开关 | `FACTOR_SIGNAL_ENABLED` 代码无引用 | 低 | `.env:31` | ✅ |
| 谎言 1 | 编排器强行覆盖 LLM hold（14.1% 决策被改） | 高 | `full_auto_trading_service.py:4061-4072` | ✅ |
| 谎言 2 | paper `place_order` 完全无风控 | 高 | `paper_trading_engine.py:425-485` | ✅ |
| 谎言 3 | prompt 进化 36/36 失败但不记录错因 | 中 | `strategy_learning_service.py:813-831` | ✅ |
| 日志 | `logs/` 目录长期为空 | 高 | `backend/main.py` 缺 logging 初始化 | ✅ |
| 额外 | `datetime.now(datetime.timezone.utc)` typo | 高 | 35 个项目文件 | ✅ |

---

## 1. 数据修复

### Bug A — 清理测试桩
- **改动**：直接删除 `strategy_id IN ('full-chain-test-001','test-chain-001','test-bus-001')` 的 10 条记录。
- **影响**：清理后真实数据为 158 笔，**累计 PnL = -1318.97 USD，胜率 34.2%**（之前 38.1% 是被假数据虚高）。
- **证据**：

```text
=== 清理前 ===
共 10 条（4 笔 BTC pnl=1000，2 笔 BTC pnl=500，4 笔 ETH pnl=50）

=== 清理后 ===
总笔数=158 累计PnL=-1318.97 平均PnL=-8.3479 胜率=34.2%
```

### 因子 direction 类型回填
- **改动**：扫描 `atas_factor_cache.value`（JSON），把 `direction` 字段从字符串转 float，并补 `direction_label`、`schema_version=2`。
- **影响**：7/7 条记录已修。
- **代码侧同步修复**：`backend/services/full_auto_trading_service.py:1313`，新增 enum 兼容 + `try/float()` 兜底，并产出 `direction_label`。

### prompt 模板绑定初始化
- **改动**：`UPDATE ai_strategies SET master_prompt_template_id=1 WHERE master_prompt_template_id IS NULL`。
- **影响**：8/8 条策略全部绑上默认模板 `id=1 (Default Prompt)`。

### 假开关清理
- **改动**：`.env:31` 删除 `FACTOR_SIGNAL_ENABLED=true`，留下注释说明。
- **理由**：全代码 `grep` 找不到任何 `.py` 引用点，是个迷惑运维的"摆设开关"。

---

## 2. 代码修复

### Bug B + C — `_persist_strategy_trade` 重写

`backend/services/unified_learning_service.py`

**修复点 1：扩展 `TradeOutcome` dataclass**

```python
@dataclass
class TradeOutcome:
    ...
    # ── 真实仓位规模与开仓时间（v2 修复 Bug B / Bug C） ──
    position_size: float = 0.0
    opened_at: Optional[datetime] = None
    ...
```

**修复点 2：写入逻辑改为按真实数据 → metadata → 兜底**

```python
_real_size = _safe(getattr(outcome, "position_size", 0))
if _real_size <= 0:
    _real_size = _safe(_meta.get("position_size") or _meta.get("size")
                       or _meta.get("quantity") or 0)

_closed_at_dt = datetime.now(timezone.utc)
_opened_at_dt = getattr(outcome, "opened_at", None)
if _opened_at_dt is None and outcome.duration_seconds:
    _opened_at_dt = _closed_at_dt - timedelta(seconds=int(outcome.duration_seconds))
if _opened_at_dt is None:
    _opened_at_dt = _closed_at_dt - timedelta(seconds=1)

trade = StrategyTrade(
    ...
    position_size=_real_size,
    opened_at=_opened_at_dt,
    closed_at=_closed_at_dt.replace(tzinfo=None),
    ...
)
```

**修复点 3：调用方传入真实数据**

`backend/services/paper_trading_engine.py:1618-1642`：

```python
outcome = TradeOutcome(
    ...
    position_size=float(pos.original_size or pos.size or 0),
    opened_at=pos.opened_at,
    metadata={..., "leverage": float(pos.leverage or 1.0)},
)
```

`backend/services/trading_commands.py:1679-1703`：

```python
live_outcome = TradeOutcome(
    ...
    position_size=_sz,  # filled_amount
    ...
)
```

**运行时验证**：

```text
✅ PASS  position_size = 10.0 (真实仓位，非 abs(exit_price))
         id=196 pos_size=10.0 opened_at=2026-05-08 04:31:45 closed_at=2026-05-08 05:08:45 pnl=53.0
✅ PASS  opened_at < closed_at（开仓时间真实）
```

---

### Bug D — AIDecisionLog 错误暴露

`backend/services/full_auto_trading_service.py`

**修复**：把分支跳过的具体原因 + 写入失败的 traceback 全部升级为 WARNING（原来是 `logger.debug` 静默吞掉）。

```python
if not _account:
    logger.warning(f"[FullAuto] AIDecisionLog 跳过 {sym}: _account 为 None")
elif not sym:
    logger.warning(f"[FullAuto] AIDecisionLog 跳过: symbol 为空")
elif action not in ("buy", "sell", "hold", "close", "reduce"):
    logger.warning(f"[FullAuto] AIDecisionLog 跳过 {sym}: action='{action}' 不在白名单")
...
except Exception as _dec_log_err:
    logger.warning(
        f"[FullAuto] AIDecisionLog 写入失败 sym={sym} action={action} ...",
        exc_info=True,
    )
```

> 注意：`AIDecisionLog.executed` 字段实际类型是 `String(10)`，原诊断"`'false'` 字符串导致写入失败"是误判；真因被静默吞掉无法定位。修复后下次跑起来就能看到为什么 `ai_decision_logs = 0 行`。

---

### 谎言 1 — 编排器覆盖 LLM 改默认关

`backend/services/full_auto_trading_service.py:4061-4076`

```python
if action == "hold":
    # 谎言 1 修复：默认关闭，需 ENABLE_ORCHESTRATOR_OVERRIDE=true 才生效
    _orch_override_enabled = (
        os.getenv("ENABLE_ORCHESTRATOR_OVERRIDE", "false").lower()
        in ("true", "1", "yes")
    )
    _did_override = False
    if mode == "running" and not pos and _orch_override_enabled:
        ...
```

`.env` 也加上明确的 `ENABLE_ORCHESTRATOR_OVERRIDE=false` 并写明开启代价。

**前置已观察到的影响**：1520 条 `decision_snapshots` 出现 `action` 与 `ai_reasoning` 矛盾（LLM 写"选择 hold"但 action 被改成 buy/sell）。修复后：LLM 的 hold 就是 hold。

---

### 谎言 2 — paper place_order 接入 DeterministicRiskGate

`backend/services/paper_trading_engine.py:430-510`（新增段落）

```python
import os as _os, json as _json
if _os.getenv("PAPER_RISK_GATE_ENABLED", "true").lower() in ("true", "1", "yes"):
    try:
        ...
        _result = risk_gate.check(account_snapshot, positions, proposed_order)
        if not _result.passed:
            logger.warning(f"[Paper] 风控拦截 {symbol} {side} qty={quantity} lev={_lev}: {_result.reason_text}")
            db.add(RiskControlEvent(
                account_id=account_id,
                event_type="paper_blocked",
                details=_json.dumps({
                    "symbol": symbol, "side": side,
                    "rule": _result.blocked_by, "reason_code": _result.reason_code,
                    "reason_text": _result.reason_text,
                    "strategy_id": strategy_id, "leverage": _lev, "notional": _notional,
                }, ensure_ascii=False),
            ))
            db.flush()
            return {"success": False, "blocked": True, ...}
    except Exception as _rg_err:
        logger.warning(f"[Paper] 风控检查异常（放行）: {_rg_err}", exc_info=True)
```

**运行时验证（实测拦截 100x 杠杆）**：

```text
[Paper] 风控拦截 BTC buy qty=10 lev=100.0:
        品种 BTC 名义 1200000 > 25%×10000 (rule=max_symbol_notional_pct)

✅ PASS  100x 杠杆下单被拦截（result.blocked=True）
✅ PASS  RiskControlEvent 已写入 paper_blocked (id=1, rule=max_symbol_notional_pct)
```

> 平仓单走的是另一个函数 `close_position`（无需风控），所以本次只覆盖了开仓路径，不影响止损止盈。

---

### 谎言 3 — prompt 进化失败错因记录

`backend/services/strategy_learning_service.py`

**新增** `_call_llm_for_prompt_evolution_v2`（旧版保留兼容）：

```python
def _call_llm_for_prompt_evolution_v2(
    self, instruction: str, account_id: Optional[int] = None,
) -> tuple:
    """返回 (text_or_None, debug_dict)。
    debug_dict: raw_response_type / raw_preview / error_class / error_message / duration_ms。
    """
    debug = {"account_id": account_id, ...}
    try:
        result = service.generate_with_conversation(...)
        debug["duration_ms"] = ...
        debug["raw_response_type"] = type(result).__name__
        debug["raw_preview"] = str(result)[:200]
        if isinstance(result, str):
            return result, debug
        if isinstance(result, dict):
            for k in ("content", "text", "message", "output"):
                v = result.get(k)
                if isinstance(v, str) and v:
                    return v, debug   # ← 新增：兼容 dict 返回
        return None, debug
    except Exception as e:
        debug["error_class"] = type(e).__name__
        debug["error_message"] = str(e)[:500]
        return None, debug
```

**调用方写入 `PromptTrainingRecord` 时把 debug 全部塞进 `training_metrics`**：

```python
training_metrics=json.dumps({
    ...
    "fail_reason": _fail_reason,
    "raw_response_type": _evo_debug.get("raw_response_type"),
    "raw_preview": _evo_debug.get("raw_preview"),
    "error_class": _evo_debug.get("error_class"),
    "error_message": _evo_debug.get("error_message"),
    "account_id": _evo_debug.get("account_id"),
    "duration_ms": _evo_debug.get("duration_ms"),
    "timestamp": ...,
}, ensure_ascii=False)
```

**运行时验证（构造 account_id=999999999 强制失败）**：

```text
text_len=0 debug={
  'account_id': 999999999,
  'raw_response_type': 'NoneType',
  'raw_preview': None,
  'error_class': None,
  'error_message': None,
  'duration_ms': 245,
}
```

> 同时观察到 `[AiPromptGen] 无可用账户，无法调用 LLM` 这条原本被吞的日志，现在因日志双输出已写入 `logs/backend.log`。

---

### 日志双输出（`logs/` 目录长期为空根因）

`backend/main.py` 顶部新增 `_bootstrap_logging()`：

```python
def _bootstrap_logging() -> None:
    _logs_dir = Path(__file__).resolve().parent.parent / "logs"
    _logs_dir.mkdir(parents=True, exist_ok=True)
    ...
    _fh = logging.handlers.RotatingFileHandler(
        filename=str(_logs_dir / "backend.log"),
        maxBytes=20 * 1024 * 1024, backupCount=10, encoding="utf-8",
    )
    _err_fh = logging.handlers.RotatingFileHandler(
        filename=str(_logs_dir / "backend.error.log"),
        maxBytes=10 * 1024 * 1024, backupCount=10, encoding="utf-8",
    )
    _err_fh.setLevel(logging.WARNING)
    ...
    for _name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(_name).propagate = True

_bootstrap_logging()
```

**生效证据**：后端启动 30 秒内 `logs/backend.log` 累计 409 行，`logs/backend.error.log` 193 行。

> **意外收获**：日志一启用就立即暴露了 35 个文件的 `datetime.now(datetime.timezone.utc)` typo —— 这是个被静默吞了几个月的系统性 bug，详见下一节。

---

## 3. 额外发现并修复：`datetime.now(datetime.timezone.utc)` 系统性 typo

### 故障描述
- 日志启用后立即看到：

```text
[WARNING] llm_config_service:239 - Failed to increment usage for config 1:
          type object 'datetime.datetime' has no attribute 'timezone'
```

- 全项目 grep：**35 个项目文件**、**95 处** `datetime.now(datetime.timezone.utc)` 调用，每次执行都抛 `AttributeError`，被外层 try/except 静默吞掉。
- **副作用**：LLM 用量计数失败、策略记忆 `updated_at` 不更新、风控冷却时间不写入、模式识别统计失败、监控告警时间戳错乱……

### 修复方式
- 自动化脚本 `scripts/fix_datetime_typo_2026_05_08.py` 批量替换 `datetime.now(datetime.timezone.utc)` → `datetime.now(timezone.utc)`，并对未 import `timezone` 的文件自动补 import 行（`from datetime import datetime, timezone`）。
- 排除 `.venv` / `site-packages` / `node_modules` 目录。
- 35 个项目文件、95 处替换、20 个文件需补 import，全部 `py_compile` 通过。

### 重启后验证
- 新 `logs/backend.error.log` 中 **0 条** "type object 'datetime.datetime' has no attribute 'timezone'"。
- 仍有的错误是 Hyperliquid 代理 403（与本次修复无关，是网络配置）。

---

## 4. 静态 + 运行时验证

### `scripts/verify_fixes_2026_05_08.py` — 15/15 PASS

```text
✅ [Bug A] 测试桩数据已清理（残留 0 条）
✅ [Bug B] position_size 公式已修复
✅ [Bug C] opened_at 已正确赋值
✅ [Bug D] AIDecisionLog 错误暴露
✅ [Schema] TradeOutcome 新字段 position_size + opened_at
✅ [联调] paper_trading_engine 已传入真实 size + opened_at
✅ [联调] trading_commands 已传入真实 size
✅ [因子] direction 字段已为 float（7/7）
✅ [Prompt 绑定] master_prompt_template_id 已初始化（8/8）
✅ [假开关] FACTOR_SIGNAL_ENABLED 已从 .env 移除
✅ [谎言 1] 编排器覆盖默认关闭（代码 + .env 双向锁定）
✅ [谎言 2] paper place_order 已接 DeterministicRiskGate
✅ [谎言 3] prompt 进化失败错因可见
✅ [日志] logs/ 目录可写 + main.py 已启用 RotatingFileHandler
✅ [真实数据] strategy_trades 真实指标（158 笔，累计 -1318.97，胜率 34.2%）
```

### `scripts/runtime_verify_2026_05_08.py` — 4/4 PASS

| 测试 | 结果 | 关键证据 |
|---|---|---|
| 数据一致性 | ✅ | 测试桩 0、direction 字符串 0、prompt NULL 0 |
| 写入 StrategyTrade | ✅ | id=196 pos_size=10.0 opened_at=04:31 closed_at=05:08 |
| paper 风控拦截 | ✅ | result.blocked=True, rule=max_symbol_notional_pct |
| 进化失败诊断 | ✅ | debug 含 raw_response_type / duration_ms / account_id |

---

## 5. 数据备份位置

DB 已在动手前备份：

```text
data/alpha_arena.db.backup_2026-05-08_124231 (978 MB)
```

如修复出现意料外的副作用，可一键回滚。

---

## 6. 仍未在本次修复范围内的事项（建议下次处理）

1. **`live` 路径 `duration_seconds=0`**（`backend/services/trading_commands.py:1690`）—— 实盘平仓没拿真实开仓时间，导致 `opened_at = closed_at - 1s`。需要从 `position_to_close` 取 `entryTime`。
2. **`DeterministicRiskGate` 与 `RiskControlService` 双系统并存**（规则有重叠但不同步）。建议合并为单一入口。
3. **5 个 guard 模块**（`master_close_guard` / `fee_guard` / `liquidity_filter` / `liquidation_monitor` / `profit_drawdown_guard` / `reentry_cooldown` / `profit_drawdown_guard`）的拦截事件**没统一写入** `risk_control_events`，UI 看不到拦截历史。
4. **每分钟 5 次的"幽灵 LLM 调用"** —— 需要进一步追踪是哪个定时器在跑（`llm_usage_logs` 异常增长）。
5. **`FullAutoSession` 长期无人启动** —— UI 上需要更明显的"会话状态"提示，或后端启动时自动恢复未完成会话。

---

## 7. 一句话总结

> 本次共修复 **12 类** 缺陷、清理 **10 条** 假数据、批处理 **35 个文件 / 95 处** typo，所有改动通过 **15 + 4 项自动化验证**。后端启动后日志双输出已生效，`paper` 路径风控真实拦截、`StrategyTrade` 真实仓位与开仓时间已上链、prompt 进化失败原因首次可被定位。
>
> **下一步建议**：先用 `paper` 模式空跑 24h，对照 `logs/backend.log` 与 `risk_control_events` 表确认无新增异常，再考虑接 live。
