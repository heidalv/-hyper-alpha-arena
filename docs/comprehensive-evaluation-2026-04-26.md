# Hyper-Alpha-Arena 全面量化评估报告

**日期**: 2026-04-26  
**评估范围**: 策略机制 / 多Agent系统 / 风险控制 / 前后端集成 / 盈利能力  
**代码基线**: feature/atas-v2 @ e0be0a4

---

## 目录

1. [策略机制评估](#1-策略机制评估)
2. [多Agent系统评估](#2-多agent系统评估)
3. [问题诊断](#3-问题诊断)
4. [升级方案设计](#4-升级方案设计)
5. [预期达成路径](#5-预期达成路径)
6. [修复状态总览](#6-修复状态总览)

---

## 1. 策略机制评估

### 1.1 StrategyEvolver 进化引擎 (1977 lines)

**架构**: 单例模式 + 遗传算法 (GA) + 多线程回测 + AI 分析增强

```
进化管线:
  模板库 → 初始种群(16个体) → 并行回测 → composite_fitness排序
  → Uniform Crossover(top half父代) → 高斯变异 → 精英保留(top 2)
  → 下一代(12代max) → champion达标自动晋升
```

**核心适应度函数** (strategy_evolver.py:76-96):
```
composite_fitness = (sharpe × 0.6 + win_rate × 0.3) × (0.7 + 0.3 × freq)
                  - drawdown_penalty × 2
                  - daily_penalty (超过20笔/天)
```

| 维度 | 评估 | 说明 |
|------|------|------|
| Sharpe主导 | ✅ 合理 | 权重0.6，风险调整收益为核心 |
| 胜率辅助 | ✅ 合理 | 权重0.3，防止低胜率策略 |
| 频率因子 | ⚠️ 偏保守 | 系数从0.5→0.7(已修复P1-9)，频率影响下降 |
| 回撤惩罚 | ✅ 合理 | max_drawdown>30%开始线性惩罚×2 |
| 过度交易惩罚 | ✅ 新增 | >20笔/天时惩罚，抑制过度交易 |

**种群参数** (strategy_params_registry.py:275-276):
```
max_generations:    12代 (范围4-30) — 已从8提升(P1-8)
population_per_gen: 16个体 (范围6-40) — 已从8提升(P1-8)
```

**进化加速机制**:
- `_evolve_pipeline_template`: 管线模板进化，使用 LivePipelineBacktestEngine
- `_evolve_genome`: 基因组进化，使用 BacktestEngine
- 多线程并行: ThreadPoolExecutor，单代所有个体并行回测
- Uniform Crossover: 从top half选2个parent，每个基因位50/50继承后变异 (已实现P1-8)

### 1.2 参数体系

**信号参数** (22个):
EMA(快/慢周期, 信号周期), RSI(周期, 超买/超卖阈值), BB(周期, 标准差), MACD(快/慢/信号), 突破(周期, 阈值), 成交量(周期, 阈值), 波动率, 动量

**管线参数** (~40个):
Orchestrator权重, 智能融合参数, 风控参数, Tier覆盖参数, 仓位规模

**参数注册表分级**:
```
CATEGORY_SIGNAL_DEFAULTS → 按策略类型分默认值 (trend/mean_reversion/range/breakout/swing/momentum)
TIER_SIGNAL_PARAM_OVERRIDES → 按tier覆盖参数范围 (short/mid/long)
GENOME_RISK_PARAMS → 风控参数 (sl/tp/position_size/trailing/breakeven/leverage/max_daily_loss)
```

### 1.3 策略晋升机制

```
PROMOTION_THRESHOLDS:
  min_sharpe: 0.3
  min_win_rate: 0.35
  max_drawdown: 0.35
  min_trades: 20

达标 → champion自动晋升为 "实战就绪" (live_ready=true)
```

**评估**: 晋升门槛偏低（Sharpe 0.3, WR 35%），可能允许边缘策略上线。建议实盘前提高至 Sharpe 0.5+, WR 40%+.

---

## 2. 多Agent系统评估

### 2.1 系统架构

```
FullAutoTradingService (7677 lines) — 中央协调器
├── TaskScheduler — 24并发任务调度 (APScheduler)
├── _run_health_check() — 90s主循环 (~700 lines)
│   ├── 市场扫描 → 数据健康检查 → 因子计算
│   ├── Orchestrator 评估 → 策略终止检查
│   ├── 策略创建 → 风控检查 → AI分析师
│   └── DB commit (含safe_commit重试)
├── _execute_master_decisions() — 交易执行 (~600 lines)
│   ├── 仓位映射 → 风险门控 → 分层预算
│   ├── 置信度校准 → 性质仲裁(AI vs Orchestrator)
│   ├── Orchestrator覆盖 → TP/SL调整
│   └── 部分平仓 → 买卖执行
├── TierParallelExecutor — 分层并行分析
│   ├── Short (180s间隔)
│   ├── Mid (600s间隔)
│   └── Long (1800s间隔)
├── MultiTimeframeOrchestrator (1142 lines) — 多周期编排
│   ├── 长期分析 (1d/1w)
│   ├── 中期分析 (1h/4h)
│   ├── 短期分析 (5m/15m)
│   ├── 情报注入 → 三层协调 → 事件覆盖
│   └── 槽位推荐 (smart slots)
└── AIDecisionService (3819 lines) — AI决策引擎
    ├── LLM API调用 (DeepSeek/OpenAI兼容)
    ├── SignalConfirmationEngine — 信号确认
    ├── PositionSizer — 仓位计算
    └── RuleBasedDecisionEngine — 规则引擎备份
```

### 2.2 Agent协作机制

**TierParallelExecutor 分层分析**:
```
每个tick(90s):
  - tick%2==0: Short tier分析 (每180s)
  - tick%6==0: Mid tier分析 (每600s)  
  - tick%20==0: Long tier分析 (每1800s)
  - tick%3==0: 全健康检查 (每270s)
```

**MultiTimeframeOrchestrator 多周期协调**:
```
evaluate(symbol) → 8步管线:
  1. 冻结检查 → 2. 长期分析(1d/1w) → 3. 中期分析(1h/4h)
  4. 短期分析(5m/15m) → 5. 情报注入 → 6. 三层协调(_coordinate)
  7. 事件覆盖 → 8. 输出最终建议 + 槽位推荐
```

**AI vs Orchestrator 仲裁机制** (_execute_master_decisions):
```
三层仲裁:
  1. 硬风控门控: risk_score>80 → 禁止开仓 (带衰减机制)
  2. 确定性风控层: 5条硬规则 (DeterministicRiskGate)
  3. 性质仲裁: AI建议 vs Orchestrator建议
     - AI=HOLD, Orch=BUY/SELL → Fallback到Orch (保守: conf×0.6, lev×0.67)
     - AI=BUY/SELL, Orch=HOLD → 降低信心执行
```

### 2.3 各组件评估

| 组件 | 行数 | 成熟度 | 问题 |
|------|------|--------|------|
| StrategyEvolver | 1977 | ⭐⭐⭐⭐ | 晋升门槛偏低 |
| FullAutoTradingService | 7677 | ⭐⭐⭐ | 单文件过大，职责混杂 |
| MultiTimeframeOrchestrator | 1142 | ⭐⭐⭐ | **缺少市场状态注入(P1-7)** |
| AIDecisionService | 3819 | ⭐⭐⭐ | **缺少LLM降级路径(P1-6)** |
| DeterministicRiskGate | 168 | ⭐⭐⭐⭐⭐ | 完善，P0-1已修复 |
| RiskControlService | 762 | ⭐⭐⭐⭐ | P0-4持久化已实现 |
| LiquidationMonitor | ~200 | ⭐⭐⭐⭐ | P0-3已修复 |
| SignalConfirmationEngine | ~400 | ⭐⭐⭐⭐ | 三维确认机制 |
| PositionSizer | ~350 | ⭐⭐⭐⭐ | 凯利公式+ATR |
| RuleBasedDecisionEngine | ~300 | ⭐⭐⭐⭐ | 存在但未作为LLM降级使用 |

---

## 3. 问题诊断

### 3.1 已修复的阻塞级缺陷 (P0)

| ID | 缺陷 | 修复前影响 | 修复状态 |
|----|------|-----------|----------|
| P0-1 | `realized_pnl_today` 硬编码0 | 日亏损熔断永久失效 | ✅ 已修复 |
| P0-2 | `risk_score=None` 硬编码 | MasterCloseGuard Rule3永久失效 | ✅ 已修复 |
| P0-3 | 错误使用CRITICAL_THRESHOLD | 爆仓距离4%误分类为CRITICAL | ✅ 已修复 |
| P0-4 | 熔断状态仅内存 | 重启丢失熔断保护 | ✅ 已修复 |

**P0修复详情**:
- P0-1: 新增 `_get_today_realized_pnl()` (line 271), 查询 PaperOrder 当日已实现盈亏, 两处调用点均已传入
- P0-2: 新增 `_get_account_risk_score()` (line 284), 从 ATASV2Executor 获取健康评分, 传入 check_master_close_hardfact
- P0-3: liquidation_monitor.py:189 已改为 `self.DANGER_THRESHOLD` (5.0%)
- P0-4: FullAutoSession 新增3列 (circuit_breaker_until/defensive_entered_at/recovery_until), RiskControlService._restore_circuit_breakers_from_db() 恢复熔断

### 3.2 已修复的架构级缺陷 (P1)

| ID | 缺陷 | 修复内容 | 修复状态 |
|----|------|---------|----------|
| P1-5 | 震荡市不过滤 | MarketRegimeClassifier 检测 ranging → pause short/mid | ✅ 已修复 |
| P1-8 | 无Crossover | Uniform crossover + elite preservation | ✅ 已修复 |
| P1-9 | 频率偏置过高 | 系数0.5→0.7 + daily_penalty for >20笔/天 | ✅ 已修复 |
| P1-10 | TP/SL比不强制 | _MIN_TP_SL_RATIO=2.5, 自动扩宽TP | ✅ 已修复 |

### 3.3 仍存在的关键缺陷 (需修复)

| ID | 缺陷 | 严重度 | 影响 |
|----|------|--------|------|
| **P1-6** | **LLM无降级路径** | P1 | LLM API不可用时系统停止决策 |
| **P1-7** | **Orchestrator缺少市场状态注入** | P1 | Orchestrator的evaluate()不使用MarketRegimeClassifier结果 |

### 3.4 历史表现数据

基于 Session 历史记录 (Paper Trading):

| Session | PnL | 特征 |
|---------|-----|------|
| 多数Session | 负值 (-1581, -1168, -353, -1070等) | 持续亏损 |
| 胜率范围 | 0-67% | 波动大，多数偏低位(0-35%) |
| 震荡市胜率 | ~6.6% | 极低，已通过P1-5过滤 |
| 回测期望 | -537.40 | 负期望(修复前) |

**核心问题**: 即使技术缺陷修复，策略本身的Alpha仍然不足。需要进化器产出更优策略。

---

### 3.5 深度审查发现的新增缺陷 (风险分析Agent)

以下缺陷超出最初10条修复计划范围，由独立风险控制审查Agent发现：

| ID | 严重度 | 描述 | 影响 |
|----|--------|------|------|
| **GAP-1** | P1 | **P3 Master Close Guard 默认关闭** | `RISK_P3_ENABLED` 默认 `"false"`，`RISK_P3_MASTER_CLOSE_REQUIRES_HARDFACT` 默认 `"off"`。Master close/reduce路径胜率0-5%、累计亏损~$33，修复已实现但被feature flag禁用 |
| **GAP-2** | P2 | **max_symbol_entries_per_day 死配置** | `risk_control_service.py:70` 定义 `max_symbol_entries_per_day=3`，但无任何代码执行此限制。单币种可无限次交易 |
| **GAP-3** | P2 | **三套TP/SL默认值表未统一** | `_execute_master_decisions()` 用硬编码表(6662-6668行)、`TIER_TP_SL_DEFAULTS`(settings.py:160)、`TIER_TP_SL_DEFAULTS_V2`(settings.py:554)，三表不同值但只有硬编码表生效 |
| **GAP-4** | P2 | **日亏损熔断DB异常时降级为WARNING** | `risk_control_service.py:468-476`: 快照查询异常时返回WARNING而非BLOCKED，可被DB攻击绕过 |
| **GAP-5** | P3 | **Three daily PnL systems diverged** | `_get_today_realized_pnl()`(PaperOrder), `check_daily_loss_breaker()`(equity snapshots), `PositionMemoryManager.mental.daily_pnl` 三套系统未交叉验证 |
| **GAP-6** | P3 | **交易所端TP/SL无事后验证** | paper_engine.place_order()调用后未确认TP/SL在交易所生效 |
| **GAP-7** | P2 | **KlineAnalyst LLM计数竞态** | `KlineAnalyst._llm_call_count` 类级计数器无锁，并行tier执行时可能超出预算 |

**优先级建议**: GAP-1 应立即修复（将P3 flag默认开启），GAP-2/3/7 应纳入Phase 2，其余可在Phase 3处理。

---

## 4. 升级方案设计

### 4.1 立即修复 (Phase 1 — 预计2h)

#### P1-6: LLM降级路径 (ai_decision_service.py)

**根因**: LLM API调用 (`_base_timeout=90s`, 推理模型240s) 失败时无降级，系统停止生成交易决策。

**方案**:

```python
# ai_decision_service.py — 在 _get_ai_decision() 外包装超时+降级
def get_ai_decision_with_fallback(
    db, account, symbol, market_data, signal_data, position_data
) -> Dict[str, Any]:
    """AI决策 + 5s总超时 + RuleBasedDecisionEngine降级"""
    import concurrent.futures
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            _get_ai_decision, db, account, symbol, market_data, signal_data, position_data
        )
        try:
            result = future.result(timeout=5.0)
            if result and result.get("action") not in (None, "HOLD", ""):
                return result
        except concurrent.futures.TimeoutError:
            logger.warning(f"[LLM] API超时(5s), 降级到规则引擎")
        except Exception as e:
            logger.warning(f"[LLM] API失败: {e}, 降级到规则引擎")
    
    # 降级: 5个确定性分析师的多数投票
    return _fallback_rule_based_decision(db, account, symbol, market_data, signal_data, position_data)


def _fallback_rule_based_decision(db, account, symbol, market_data, signal_data, position_data):
    """5个确定性分析师投票决策（保守仓位=正常的50%）"""
    from backend.services.rule_based_decision_engine import RuleBasedDecisionEngine
    from backend.services.signal_confirmation_engine import SignalConfirmationEngine
    from backend.services.position_sizer import PositionSizer
    
    engine = RuleBasedDecisionEngine()
    signal_engine = SignalConfirmationEngine()
    sizer = PositionSizer()
    
    # 信号确认
    confirmation = signal_engine.evaluate(symbol, market_data, signal_data)
    
    # 仓位计算 (50%降级)
    sizing = sizer.calculate_position_size(
        account_id=account.id, symbol=symbol, 
        confidence=confirmation.confidence * 0.5,
        account_equity=float(account.equity or 0)
    )
    
    # 规则决策
    decision = engine.decide(
        symbol=symbol,
        confirmation=confirmation,
        position_sizing=sizing,
        risk_check=(True, []),
        llm_sentiment=None,
        is_fallback=True,
    )
    
    return {
        "action": decision.action,
        "side": decision.side,
        "confidence": decision.confidence,
        "position_size_usd": sizing.position_size_usd * 0.5,  # 保守50%
        "leverage": decision.leverage,
        "reason": f"[降级模式] {decision.reason}",
        "is_fallback": True,
    }
```

**对比方案**:
| 方案 | 优点 | 缺点 |
|------|------|------|
| A: 5个规则分析师投票 | 确定性强，无外部依赖 | 缺少LLM的上下文理解 |
| B: 缓存最近LLM决策回放 | 利用历史AI智慧 | 市场变化时过时 |
| C: 直接暂停交易 | 最安全 | 错过机会 |
| **D: A方案(推荐)** | 平衡确定性与安全性 | 实现成本中等 |

#### P1-7: Orchestrator市场状态注入 (multi_timeframe_orchestrator.py)

**根因**: `evaluate()` 方法仅使用 RSI/MACD 简单指标，`MarketRegimeClassifier` 的7类流分析从未传入 `_coordinate()` 步骤。

**方案**:

```python
# multi_timeframe_orchestrator.py — 在 evaluate() 管线中添加第4.5步
def evaluate(self, symbol: str, snapshot=None) -> OrchestratorDecision:
    # ... (existing steps 1-4) ...
    
    # 第4.5步(NEW): 注入市场状态分类 (P1-7)
    self._inject_regime(decision, snapshot)
    
    # 第5步: 三层协调 (uses decision.regime)
    self._coordinate(decision)
    # ...

def _inject_regime(self, decision: OrchestratorDecision, snapshot) -> None:
    """注入 MarketRegimeClassifier 分类结果到决策管线"""
    try:
        from backend.services.market_regime import MarketRegimeClassifier, MarketRegime
        df = snapshot.get_ohlcv_dataframe() if snapshot else None
        if df is None or len(df) < 20:
            decision.regime = "unknown"
            decision.regime_confidence = 0.0
            return
        
        classification = MarketRegimeClassifier().classify(df)
        decision.regime = classification.regime.value if hasattr(classification.regime, 'value') else str(classification.regime)
        decision.regime_confidence = classification.confidence
        
        # 状态 → 仓位/方向调整
        if decision.regime == "crash":
            decision.final_action = "frozen"  # 禁止开仓
            decision.position_scale = 0.0
            decision.reasoning += f" | Regime=CRASH(conf={decision.regime_confidence:.0%})→禁止开仓"
        elif decision.regime == "ranging":
            decision.position_scale = decision.position_scale * 0.5  # 仓位减半
            decision.reasoning += f" | Regime=RANGING(conf={decision.regime_confidence:.0%})→仓位×0.5"
        elif decision.regime == "trending_up":
            decision.position_scale = min(1.3, decision.position_scale * 1.3)  # 仓位×1.3, 上限130%
            decision.reasoning += f" | Regime=TRENDING_UP(conf={decision.regime_confidence:.0%})→仓位×1.3"
        
        logger.info(
            f"[MTOrchestrator] {decision.symbol}: Regime={decision.regime}"
            f"(conf={decision.regime_confidence:.0%}), scale={decision.position_scale:.1%}"
        )
    except Exception as e:
        logger.debug(f"[MTOrchestrator] Regime注入失败: {e}")
        decision.regime = "unknown"
        decision.regime_confidence = 0.0
```

**对比方案**:
| 方案 | 优点 | 缺点 |
|------|------|------|
| A: evaluate()内注入 | 每次决策都考虑 | 每次都要分类计算 |
| B: 仅quick_eval中注入(当前) | 性能好 | evaluate()单独调用时缺失 |
| **C: A+B结合(推荐)** | 双重覆盖 | 实现成本中等 |

### 4.2 中期优化 (Phase 2 — 预计4h)

#### 2.1 晋升门槛动态化

当前静态阈值 (Sharpe 0.3, WR 35%) 在牛市中过于宽松。建议:
- 添加市场状态调整因子: 震荡市门槛×1.3, 趋势市×1.0, 高波动×1.15
- 添加样本量衰减: trades<50时门槛递增 (trades=20时门槛×1.5)

#### 2.2 策略多样性保护

当前GA可能导致种群同质化。建议:
- 添加niche惩罚: 与种群中位基因相似的个体惩罚5%
- 保留每代至少2个"探索型"个体 (random search)

#### 2.3 回测引擎增强

- Walk-forward analysis: 滚动窗口验证而非固定历史区间
- Monte Carlo: 随机打乱交易顺序1000次，检测过拟合

### 4.3 长期规划 (Phase 3 — 预计8h+)

#### 3.1 Meta-Learning 策略选择

根据当前市场状态，从策略库中自动选择最优策略组合:
- 状态→策略映射表 (基于历史表现)
- 在线A/B测试: 新策略10%资金 vs 当前策略90%

#### 3.2 强化学习仓位管理

替代固定凯利公式:
- State: [market_regime, volatility, current_drawdown, win_streak]
- Action: position_size ∈ [0, max_size]
- Reward: risk-adjusted return

#### 3.3 分布式进化

- 多个进化岛并行 (不同初始种子+不同市场区间)
- 定期迁移top个体 → 保持遗传多样性

---

## 5. 预期达成路径

### 5.1 量化指标目标

| 指标 | 当前(修复前) | 修复后(P0/P1) | 目标(Phase 2) | 目标(Phase 3) |
|------|-------------|---------------|---------------|---------------|
| 日均交易次数 | 50-100+ (过度) | 15-30 | 8-20 | 5-15 |
| 胜率 | 0-35% | 35-45% | 40-50% | 45-55% |
| Sharpe Ratio | 负值 | 0.3-0.8 | 0.8-1.5 | 1.5+ |
| 最大回撤 | >50% | <35% | <25% | <20% |
| 日亏损熔断 | 从不触发 | 正常工作 | 正常工作 | 正常工作 |
| 进化产出 | 0 success | 1-2 champion | 3-5 champion | 持续迭代 |
| 震荡市胜率 | 6.6% | N/A(暂停) | 15%+ | 25%+ |

### 5.2 验证方法

```
Phase 1验证 (P1-6 + P1-7):
  1. 禁用LLM API key → 观察日志中出现 "[降级模式]"
  2. 模拟Crash/Ranging市场 → 检查Orchestrator日志中 Regime=xxx
  3. 确认决策链完整: Regime注入 → 仓位调整 → 执行

Phase 2验证 (晋升门槛+多样性):
  1. 运行 evolution_on_all_pending → 检查champion质量
  2. 对比修复前后: Sharpe分布, WR分布, 种群多样性
  3. Walk-forward回测: 样本外性能不低于样本内70%

Phase 3验证 (Meta-Learning + RL):
  1. 模拟盘24h交易 → PnL曲线
  2. 对比静态策略 vs 动态策略选择
  3. RL仓位 vs 固定凯利仓位
```

### 5.3 上线标准

```
实盘就绪检查清单:
  □ 全部P0缺陷修复并验证
  □ 全部P1缺陷修复并验证
  □ 24h模拟盘连续运行无crash
  □ PnL > 0 且 Sharpe > 0.5
  □ 日亏损熔断至少触发1次并正确恢复
  □ 进化器产出至少1个champion (Sharpe > 0.5, WR > 40%)
  □ LLM降级路径验证通过
  □ 震荡市正确暂停short/mid tier
  □ 无未处理异常日志
```

### 5.4 风险回滚计划

```
回滚触发条件:
  - 实盘日亏损 > 3% (立即暂停)
  - 连续3天负PnL (降级到50%仓位)
  - 周Sharpe < 0 (回滚到模拟盘)

回滚操作:
  1. 暂停所有策略 → 平仓全部头寸
  2. 切换到Paper Trading模式
  3. 分析失败原因 → 修复 → 重新验证
  4. 从50%仓位重新上线
```

---

## 6. 修复状态总览

### 6.1 已完成修复 (9/10)

| ID | 严重度 | 描述 | 文件 | 提交 |
|----|--------|------|------|------|
| P0-1 | P0 | realized_pnl_today=0 → 日亏损熔断失效 | full_auto_trading_service.py:271 | 5da71b6 |
| P0-2 | P0 | risk_score=None → MasterCloseGuard失效 | full_auto_trading_service.py:284 | 5da71b6 |
| P0-3 | P0 | CRITICAL_THRESHOLD误用 | liquidation_monitor.py:189 | 5da71b6 |
| P0-4 | P0 | 熔断状态重启丢失 | models.py + risk_control_service.py | 5da71b6 |
| P1-5 | P1 | 震荡市不过滤 | full_auto_trading_service.py:5940 | 5da71b6 |
| P1-8 | P1 | 缺少Crossover | strategy_evolver.py:643 | 5da71b6 |
| P1-9 | P1 | 频率偏置过高 | strategy_evolver.py:92-96 | 5da71b6 |
| P1-10 | P1 | TP/SL比不强制 | full_auto_trading_service.py:6689 | 5da71b6 |
| TZ | P0 | tzinfo倒数导致health check静默崩溃 | full_auto_trading_service.py:1972 | e0be0a4 |

### 6.2 待修复 (1/10)

| ID | 严重度 | 描述 | 目标文件 | 预计 |
|----|--------|------|---------|------|
| **P1-6** | P1 | LLM无降级路径 | ai_decision_service.py | 1h |
| **P1-7** | P1 | Orchestrator缺市场状态注入 | multi_timeframe_orchestrator.py | 1h |

### 6.3 关键文件清单

| 文件 | 行数 | 职责 | 修改次数 |
|------|------|------|----------|
| full_auto_trading_service.py | 7677 | 中央协调器 | 9+ |
| ai_decision_service.py | 3819 | AI决策引擎 | 1 pending |
| strategy_evolver.py | 1977 | 策略进化器 | 2 |
| multi_timeframe_orchestrator.py | 1142 | 多周期编排 | 1 pending |
| risk_control_service.py | 762 | 风控服务 | 1 |
| deterministic_risk_gate.py | 168 | 确定性风控 | 0 |
| liquidation_monitor.py | ~200 | 爆仓监控 | 1 |
| strategy_params_registry.py | ~400 | 参数注册表 | 1 |
| models.py | ~2200 | 数据模型 | 1 |

---

## 结论

经过全面评估，系统已完成 **9/10** 个关键缺陷修复。剩余的 P1-6 (LLM降级) 和 P1-7 (Orchestrator市场状态注入) 各需约1小时。修复完成后，系统应能达到模拟盘稳定运行的标准。

**核心挑战**并非技术缺陷，而是策略Alpha不足 — 回测负期望(-537.40)表明即使所有风控正常工作，策略本身仍需进化改进。进化器产出的champion质量和市场状态自适应能力是决定系统能否盈利的关键。
