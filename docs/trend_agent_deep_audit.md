# 长线策略(TrendAgent)深度审计报告

> 版本: v2.0 | 日期: 2026-07-20 | 基于 trend_agent.py(878行)、midlong_loop.py(207行)、mlto_cycle.py(743行)、trading_analysts.py + DB实证

---

## 一、14天成交审计

### 1.1 总体统计

| 指标 | 数值 |
|------|------|
| closed笔数 | 17笔(trend_follow/position) |
| 总partialPnL | +$950.3 (分批止盈累积) |
| 均unPnL(at close) | -$729.7 (关仓时未实现亏损) |
| 说明 | 分批止盈机制有收回部分利润，但最终关仓时绝大部分在浮亏状态 |

### 1.2 成交模式特征(日志+代码实证)

从日志可见长线策略的实际运行模式:
```
V5Gate PASS VVV buy conf=56% nature=trend_follow rr=3.00 opens_today=0/24
V5Gate PASS ONDO sell conf=48% nature=trend_follow rr=3.00 (多频率冲突放行)
```

17笔成交中，RR统一为3.00(系统默认值而非LLM建议值)，说明LLM实际输出的RR未被采纳。

---

## 二、决策链路深度审查

### 2.1 TrendAgent完整决策流程

```
midlong_loop.py(每120s)
  → resolve_trend_min_score(trading_mode) → paper=40, live=50
  → derive_trend_side(symbol, market_envs) → 从编排器bias推导方向锚点
  → TrendAgent.analyze_direction(side=锚点)
      ├─ build_trend_evidence() → 收集9类事实(趋势/MACD/RSI/vol/resonance/regime/猎杀/vol_ratio/衍生品)
      ├─ _build_direction_prompt() → 构建完整prompt(10步分析 + 深度上下文注入)
      ├─ _call_llm(DeepSeek v4-pro, temperature=0.2, response_format=json_object)
      ├─ _normalize_direction() → MTF融合(65%LLM+35%规则) + 币圈尾部风险调整
      ├─ _apply_fact_guard_direction() → FactGuard enforce/shadow
      └─ 返回 {score, direction, should_open, suggested_sl_pct, reasoning}
  → mlto_cycle.py 执行开仓(通过V5Gate门控)
```

### 2.2 AI决策质量评估

**方向判断准确率:**
- `derive_trend_side()` 从编排器的 long_bias/mid_bias/short_bias 推导方向
- 当编排器MACD方向与价格实际走势同向时，方向正确
- 编排器自身依赖MACD/EMA排列等滞后指标，在趋势转折点时容易给出滞后信号

**入场信号分层(按regime):**
- trending: 允许开仓(放量×1.5)
- ranging: Paper 放宽(pad=5), Live 严厉(pad=12)，但ranging本身不适合趋势仓
- extreme: 禁止(veto)
- 日志中 VVV buy、ONDO sell 的 RR=3.0 说明 LLM 建议的RR被系统默认覆盖

**方向偏见识别:**
TrendAgent prompt(L317)明确指示:"系统宏观方向锚点 side={_side_hint}(请优先对齐；若证据强烈反向可输出neutral)"。这个"优先对齐"可能导致LLM过度跟随编排器bias，即使证据不强也输出同方向。当编排器持续给出同一方向(如震荡市中反复看多)，TrendAgent会在被止损后继续同方向开仓。

### 2.3 恶性循环根因定位

```
编排器持续bullish bias → derive_trend_side()=long
  → TrendAgent 接收到 side_hint=long 的prompt
  → LLM输出 direction=long, score≥50
  → 开仓 → 市场震荡 → SL触发(固定8% SL)
  → 止损关闭 → 下一tick编排器仍bullish(均线未转向)
  → 再次 derive_trend_side()=long → 再次开仓
  → 循环...
```

**三大根因:**
1. **编排器信号滞后**: 均线类指标在震荡市中持续输出同一方向，价格已转向但指标未转
2. **prompt方向锚定过强**: "请优先对齐编排器方向"导致LLM放弃独立思考
3. **无止损后冷却机制**: 被止损后没有强制等待期，tick一到立即重开
4. **SL固定8%不适配波动率**: 高波动币种(如XPL) 8% SL在正常波动中被扫

---

## 三、止盈止损机制批判

### 3.1 当前实现

