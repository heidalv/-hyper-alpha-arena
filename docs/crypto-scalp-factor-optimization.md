# 加密货币短线因子策略优化 + 因子全链条闭环方案

> 版本：v1.0 | 日期：2026-07-30
> 基于：代码盘点(13文件/20+参数) + 竞品调研(Hummingbot/Freqtrade/Jesse) + 学术文献(20篇arXiv)

---

## 一、因子完整链条现状与断点

### 1.1 链条 8 环节审计

| # | 环节 | 代码 | 实际运行 | 状态 |
|---|---|---|---|---|
| 1 | **挖掘(Mine)** | AlphaMiner mine_random(占位) + LLM Codegen(占位) | 随机AST 0命中；LLM未接入 | ❌ 空壳 |
| 2 | **验证(Evaluate)** | factor_evaluator (真forward IC) + factor_backtest_scorer (walk-forward 3折) | 代码完整但只对custom_factor_store候选跑过(23个全rejected) | ⚠️ 部分 |
| 3 | **清洗(Purge)** | purge_pipeline (正交去冗余) | 从未对994个AI因子跑过 | ❌ 缺失 |
| 4 | **晋升(Promote)** | ShadowJudge状态机(ORTHO→PAPER→SMALL_LIVE→ACTIVE) | factor_active_set已填100个(手动)，但影子期从未真实运行 | ⚠️ 部分 |
| 5 | **引用(Reference)** | base_factors._load_active_custom_factors + _load_active_evolution_factors | 994个AI因子直接import进registry，**无任何验证就上线** | ❌ 核心缺陷 |
| 6 | **监控(Monitor)** | factor_ic_evaluator(每日) + factor_decay_monitor(内存) | IC评估在跑(969因子)，但decay_monitor重启清零 | ⚠️ 部分 |
| 7 | **降权(Downweight)** | runtime_weights.json (胜率<40%→0.25) | 在跑，350个F级已退役 | ✅ 运行 |
| 8 | **退役(Retire)** | factor_quality_reports.grade=F → 权重0.1 | 已执行手动退役 | ✅ 完成 |

### 1.2 核心断点

**断点 1（最严重）：994个AI因子无验证直接上线**
- 994个`ai_gen_*.py`文件被`factor_loader.py`直接import进registry
- **从未走过验证→清洗→晋升闸门**
- 41%的因子IC为负（反向有害），只有40%有效

**断点 2：挖掘器空壳**
- AlphaMiner的`mine_random`是纯随机AST采样，0命中
- LLM Codegen是占位（`self._has_llm = False`，硬编码返回示例表达式）
- factor_discovery.py 和 ai_factor_discovery_service.py 都有代码但从未被调度

**断点 3：影子期从未运行**
- ShadowJudge状态机完整(ORTHO→PAPER→SMALL_LIVE→ACTIVE)
- 但`paper_sharpe`是ICIR×√252的代理值，不是真实模拟
- `live_deviation`依赖未接入的DualTrackExecutor

**断点 4：回测验证只对候选跑，不对存量因子跑**
- factor_backtest_scorer的walk-forward回测只针对custom_factor_store的新候选
- 994个已在线AI因子从未做过walk-forward样本外验证

---

## 二、参数优化方案（基于代码盘点+竞品+文献）

### 2.1 指标周期适配加密5m节律

**文献依据**：Hurst从0.42→0.49(2402.11930)意味着市场趋有效，传统因子需更高频或叠加加密原生因子；5m/1H自相关显著(2003.13517)。

**竞品参考**：Hummingbot默认3m+MACD 21/42/9；Freqtrade RSI 14但经优化常偏离。

| 指标 | 当前 | 优化 | 理由 |
|---|---|---|---|
| RSI | 14 (5m×14=70min) | **7** (5m×7=35min) | 覆盖半小时动量；加密反转更快 |
| ATR | 14 (5m×14=70min) | **20** (5m×20=100min≈1.7h) | 覆盖1-2个交易时段节律 |
| MACD | 12/26, signal 9 | **8/21, signal 5** | 更快响应；Hummingbot用21/42/9 |
| EMA 趋势 | 9/21/50 | **8/13/21** | 斐波那契数列适合加密短周期 |
| BB/σ/zscore | 20 | **40** (5m×40≈3.3h) | 覆盖半天波动 |

