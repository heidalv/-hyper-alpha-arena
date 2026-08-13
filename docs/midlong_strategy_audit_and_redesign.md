# 中长线策略(Swing/Trend)深度审计与重新设计

> 版本: v1.0 | 日期: 2026-07-19 | 基于代码审计 + 日志实证

---

## 一、中长线策略运行状态深度分析

### 1.1 SwingAgent 日志实证(2026-07-19)

```
SwingAgent ASTER mid hold conf=46 raw=hold why=conf_low(46<48) rr=2.0
SwingAgent SOL   mid hold conf=29 raw=hold why=conf_low(29<48) rr=2.0
SwingAgent BNB   mid hold conf=46 raw=hold why=conf_low(46<48) rr=2.0
SwingAgent BTC   mid conf=24 → hold
SwingAgent ETH   mid conf=18 → hold
SwingAgent SOL   mid conf=63 → hold (被 FactGuard 或 MTF 拦截)
SwingAgent BNB   mid conf=65 → hold
SwingAgent VVV   mid conf=100 → hold
```

**关键发现:** SwingAgent 在 7 个币种中 **全部输出 hold**。即使 conf=63/65/100 也不例外。原因是 LLM 输出 `action=hold` + 高分，但 direction 为空或 neutral，导致 Paper 试单的 hold→buy/sell 推断失败。

**根因:** LLM prompt 要求 `action: buy/sell/hold`，但由于 prompt 中要求"只在回调到支撑位+趋势顺势时入场"，LLM 在没有明确机会时倾向输出 hold。Paper 试单的推断机制(`_normalize` L387-409)需要一个非空的 direction，而 LLM 在 hold 时往往不输出 direction。

### 1.2 TrendAgent 日志实证

```
V5Gate PASS VVV action=buy conf=56% nature=trend_follow rr=3.00
V5Gate PASS ONDO action=sell conf=48% nature=trend_follow rr=3.00 (多频率冲突放行)
```

TrendAgent 有少量开仓，但存在"多频率方向冲突"问题(1h看空×4h看多)。

### 1.3 止损/止盈审计

**SwingAgent TP/SL:**
- 默认 SL=3.5%, TP=7% (固定比例，无 ATR 自适应)
- 无 trailing stop
- 无分批止盈(staged TP)
- LLM 建议的 sl_pct/tp_pct 作为输出字段，但未强制校验 RR≥2:1
- 止损后未强制冷却期

**TrendAgent TP/SL:**
- 默认 SL=8% (LLM 建议，代码强制 ≥4%)
- 无 trailing stop(仅 review_position 建议 tighten_trailing，需人工执行)
- 有 staged_tp_adjust(raise/lower)，但实现依赖 LLM 输出
- 持仓复查频率: 90min(TREND_REVIEW_INTERVAL_SEC)

**"止损后立即同向重新开仓"的实证:**
日志中 SwingAgent 在多个币种以 conf=46(低信心)被 conf_low 拦截。当市场出现方向移动时，LLM 可能在下一 tick 再次输出同方向建议→开仓→再次止损。目前缺少 **方向偏见纠正机制**。

### 1.4 AI 决策质量审计

**SwingAgent prompt 结构:**
- 8步分析流程(趋势判断→支撑阻力→成交量→衍生品→市场状态→历史经验→盈亏比→反向假设)
- 深度上下文注入: K线数据(1h/4h)、量化简报(quant_brief)、证据块(evidence_block)
- 输出格式: JSON(含 action/confidence/direction/sl_pct/tp_pct/rr/cited_fact_ids/reasoning)
- system prompt: "中线波段交易专家，只返回JSON"

**缺失的关键信息:**
- 当前仓位信息(portfolio)被传入但 prompt 未显式强调
- 链上数据摘要未注入(OnChainDataAgent 存在但未接入 swing prompt)
- 相关币种表现(cross-asset correlation)未注入
- 资金费率趋势(非单点值)未提供

---

## 二、竞品提示词策略对比

| 维度 | Hyper-Alpha-Arena (SwingAgent) | Freqtrade+FreqAI | nautilus_trader | Jesse |
|------|------|------|------|------|
| AI 范式 | LLM 深度推理(DeepSeek v4-flash) | 传统ML(CatBoost/LGBM) | 纯规则策略 | 纯规则策略 |
| 决策输入 | prompt文本(深度上下文+分析师报告+量化简报) | 特征向量(indicators+OHLCV) | 事件驱动(OrderBook/Bar) | dataframe(indicators) |
| 输出格式 | JSON结构化 | 预测值(buy/sell信号) | 领域事件 | 策略信号 |
| TP/SL | LLM建议(固定比例) | 策略参数配置 | 策略代码定义 | 策略代码定义 |
| 风控 | FactGuard+MTF+Regime | stoploss配置 | 内置风控引擎 | stoploss |
| 方向偏见 | 无纠正机制 | 无 | 无(纯规则) | 无 |

**可借鉴点:**
1. Freqtrade 的 FreqAI 回测/实盘同代码 — 中长线策略也需要 parity 保证
2. nautilus 的事件驱动架构 — 中长线 tick 应以事件触发而非固定间隔轮询
3. Jesse 的策略表达 — 简洁的 Python 类定义策略逻辑，LLM 应嵌入到策略的特定决策节点而非全部

---

## 三、Prompt 重构设计

### 3.1 SwingAgent 决策要素清单