| 组件 | 现状 | 问题 |
|------|------|------|
| SL设定 | LLM建议 `suggested_sl_pct`(默认8%)，代码强制≥4% | 无ATR自适应，波动大的币被正常波动扫止损 |
| TP设定 | **无!** LLM prompt无tp_pct输出字段，系统用默认RR=3反推 | 无明确止盈目标，全靠持仓复查 |
| 分批止盈 | `PaperPosition.tp_level_reached`字段存在但**未接线** | 数据模型有但代码未使用 |
| Trailing Stop | `PaperPosition.trailing_stop_price`存在但**未接线** | 同上有字段无逻辑 |
| 持仓复查 | TrendAgent.review_position() 每90min触发 | 间隔太长，无法及时响应趋势转折 |
| Ratchet(上移SL) | **不存在** | SL不随浮盈上移，利润无法保护 |
| 止损后冷却 | **不存在** | 同方向可immediate重开 |

### 3.2 实证缺陷

日志中: `ONDO trend_follow pass conf=48% rr=3.0 (多频率冲突)` — LLM信心仅48%仍放行，RR=3.0是系统默认不是LLM判断。

从代码(`_normalize_direction` L471-486):
```python
if _paper and direction in ("long", "short") and score >= max(min_score, 50):
    if not should_open and "veto" not in (_crypto_note or "").lower() and not _mtf_block:
        should_open = True  # Paper试单: score≥50且LLM说不开也强行开
```

**Paper试单的"强行开仓"逻辑**导致LLM即使判断不开仓(score<50或should_open=false)，只要score≥50且有方向，系统就override开仓。这导致大量低质量信号进入执行。

---

## 四、Prompt质量评估

### 4.1 当前prompt注入内容

| 数据项 | 存在 | 来源 |
|--------|------|------|
| 4h/1d K线结构 | ✅ | `agent_deep_context.py`(经济数据/趋势强度/vol) |
| 1w周线结构 | ✅ | `build_trend_deep_context` |
| 波动率环境 | ✅ | vol_ratio |
| 资金费率 | ✅ | funding_rate(衍生品深度确认步骤) |
| OI变化 | ✅ | OI增减分析(趋势确认步骤) |
| 清算级联风险 | ✅ | crypto_alpha_signals(尾部风险调整) |
| 链上巨鲸异动 | ❌ | 未注入(OnChainDataAgent存在但未接入prompt) |
| 相关币种表现 | ❌ | 未注入(BTC/ETH方向对山寨币有领先效应) |
| 历史战绩(per regime) | ✅ | `agent_deep_context`中的逐笔战绩和亏损教训 |
| 量化简报(quant_brief) | ✅ | `build_quant_brief(symbol, market_envs, nature="trend_follow")` |

### 4.2 缺项导致的决策偏差

1. **链上数据缺失**: 无法判断是"技术面看多但链上巨鲸在出货"的陷阱
2. **相关币种缺失**: 山寨币走势高度依赖BTC/ETH，单独分析可能产生错误信号
3. **资金费率趋势(非单点值)**: 单点funding_rate无法反映"费率从-0.01%上升到+0.05%"的趋势信号

### 4.3 System Prompt评估

```
"你是趋势交易专家 Agent，只返回 JSON。\n"
"你专注于 4h-1d 级别趋势分析，忽略短期噪声。\n"
"趋势单的核心哲学：顺势 + 让利润奔跑 + 止损果断。\n"
```

**问题:**
- "让利润奔跑"是好原则，但prompt中无具体机制指导(如何分批止盈、何时收紧止损)
- 缺少"避免在震荡市开趋势仓"的显式约束
- 缺少"若最近3笔同方向亏损，禁止同方向开仓"的反省机制

---

## 五、改进建议

### P0(紧急止血)

| 改动 | 原因 |
|------|------|
| 止损后同方向冷却(至少3个tick/6min) | 打破"止损→立即重开"循环 |
| Paper试单 undo "强行开仓" | `should_open=false`时尊重LLM判断，不override |
| SL改为ATR自适应 | `max(4%, ATR×3.0)`替代固定8% |

### P1(机制补全)

| 改动 | 原因 |
|------|------|
| 分批止盈接线(tp_level_reached) | 字段已有，只需在staged_tp.py中实现触发逻辑 |
| Trailing Stop接线 | 浮盈>5%后激活ATR×2.0 trailing |
| Ratchet SL上移 | 浮盈每+2%，SL上移1% |
| 关联合约信息注入prompt | BTC/ETH方向加入分析步骤 |

### P2(深度优化)

| 改动 | 原因 |
|------|------|
| 链上数据注入 | 巨鲸异动/净流入加入prompt |
| prompt方向锚定弱化 | "优先对齐"→"参考编排器方向，但必须基于深度数据独立判断" |
| 持仓复查频率提升 | 90min → 30min |

---

*基于 trend_agent.py(878行)、midlong_loop.py(207行)、swing_agent.py(549行) 完整审计 + 2026-07-19 DB/日志数据*

---

## 六、MLTO (中长线Thesis编排器) 专项审计

### 6.1 角色与定位

