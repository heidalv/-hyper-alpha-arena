# 全自动模拟交易实盘监测诊断报告（Round 4）

> **日期**：2026-05-08  
> **触发**：用户启动 paper 模式全自动交易，要求实地监测、验证交易/AI 策略逻辑修改是否正确，并继续找逻辑问题。  
> **范围**：从 fa_8b35998200 启动到首笔 ETH 成交的端到端体检 + 修复。

---

## 一、执行摘要（一图看懂）

| 指标 | 启动时（07:20 UTC） | 中间状态 | 修复完成（08:11 UTC） |
|---|---|---|---|
| 30min 决策 | 5 条（**全部被拦**） | 0 条（循环挂死） | 1 条 → 1 单成交 |
| 30min 风控拦截 | 2 条 unified_blocked | 异常 UnboundLocalError | 0 条（正常放行） |
| Paper 订单 | 0 | 0 | **1 笔 filled** |
| 当前持仓 | 0 | 0 | **1 个 ETH long** |
| Total Equity | $100（错） / 实际 $10,000 | $10,000 | $9,986 |
| 系统状态 | 🔴 100% 拦截 | 🔴 主循环抛错 | 🟢 正常运行 |

**结论**：从启动开始**全自动从未真正下过单**，本次监测共发现 **5 个致命 bug**（其中 3 个是阻塞性的），全部修复后已下首笔 ETH long。

---

## 二、本次发现并修复的 5 个 Bug

### Bug #1: Paper 账户余额默认值过低，且账户错位（致命）

**症状**  
风控反复拦截："单币种ETH仓位$160.00超过限制$25.00 (25%权益)" — 系统认为权益只有 **$100**，但实际 PaperBalance 表里 `total_equity = $10,000`。

**根因**  
1. Session `fa_8b35998200` 关联到 `account_id=3`（"主力"账户，`trading_mode=live`），并非真正的 PAPER 账户（id=4）。
2. account_id=3 没有 PaperBalance 记录，被 `_get_or_create_balance` 自动创建为 `$100` 的默认余额（`paper_trading_engine.py:2261-2263` hardcoded `$100`）。
3. AI 决策每次给 $160 名义敞口，刚好 1.6× 假权益，必然被拦死。

**修复**  
- 把 `account_id=3` 的 PaperBalance 充值到 $10,000（紧急救火）。
- `_get_or_create_balance` 默认值从 `$100` → `$10,000`，并优先读 `accounts.initial_capital`、`PAPER_DEFAULT_BALANCE` 环境变量；触发自动创建时记 `WARNING` 级日志，提示这是异常路径。

**关键文件**：`backend/services/paper_trading_engine.py:2252-2289`

---

### Bug #2: `full_auto_trading_service._get_balance_info` 方法不存在（致命，静默吞）

**症状**  
代码 `_bal_info = self._get_balance_info(db, account_id)` 在两处被调用，但该方法**全代码库没有定义**。每次调用必抛 `AttributeError`，被外层 `except Exception as _rg_err: logger.debug(...)` 静默吞掉，导致：
- 第 7005 行的 unified_check **永远不跑**（"统一风控跳过" debug 日志）
- 第 7096 行的 sub_position_manager.review_open 接到 `total_equity=0`

**根因**  
重构时方法名笔误，方法没实现，但 `try/except` 把错误吞掉了。

**修复**  
两处都改为 `paper_engine.get_balance(db, account_id) or {}`。

**关键文件**：`backend/services/full_auto_trading_service.py:7005`, `7096`

---

### Bug #3: 风控基准错误（notional 而非 margin）— 100% 拦截杠杆账户（致命）

**症状**  
余额修正后，下一轮拦截理由变成："品种 ASTER 名义 16000 > 25%×10000" — AI plan 给出的 notional 是 $14,400-$16,000（10x 杠杆 × 16% margin），永远超 25% 名义上限。

**根因**  
- `deterministic_risk_gate.check`：`max_symbol_notional_pct = 25%` 用 **notional/equity** 做基准，10x 杠杆下等价于 2.5% margin，对杠杆永续合约**过严**。
- `risk_control_service.check_single_symbol_limit` 和 `max_position_per_trade` 同样用 notional → notional 一旦放大就被拦。
- 原代码只对 `equity < $500` 启用 margin 基准（"小资金特判"），$10,000 账户被默认锁死。

**修复**（双层同步）  
1. `deterministic_risk_gate.py:82-105`：Rule 1 全账户统一用 **margin/equity** 基准，名义敞口由 Rule 4 `max_portfolio_leverage` 独立约束。
2. `risk_control_service.check_all`：新增 `order_margin` 参数；`single_symbol_limit` 和 `max_position_per_trade` 优先用 margin 基准。
3. `unified_risk_gate.unified_check`：把 `margin` 透传给 stateful 层。

**验证**（runtime 三组测试）  
| 输入 | 期望 | 实际 |
|---|---|---|
| 16% margin, 10x lev | ✅ 通过 | ✅ passed |
| 26% margin, 10x lev | ❌ 拦 (>25%) | ❌ blocked deterministic/symbol_margin |
| 21% margin, 10x lev | ❌ 拦 (>20% 单笔) | ❌ blocked stateful/max_position_per_trade |

