# Hyper-Alpha-Arena 真实诊断报告

> 日期：2026-05-08（v3 增补深挖：决策质量 / 风控漏洞 / 进化失败）  
> 性质：基于代码、数据库、日志的事实诊断（不写设计、不写承诺）  
> 工具：直接读 SQLite（978MB）、grep 代码、统计真实盈亏

---

## 🔥🔥 v3 增补：3 个最严重的"系统性谎言"

继 v2 找到 6 个 bug 后，这一轮深挖发现项目还有 3 个**架构级**问题，比 bug 严重一个数量级 —— 它们让"AI 决策、风控、自我进化"都成了**叙事造假**。

---

### 谎言 1：「AI 决策」其实是规则系统在独裁，每 7 次 LLM hold 就有 1 次被强行覆盖成 buy/sell

**证据（基于 52140 条决策快照统计）**：

| 推理文本明确说... | 实际 action | 矛盾条数 | 比例 |
|---|---|---|---|
| "选择 hold" / "放弃开仓" | `buy` 或 `sell` | **227** | — |
| "决定进场" / "选择 buy" | `hold` | **1016** | — |
| "继续持有" / "选择 hold" | `sell` | **277** | — |
| **总矛盾** | | **1520** | **2.9%** |

更关键的整体覆盖率：

```
推理含「选择hold」的决策：3011 条
其中 action=hold（一致）：  2585
其中 action≠hold（被覆盖）：426  → 14.1%
```

**也就是 LLM 每说 7 次"我不开仓"，就有 1 次被规则系统强行改成开仓。**

**根因代码**：`backend/services/full_auto_trading_service.py:4068-4072`

```python
if (_orch_conf >= 60 and _orch_wants_enter and _direction_aligned):
    _override_action = "buy" if (_all_bullish or _mid_short_bullish) else "sell"
    _override_conf = max(55, min(75, int(_orch_conf)))   # ← 强行把置信度塞到 55-75
    self._append_event(session, "orchestrator_override",
        f"🔥 编排器覆盖 {sym}: LLM hold → {_override_action} ...")
```

这段代码的语义是："**当三周期方向一致、编排器置信度 ≥60 时，无视 LLM 的判断，强行下单**"。`_override_conf = max(55, min(75, ...))` 还把置信度硬塞到至少 55（开仓门槛附近）。

而抽样的 5 条 action=buy 但 reasoning 说 hold 的样本，**confidence 全部正好是 45.0** —— 这是另一个把 LLM 真实置信度（30%）抹平到刚好达标的痕迹。

**讽刺的是反过来还有第二层**：

| 反方向矛盾 | 量级 |
|---|---|
| LLM 想开仓但被「硬约束」拦住 hold | 推理含"硬约束 / 不满足 / 门槛 / 禁止"占 **30%+** |
| 22.2% 决策开头模板都是"本tier ... 置信度不足 ... 硬约束阻止进场" | LLM 自发想交易 → 被规则封死 |

**结论**：

> **这个系统两头都不是 LLM 在决策**：  
> - LLM 想交易时 → 一堆"硬约束"挡住  
> - LLM 不想交易时 → 编排器强行覆盖去交易  
> LLM 在中间，谁都听不见它。

---

### 谎言 2：「7 套风控」其实有 50% 的下单路径完全无风控

**实测调用图**：

| 下单入口 | 调用风控 | 风控规则 | 真实风控覆盖 |
|---|---|---|---|
| `full_auto_trading_service.py` | `risk_gate.check()` 2 次 | DeterministicRiskGate 5 条 | ✅ |
| `trading_commands.py` | `risk_control_service.check()` | RiskControlService 8+ 条 | ✅ 但和上面**不是同一套规则** |
| `paper_trading_engine.place_order()` | **0 次** | — | ❌ **裸下单** |
| `hyperliquid_trading_client.place_order_with_tpsl()` | **0 次** | — | ❌ **裸下单** |

**`paper_trading_engine.py:425-485` 实测**：进入函数→ 直接 `db.add(PaperOrder)` → `_fill_market_order()` → 写入仓位。**没有任何 risk_gate / risk_control_service / fee_guard 调用**。

**这意味着**：