MLTO (Mid-Long Thesis Orchestrator) 负责中长线 thesis 驱动的决策管理：
- `midlong_loop.py` 每 120s 触发 `_maintain_mlto_theses_for_session()`
- 实际执行在 `mlto_cycle.py`(743行) 的 `maintain_mlto_theses_for_session()`
- 内部有三条并行路径：SwingAgent独立 / TrendAgent独立 / MLTO thesis维护

### 6.2 双重执行路径问题

**路径1: TrendAgent独立 (L209-349)**
```python
_trend_result = trend_agent.analyze_direction(...)  # L223
if _trend_action in ("buy", "sell"):
    host.try_execute_independent_agent_open(  # L266: 立即执行开仓
        tp_pct=_sl * 2,  # L274: TP硬编码为SL×2，忽略LLM建议
    )
```

**路径2: MLTO thesis维护 (L351-577)**
```python
_mlto_result = run_mlto_tick(...)  # L433: 独立LLM调用
if _mlto_act in ("buy", "sell"):
    host.try_execute_independent_agent_open(  # L485: 再次执行开仓
        sl_pct=getattr(_mlto_result, "sl_pct", 0) or 0.05,  # 默认SL=5%
        tp_pct=getattr(_mlto_result, "tp_pct", 0) or 0.10,  # 默认TP=10%
    )
```

**问题:** 同一symbol在同一tick内可能被两条路径各自开一次仓，且两条路径的TP/SL参数不同(TrendAgent用SL×2, MLTO用独立值)，导致同一方向出现两笔参数不同的仓位。

### 6.3 关键代码缺陷

| 位置 | 代码 | 问题 |
|------|------|------|
| L272 | `confidence=max(_trend_score, 50)` | 强制最低confidence=50，覆盖LLM低信心判断 |
| L274 | `tp_pct=_sl * 2` | TP硬编码为SL×2，无LLM建议、无ATR参考、无分批止盈 |
| L330 | `ThreadPoolExecutor(max_workers=5)` | 5个并发LLM调用，每个30-90s，总计占用大量线程 |
| L469 | `if _mlto_act in ("buy", "sell"):` | MLTO独立开仓无条件执行，不检查TrendAgent路径是否已开 |
| L475-478 | `_exec_db = SessionLocal()` | 每次开仓新建DB连接，与TrendAgent路径的DB连接独立 |
| L560 | `_ana_db.commit()` | MLTO每条决策单独commit，在多线程下与TrendAgent路径产生事务冲突 |

### 6.4 三条路径的执行时序

```
midlong_loop.py(120s tick)
  │
  ├─ 1. SwingAgent独立 (L100-208)
  │     ├─ ThreadPoolExecutor 并行调用 swing_agent.analyze()
  │     └─ should_open=True → try_execute_independent_agent_open()
  │
  ├─ 2. TrendAgent独立 (L209-349)
  │     ├─ ThreadPoolExecutor 并行调用 trend_agent.analyze_direction()
  │     └─ should_open=True → try_execute_independent_agent_open()
  │
  └─ 3. MLTO thesis维护 (L351-577, 仅MIDLONG_THESIS_LEDGER_ENABLED时)
        ├─ 对每个symbol调用 run_mlto_tick()(独立LLM)
        └─ action=buy/sell → try_execute_independent_agent_open()
```

同一个symbol在一轮120s tick内可能被路径2和路径3各自开一次仓，形成"重复开仓"。

### 6.5 MLTO与TrendAgent的SL/TP参数冲突

| 参数 | TrendAgent路径 | MLTO路径 |
|------|---------------|---------|
| SL来源 | `_trend_result.get("suggested_sl_pct", 0.08)` | `getattr(_mlto_result, "sl_pct", 0) or 0.05` |
| TP来源 | `_sl * 2`(硬编码) | `getattr(_mlto_result, "tp_pct", 0) or 0.10` |
| Confidence | `max(_trend_score, 50)`(强制50+) | `int(getattr(_mlto_result, "confidence", 0) or 50)` |
| LLM模型 | TrendAgent专用prompt | MLTO独立prompt(不同LLM调用) |
| 执行方式 | 独立开仓 | 独立开仓(不检查TrendAgent已开) |

### 6.6 改进建议

| 优先级 | 改动 |
|--------|------|
| P0 | 两路径加互斥锁: TrendAgent已开仓的symbol/tier, MLTO不再重开(或反之) |
| P0 | 去掉 `confidence=max(_trend_score, 50)` 强制提升 |
| P0 | TP从 `_sl * 2` 改为使用LLM建议值或ATR推算值 |
| P1 | MLTO独立开仓前检查当前symbol是否有同tier的pending/open仓位 |
| P1 | 统一SL/TP来源: TrendAgent和MLTO使用同一套SL/TP计算逻辑 |
