# 日志规范 v1（trace_id + 前缀约定）

> 配套 `backend/utils/trace_context.py` + `backend/middleware/trace.py`
> 阶段 1 落地，2026-06-19

---

## 1. 日志格式

所有后端日志统一格式（`main.py:_bootstrap_logging`）：

```
%(asctime)s [%(levelname)s] [tr=%(trace_id_short)s] %(name)s:%(lineno)d - %(message)s
```

**示例**：
```
2026-06-19 17:00:00 [INFO] [tr=req-a3f1b2c4] backend.services.full_auto_trading_service:9935 - [FullAuto] tick 入口
2026-06-19 17:00:01 [INFO] [tr=-] backend.main:412 - [startup] 数据库初始化  # 无 trace_id（启动期）
```

- `[tr=...]` 中 `tr` = trace；`-` 表示无 trace_id（启动期或未走中间件的后台任务）
- `trace_id_short` = 前缀 + uid 前 8 位（如 `req-a3f1b2c4`），完整 trace_id 在 record.trace_id 字段

## 2. trace_id 来源

| 场景 | 来源 | 前缀 |
|---|---|---|
| HTTP 请求 | `TraceMiddleware`（每请求生成或复用 `X-Request-ID` 头） | `req-` |
| full_auto tick | `_run_unified_loop_wrapped` 手动绑定 | `fullauto-{sid6}-` |
| 套利 tick | 待补（阶段 5） | `arb-` |
| opencode 分析 | 待补（阶段 5） | `opencode-` |
| 后台任务 | 手动 `with bind_trace(new_trace("xxx")):` | 自定义 |

**响应头**：每个 HTTP 响应回写 `X-Request-ID`，前端/调用方可用于关联。

## 3. 子系统前缀约定（日志 message 内）

| 子系统 | message 前缀 | 示例 |
|---|---|---|
| Paper 交易 | `[Paper]` / `[PaperEngine]` | `[Paper] 成交: BTC buy ...` |
| 实盘交易 | `[Live]` / `[HL]` | `[Live] 下单: BTC buy ...` |
| FullAuto | `[FullAuto]` | `[FullAuto] 总控决策 ETH[趋势]: hold` |
| 风控 | `[Risk]` / `[Guard]` | `[Risk] 拦截: BTC 杠杆超限` |
| 净额 | `[Paper] 净额...` | `[Paper] 净额对冲释放: ...` |
| 套利 | `[Arb]` / `[Rebate]` | `[Arb] S8 扫描完成` |
| OpenCode | `[OpenCode]` | `[OpenCode] proposal applied` |
| 追踪 | `[Trace]` | `[Trace] trace_id 中间件已启用` |

**新增日志请遵循此前缀约定**，保持与现有 `[Paper]`/`[FullAuto]` 一致。

## 4. 手动绑定 trace_id（后台任务）

```python
from backend.utils.trace_context import bind_trace, new_trace

# 调度器 tick / 长任务
with bind_trace(new_trace("arb")):
    logger.info("[Arb] tick 开始")  # 自动带 [tr=arb-xxx]
    await run_arbitrage_tick()
```

## 5. 查询日志

按 trace_id 关联一次请求/ tick 的所有日志：

```bash
# Linux/Mac
grep "tr=req-a3f1b2c4" logs/backend.log

# Windows PowerShell
Select-String -Path logs/backend.log -Pattern "tr=req-a3f1b2c4"
```

## 6. 开关

- `TRACE_ID_ENABLED=false`（环境变量）禁用中间件（极端调试场景）
- `TraceIdFilter` 在 logger 层安装，即使中间件禁用，`bind_trace` 仍可用于手动绑定

## 7. 不变性

- trace_id 在同一请求/ tick 内**不变**
- 跨子系统（paper/live/套利）共享同一 trace_id（通过 ContextVar 自动传播）
- 线程切换时 ContextVar 默认不传播 —— **新线程必须手动 `bind_trace`**（见 `_run_unified_loop_wrapped` 示例）