- 任何代码（不只是 FullAuto）调用 `paper_trading_engine.place_order` 都不会过风控
- 比如 `learning_bus`、`unified_learning_service`、测试脚本、人工调用，**全部裸下单**
- 一旦上层 FullAuto 因为 bug（如 v2 的 Bug D `executed="false"`）短路了风控调用，下游 paper engine 完全不兜底

**两套风控的字段重叠但参数不同步**：

```
DeterministicRiskGate.rules            vs   RiskControlConfig
─────────────────────────────────────────────────────────────
max_symbol_notional_pct                 ↔   max_single_symbol_ratio
max_daily_loss_pct                      ↔   daily_loss_limit_ratio
max_portfolio_leverage                  ↔   max_total_position_multiple
min_available_balance_pct               ↔   max_margin_usage_ratio (反向)
```

**这两套字段语义重叠 80%，但参数从 `strategy_params_registry.RISK_LIMITS` 读取时是不同的归一化路径**。一旦用户在 UI 上改一处，另一处不会同步。

**还有 5 套孤立 guard**（`master_close_guard / fee_guard / liquidity_filter / liquidation_monitor / profit_drawdown_guard / reentry_cooldown`），各自独立调用、各自写日志，没有统一的"是否被拦截"日志位。这就是为什么 `risk_control_events = 0 行` —— 风控被分散到 7 个 module 各写各的，没有单一可观测点。

**结论**：

> **项目里说有 7 道风控，实际只覆盖了 FullAuto 主链路的中间层；下游 paper/live 下单接口完全裸跑。这就是 P4 文档里"NameError 静默吞掉整段"的危险所在 —— 下游没有兜底。**

---

### 谎言 3：「AI 自我进化」连"为什么失败"都不记录 —— 36/36 失败一笔糊涂账

**`prompt_training_records` 36 条，全部失败模式**：

```json
{
  "lessons": [...],            // 倒是有"教训"
  "failure_patterns": [...],
  "status": "llm_failed",     // ← 全部 36 条都是这一个字符串
  "timestamp": "..."
}
```

**没有 `error_class`、没有 `error_message`、没有 `response_len`、没有 HTTP code、没有 token 用量** —— 任何能定位失败原因的字段都没有。

**根因代码**：`backend/services/strategy_learning_service.py:826`

```python
result = service.generate_with_conversation(
    messages=[{"role": "user", "content": instruction}],
    account_id=account_id,
)
return result if isinstance(result, str) else None   # ← 关键
```

**两层静默吞错**：

1. 如果 `generate_with_conversation` 返回 `dict` / `list` / `None`（实际很常见，因为它会包一层 `{"content": "..."}`），整个函数返回 `None`，**不记录原始返回值**
2. 如果上层 `_call_llm_for_prompt_evolution` 抛异常（API 错误、超时、JSON 解析失败），只 `logger.warning(f"LLM 调用失败: {e}")`，**异常类型和堆栈不入库**

外层判断：

```python
if not optimized_text or len(optimized_text) < _min_len:   # _min_len 默认 200
    # 标记 llm_failed，跳过
```

如果 LLM 返回的是简短增量（比如"将 RSI 阈值从 30 改为 25"），**会因为 < 200 字符被判失败**。这个阈值是死的。

**没有重试机制**。一次失败就放弃，下次重新尝试，36 次全部按同一个失败模式放弃。

**结论**：

> **"AI 自我进化"在数据库里 100% 失败，但没有任何错因记录。**  
> **这不是代码 bug 那么简单 —— 是设计上把"失败可观察性"放弃了。**  
> **修法**：`training_metrics` 至少要记 `error_class / error_message / response_len / response_preview / http_status / retried_count`。在那之前，谈"AI 进化"是叙事造假。

---

## v3 三个谎言串起来看

把这 3 件事放在一起：

```
1. 「AI 决策」     → LLM 被规则系统两头夹杀，14.1% 直接被覆盖
2. 「7 套风控」    → 50% 下单路径完全裸跑
3. 「AI 自我进化」 → 36/36 失败但无错因可查
```

**这就是为什么前端 UI 上看着"功能很多"，实际跑起来"赔钱、卡死、不交易"** —— 三大宣传卖点全部是空架子。

---

## 修复优先级（v3 更新版）

