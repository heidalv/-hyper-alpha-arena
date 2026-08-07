# Alpha Arena 专有模型训练方案 v3.0

> 版本: v3.0 (架构融合版)
> 日期: 2026-04-15
> 目标: 基于 FLAG-Trader 理念，深度融合项目现有学习进化系统
> 硬件: 2x 魔改 RTX 2080Ti 22GB (44GB 总显存)

## 核心理念

本方案不是训练一个通用交易模型，而是基于项目现有架构，设计一个与Alpha Arena深度融合的决策增强引擎。

### 之前的错误方向
独立SFT微调 -> 训练一个通用加密货币交易模型 -> 无法直接集成到现有系统

### 正确的融合方向
1. 项目现有架构 (unified_learning / strategy_learning / evolution_scheduler)
2. 提取训练信号 (TradeOutcome -> Reward Signal)
3. 基于FLAG-Trader的训练框架 (Policy Gradient + Value Network)
4. 输出格式兼容 (直接替代现有ai_decision_service)

## 1. 项目架构深度解析

### 1.1 统一学习服务 (unified_learning_service.py)

TradeOutcome 数据结构 - 这是训练的燃料：
- source: str (live / paper / backtest)
- strategy_id, symbol, side, trade_nature
- pnl, pnl_pct, duration_seconds
- regime_at_entry, regime_at_exit
- fingerprint_at_entry, confidence

学习算法核心：
- EMA增量更新：new = old + alpha x (new_value - old_value)
- 三源权重：live=1.0, paper=0.6, backtest=0.3
- 亏损熔断：7/10/13/15次连续亏损触发分级保护

### 1.2 策略学习服务 (strategy_learning_service.py)

定期复盘机制：
1. 分析近期交易 -> 提取模式
2. 提取关键教训
3. 按市场状态分类分析
4. V3因子权重复盘
5. 进化提示词

StrategyMemory表 - 环境感知训练的数据基础：
- total_trades, win_rate, avg_profit, avg_loss
- sharpe_ratio, max_drawdown
- performance_by_regime: JSON (这是环境感知训练的关键)
- successful_patterns, failed_patterns, key_lessons

### 1.3 AIDecisionLog表 - 决策-结果配对数据

关键字段：
- prompt_snapshot: Text (完整LLM prompt -> 训练输入)
- reasoning_snapshot: Text (LLM推理过程 -> 训练输出参考)
- decision_snapshot: Text (LLM最终决策JSON)
- realized_pnl: Decimal (平仓后的实际PnL -> Reward Signal)
- ai_strategy_id: str (关联策略)
- decision_quality_score: float

## 2. FLAG-Trader 训练框架设计

### 2.1 核心架构

市场数据 -> 因子引擎 -> 状态编码器 -> LLM策略网络
                                             |
              Policy Head   Value Head    Reward Signal   三源交易结果
                                             |
                              联合损失函数              来自实盘/模拟/回测
                                             |
                                      奖励引擎

### 2.2 状态表示设计

将交易状态编码为LLM可理解的文本表示：
1. 市场概览: timestamp, symbol, close, volatility_24h, regime
2. 因子信号: RSI, MACD, funding_rate, oi_change等及其方向
3. 仓位状态: positions, long_exposure, short_exposure, available_margin
4. 策略配置: trade_nature, timeframe_tier, stop_loss_pct, take_profit_pct, leverage
5. 策略历史表现: total_trades, win_rate, sharpe_ratio, max_drawdown
6. 最近交易: 最近N笔交易的symbol, side, pnl_pct, regime

### 2.3 奖励函数设计

基于FLAG-Trader的多维度奖励函数：
1. 基础PnL奖励: pnl_pct * 100 (归一化)
2. Sharpe贡献奖励: 基于增量Sharpe计算
3. 风险调整奖励: 考虑回撤因素
4. 环境适应奖励: 在正确环境下做正确的事
5. 交易质量奖励: 决策质量分
6. 惩罚项: 过度交易、违反策略约束等

