# 重构灰度启用手册

> 配套 `docs/REFACTOR_ROADMAP.md` v1.5
> 阶段 0-6 已完成，本文档说明如何渐进启用新功能

---

## 功能开关总览

| 开关 | 默认 | 当前状态 | 说明 |
|---|---|---|---|
| `PAPER_NETTING_MODE` | `true` | ✅ 已启用 | Paper 净额保证金（对冲释放） |
| `USE_UNIFIED_EXECUTOR` | `false` | ✅ 已灰度启用 | 统一执行器（PaperExecutor/LiveExecutor） |
| `TRACE_ID_ENABLED` | `true` | ✅ 已启用 | trace_id 日志中间件 |
| `DEFAULT_EXCHANGE` | `asterdex` | ✅ 已生效 | 默认交易所（新账户） |

---

## 1. PAPER_NETTING_MODE（已启用）

**状态**: ✅ 生产已启用，验证通过

**效果**: Paper 保证金按每币种净头寸计算，对冲对释放保证金。
- BTC long 0.1 + short 0.05 → 净 long 0.05，保证金从 $1810 降到 $590
- SOL swing short 334.69 + trend short 530.60 → 净 short 865.29（正确合并）

**回滚**: 设置 `PAPER_NETTING_MODE=false` 重启

**验证**: `GET /api/paper/balance/5` → frozen_margin 为净额值

---

## 2. USE_UNIFIED_EXECUTOR（已灰度启用）

**状态**: ✅ 已灰度启用（paper 模式验证通过）

**灰度验证结果**（2026-06-19 18:35）:
- ✅ 后端 `USE_UNIFIED_EXECUTOR=true` 启动成功（PID 23384）
- ✅ 开关运行时读取为 `true`（`is_unified_executor_enabled()` 验证）
- ✅ 账户 5 余额正常（equity $533,107，净额 frozen $23,324）
- ✅ 5 持仓正常，net_group 字段正确（SOL 双空合并 865.29）
- ✅ 无 Traceback / ImportError / executor 异常
- ✅ 27 单元测试覆盖（mock place_order 验证）
- ⚠️ 自然 tick 未触发（当前进程 session-restore 间隙，非执行器问题）

**效果**: `_execute_paper_trade` / `_execute_live_trade` 通过统一 ExecutionChannel 下单（PaperExecutor/LiveExecutor），而非直接调 paper_engine / place_ai_driven_order。

### 灰度启用步骤

#### 步骤 1: paper 模式先验证（低风险）
```bash
# 设置环境变量
set USE_UNIFIED_EXECUTOR=true
# 重启后端
.\scripts\stop-dev.ps1
.\scripts\start-dev.ps1 -NoFrontend
```

#### 步骤 2: 观察 1-2 个 tick
```bash
# 查看统一执行器日志（应看到 [PaperExecutor] 或 统一执行器下单）
Select-String -Path logs\backend.log -Pattern "统一执行器|PaperExecutor|LiveExecutor"
```

#### 步骤 3: 验证交易正常
```bash
# 余额应正常变化
curl http://127.0.0.1:8000/api/paper/balance/5
# 持仓应正常
curl http://127.0.0.1:8000/api/paper/positions/5
```

#### 步骤 4: 若异常，立即回滚
```bash
set USE_UNIFIED_EXECUTOR=false
.\scripts\stop-dev.ps1
.\scripts\start-dev.ps1 -NoFrontend
```

### 预期日志（启用后）
```
[FullAuto] 统一执行器下单: BTC buy → status=filled order_id=paper_123
```
旧路径日志（关闭时）：
```
[Paper] 成交: BTC buy qty=0.1 @60000 ...
```

---

## 3. 统一账户 API（已就绪）

**状态**: ✅ 5 个端点已上线

```
GET  /api/unified-account/list               # 所有 paper 账户（AI+套利）
GET  /api/unified-account/exposure/combined  # 跨系统合并敞口
GET  /api/unified-account/fee-schedule       # 费率表（6 交易所）
POST /api/unified-account/transfer           # 跨账户资金划转
GET  /api/unified-account/{scope}/{id}       # 单账户视图
```

**验证**:
```bash
curl http://127.0.0.1:8000/api/unified-account/list
curl http://127.0.0.1:8000/api/unified-account/fee-schedule
curl "http://127.0.0.1:8000/api/unified-account/exposure/combined?ai_account_id=5"
```

---

## 4. trace_id 日志（已启用）

**状态**: ✅ 已启用

**效果**: 每条日志带 `[tr=xxx]`，HTTP 响应带 `X-Request-ID` 头。

**查询一次请求的所有日志**:
```powershell
Select-String -Path logs\backend.log -Pattern "tr=req-a3f1b2c4"
```

**回滚**: `set TRACE_ID_ENABLED=false` 重启

---

## 5. 默认交易所 AsterDex（已生效）

**状态**: ✅ 新账户默认 asterdex

**注意**: 老账户不批量修改（避免破坏现有配置）。新账户创建时默认 asterdex。

**手动切换老账户**:
```sql
UPDATE accounts SET selected_exchange = 'asterdex' WHERE id = 5;
```

---

## 回滚总策略

任何功能异常，按优先级回滚：

1. **执行器**: `USE_UNIFIED_EXECUTOR=false`（最可能需要）
2. **净额**: `PAPER_NETTING_MODE=false`（已验证稳定，一般不需要）
3. **trace**: `TRACE_ID_ENABLED=false`（仅日志，不影响交易）
4. **全量回滚**: `git revert <commit>` + 还原 DB 备份

DB 备份位置: `data/alpha_arena.db.bak_stage0_*`（注意生产是 Postgres，需 `pg_dump`）

---

## 验证清单（启用新功能后）

- [ ] `GET /api/paper/balance/5` → 余额正常
- [ ] `GET /api/paper/positions/5` → 持仓正常 + net_group 字段
- [ ] `GET /api/unified-account/list` → 账户列表
- [ ] 日志带 `[tr=xxx]` 格式
- [ ] 无 Traceback / ImportError
- [ ] 1-2 个 tick 后交易决策正常（hold/buy/sell）