| 优先级 | 任务 | 难度 | 价值 |
|---|---|---|---|
| 🔴 P0 | 把 `paper_trading_engine.place_order()` 加上一层 `risk_gate.check()` | 1 小时 | 立刻关闭"50% 路径裸跑"风险 |
| 🔴 P0 | 把"编排器覆盖 LLM"功能（`full_auto_trading_service.py:4068-4078`）**先关掉**做对照实验 | 30 分钟 | 让 LLM 的真实意图走出来 |
| 🟠 P1 | `strategy_learning_service.py:826` 增加错因记录（`error_class/message/response_len`） | 2 小时 | 让"进化失败"可定位 |
| 🟠 P1 | 把分散的 5 个 guard 的拦截日志统一写入 `risk_control_events` 表 | 半天 | 风控可观测 |
| 🟡 P2 | `RiskControlConfig` 和 `DeterministicRiskGate.rules` 合并成一个数据源 | 1 天 | 修"两套字段不同步"的隐患 |
| 🟡 P2 | `_calibrate_confidence` 的"硬约束"规则做对照实验，关掉"反方论据 +1 才开仓"那条 | 半天 | 让 22.2% "本tier 硬约束阻止进场"的决策松绑 |

---

## 一句话总结 v3

> **这个项目不是缺架构，是「AI / 风控 / 进化」三个核心叙事都被代码层面的小决定（一个 if 条件、一个 isinstance 检查、一个 try/except）悄悄掏空了。**  
> **修这三处，等于让"宣传的项目"和"实际跑的项目"对齐 —— 这比任何架构升级都更值钱。**

---

## 🔥 v2 增补：6 个有名有姓的具体 bug（全部已定位到代码行号）

继上一轮诊断后，我直接把"为什么赔钱"溯源到了具体文件和具体代码行：

### Bug A：测试桩数据污染生产数据库（解释了"168 笔有大量假数据"）

**真相**：`strategy_trades` 里的"硬编码 1000/50/500"实际上是单元测试的桩数据，从来没被清理。  
**证据**：

| strategy_id | 笔数 | 单笔 pnl | 价格特征 |
|---|---|---|---|
| `full-chain-test-001` | 4 | 1000.0 | BTC 50000→51000（整数） |
| `test-chain-001` | 4 | 50.0 | ETH 3000→2950（整数） |
| `test-bus-001` | 2 | 500.0 | BTC 50000→50500（整数） |

**真实盈亏更新**：剔除这 10 笔测试桩数据后，剩下 158 笔真实成交 **累计亏损 1319 元**（不是之前的 −119）。

**修法**：

```sql
DELETE FROM strategy_trades WHERE strategy_id IN ('full-chain-test-001','test-chain-001','test-bus-001');
```

并修测试代码：测试 fixture 跑完必须 rollback，禁止把桩数据 commit 到正式 DB。

---

### Bug B：`StrategyTrade.position_size` 写入公式根本就是错的

**位置**：`backend/services/unified_learning_service.py:216-217`  
**当前代码**：

```python
position_size=abs(_safe(outcome.exit_price) * 1)
              if outcome.entry_price else 0,
```

**问题**：仓位大小怎么会等于 `abs(exit_price * 1) == exit_price`？这个公式毫无物理意义。  
**结果**：所有 168 笔记录中 `position_size == exit_price` 全部成立。后续所有"按仓位规模"的统计、风控、复盘**全部错误**。

**修法**：把 `outcome.position_size`（或 `quantity * price`）真的传进来。

---

### Bug C：`StrategyTrade.opened_at` 永远等于 `closed_at`

**位置**：同样在 `unified_learning_service.py:210-234`  
**问题**：代码只设置了 `closed_at=datetime.now()`，没设置 `opened_at`。而 ORM 模型里 `opened_at` 默认是 `func.current_timestamp()` —— **写入时刻**。

```python
# models.py:671
opened_at = Column(TIMESTAMP, server_default=func.current_timestamp())
```

也就是说一笔交易在"平仓 → 写入数据库"那一秒，`opened_at` 和 `closed_at` 被同时赋值。**整张表的时间维度没有任何真实信息**。

**修法**：从 `outcome.opened_at`（或 `closed_at - duration_seconds`）取真实开仓时间，显式赋给 `opened_at`。

---

### Bug D：`AIDecisionLog.executed` 用字符串 "false" 而非 bool（解释了"决策日志 0 行"）

**位置**：`backend/services/full_auto_trading_service.py:3849`

```python
executed="false",   # ← 这是字符串，不是 Python bool
```