### 2.2 资金费率计入EV持仓成本

**文献依据**：资金费率异方差+Granger因果(1912.03270)；资金感知做市优于经典(2605.06405)。

**当前**：`scalp_ev_gate.py:163` 的 `round_trip_cost = (fee+slip)*2`，**缺失资金费率**。

**修复**：
```python
funding_rate = market_data.get("funding_rate", 0) or 0
expected_hold_hours = 0.5  # scalp平均30分钟
funding_cost = abs(funding_rate) * expected_hold_hours / 8
round_trip_cost = (fee_rate + slippage) * 2 + funding_cost
```

### 2.3 TP/SL随regime自适应

**竞品参考**：Hummingbot用triple_barrier_config(止损/止盈/时间限制三重)；Jesse支持分批止盈。

**当前**：RR固定2.5，不区分震荡/趋势。

**修复**：
```python
if regime == "ranging":
    rr_mult = 1.5; sl_range = (0.008, 0.020)  # 薄利多开
elif regime == "trending":
    rr_mult = 2.5; sl_range = (0.005, 0.015)  # 紧止损追势
else:  # volatile/crash
    rr_mult = 0; sl_range = (0.015, 0.030)  # 不开仓
```

### 2.4 周末/低流动性时段过滤

**文献依据**：周末波动率略低(2111.15351)；UTC 22:00-00:00流动性最薄。

**当前**：grep确认整个scalp子系统0处weekend/weekday判断。

**修复**：scalp_loop开仓前检查UTC时段，周末缩仓50%+提高wick阈值1.5倍。

### 2.5 持仓上限收紧

**当前**：`AUTO_COIN_MAX_HOLD_HOURS_SHORT=72`（3天），实际scalp<30min。

**修复**：改为2h（已有SCALP_TIME_STOP_MINUTES=15兜底，但settings层仍需收紧）。

### 2.6 引入加密原生因子

**文献依据**：OFI是最稳定短线IC因子(2602.00776)；一刻钟周期效应(2607.09426)；链上流入1-6h预测力(2411.06327)；USDT mint事件5-30min预测(2501.05232)。

| 因子 | 数据源 | 频率 | IC证据 |
|---|---|---|---|
| 订单流失衡OFI | LOB实时 | 1s-1m | 最稳定(2602.00776) |
| 一刻钟开盘效应 | 整点时间 | 15min | 样本外可预测(2607.09426) |
| 资金费率(成本+信号) | 交易所API | 8h | Granger因果(1912.03270) |
| 链上净流入 | 链上数据 | 1-6h | 日内预测力(2411.06327) |
| USDT mint事件 | Whale Alert | 5-30min | 事件型正因子(2501.05232) |
| 清算热力图 | Coinglass | 实时 | 实务强(3.51%日强平) |

---

## 三、因子全链条闭环修复方案

### 3.1 挖掘环节修复

**方案：接入LLM Codegen**

当前`alpha_miner.py:298`的`generate_and_audit`是占位。接入DeepSeek（已在系统中配置）：

```python
def generate_and_audit(self, prompt, existing_pool_exprs=None):
    from backend.services.llm_config_service import call_llm_api_sync
    cfg = get_llm_config_for_analysis(None)
    resp = call_llm_api_sync(cfg, [
        {"role": "system", "content": "你是量化因子研究员，只返回JSON格式表达式AST"},
        {"role": "user", "content": prompt}
    ], temperature=0.3, max_tokens=4096, response_format={"type": "json_object"})
    candidate = parse_llm_to_ast(resp)
    result = audit(candidate)  # look-ahead检测
    return CodegenResult(expr_ast=candidate if result.ok else None, ...)
```

### 3.2 验证环节修复

**方案：对994个存量AI因子做walk-forward验证**

当前`factor_backtest_scorer`只跑custom_factor_store候选。需要：