**关键文件**：
- `backend/services/deterministic_risk_gate.py:82-112`
- `backend/services/risk_control_service.py:160-237, 268-340`
- `backend/services/unified_risk_gate.py:158-162`

---

### Bug #4: `_consec_loss_pause` 未初始化 → 主循环抛 UnboundLocalError（致命）

**症状**  
后端日志反复打：
```
[FullAuto] 统一循环异常 fa_8b35998200: cannot access local variable '_consec_loss_pause'
File "full_auto_trading_service.py", line 6414
UnboundLocalError: cannot access local variable '_consec_loss_pause'
```
**整个 `_run_quick_orchestrator_eval` 在第一个策略循环就崩溃**，每分钟一次。

**根因**  
变量在循环体内（第 6430 行）才定义，但第 6414 行就引用，首次进入循环时未初始化。

**修复**  
在 `for strat in matching` 循环外初始化 `_consec_loss_pause = False`，让首次循环可以安全进入；后续循环复用上一轮的值。

**关键文件**：`backend/services/full_auto_trading_service.py:6394-6414`

---

### Bug #5: 4 个文件缺 `timezone` 导入 → 策略创建/分析报告/信号统计全部抛错（致命）

**症状**  
日志反复：
```
[FullAuto] 为 BTC 创建策略失败: name 'timezone' is not defined
[FullAuto] 为 ETH 创建策略失败: name 'timezone' is not defined
... (7 个币种全部失败)
```
导致 `total_strategies_created = 0`，新策略全建不出来。

**根因**  
4 个文件用了 `datetime.now(timezone.utc)` 但**没在文件级 import `timezone`**：
1. `backend/api/ai_strategy_routes.py` — 全文件无 import datetime（4 处使用）
2. `backend/api/signal_routes.py` — 局部 import 没带 timezone
3. `backend/api/smart_signal_routes.py` — 局部 import 没带 timezone
4. `backend/services/trade_planner_agent.py` — 局部 import 没带 timezone
5. `backend/services/exchange_incentive_monitor.py` — 局部 import 没带 timezone

可能源于上一轮"datetime.timezone → timezone"的批量修改时未补回 import。

**修复**  
- `ai_strategy_routes.py`：文件顶部加 `from datetime import datetime, timezone`，并修正一处 `dt.timezone.utc` → `tz.utc`。
- 另 4 个文件的局部 import 都补上 `, timezone`。
- 自查脚本扫剩余：`0` 个文件遗漏。

**关键文件**：见上述 5 个文件。

---

## 三、修复后的端到端验证（08:11 UTC）

### 首笔成交记录
```
Order #1
  symbol: ETH
  side:   buy (long)
  status: filled
  qty:    7.0180
  fp:     $2281.62
  lev:    8x
  margin: $2001.55  (20% 权益, 正好踩到 max_position_per_trade 上限)
  fee:    $5.60     (0.07%, 合理 maker+taker 平均)
  pnl:    None      (持仓中)
```

### 持仓
```
Position #1
  ETH long
  size:        7.0180
  entry:       $2281.62
  mark:        $2280.45
  leverage:    8x
  unrealized:  -$8.20  (-4.1% leveraged)
  TP:          $2536.33  (+11%)
  SL:          $2177.26  (-4.6%)
  tier:        mid
  nature:      swing
```

### 余额
| 项目 | 启动 | 当前 | 变化 |
|---|---|---|---|
| total_equity | $10,000 | $9,986.20 | -$13.80 |
| available | $10,000 | $7,992.84 | -$2,007.16 (frozen) |
| frozen_margin | 0 | $2,001.55 | +$2,001.55 |
| unrealized_pnl | 0 | -$8.20 | 持仓浮亏 |
| total_fee_paid | 0 | $5.60 | 已扣手续费 |

### 风控状态
- 30min 内 0 条 `unified_blocked`（彻底放行）
- 12 个活跃策略
- 死锁救援仍在跑：6 个 symbol 在 ranging regime 被暂停后强制恢复（**Workaround，不是根治**）

---

## 四、还能继续深挖的逻辑问题

### 🟡 问题 A：震荡市暂停 → 死锁救援 → 永远循环（设计缺陷）

**现象**  
日志反复：
```
[QuickEval] ETH 市场状态=ranging (conf=60%), 暂停short/mid
[QuickEval] ETH/mid 震荡市暂停midtier，策略暂停
[QuickEval] ETH/short 震荡市暂停shorttier，策略暂停
[QuickEval] ETH 死锁检测：所有策略暂停但无风险事件，强制恢复
```

**含义**  
当前 6/7 个 symbol 都被判定为 `ranging`（震荡市），系统按规则暂停 short/mid tier，但因为该 symbol 没有 long tier 策略，触发"所有策略暂停"，进入死锁救援，**强制全部恢复**。

**这等于规则形同虚设**：暂停 → 立刻强制恢复 = 没暂停。  
**真正问题**：要么"震荡市暂停"规则有问题（标准过松），要么 long tier 没创建（数据/策略库缺失）。