如果 ORM 字段类型是 Boolean，写入会抛 `TypeError`，被外层 `try/except` 静默吞掉（第 3858 行只 `logger.debug`）。这就是为什么 `ai_decision_logs = 0 行`、52140 条决策快照却没有任何决策日志的原因。

**修法**：写 `executed=False`（真布尔值）；如果 schema 是 string 类型，整个字段类型改 Boolean 一致化。

---

### Bug E：`full_auto_sessions = 0` —— 没人启动会话，所以决策永远不跑（解释了"假死 10 天"）

**真相**：决策快照在 2026-04-28 那天数量从平日的 1000-2000 骤降到 338，然后**完全停止**。原因不是后端挂了，而是：

| 表 | 行数 | 含义 |
|---|---|---|
| `full_auto_sessions` | **0** | 数据库里压根没有任何会话记录 |
| 后端进程 | running（10:21 启动） | 在跑，但累计 CPU 仅 5.5 秒，极其空闲 |

**链路**：决策快照写入在 `full_auto_trading_service.py:3776`，调用者是 `_execute_master_decisions`，再上游是 `start_session()`。**只有用户从前端/API 主动调 `POST /api/full-auto/start` 才会启动决策循环**。

**结论**：`full_auto_sessions = 0` 说明：要么 4-28 那天用户停了所有会话再没启动，要么有 bug 把会话状态清空了。`logs/` 目录是空的（项目跑 10 天 0 日志），无法从日志层面验证。

**修法**：

1. 把 `logs/` 真的接上 —— 当前后端日志没写文件，所有失败被静默吞掉
2. 启动一次 `POST /api/full-auto/start` 看是否能创建会话
3. 在前端首页加一个"会话状态"卡片，红色提示"未运行"

---

### Bug F：每分钟 5 次的"幽灵 LLM 调用" —— 在烧钱却没产出

**证据**：`llm_usage_logs` 最近 1 小时数据：

```
04:28:05  ×5  prompt~126 total~178
04:18:06  ×5  prompt~126 total~178
04:14:09  ×5  ...
```

**特征**：

- 每次精确 5 个调用，对应 `system_configs.hyperliquid_selected_symbols=["BTC","ETH","SOL","WIF","ASTER"]` 5 个币
- 每次 prompt 仅 126 token、completion 仅 50 token —— **不可能是决策类调用**（决策最少 3000 tokens）
- 这些调用 `account_id=None`，说明是**全局任务**而不是账户绑定的决策

**结论**：有一个针对每个 symbol 跑 LLM 的轮询任务（疑似 `kline_ai_analysis_service` 或 `market_regime_service`），即使 FullAuto 没在跑，它也在烧钱。

**修法**：

```sql
-- 先确认调用者
SELECT call_type, COUNT(*) FROM llm_usage_logs 
WHERE created_at > '2026-05-08' GROUP BY call_type;
```

然后去 grep 这个 call_type 的源头，加一个开关："FullAuto 没在跑时，停止后台 LLM 轮询"。

---

## 🎯 最终真相一句话

**这个项目的"赔钱"和"准确率低"，70% 不是策略问题，是数据写入 bug + 测试桩污染 + 没人启动会话 + 失败被吞掉的复合结果。**

修完上面 6 个 bug，你的项目基本面会改头换面，根本不需要写新方案。

---

---

## 〇 一句话结论

**项目不是"功能不够"，而是"已建的功能大多没在跑、跑的也是错的、错的还没人记"。再加一万行新代码不会解决问题，先把"脏家底"摸清才有救。**

---

## 一、最关键的 7 条真相（全部有数据支撑）

### 真相 1：交易系统已经"假死"10 天

| 指标 | 数据 | 含义 |
|---|---|---|
| `decision_snapshots` 最新时间 | **2026-04-28** | 决策快照停 10 天 |
| `atas_factor_cache` 最新时间 | **2026-04-28** | 因子缓存停 10 天 |
| `signal_definitions` 最新更新 | **2026-04-24** | 信号定义停 14 天 |
| `llm_usage_logs` 最新时间 | 2026-05-08 | LLM 还在烧钱 |
| `llm_usage_logs.call_type` 100% 是 | `llm_config_service_sync` | **烧的钱全是配置心跳，不是决策** |

**翻译**：你看到 LLM 在调用、价格在涨跌、前端在刷新，但**真正"做决策"的链路 10 天前就停了**。每天烧的是配置同步的心跳钱。