1. 遍历994个AI因子，对每个因子用6个月5m K线跑walk-forward(3折)
2. 训练窗口IC定方向 → 验证窗口算净收益/Sharpe/胜率
3. 输出IC_mean/ICIR/halflife → 写入factor_quality_reports
4. IC<-0.02的标记F级退役，IC>0.02的标记B级进active_set

### 3.3 清洗环节修复

**方案：对994个因子做正交去冗余**

当前`purge_pipeline`从未对AI因子跑过。需要：
1. 计算所有B级因子(414个)的IC时间序列相关矩阵
2. 层次聚类，|corr|>0.8的只保留IC最高的
3. 目标：994→≤50个去冗余因子

### 3.4 影子期修复

**方案：真实paper trading影子期**

当前ShadowJudge的`paper_sharpe`是ICIR×√252代理值。需要：
1. 候选因子在paper账户跑7天真实信号
2. 记录每笔信号的forward return
3. 7天后算真实Sharpe → Sharpe>0.5 + IC>0.02 → 晋升ACTIVE

### 3.5 引用环节修复

**方案：只有active_set的因子才加载**

当前`base_factors._merge_registry`直接import所有994个AI因子。改为：
1. 只import `factor_active_set` 中 state=ACTIVE 的因子
2. 其余的因子文件移到quarantine目录
3. `factor_loader.py` 只扫描active因子

---

## 四、回测验证框架

| 维度 | 方法 | 文献依据 |
|---|---|---|
| 数据 | 5m K线 6个月(含趋势+震荡+暴跌) | — |
| 滑点 | 按订单规模vs顶档深度，价格回复型(2305.07559) | 非固定bps |
| 手续费 | Aster maker 0.005%×2 | — |
| 资金费率 | 8h结算 × 持仓时长 × 费率 | 1912.03270 |
| 清算 | 模拟维持保证金，3X多/5X空 | 2102.04591 |
| 重尾 | 滚动窗 + t分布 | 2402.11930 |
| 存活筛选 | 排除已退市币种 | 2308.08554 |
| 频率 | 因子研究5m，OFI用1s | 2607.09426整点效应 |

---

## 五、分阶段实施路线图

### 阶段 1（P0，2-3天）：参数适配 + EV修复

| # | 任务 | 文件 | 优先级 |
|---|---|---|---|
| 1.1 | 指标周期改5m适配 | `base_factors.py` RSI7/ATR20/MACD8-21/EMA8-13-21 | P0 |
| 1.2 | 资金费率计入EV | `scalp_ev_gate.py:163` | P0 |
| 1.3 | 持仓上限72h→2h | `settings.py:1942` | P0 |

### 阶段 2（P1，3-5天）：regime自适应 + 时段过滤

| # | 任务 | 文件 | 优先级 |
|---|---|---|---|
| 2.1 | TP/SL regime自适应 | `structure_stop_calculator.py:94` | P1 |
| 2.2 | 周末/时段过滤 | `scalp_loop.py` | P1 |
| 2.3 | 清算区SL buffer | `structure_stop_calculator.py` | P1 |

### 阶段 3（P1，3-5天）：存量因子验证+清洗

| # | 任务 | 说明 | 优先级 |
|---|---|---|---|
| 3.1 | 994个AI因子walk-forward验证 | 每个因子6个月5m回测，输出IC/ICIR/halflife | P1 |
| 3.2 | 正交去冗余 | 414个B级因子→≤50个 | P1 |
| 3.3 | 因子文件quarantine | 非active因子移到隔离目录，不再import | P1 |

### 阶段 4（P2，长期）：LLM挖掘+影子期+原生因子

| # | 任务 | 说明 | 优先级 |
|---|---|---|---|
| 4.1 | LLM Codegen接入 | AlphaMiner调DeepSeek生成因子表达式 | P2 |
| 4.2 | 真实影子期 | 候选因子paper跑7天真实Sharpe | P2 |
| 4.3 | OFI因子 | 接入LOB数据计算订单流失衡 | P2 |
| 4.4 | 链上因子 | USDT净流入/USDT mint事件 | P2 |
| 4.5 | 回测框架 | tick级回测+价格回复型滑点+清算模拟 | P2 |