**建议**  
1. 监测 1-2 小时，统计实际成交是否多发于这种"假暂停"环境，验证规则是否合理。  
2. 如果是 long tier 缺失：用 `_register_default_long_tier_for_paper` 一类的初始化器补全。  
3. 把救援动作落盘到 `risk_control_events` 让前端可见。

---

### 🟡 问题 B：完整决策周期偏低（每 270 秒 1 次）

**现象**  
`_FULL_CHECK_EVERY_N_TICKS = 3`，90s × 3 = 270s 才跑一次 `_run_health_check`（真正能下单的路径），其余 tick 只做暂停/恢复。

**风险**  
- 短线 scalp/intraday 类策略对入场时机敏感，3 分钟决策一次明显不够。
- 风险事件（清算、急速回撤）反应可能滞后。

**建议**  
- 让短线策略可以独立触发（事件驱动 + 阈值）。  
- short tier 的全自动可以缩到 60-90s 一次完整 tick。

---

### 🟡 问题 C：Hyperliquid 衍生品 API connection refused

**现象**  
`Hyperliquid API 异常: [Errno 61] Connection refused` 反复出现（每次循环 7 次）。  
但**不影响核心交易**（K-line、价格抓取走的是 ccxt 的 hyperliquid，正常工作）。

**含义**  
- `derivatives_analytics_service.py:302` 直连原生 Hyperliquid REST 端口被拒（沙箱/代理问题）。
- 资金费率、未平仓量降级为 ccxt 公开数据 + 本地估算（看到 "fallback OI change" 日志在跑）。

**建议**  
检查 .env 里 `HYPERLIQUID_API_BASE` 是否指向了某个被防火墙拦截的内网地址，或本地 mock。

---

### 🟡 问题 D：单笔保证金贴上限开仓（20% margin = max_position_per_trade）

**现象**  
首笔 ETH 用 $2,001.55 margin，正好等于 `max_position_per_trade_pct = 20%` × 权益 $10,000 = $2,000。AI 决策器似乎倾向"贴上限开仓"。

**含义**  
- 单一币种亏损直接吃 20% 权益（杠杆 8x → 5% 价格波动就 -$1,000）。
- 如果同时持 3 个币种贴上限，总仓位 60% margin × 8x = 480% notional，扛不住任何反向冲击。

**建议**  
- 把"贴上限"规则化：单笔 margin 推荐 **8-12%**，留余量给加仓和反手。
- AI 决策 prompt 里增加 "建议保留至少 50% available_balance" 约束。

---

### 🟡 问题 E：决策端 LLM 偏空，但系统已开 long（决策一致性）

**观察**  
- 30min 内 5 个决策快照：3 个 hold、2 个 sell、0 个 buy，但成交是 1 个 buy。
- AI 看空多于看多，但实际 long 持仓建立 — 说明**短线 LLM tier 决策**和**完整 master 决策**用了不同信号。

**风险**  
快评 tier（短线）和 master 决策不同步，可能导致 AI 短线认为该减仓但 master 选择持有，最终止损被砍掉。

**建议**  
master 决策时把短线 tier 决策快照也喂进去，做"一致性辩论"。

---

## 五、本轮修复合规性验证（自检）

| 检查项 | 结果 |
|---|---|
| `_get_or_create_balance` 默认值已升级 | ✅ |
| `_get_balance_info` 调用都改成 `paper_engine.get_balance` | ✅ (2/2) |
| Deterministic Rule 1 用 margin 基准 | ✅ |
| Stateful single_symbol_limit + max_position_per_trade 用 margin 基准 | ✅ |
| `unified_check` 把 margin 透传给 stateful | ✅ |
| `_consec_loss_pause` 循环外初始化 | ✅ |
| 5 个文件 datetime/timezone 导入修齐 | ✅ |
| 全局扫描剩余缺 import 文件 | ✅ 0 个 |
| Linter 错误 | ✅ 0 个 |
| 实盘验证：能开仓 | ✅ ETH 8x long, $2002 margin |

---

## 六、下一步监测重点

1. **15-30 分钟内**观察是否有 ETH 仓位的 TP/SL 触发、是否出第二笔订单。
2. **1-2 小时**累计 5-10 笔后看胜率/手续费/平均盈亏比。
3. **24 小时**看震荡市暂停-救援循环是否影响 PnL。
4. **持续**观察 master 决策频率（理论 270s 一次）和 LLM 短线决策的一致性。

---

## 七、附：本轮修改文件清单

```
M backend/services/paper_trading_engine.py
M backend/services/full_auto_trading_service.py
M backend/services/deterministic_risk_gate.py
M backend/services/risk_control_service.py
M backend/services/unified_risk_gate.py
M backend/api/ai_strategy_routes.py
M backend/api/signal_routes.py
M backend/api/smart_signal_routes.py
M backend/services/trade_planner_agent.py
M backend/services/exchange_incentive_monitor.py
+ docs/full_auto_monitoring_round4_2026-05-08.md
```