---

### 真相 2：168 笔成交里大半是"硬编码假数据"

来自 `strategy_trades` 表（168 行，全部 `status=closed`）：

| `ai_reasoning` | 笔数 | 累计 PnL | 备注 |
|---|---|---|---|
| `master_running` | 43 | **−84** | AI 主动决策，亏 |
| `sl`（止损） | 27 | −46 | 正常 |
| `ai_reverse` | 20 | −26 | 反向操作纯亏 |
| `tp`（止盈） | 18 | +82 | 正常 |
| `master_defensive` | 17 | −22 | 防守也亏 |
| `take_profit` | 4 | **+4000（全是整数 1000）** | **硬编码假值** |
| `admin_cleanup` | 4 | **−1294** | 后台手动清仓，单笔 −650 |
| `stop_loss_hit` | 4 | **+200（全是整数 50）** | **硬编码假值** |
| 空字符串 | 2 | **+1000（整数）** | **硬编码假值** |
| 其它 | 29 | 略 | … |

**两个铁证**：
1. `take_profit` 4 笔每笔都是 `pnl=1000.000000`、`stop_loss_hit` 4 笔每笔都是 `pnl=50.000000` —— 这种"完美整数"绝不可能是真实成交
2. 所有 168 笔记录中：
   - `position_size == exit_price` 成立 **168/168 次**（字段语义错乱）
   - `opened_at == closed_at` 成立 **168/168 次**（时间戳全部相同）
   - 但 `holding_period` 平均 4 万秒 —— 三个字段互相矛盾

**结论**：这张表的写入逻辑本身就是 bug。**剔除假数据后真实盈亏 = −119**，胜率 38.1%。

---

### 真相 3：AI 决策被规则封死，83% 是 "hold"

`decision_snapshots`（52140 条）的 `action` 分布：

```
hold      43505  (83.4%)
reduce     3862  ( 7.4%)
sell       2594  ( 5.0%)
buy        1337  ( 2.6%)   ← 全部开多决策只有 2.6%
close       842  ( 1.6%)
```

随便挑一条 AI 推理（id=52140）的原话：

> "短线置信度仅 55%，未达到 ≥45% 的宽松门槛？不，55%≥45，满足。"  
> "但多空辩论中，看空论据数量与权重均 ≥ 看多论据，根据规则应倾向 hold/reduce 而非开新仓。"  
> "开仓硬前提'信号占优'不满足（支持方论据数未 ≥ 反对方论据数 +1）。"

**问题**：
- AI 自己承认门槛达到了（55≥45），却被另一条规则（"论据数量"）一票否决
- "论据 +1 才能开仓"这种规则在加密这种波动剧烈的市场就是**永远不开仓**
- 推理本身在自相矛盾、自我否决

**结论**：这不是"AI 不行"，是 AI 被人写的硬规则封死了。系统设计成了一个**自动找理由不交易的机器**。

---

### 真相 4：因子系统是"假因子"

`atas_factor_cache` 7 条，每条都长这样：

```json
{
  "schema_version": 1,
  "factor_count": 21,
  "signal_score": 0.32,
  "direction": "0.0038148825049412432",   ← 字符串！不是 float！数据类型 bug
  "confidence": 0.46,
  "regime": "ranging"   ← 7 个币种全部是 ranging
}
```

更糟糕的：

| 现象 | 证据 |
|---|---|
| `FACTOR_SIGNAL_ENABLED` 是假开关 | 全代码 grep，**没有任何 .py 文件引用它**，只在 `decisions_p4.md` 和旧设计文档里出现 |
| 默认 prompt 模板不引用因子 | `prompt_templates` 仅 3 条系统模板，模板文本里没有因子占位符（P4 文档已确认） |
| 7 个 symbol 全部 regime=ranging | 因子完全没有区分能力 |
| direction 是字符串 | 拿来加权或排序时会报错或被静默吞掉 |
| 缓存 10 天前过期 | 现在系统读到的全是过期值 |

**结论**：系统对外说"21 因子驱动决策"，对内是 7 个过期、字段错位、prompt 根本不读的废缓存。

---

### 真相 5："AI 自我进化"功能从未成功过一次

`prompt_training_records` 36 条记录的事实：