### 2.4 训练数据构建

从项目数据库提取 (State, Action, Reward) 训练三元组：
1. 从AIDecisionLog提取决策记录
2. 获取对应的策略上下文(AIStrategy)
3. 获取策略记忆(StrategyMemory)
4. 获取当时的因子值(SignalTradeFeedback)
5. 构建完整状态表示(StateEncoder)
6. 构建动作表示(Action)
7. 计算奖励(RewardEngine)

## 3. 融合架构的模型服务

### 3.1 训练后的模型角色

训练后的模型作为决策增强层，与通用LLM协同工作：
现有因子引擎 -> 编排器决策 -> (通用LLM + 训练模型) -> 决策融合层 -> 执行引擎

### 3.2 模型输出格式

class TradingDecision:
    operation: str (buy / sell / hold)
    symbol: str
    quantity: float
    leverage: float
    stop_loss_pct: float
    take_profit_pct: float
    confidence: float (0-1)
    reasoning: str (推理过程)
    risk_assessment: str (风险评估)
    regime_awareness: str (环境判断)

## 4. 实施路线图

Phase 1: 数据管道构建 (Week 1-2)
- [ ] 任务1.1: 实现 TrainingDataExtractor
- [ ] 任务1.2: 实现 StateEncoder
- [ ] 任务1.3: 实现 RewardEngine
- [ ] 任务1.4: 验证数据管道正确性

Phase 2: 训练框架搭建 (Week 3-4)
- [ ] 任务2.1: 模型架构设计 (Qwen3.5-9B + LoRA/QLoRA)
- [ ] 任务2.2: 训练循环实现 (PPO/GRPO策略梯度)
- [ ] 任务2.3: 训练稳定性 (梯度裁剪、学习率调度、早停)

Phase 3: 与项目集成 (Week 5-6)
- [ ] 任务3.1: 决策融合服务
- [ ] 任务3.2: 渐进式替换
- [ ] 任务3.3: 持续学习

## 5. 关键技术点

### 5.1 环境感知训练

利用 performance_by_regime 实现环境感知的策略：
- 从策略记忆中提取特定环境下的交易样本
- 样本不足时从相近环境迁移
- 支持跨环境泛化能力训练

### 5.2 因子贡献度引导训练

利用 signal_feedback_tracker 的贡献度分析引导模型关注高价值因子：
- 归一化为注意力权重
- 在损失函数中加入因子重要性正则项

### 5.3 策略一致性约束

确保模型输出与策略配置一致：
- 约束止损在策略配置的合理范围
- 约束杠杆不超过策略上限
- 约束止盈满足最低盈亏比

## 6. FLAG-Trader 论文核心要点

### 6.1 论文核心贡献

1. 参数高效微调: 部分层微调 + LoRA，保持预训练知识同时适应金融领域
2. 混合RL组件: 将外部环境奖励梯度融入策略更新，与交易绩效指标对齐
3. Actor-Critic架构: LLM作为Policy Network，Value Network评估状态价值

### 6.2 关键公式

策略梯度损失:
L_policy = -E[log pi_theta(a|s) x R(s,a)]

价值函数损失:
L_value = E[(V(s) - Q(s,a))^2]

总损失:
L_total = L_policy + lambda_v x L_value + lambda_ent x L_entropy

### 6.3 实验结果

- 在加密货币、日经225、沪深300等数据集上显著优于基线
- 135M参数模型可超越大型商业模型
- 环境适应能力强，跨市场泛化性好

## 核心结论

训练后的模型能够：
1. 理解项目的策略配置（genome, timeframe_tier, trade_nature）
2. 理解因子权重和信号贡献度
3. 理解市场环境判断（bull/bear/ranging）
4. 理解策略历史表现（performance_by_regime）
5. 输出与现有 ai_decision_service 兼容的决策格式

这样训练后的模型可以直接集成到 Alpha Arena 的决策流程中，与现有的因子引擎、编排器、风控模块无缝协作。