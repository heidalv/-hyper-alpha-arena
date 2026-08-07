# 《Hyper-Alpha-Arena 架构重构与迁移方案》

> **文档版本**: v2.0 · 2026-06-19
> **状态**: ✅ 全部 6 阶段完成
> **决策**:
> - 本轮交付范围：仅方案文档（本文件）
> - 账户统一：统一服务层 + 保留双表（零数据迁移）
> - 执行层：新增双执行器代理层（live_executor.py + paper_executor.py）
>
> **进度**:
> - ✅ 阶段 0-6 全部完成
> - 97 单元测试通过，7 次生产重启验证无回归
> - 灰度启用手册: `docs/REFACTOR_GRADUAL_ROLLOUT.md`

---

## 目录
1. [现状深度扫描结论](#一现状深度扫描结论耦合点清单)
2. [模块依赖图（目标态）](#二模块依赖图重构后目标态)
3. [分阶段执行路线图](#三分阶段执行路线图6-阶段每阶段可独立回滚)
4. [数据迁移计划](#四数据迁移计划最小化)
5. [回滚策略](#五回滚策略每阶段独立)
6. [风险矩阵](#六风险矩阵)
7. [执行顺序与依赖](#七建议执行顺序与依赖)
8. [确认清单](#八本轮交付确认)

---

## 一、现状深度扫描结论（耦合点清单）

### 1. 执行层 — 4 条分散路径（最大风险源）

| 路径 | 入口 | 底层 | 持仓存储 |
|---|---|---|---|
| AI 实盘 (HL native) | `trading_commands.place_ai_driven_order:2059` → `place_ai_driven_hyperliquid_order:438` | `HyperliquidTradingClient` (SDK) | 交易所侧 |
| AI 实盘 (CCXT) | 同上 → `_execute_ccxt_ai_trade:2102` | `ExchangeManager` + adapter | 交易所侧 |
| AI 模拟 | `full_auto_trading_service.py:11621` dispatch → `_execute_paper_trade:11709` → `paper_engine.place_order:572` | `paper_exchange_simulator` | `paper_positions` 表 + 净额 |
| 套利实盘/模拟 | `ArbitrageOrchestrator:47` → `arbitrage/live_executor.py:LiveExecutor:30` | `ExchangeManager` / 自有内存 `_paper_positions` dict | 内存 + `position_persistence` |

**核心问题**：
- `backend/services/exchange/live_executor.py` 和 `paper_executor.py` **均不存在**（目标文件名）
- 实盘费率在 `hyperliquid_trading_client.py:258` 被**显式禁用**（`# 🔓 DISABLED: No builder fees`），而模拟盘在 `paper_exchange_simulator.py:93-142` 有完整 per-exchange 费率表 —— 两边数字不可比
- `full_auto_trading_service.py:11621` 的 dispatch 是唯一统一入口：
  ```python
  if trading_mode == "paper":
      self._execute_paper_trade(db, session, strat, decision_data)  # L11709
  else:
      self._execute_live_trade(db, session, strat, dec)             # L12607
  ```

### 2. 账户体系 — 两套并行 paper 账户树

| 树 | 根表 | 持仓/余额表 | FK 链 |
|---|---|---|---|
| AI Paper | `accounts` (account_type=PAPER) | `paper_balances`(1:1) + `paper_positions` + `paper_orders` + `paper_funding_ledger` | 全部 `account_id FK→accounts.id` |
| 套利 Paper | `arbitrage_paper_accounts` (`models.py:3233`) | `arbitrage_paper_exchange_balances` + `arbitrage_paper_ledgers` + `rebate_positions`(⚠️无 account FK) | `account_id FK→arbitrage_paper_accounts.id`（**与 accounts.id 列名撞名**） |

**额外问题**：
- `rebate_positions` (`models.py:3116`) **无 account_id FK**，仅靠 `position_id` 字符串标识
- `LiveExecutor._paper_positions` 是第三套内存持仓（套利 V3 专用），与上述两套都不通
- `ArbitragePaperAccountDB.owner_account_id` (`models.py:3239`) nullable，软关联到 `accounts.id`

### 3. 默认值冲突（3 处不一致 + 1 处 typo）

| 位置 | 当前默认 | 目标 |
|---|---|---|
| `settings.py:1039` `AUTO_COIN_DEFAULT_EXCHANGE` | `"hyperliquid"` | `"asterdex"` |
| `models.py:82` `Account.selected_exchange` | `"hyperliquid"` | `"asterdex"` |
| `models.py:909` `UserExchangeConfig.selected_exchange` | `"binance"` | `"asterdex"` |
| `user_routes.py:178` 白名单 | 含 `"aster"` (**typo**) | `"asterdex"` |
| `frontend/app/lib/types/exchange.ts:36` `DEFAULT_EXCHANGE` | `'hyperliquid'` | `'asterdex'` |

**注意**：套利 hub 内部已优先 asterdex：
- `ExchangeAllocationGrid.tsx:14` `EXCHANGE_ORDER = ['asterdex', 'hyperliquid', ...]`
- `engine.py:452` `s8_exchange = "asterdex"`
- `AiConfigDialog.tsx:96` 默认 `['asterdex', 'binance', 'hyperliquid']`

### 4. 费率/保证金 — 5+ 处定义，3 个不同值

| 位置 | 值/来源 | 权威性 |
|---|---|---|
| `paper_exchange_simulator.py:93-142` `DEFAULT_EXCHANGE_RULES` | per-exchange 表（最权威） | ★ 最权威 |
| `paper_trading_engine.py:48-54` `MAINTENANCE_MARGIN_RATE` | 全局 settings（**覆盖** per-exchange） | 冲突源 |
| `paper_netting.py:137,179,261,281` | 硬编码 0.005 | 低 |
| `position_tracker_service.py:209` | 硬编码 0.004 | 低（不一致） |
| `hyperliquid_trading_client.py:390` | `used_margin * 0.5` 启发式 | 实盘专用 |

**费率详情**（`paper_exchange_simulator.py`）：
```
hyperliquid: maker 0.0002 / taker 0.00035 / mmr 0.005 / min_notional 10
asterdex:    maker 0.00005 / taker 0.00005 / mmr 0.005 / min_notional 5
binance:     maker 0.0002  / taker 0.0004  / mmr 0.004 / min_notional 5
okx:         maker 0.0002  / taker 0.0005  / mmr 0.005 / min_notional 5
bybit:       maker 0.0002  / taker 0.00055 / mmr 0.005 / min_notional 5
gateio:      maker 0.0002  / taker 0.0005  / mmr 0.005 / min_notional 10
```

### 5. 日志 — 无统一 trace_id

- 两套格式配置冲突：
  - `main.py:31` `_bootstrap_logging`（**实际生效**）：`%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s`
  - `config/logging_config.py:11`（**死代码**）：`%(asctime)s - %(levelname)s - %(name)s - %(message)s`
- 无 `trace_id`/`correlation_id`/`ContextVar`/`LoggingFilter`
- Paper 用 `[Paper]` 前缀，实盘无统一前缀（`[TPSL CACHE]`/`[DEBUG __init__]`/`[FINAL]` 混杂）
- **结论**：paper 交易和 live 交易在日志中无法用共享 ID 关联

### 6. opencode — 5 文件流水线，无单例 orchestrator

入口链：
```
opencode_bridge.run_scheduled_analysis:534  (入口)
  → opencode_context_pack.build_context_pack  (L1 上下文)
  → opencode_action_router.route_analysis_result  (分发: 严重度→动作)
    → opencode_proposal_applier.create_proposal / apply_proposal / evaluate_applied_proposals  (闭环)
    → opencode_proposal_reviewer.validate_patches_hard  (硬规则门)
```

**已实现**：闭环验证（baseline vs after SRR 比对 + 自动 rollback）
**未实现**：不审查单笔决策，只在聚合参数层介入
**DB 表**：`opencode_insights` (`models.py:3447`) + `opencode_evolution_proposals` (`models.py:3464`)
**可调参数白名单**：`opencode_proposal_reviewer.py:13-23`（7 个 tuning key）

---

## 二、模块依赖图（重构后目标态）

```
┌─────────────────────────────────────────────────────────────┐
│  策略层 (零改动)                                              │
│  full_auto_trading_service  /  ArbitrageOrchestrator         │
│  rebate_arb.engine          /  手动 API                       │
└───────────────┬─────────────────────────────┬───────────────┘
                │ 统一执行接口                  │
                ▼                              ▼
┌───────────────────────────┐   ┌─────────────────────────────┐
│  ★ live_executor.py (新增) │   │  ★ paper_executor.py (新增)  │
│  - place_order(...)        │   │  - place_order(...)          │
│  - get_positions(...)      │   │  - get_positions(...)        │
│  - 内部路由 HL/CCXT        │   │  - 内部调 paper_engine+净额  │
└───────────┬───────────────┘   └───────────┬─────────────────┘
            │                               │
            ▼                               ▼
┌──────────────────────┐   ┌─────────────────────────────────┐
│ HyperliquidTrading   │   │ paper_trading_engine (已净额化)  │
│ Client / ExchangeMgr │   │ paper_exchange_simulator         │
└──────────────────────┘   └─────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  ★ unified_account_service.py (新增统一服务层)               │
│  - get_unified_paper_account(scope) → 归一化视图             │
│  - transfer_capital(from, to, amount)                        │
│  - get_combined_exposure(account_id)  ← 跨系统协调复用       │
│  内部双表共存: PaperBalance(AI) ↔ ArbitragePaperAccountDB   │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  ★ fee_schedule_service.py (新增费率中心)                    │
│  - get_fee_rate(exchange, tier, is_maker) → 单一真相源       │
│  - get_maint_margin_rate(exchange, notional) → 分档          │
│  取代 5+ 处散落定义                                          │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  ★ logging: trace_context.py (新增) + 统一 Filter            │
│  trace_id 贯穿 paper/live/套利，日志格式统一                 │
└─────────────────────────────────────────────────────────────┘
```

---

## 三、分阶段执行路线图（6 阶段，每阶段可独立回滚）

### 阶段 0：前置准备（0.5 天，零风险）
- [ ] 建分支 `refactor/unified-exec-account`
- [ ] 跑全量现有测试，记录基线（`pytest backend/tests/`）
- [ ] 快照当前 DB（`alpha_arena.db` 备份）
- [x] 编写本文档 `docs/REFACTOR_ROADMAP.md`

### 阶段 1：默认交易所 + 日志 trace_id（1.5 天，低风险）
**目标**：AsterDex 设默认 + 日志可追踪。**不触碰执行/账户逻辑。**

| 子任务 | 文件 | 改动 |
|---|---|---|
| 1.1 统一默认值 | `settings.py:1039` | `AUTO_COIN_DEFAULT_EXCHANGE` 默认改 `"asterdex"`；新增 `DEFAULT_EXCHANGE` 常量 |
| 1.2 | `models.py:82` | `Account.selected_exchange` 默认 `"asterdex"`（新账户生效，老账户不动） |
| 1.3 | `models.py:909` | `UserExchangeConfig.selected_exchange` 默认 `"asterdex"` |
| 1.4 修 typo | `user_routes.py:178` | 白名单 `"aster"` → `"asterdex"` |
| 1.5 前端默认 | `frontend/app/lib/types/exchange.ts:36` | `DEFAULT_EXCHANGE = 'asterdex'` |
| 1.6 trace_id 基础设施 | 新增 `backend/utils/trace_context.py` | `ContextVar` + `generate_trace_id()` + `LoggingFilter` 注入 `trace_id` 字段 |
| 1.7 统一格式 | `main.py:31` `_bootstrap_logging` | 格式加 `[%(trace_id)s]`；废弃 `config/logging_config.py` 死代码 |
| 1.8 中间件 | 新增 `backend/middleware/trace_middleware.py` | 每请求生成 trace_id，注入 ContextVar |
| 1.9 前缀规范 | paper/live 日志 | Paper 保留 `[Paper]`，新增 `[Live]` 前缀规范文档 |

**回滚**：`git revert` 阶段 1 commit，`DEFAULT_EXCHANGE` 环境变量覆盖。

### 阶段 2：费率中心化（1 天，低风险）
**目标**：单一费率真相源，消除 5+ 处定义。

| 子任务 | 文件 | 改动 |
|---|---|---|
| 2.1 新建费率服务 | 新增 `backend/services/fee_schedule_service.py` | 基于 `paper_exchange_simulator.DEFAULT_EXCHANGE_RULES` 提取，支持分档 maint margin |
| 2.2 替换散落定义 | `paper_trading_engine.py:48-54` | `MAINTENANCE_MARGIN_RATE` 改为调 `fee_schedule_service.get_maint_margin_rate(exchange)` |
| 2.3 | `paper_netting.py:137` 等 | 净额爆仓价改传 exchange 参数 |
| 2.4 | `position_tracker_service.py:209` | 同上 |
| 2.5 实盘费率 | `trading_commands.py` live 路径 | 实盘成交后从交易所回读实际费率，记入 `paper_orders.fee` 对应字段（可选，不阻塞） |

**回滚**：保留 `MAINTENANCE_MARGIN_RATE` 全局变量作为 fallback；`fee_schedule_service` 异常时降级。

### 阶段 3：执行层标准化（3 天，中风险）★核心
**目标**：新增 `live_executor.py` + `paper_executor.py`，策略层切换通道零改动。

#### 3.1 新建 `backend/services/exchange/paper_executor.py`
```python
class PaperExecutor:
    """模拟执行器 — 封装 paper_engine + 净额逻辑，对外提供实盘同构接口"""
    async def place_order(self, ctx: OrderContext) -> OrderResult: ...
    async def get_positions(self, account_id) -> List[Position]: ...
    async def close_position(self, pos_id, reason) -> CloseResult: ...
    # 内部: paper_engine.place_order + paper_netting 净额视图
```
- 复用现有 `paper_engine`（已净额化）+ `paper_exchange_simulator`
- **不重写**模拟逻辑，仅做接口封装 + trace_id 注入

#### 3.2 新建 `backend/services/exchange/live_executor.py`
```python
class LiveExecutor:
    """实盘执行器 — 封装 HL native + CCXT，与 PaperExecutor 同构"""
    async def place_order(self, ctx: OrderContext) -> OrderResult: ...
    # 内部路由: account.selected_exchange → HL/CCXT adapter
```
- 内部调现有 `place_ai_driven_hyperliquid_order` / `_execute_ccxt_ai_trade`（**不删原代码**，仅包一层）
- `OrderContext`/`OrderResult` 统一 dataclass（与 PaperExecutor 共享）

#### 3.3 策略层切换（渐进）
- `full_auto_trading_service._execute_paper_trade:11709` → 改调 `PaperExecutor`（保留旧路径作 fallback）
- `full_auto_trading_service._execute_live_trade:12607` → 改调 `LiveExecutor`
- `ArbitrageOrchestrator` 的 `LiveExecutor`（注意同名不同类，在 `arbitrage/live_executor.py`）→ 评估是否迁入新统一 executor（**阶段 3 暂不动**，留阶段 5）

**回滚**：`_execute_paper_trade`/`_execute_live_trade` 保留旧实现，加 `USE_UNIFIED_EXECUTOR` 开关，异常 fallback 旧路径。

### 阶段 4：账户统一服务层（2 天，中风险）
**目标**：双表共存 + 统一 API，零数据迁移。

#### 4.1 新建 `backend/services/unified_account_service.py`
```python
class UnifiedAccountService:
    def get_unified_paper_account(self, scope: str) -> UnifiedPaperAccountView:
        # scope: "ai" → PaperBalance; "arbitrage" → ArbitragePaperAccountDB
        # 返回归一化 dict: {id, equity, available, frozen, scope, source_table}
    
    def transfer_capital(self, from_scope, to_scope, amount) -> TransferResult:
        # 跨账户资金划转（记账层，不动真实资金）
    
    def get_combined_exposure(self, account_id) -> dict:
        # 跨系统敞口（复用 cross_system_coordinator 思路）
```

#### 4.2 修 rebate_positions 无 FK 问题
- **不迁移数据**，仅在 `unified_account_service` 层做软关联（通过 `position_id` 前缀或 `metadata_json` 反查 account）
- 新增 `rebate_positions.owner_account_id` 列（nullable，migration 加列，老数据留空）

#### 4.3 跨系统协调器接入
- 之前已批准的 `cross_system_coordinator.py` 改调 `unified_account_service.get_combined_exposure`

**回滚**：`unified_account_service` 纯新增，不影响旧路径；`rebate_positions.owner_account_id` 加列 nullable 无破坏。

### 阶段 5：前端整合 + opencode 适配（3 天，中风险）
#### 5.1 前端入口合并
- 参考 `ArbitrageSetupGuide.tsx:63-68` 的 4 步流，在 AI 交易员侧复用同一向导组件
- 统一账户绑定入口：`ArbitrageTraderBinding` 逻辑抽取为通用 `AccountTraderBinding`
- 新增高级配置面板：速度档位（已有 `PAPER_PACE_GEARS`）、滑点系数（新增前端控件，调 `fee_schedule_service`）、默认杠杆（已有 `default_leverage`）

#### 5.2 opencode 适配统一账户
- `opencode_context_pack.py` 的 `build_context_pack` 改调 `unified_account_service` 取账户视图
- 闭环验证 `evaluate_applied_proposals` 增加"跨账户"维度（AI paper + 套利 paper 分别评估）

### 阶段 6：审计 + 清理（1 天，低风险）
- 全量回归测试（对比阶段 0 基线）
- 删除已确认废弃的旧代码路径（阶段 3/4 的 fallback 验证稳定后）
- 文档更新：`ARCHITECTURE.md` 更新依赖图

---

## 四、数据迁移计划（最小化）

**原则**：本轮**不做表结构合并**，仅做加列 + 软关联。

| 迁移 | 类型 | 风险 | 回滚 |
|---|---|---|---|
| `accounts.selected_exchange` 老数据批量改 asterdex | UPDATE（可选） | 低 — 仅改默认，用户可手动切回 | 还原 |
| `rebate_positions` 加 `owner_account_id` 列 | ADD COLUMN nullable | 零 — nullable 不破坏 | DROP COLUMN |
| `arbitrage_paper_exchange_balances` 加注释说明 FK 指向 | 注释 | 零 | N/A |
| 老账户默认值不批量改 | — | — | 新账户才用新默认 |

**无数据导出/导入**，无表合并，无表删除。最坏情况 `git revert` + 还原 DB 备份。

---

## 五、回滚策略（每阶段独立）

1. **每阶段独立 commit + 独立分支**，可单独 revert
2. **功能开关**：
   - `USE_UNIFIED_EXECUTOR`（阶段 3）
   - `USE_UNIFIED_ACCOUNT`（阶段 4）
   - `USE_FEE_SCHEDULE`（阶段 2）
   - 默认 `false`，验证后逐个开 `true`
3. **DB 备份**：阶段 0 快照，每阶段前再快照
4. **灰度**：先 paper 模式验证新 executor，再切 live
5. **监控**：阶段 1 的 trace_id 落地后，每阶段用日志对比交易行为是否回归
6. **已有开关复用**：`PAPER_NETTING_MODE`（净额模式，已实现并验证通过）

---

## 六、风险矩阵

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 执行层切换导致交易中断 | 中 | 高 | 双路径共存 + 开关 + paper 先行 |
| 费率中心化改变历史盈亏计算 | 中 | 中 | 保留旧常量 fallback + 对账 |
| 净额保证金与历史余额不一致 | 低 | 中 | `PAPER_NETTING_MODE` 开关（已实现） |
| 前端入口合并破坏现有用户流 | 中 | 中 | 新旧入口并存 1 周 |
| opencode 闭环验证误判 | 低 | 低 | 保留 manual confirm |
| `rebate_positions` 无 FK 导致软关联失败 | 低 | 低 | 仅审计用，不影响交易 |
| AsterDex 实盘 API 限制（单向 vs 双向） | 中 | 中 | 已确认 HL/Asterdex 均为 One-Way；净额模式已适配 |

---

## 七、建议执行顺序与依赖

```
阶段0 (准备) ──▶ 阶段1 (默认+日志) ──▶ 阶段2 (费率)
                                          │
                                          ▼
                  阶段3 (执行层) ◀── 可并行 ──▶ 阶段4 (账户)
                                          │
                                          ▼
                  阶段5 (前端+opencode) ──▶ 阶段6 (审计清理)
```

**关键路径**：阶段 3（执行层）是核心，建议优先且独立验证。
**快速降险**：阶段 1/2 可快速完成（2.5 天），优先执行以降低后续阶段风险。
**并行机会**：阶段 3 和阶段 4 无强依赖，可并行（但建议串行以降低 review 负担）。

---

## 八、本轮交付确认

- ✅ **本文档即为交付物**（`docs/REFACTOR_ROADMAP.md`）
- ⏸️ **不写任何业务代码**，等待确认阶段执行
- 已确认决策：
  - 交付范围：仅方案文档 ✓
  - 账户统一：统一服务层 + 保留双表 ✓
  - 执行层：新增双执行器代理层 ✓

### 待用户确认的执行指令
确认后我将按阶段 0 → 1 → 2 ... 顺序执行，每阶段完成后停下让你 review。

可下达的指令：
- `执行阶段1` — 默认交易所 + 日志 trace_id（低风险，1.5 天）
- `执行阶段2` — 费率中心化（低风险，1 天）
- `执行阶段3` — 执行层标准化（中风险，3 天，核心）
- `执行阶段1-2` — 连续执行低风险阶段
- `执行全部` — 按 0→6 顺序连续执行（不推荐，风险高）

---

## 附录 A：关键文件索引

### 执行层
- `backend/services/trading_commands.py:2059` — `place_ai_driven_order`（实盘路由）
- `backend/services/trading_commands.py:438` — `place_ai_driven_hyperliquid_order`（HL native）
- `backend/services/trading_commands.py:2102` — `_execute_ccxt_ai_trade`（CCXT）
- `backend/services/full_auto_trading_service.py:11621` — paper/live dispatch
- `backend/services/full_auto_trading_service.py:11709` — `_execute_paper_trade`
- `backend/services/full_auto_trading_service.py:12607` — `_execute_live_trade`
- `backend/services/paper_trading_engine.py:572` — `paper_engine.place_order`
- `backend/services/exchange/paper_exchange_simulator.py:202` — `simulate_exchange_order`
- `backend/services/exchange/exchange_factory.py:52-65` — 适配器注册
- `backend/services/arbitrage/orchestrator.py:47` — `ArbitrageOrchestrator`
- `backend/services/arbitrage/live_executor.py:30` — 套利 `LiveExecutor`

### 账户/DB
- `backend/database/models.py:33` — `Account`
- `backend/database/models.py:2098` — `PaperBalance`
- `backend/database/models.py:2120` — `PaperPosition`
- `backend/database/models.py:3233` — `ArbitragePaperAccountDB`
- `backend/database/models.py:3256` — `ArbitragePaperExchangeBalanceDB`
- `backend/database/models.py:3116` — `RebatePositionDB`（⚠️无 account FK）
- `backend/database/models.py:919` — `ExchangeCredential`

### 默认值
- `backend/config/settings.py:1039` — `AUTO_COIN_DEFAULT_EXCHANGE`
- `backend/api/user_routes.py:178` — 白名单（含 typo）
- `frontend/app/lib/types/exchange.ts:36` — `DEFAULT_EXCHANGE`

### 费率
- `backend/services/exchange/paper_exchange_simulator.py:93-142` — `DEFAULT_EXCHANGE_RULES`（最权威）
- `backend/services/paper_trading_engine.py:48-54` — `MAINTENANCE_MARGIN_RATE`
- `backend/services/paper_netting.py` — 净额爆仓价（已实现，硬编码 0.005）
- `backend/services/position_tracker_service.py:209` — 硬编码 0.004

### 日志
- `backend/main.py:31` — `_bootstrap_logging`（实际生效）
- `backend/config/logging_config.py:11` — 死代码

### opencode
- `backend/services/opencode_bridge.py:534` — 入口
- `backend/services/opencode_action_router.py` — 分发
- `backend/services/opencode_proposal_applier.py` — 闭环
- `backend/services/opencode_proposal_reviewer.py:13-23` — 参数白名单

### 前端
- `frontend/app/components/arbitrage-hub/ArbitrageSetupGuide.tsx:63-68` — UX 参考流
- `frontend/app/lib/opencodeApi.ts` — opencode API 客户端
- `frontend/app/lib/arbitrageApi.ts:1240-1252` — bind-trader 流程
- `frontend/app/components/trader/TraderManagement.tsx` — AI 交易员配置
- `frontend/app/components/arbitrage-hub/ArbitrageTraderBinding.tsx:49-67` — 绑定入口

---

## 附录 B：已完成的净额重构（前置工作）

本路线图基于已完成的 Paper Engine One-Way 净额重构（2026-06-19）：
- `backend/services/paper_netting.py` — 净额计算工具（24 单元测试通过）
- `backend/services/paper_trading_engine.py` — `_recalc_balance` 净额化、`_unify_leverage_for_side` 跨方向、`get_positions` 注入 net_group 字段
- `backend/config/settings.py` — `PAPER_NETTING_MODE` 开关（默认 true）
- 生产验证：账户 5 余额正常，SOL 双空正确合并为净空 865.29

净额模式为后续执行层标准化（阶段 3）提供了基础 —— `PaperExecutor` 将直接复用净额逻辑。