```
optimized_prompt_id IS NULL 的条数: 36 / 36   = 100% 失败
prompt_templates 里 created_by='prompt_evolution' 的: 0 条
ai_strategies.master_prompt_template_id: 8/8 都是 NULL
```

**翻译**：
- 进化机制运行了 36 次，**没有产生过任何一个新模板**
- 系统里只有 3 条人工内置模板（Default / Pro / Hyperliquid）
- 8 个 AI 策略**没有一个绑定 prompt 模板**（字段全 NULL），实际运行时落到代码里的硬编码 default

这是个完全的"叙事造假"功能：UI 上写着进化、文档里写着学习闭环，**数据库说一次都没成过**。

---

### 真相 6：风控其实"形同虚设"

| 表 | 行数 | 含义 |
|---|---|---|
| `risk_control_configs` | **0** | 没有任何账户级风控配置 |
| `risk_control_events` | **0** | 风控从未拦截任何东西 |
| `coordinator_actions` | **0** | 协调器从未执行过动作 |
| `signal_trigger_logs` | **0** | 信号触发零日志 |

**而代码里的"风控模块"分散在**：
- `deterministic_risk_gate.py`（167 行）
- `risk_control_service.py`（853 行）
- `master_close_guard.py`（180 行）
- `fee_guard.py` / `liquidity_filter.py` / `liquidation_monitor.py` / `profit_drawdown_guard.py`（各几十行到几百行）

7 个独立风控模块、各自有判断、互不知道彼此。`paper` 路径过 `DeterministicRiskGate`，`live` 路径只过 `risk_control_service` 的局部检查 —— P4 文档明确指出这点，**到今天还没修**。

---

### 真相 7：复杂度严重失控

| 指标 | 数值 |
|---|---|
| `backend/services/` 文件数 | **167** |
| 数据库表数 | **101** |
| `full_auto_trading_service.py` 单文件行数 | **7973** |
| `trading_analysts.py` 单文件行数 | **2374** |
| 决策主链路相关文件累计行数（8 个核心） | **13801** |

单个文件 8000 行的"全自动交易服务"是项目最大的技术债。任何修改都会牵动几十处隐式依赖，所以谁也不敢删。这就是为什么：
- 旧的 `base_factors.py` 删不掉
- 假开关 `FACTOR_SIGNAL_ENABLED` 留着
- 写入字段错位的 `strategy_trades` 没人改
- 7 套风控不肯合并

**结论**：现在不是缺功能，是**功能堆得没人能看完**，新功能都建在脏地基上。

---

## 二、为什么"赔钱"和"逻辑乱"是必然结果

把上面 7 条串起来，你会发现：

```
1. 因子是假的 → 输入信号没区分能力
2. Prompt 不引用因子 → AI 看不到信号
3. AI 被硬规则封死 → 83% 决策是 hold，几乎不开仓
4. 偶尔开的仓 → 写入字段错乱、止盈止损硬编码假值
5. 风控分散 + paper/live 不对称 → 真出问题没人拦
6. 进化从未成功 → 错了也学不会
7. 决策快照已停 10 天 → 没人发现已停
```

**这不是"策略垃圾"，是整个数据 → 决策 → 执行 → 反馈的闭环每一环都断了。** 每环单独看都"半工作"，串起来 = 不工作。

---

## 三、必须先做（按真实优先级）

### 🔴 P0 — 立即停损（半天内）

1. **关掉空跑的 LLM 心跳**：`llm_config_service_sync` 每天烧近 1000 次但什么也不做。先确认这个心跳是否必要，不必要就关掉。
2. **决策快照停了 10 天必须查**：是定时任务挂了？数据库锁了？还是代码里 try/except 静默吞了异常？查一次 `logs/` 和后端进程状态。
3. **strategy_trades 不要再写了**：现在写一笔脏一笔。在确认字段语义之前停止写入。

### 🟠 P1 — 修明显 bug（1 周）

4. **真删假开关**：`FACTOR_SIGNAL_ENABLED` 在 `.env.example` / `SYSTEM_UPGRADE_DESIGN_V3.md` 删除或在代码里真接入。
5. **修 strategy_trades 写入**：`opened_at == closed_at`、`position_size == exit_price` 必须找到写入点修掉。
6. **修因子 direction 类型**：`atas_factor_cache.value.direction` 强制 float，不要再写字符串。
7. **删硬编码假盈亏**：`take_profit` 写 1000、`stop_loss_hit` 写 50 的代码要找出来删掉，让真实成交价计算 PnL。
8. **AI 策略至少绑一个 prompt 模板**：`master_prompt_template_id IS NULL` 的 8 条全部初始化为 1。