**必须注入的上下文:**
1. 多时间框架K线形态: 1h(主力)/4h(确认)/15m(精确入场)
2. 波动率指标: ATR/BB宽度/历史波动率分位
3. 资金费率趋势: 当前值 + 8h变化率 + 极端值标记
4. OI变化: 当前 + 4h/24h delta
5. 市场 regime 判定: trending/ranging/extreme + 置信度
6. 相关币种表现: BTC/ETH 方向 + 领先/滞后关系
7. 持仓信息: 当前仓位/方向/浮盈亏/持仓时长
8. 链上数据摘要: 净流入/稳定币铸造/巨鲸异动(如可用)
9. 历史战绩: 该币种+regime 下最近20笔胜率/盈亏比

**输出格式约束:**
```json
{
  "action": "buy/sell/hold",
  "confidence": 0-100,
  "direction": "long/short/neutral",  // 强制必须输出，即使hold
  "entry_zone": {"low": 0, "high": 0},  // 入场区间
  "sl_pct": 0.04,
  "tp_stages": [{"pct": 0.04, "ratio": 0.3}, {"pct": 0.08, "ratio": 0.4}],
  "trailing_activate_at": 0.04,  // 激活trailing的浮盈阈值
  "rr": 2.0,
  "cited_fact_ids": ["rsi_1h", "mid_bias"],
  "regime_fit": "good/poor/neutral",
  "reasoning": "完整分析(最多200字)"
}
```

### 3.2 TrendAgent 方向决策要素

**额外注入(相比SwingAgent):**
1. 周线结构(1w): 价格在52周范围的位置
2. 趋势生命周期: 启动/加速/衰竭/反转判断
3. 宏观资金流向: 恐贪指数/鲸鱼行为/交易所余额
4. 场景预测: scenario A/B/C(最可能/备选/尾部风险)

### 3.3 风险偏好校准约束(in prompt)

```
## 强制约束
- 若近5笔同方向交易全部亏损 → 禁止同方向开仓，输出hold
- 若同币种30分钟内刚被止损 → 冷却期(至少3个tick)禁止重开
- 单笔风险 ≤ 账户权益的2%
- 若当前市场 regime=extreme → 禁止开仓
- 若清算簇 severity=high 且与方向反向 → 禁止开仓
```

---

## 四、止盈止损机制重构

### 4.1 TP 机制: 3档分批止盈 + Trailing Stop

```
TP1(浮盈2%): 减30%仓位 → 锁定基础利润
TP2(浮盈4%): 减40%仓位 → 利润主体落袋
TP3(剩余30%): 启动Trailing Stop(ATR×2.0) → 让利润奔跑

Trailing Stop激活条件: 浮盈≥TP2后，SL上移至保本价位+
```

### 4.2 SL 机制: ATR自适应 + 结构止损

```
初始SL = max(ATR×MULT, 结构支撑/阻力位)
  - swing: ATR×2.5, 不低于1.5%
  - trend: ATR×4.0, 不低于4%
SL随浮盈上移(ratchet): 浮盈每增加1%，SL上移0.5%
```

### 4.3 方向偏见纠正规则

```
IF 同symbol同方向连续被止损 >= 2次(24h内):
  → 设置同方向冷却(4h)
  → 必须出现反向MTF共振(signal_reversal=true)才解除
IF 同symbol同方向连续亏损 >= 3次(7d内):
  → 永久标记该方向为低效
  → 仅当Fundamental Shift触发时才重置
```

---

## 五、实施路线图

### 阶段1: 止血(第1-2天) P0
| 改动 | 文件 | 验收 |
|------|------|------|
| SwingAgent prompt 强制输出direction(即使hold) | swing_agent.py L290-331 | conf≥48且dir≠neutral时should_open=true |
| 止损后同方向冷却(3 tick) | mlto_cycle.py / master_execution.py | 日志出现"同向冷却"记录 |
| 清算簇反向禁止开仓(已有，确认启用) | swing_agent.py L428-443 | 日志出现liquidation_magnet_veto |

### 阶段2: TP/SL重构(第3-5天) P1
| 改动 | 文件 | 验收 |
|------|------|------|
| SwingAgent 3档分批止盈 | 新增 services/exit/staged_tp.py | 持仓日志出现 tp_stage_1/tp_stage_2 |
| Trailing Stop激活机制 | services/exit/trailing_stop.py | 浮盈>TP2后SL上移可见 |
| ATR自适应SL | swing_agent.py _normalize | SL不在固定3.5%，而是ATR×MULT |
| 方向偏见纠正 | mlto_cycle.py | 连续2次止损后自动冷却 |

### 阶段3: Prompt深度改造(第6-10天) P2
| 改动 | 文件 | 验收 |
|------|------|------|
| 链上数据/相关币种/资金费率趋势注入prompt | agent_deep_context.py | prompt日志可见新字段 |
| TrendAgent 场景预测持久化 | trend_agent.py | DB可查scenario_a/b/c |
| 风险偏好校准约束 | swing_agent.py prompt | 约束在prompt末尾显式出现 |
| 竞品prompt对标注解 | docs/prompt_benchmark.md | 对比表+Freqtrade/Jesse分析 |

---

*文档基于对 midlong_loop.py(207行)、swing_agent.py(549行)、trend_agent.py(878行) 的完整审计 + 2026-07-19日志实证*