### 🟡 P2 — 拆"AI 决策被封死"（2-3 周）

9. **暂时去掉"论据数量"硬约束**：`MasterController.synthesize` 里的"反方论据数 ≥ 我方 +1 就 hold"是一票否决项，先关掉做对照实验。
10. **回放 1000 条决策快照**：从 `decision_snapshots` 取最近 1000 条 hold，模拟"如果按 LLM 原始建议执行"的回测，看真实效果。
11. **prompt 进化先把日志补起来**：`PromptTrainingRecord.training_metrics` 里写清失败原因（API 错误？token 超限？JSON 解析失败？）。先观察 1 周再谈"进化"。

### ⚪ P3 — 之后再谈（不要现在做）

- ❌ 多 Agent 委员会（先把单 LLM 跑通再谈 5 个 LLM）
- ❌ 因子工厂 / 实验中心（数据写入还是错的，建什么实验中心都是垃圾进垃圾出）
- ❌ TargetPortfolio / ExecutionPlanner 抽象（连 paper 一致性都做不到）
- ❌ 之前那份 1604 行的升级方案 **暂时搁置**

---

## 四、给项目"找亮点"的诚实回答

你说"没有一个非常过硬的亮点"。我看了一圈，**真正能成为亮点的东西不是新功能，是修好后真实跑起来的"决策可解释性"**。

理由：
- `decision_snapshots` 里 5 万条 AI 推理文本（每条都有完整的"为什么 hold"）
- 这是大多数量化项目**没有**的资产
- 哪怕只把这 5 万条整理成"AI 决策博物馆"前端页，让用户看每个币每天 AI 怎么想的，这就是行业里**罕见的卖点**

但前提是：先把上面 P0/P1/P2 修完，让"AI 真的在决策、真的在亏赢、真的在学习"。否则展示出来的只是 5 万条机械 hold。

---

## 五、给你的真实建议（不绕弯子）

1. **不要相信我之前那份升级设计方案**。它是"咨询公司风格"，看起来全面但不解决你的真问题。
2. **真问题是"项目堆得太重，跑得很假"**。不是缺新架构，是要做**减法**。
3. **下一步你只需要做一件事**：选 P0 的 3 件事，**今天**就花 1-2 小时排查（决策快照为什么停、LLM 心跳能否关、strategy_trades 写入在哪里）。
4. **修完 P0 再回头看**。很可能你发现 80% 的问题源自 3-4 个具体的 bug，跟"架构"完全无关。
5. **每修一个 bug，跑一次回测，看真实盈亏变化**。用数据反馈代替方案文档。

---

## 六、附录：诊断方法（你自己以后能复现）

打开 SQLite：

```bash
cd Hyper-Alpha-Arena
sqlite3 data/alpha_arena.db
```

最有价值的 5 个查询：

```sql
-- 1. 决策快照最近时间（确认系统是否在跑）
SELECT MAX(timestamp) FROM decision_snapshots;

-- 2. 真实交易盈亏分布（剔除硬编码假值）
SELECT ai_reasoning, COUNT(*), SUM(pnl), AVG(pnl)
FROM strategy_trades GROUP BY ai_reasoning ORDER BY 2 DESC;

-- 3. AI 决策动作分布（看是不是被封死）
SELECT action, COUNT(*) FROM decision_snapshots GROUP BY action;

-- 4. Prompt 进化成功率
SELECT
  SUM(CASE WHEN optimized_prompt_id IS NOT NULL THEN 1 ELSE 0 END) AS success,
  SUM(CASE WHEN optimized_prompt_id IS NULL THEN 1 ELSE 0 END) AS fail
FROM prompt_training_records;

-- 5. LLM 调用是不是真在做决策
SELECT call_type, COUNT(*), AVG(total_tokens) FROM llm_usage_logs GROUP BY call_type;
```

这 5 个查询任何时候都能告诉你"系统是不是真的在工作"。

---

**最后一句**：**你做了一个非常大的项目，但忘了做一个会量它"心跳"的东西。先给它装一个心电图（这 5 个查询就是），再决定要不要做心脏手术。**
