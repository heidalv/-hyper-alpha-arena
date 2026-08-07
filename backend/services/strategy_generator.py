"""
Strategy Generator - 统一策略生成引擎

ATAS系统的核心策略生成模块，实现端到端的策略生成流程：
1. 从市场分析到策略生成
2. 整合中长期规划与短期执行
3. 统一信号系统与因子分析
4. 策略生命周期管理

Author: ATAS System v4.0
"""

import time
import logging
import threading
import uuid
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
import json

logger = logging.getLogger(__name__)


# ============================================================================
# 枚举和数据类型定义
# ============================================================================

class StrategyPhase(Enum):
    """策略阶段"""
    DRAFT = "draft"              # 草稿 - 配置中
    ANALYZING = "analyzing"      # 分析中 - 正在生成
    READY = "ready"              # 就绪 - 待激活
    ACTIVE = "active"            # 激活 - 执行中
    PAUSED = "paused"            # 暂停
    COMPLETED = "completed"      # 完成
    CANCELLED = "cancelled"      # 取消


class StrategyHorizon(Enum):
    """策略时间跨度"""
    INTRADAY = "intraday"        # 日内 (1-24h)
    SWING = "swing"              # 波段 (1-7d)
    POSITION = "position"        # 中期 (1w-1m)
    LONG_TERM = "long_term"      # 长期 (1m+)


class RiskProfile(Enum):
    """风险偏好"""
    CONSERVATIVE = "conservative"  # 保守 (低风险低收益)
    MODERATE = "moderate"          # 稳健 (中等风险收益)
    AGGRESSIVE = "aggressive"      # 激进 (高风险高收益)


@dataclass
class StrategyConfig:
    """策略配置"""
    # 基本信息
    name: str = "未命名策略"
    description: str = ""
    
    # 交易标的
    symbols: List[str] = field(default_factory=lambda: ["BTC", "ETH"])
    
    # 时间跨度
    horizon: StrategyHorizon = StrategyHorizon.SWING
    
    # 风险配置
    risk_profile: RiskProfile = RiskProfile.MODERATE
    max_position_pct: float = 25.0      # 单仓最大占比 %
    max_total_exposure: float = 80.0    # 总敞口最大 %
    max_daily_loss_pct: float = 5.0     # 日最大亏损 %
    stop_loss_pct: float = 3.0          # 止损百分比
    take_profit_pct: float = 6.0        # 止盈百分比
    
    # 信号配置
    enabled_signal_pools: List[int] = field(default_factory=list)  # 启用的信号池ID
    min_signal_strength: float = 0.6    # 最小信号强度
    
    # 因子权重
    factor_weights: Dict[str, float] = field(default_factory=dict)
    
    # 执行配置
    auto_execute: bool = False          # 自动执行
    require_confirmation: bool = True   # 需要确认
    max_leverage: float = 3.0           # 最大杠杆


@dataclass
class StrategyPlan:
    """生成的策略计划"""
    # 中长期规划
    market_cycle: str = "unknown"
    cycle_confidence: float = 0.0
    position_bias: str = "neutral"
    key_support: float = 0.0
    key_resistance: float = 0.0
    
    # 短期战术
    tactical_action: str = "wait"
    tactical_confidence: float = 0.0
    entry_timing: str = "standard"
    suggested_entry: float = 0.0
    suggested_stop_loss: float = 0.0
    suggested_take_profit: float = 0.0
    
    # 信号聚合
    active_signals: List[Dict] = field(default_factory=list)
    signal_consensus: str = "neutral"   # bullish/bearish/neutral
    signal_strength: float = 0.0
    
    # 因子分析
    key_factors: Dict[str, float] = field(default_factory=dict)
    factor_score: float = 0.0
    
    # 风险评估
    risk_score: float = 0.0             # 0-100
    volatility_level: str = "normal"
    
    # 推荐动作
    recommended_actions: List[Dict] = field(default_factory=list)


@dataclass
class ExecutionState:
    """执行状态"""
    is_active: bool = False
    trades_today: int = 0
    pnl_today: float = 0.0
    pnl_total: float = 0.0
    win_rate: float = 0.0
    last_trade_time: Optional[datetime] = None
    pending_orders: List[Dict] = field(default_factory=list)
    active_positions: List[Dict] = field(default_factory=list)


@dataclass
class Strategy:
    """完整策略实例"""
    # 唯一标识
    strategy_id: str = ""
    
    # 配置
    config: StrategyConfig = field(default_factory=StrategyConfig)
    
    # 当前计划
    plan: StrategyPlan = field(default_factory=StrategyPlan)
    
    # 执行状态
    execution: ExecutionState = field(default_factory=ExecutionState)
    
    # 生命周期
    phase: StrategyPhase = StrategyPhase.DRAFT
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    activated_at: Optional[datetime] = None
    
    # 关联账户
    account_id: Optional[int] = None
    environment: str = "testnet"
    
    # 分析历史
    analysis_history: List[Dict] = field(default_factory=list)


# ============================================================================
# 策略生成引擎
# ============================================================================

class StrategyGenerator:
    """
    统一策略生成引擎
    
    核心功能：
    1. create_strategy() - 创建新策略
    2. generate_plan() - 生成策略计划
    3. activate_strategy() - 激活策略
    4. update_strategy() - 更新策略
    5. get_execution_status() - 获取执行状态
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        
        # 策略存储 (内存)
        self._strategies: Dict[str, Strategy] = {}
        self._strategies_lock = threading.Lock()
        
        # 活跃策略
        self._active_strategy_id: Optional[str] = None
        
        logger.info("[StrategyGenerator] 初始化完成")
    
    def create_strategy(
        self,
        config: Optional[StrategyConfig] = None,
        account_id: Optional[int] = None,
        environment: str = "testnet",
        symbol: Optional[str] = None,
    ) -> Strategy:
        """
        创建新策略
        
        Args:
            config: 策略配置 (可选，使用默认值)
            account_id: 关联账户ID
            environment: 交易环境
            symbol: 主要交易标的（用于因子推荐）
            
        Returns:
            Strategy 策略实例
        """
        strategy_id = str(uuid.uuid4())[:12]
        config = config or StrategyConfig()
        
        # V3 整合：从因子引擎获取推荐因子组合
        recommended_factors, recommended_weights = self._recommend_factors(
            symbol or (config.symbols[0] if config.symbols else "BTC")
        )
        if recommended_factors and not config.factor_weights:
            config.factor_weights = recommended_weights
        
        strategy = Strategy(
            strategy_id=strategy_id,
            config=config,
            account_id=account_id,
            environment=environment,
            phase=StrategyPhase.DRAFT,
        )
        
        # 存储因子推荐结果到策略元数据
        if recommended_factors:
            strategy.analysis_history.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "factor_recommendation",
                "enabled_factors": recommended_factors,
                "factor_weights": recommended_weights,
            })
        
        with self._strategies_lock:
            self._strategies[strategy_id] = strategy
        
        logger.info(
            f"[StrategyGenerator] 创建策略 {strategy_id}: {config.name}, "
            f"factors={len(recommended_factors)}, weights={len(recommended_weights)}"
        )
        return strategy
    
    def generate_plan(
        self,
        strategy_id: str,
        force_refresh: bool = False
    ) -> Optional[StrategyPlan]:
        """
        生成策略计划
        
        整合中长期规划、短期战术、信号系统、因子分析，
        生成可执行的策略计划。
        
        Args:
            strategy_id: 策略ID
            force_refresh: 强制刷新数据
            
        Returns:
            StrategyPlan 策略计划
        """
        strategy = self.get_strategy(strategy_id)
        if not strategy:
            logger.error(f"策略不存在: {strategy_id}")
            return None
        
        # 更新阶段
        strategy.phase = StrategyPhase.ANALYZING
        strategy.updated_at = datetime.now(timezone.utc)
        
        config = strategy.config
        plan = StrategyPlan()
        
        logger.info(f"[StrategyGenerator] 开始生成策略计划: {strategy_id}")
        
        try:
            # 1. 获取统一数据快照
            from services.unified_data_pool import get_unified_data_pool
            data_pool = get_unified_data_pool()
            
            snapshot = data_pool.capture_snapshot(
                symbols=config.symbols,
                account_id=strategy.account_id,
                environment=strategy.environment,
                include_klines=True,
                include_strategy=True,
            )
            
            if not snapshot:
                logger.warning("无法获取数据快照")
                strategy.phase = StrategyPhase.DRAFT
                return None
            
            # 2. 提取中长期规划
            strat = snapshot.strategy
            plan.market_cycle = strat.market_cycle
            plan.cycle_confidence = strat.cycle_confidence
            plan.position_bias = strat.position_bias
            plan.key_support = strat.key_support
            plan.key_resistance = strat.key_resistance
            
            # 3. 提取短期战术
            plan.tactical_action = strat.tactical_action
            plan.tactical_confidence = strat.tactical_confidence
            plan.entry_timing = strat.entry_timing
            plan.suggested_stop_loss = strat.suggested_stop_loss
            plan.suggested_take_profit = strat.suggested_take_profit
            
            # 4. 获取当前价格计算入场点
            primary_symbol = config.symbols[0] if config.symbols else "BTC"
            if primary_symbol in snapshot.markets:
                current_price = snapshot.markets[primary_symbol].price
                plan.suggested_entry = current_price
                
                # 根据配置调整止损止盈
                if plan.suggested_stop_loss == 0:
                    plan.suggested_stop_loss = current_price * (1 - config.stop_loss_pct / 100)
                if plan.suggested_take_profit == 0:
                    plan.suggested_take_profit = current_price * (1 + config.take_profit_pct / 100)
            
            # 5. 信号聚合
            plan.active_signals = strat.active_signals
            plan.signal_consensus, plan.signal_strength = self._aggregate_signals(
                strat.active_signals, 
                config.enabled_signal_pools,
                config.min_signal_strength
            )
            
            # 6. 因子分析
            plan.key_factors = strat.factors
            plan.factor_score = self._calculate_factor_score(
                strat.factors,
                config.factor_weights
            )
            
            # 7. 风险评估
            plan.risk_score = self._assess_risk(
                plan, config, snapshot
            )
            plan.volatility_level = self._get_volatility_level(snapshot, primary_symbol)
            
            # 8. 生成推荐动作
            plan.recommended_actions = self._generate_recommendations(
                plan, config, snapshot
            )
            
            # 更新策略
            strategy.plan = plan
            strategy.phase = StrategyPhase.READY
            strategy.updated_at = datetime.now(timezone.utc)
            
            # 记录分析历史
            strategy.analysis_history.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "market_cycle": plan.market_cycle,
                "tactical_action": plan.tactical_action,
                "signal_consensus": plan.signal_consensus,
                "factor_score": plan.factor_score,
                "risk_score": plan.risk_score,
            })
            
            logger.info(
                f"[StrategyGenerator] 策略计划生成完成: "
                f"周期={plan.market_cycle}, 战术={plan.tactical_action}, "
                f"信号={plan.signal_consensus}, 风险={plan.risk_score:.1f}"
            )
            
            return plan
            
        except Exception as e:
            logger.error(f"生成策略计划失败: {e}")
            strategy.phase = StrategyPhase.DRAFT
            return None
    
    def activate_strategy(self, strategy_id: str) -> bool:
        """
        激活策略
        
        将策略从READY状态转为ACTIVE状态，开始执行。
        """
        strategy = self.get_strategy(strategy_id)
        if not strategy:
            return False
        
        if strategy.phase != StrategyPhase.READY:
            logger.warning(f"策略 {strategy_id} 状态不是READY，无法激活")
            return False
        
        # 停用其他策略
        with self._strategies_lock:
            for sid, s in self._strategies.items():
                if s.phase == StrategyPhase.ACTIVE:
                    s.phase = StrategyPhase.PAUSED
                    s.updated_at = datetime.now(timezone.utc)
        
        # 激活当前策略
        strategy.phase = StrategyPhase.ACTIVE
        strategy.activated_at = datetime.now(timezone.utc)
        strategy.updated_at = datetime.now(timezone.utc)
        strategy.execution.is_active = True
        
        self._active_strategy_id = strategy_id
        
        logger.info(f"[StrategyGenerator] 策略已激活: {strategy_id}")
        return True
    
    def pause_strategy(self, strategy_id: str) -> bool:
        """暂停策略"""
        strategy = self.get_strategy(strategy_id)
        if not strategy:
            return False
        
        if strategy.phase != StrategyPhase.ACTIVE:
            return False
        
        strategy.phase = StrategyPhase.PAUSED
        strategy.updated_at = datetime.now(timezone.utc)
        strategy.execution.is_active = False
        
        if self._active_strategy_id == strategy_id:
            self._active_strategy_id = None
        
        logger.info(f"[StrategyGenerator] 策略已暂停: {strategy_id}")
        return True
    
    def cancel_strategy(self, strategy_id: str) -> bool:
        """取消策略"""
        strategy = self.get_strategy(strategy_id)
        if not strategy:
            return False
        
        strategy.phase = StrategyPhase.CANCELLED
        strategy.updated_at = datetime.now(timezone.utc)
        strategy.execution.is_active = False
        
        if self._active_strategy_id == strategy_id:
            self._active_strategy_id = None
        
        logger.info(f"[StrategyGenerator] 策略已取消: {strategy_id}")
        return True
    
    def get_strategy(self, strategy_id: str) -> Optional[Strategy]:
        """获取策略"""
        with self._strategies_lock:
            return self._strategies.get(strategy_id)
    
    def get_active_strategy(self) -> Optional[Strategy]:
        """获取当前活跃策略"""
        if self._active_strategy_id:
            return self.get_strategy(self._active_strategy_id)
        return None
    
    def list_strategies(self, phase: Optional[StrategyPhase] = None) -> List[Strategy]:
        """列出策略"""
        with self._strategies_lock:
            strategies = list(self._strategies.values())
        
        if phase:
            strategies = [s for s in strategies if s.phase == phase]
        
        return sorted(strategies, key=lambda s: s.updated_at, reverse=True)
    
    def update_config(self, strategy_id: str, config_updates: Dict[str, Any]) -> bool:
        """更新策略配置"""
        strategy = self.get_strategy(strategy_id)
        if not strategy:
            return False
        
        for key, value in config_updates.items():
            if hasattr(strategy.config, key):
                setattr(strategy.config, key, value)
        
        strategy.updated_at = datetime.now(timezone.utc)
        
        # 如果策略已就绪，需要重新生成计划
        if strategy.phase == StrategyPhase.READY:
            strategy.phase = StrategyPhase.DRAFT
        
        return True
    
    def get_execution_status(self, strategy_id: str) -> Optional[Dict]:
        """获取执行状态"""
        strategy = self.get_strategy(strategy_id)
        if not strategy:
            return None
        
        exec_state = strategy.execution
        
        return {
            "strategy_id": strategy_id,
            "phase": strategy.phase.value,
            "is_active": exec_state.is_active,
            "trades_today": exec_state.trades_today,
            "pnl_today": exec_state.pnl_today,
            "pnl_total": exec_state.pnl_total,
            "win_rate": exec_state.win_rate,
            "last_trade_time": exec_state.last_trade_time.isoformat() if exec_state.last_trade_time else None,
            "pending_orders": len(exec_state.pending_orders),
            "active_positions": len(exec_state.active_positions),
        }
    
    # ========== 私有方法 ==========
    
    def _aggregate_signals(
        self,
        signals: List[Dict],
        enabled_pools: List[int],
        min_strength: float
    ) -> Tuple[str, float]:
        """聚合信号"""
        if not signals:
            return "neutral", 0.0
        
        bullish = 0
        bearish = 0
        total_weight = 0
        
        for signal in signals:
            # 过滤信号池
            if enabled_pools and signal.get("pool_id") not in enabled_pools:
                continue
            
            weight = signal.get("weight", 1.0)
            direction = signal.get("direction", "neutral")
            
            if direction == "bullish" or direction == "long":
                bullish += weight
            elif direction == "bearish" or direction == "short":
                bearish += weight
            
            total_weight += weight
        
        if total_weight == 0:
            return "neutral", 0.0
        
        net_score = (bullish - bearish) / total_weight
        strength = abs(net_score)
        
        if strength < min_strength:
            return "neutral", strength
        
        if net_score > 0:
            return "bullish", strength
        else:
            return "bearish", strength
    
    # ========================================================================
    # V3 整合：因子引擎推荐
    # ========================================================================
    
    def _recommend_factors(
        self,
        symbol: str,
        top_n: int = 10,
    ) -> Tuple[List[str], Dict[str, float]]:
        """
        V3 整合：从因子引擎获取推荐因子组合
        
        基于当前市场状态和因子自适应权重，推荐 top-N 因子。
        
        Args:
            symbol: 交易标的
            top_n: 推荐因子数量
            
        Returns:
            (推荐因子名称列表, 因子权重字典)
        """
        try:
            from services.factor_engine import (
                factor_engine,
                get_factor_weighting,
            )
            from backend.services.market_data import get_kline_data
            import pandas as pd
            from backend.database.connection import SessionLocal
            
            db = SessionLocal()
            try:
                # 获取 K 线数据
                _raw = get_kline_data(symbol.upper(), period="15m", count=200)
                klines_df = pd.DataFrame(_raw) if _raw else None
                if klines_df is None or klines_df.empty:
                    logger.debug(f"[StrategyGenerator] 无 K 线数据，跳过因子推荐: {symbol}")
                    return [], {}
                
                # 计算所有因子值
                factor_values = factor_engine.compute_all_factors(klines_df)
                if not factor_values:
                    return [], {}
                
                # 获取自适应权重
                weighting = get_factor_weighting()
                adaptive_result = weighting.calculate_adaptive_weights(factor_values)
                weights = adaptive_result.weights
                regime = adaptive_result.regime.value
                
                # 按权重排序取 top-N
                sorted_factors = sorted(weights.items(), key=lambda x: x[1], reverse=True)
                top_factors = sorted_factors[:top_n]
                
                recommended = [name for name, _ in top_factors]
                recommended_weights = {name: w for name, w in top_factors}
                
                logger.info(
                    f"[StrategyGenerator] 因子推荐完成: {symbol}, "
                    f"regime={regime}, top_factors={recommended[:5]}"
                )
                return recommended, recommended_weights
                
            finally:
                db.close()
                
        except Exception as e:
            logger.warning(f"[StrategyGenerator] 因子推荐失败（不影响策略创建）: {e}")
            return [], {}
    
    def _calculate_factor_score(
        self,
        factors: Dict[str, float],
        weights: Dict[str, float]
    ) -> float:
        """计算因子得分"""
        if not factors:
            return 0.0
        
        # 默认权重
        default_weights = {
            "rsi": 0.15,
            "macd": 0.15,
            "bb_position": 0.10,
            "atr_pct": 0.10,
            "trend_strength": 0.20,
            "volume_trend": 0.15,
            "momentum": 0.15,
        }
        
        # 合并权重
        final_weights = {**default_weights, **weights}
        
        score = 0.0
        total_weight = 0.0
        
        for name, value in factors.items():
            weight = final_weights.get(name, 0.05)
            
            # 标准化因子值到 0-100
            normalized = self._normalize_factor(name, value)
            
            score += normalized * weight
            total_weight += weight
        
        if total_weight == 0:
            return 0.0
        
        return score / total_weight
    
    def _normalize_factor(self, name: str, value: float) -> float:
        """标准化因子值"""
        if "rsi" in name.lower():
            return value  # RSI 已经是 0-100
        if "macd" in name.lower():
            return 50 + (value * 10)  # 假设 MACD 在 -5 到 5 之间
        return 50  # 默认中性
    
    def _assess_risk(
        self,
        plan: StrategyPlan,
        config: StrategyConfig,
        snapshot: Any
    ) -> float:
        """评估风险"""
        risk_score = 50.0  # 基础风险
        
        # 波动性风险
        if plan.volatility_level == "high":
            risk_score += 20
        elif plan.volatility_level == "low":
            risk_score -= 10
        
        # 周期风险
        if plan.market_cycle in ["high_volatility", "distribution"]:
            risk_score += 15
        elif plan.market_cycle in ["bull_trend", "accumulation"]:
            risk_score -= 10
        
        # 信号一致性
        if plan.signal_strength < 0.3:
            risk_score += 10  # 信号弱，风险高
        elif plan.signal_strength > 0.7:
            risk_score -= 5
        
        # 杠杆风险
        if config.max_leverage > 8:
            risk_score += 15
        elif config.max_leverage <= 2:
            risk_score -= 10
        
        return max(0, min(100, risk_score))
    
    def _get_volatility_level(self, snapshot: Any, symbol: str) -> str:
        """获取波动水平"""
        if not snapshot or symbol not in snapshot.indicators:
            return "normal"
        
        indicators = snapshot.indicators.get(symbol, {})
        atr_pct = indicators.get("atr_pct", 0)
        
        if atr_pct > 0.05:
            return "high"
        elif atr_pct < 0.02:
            return "low"
        return "normal"
    
    def _generate_recommendations(
        self,
        plan: StrategyPlan,
        config: StrategyConfig,
        snapshot: Any
    ) -> List[Dict]:
        """生成推荐动作"""
        recommendations = []
        
        # 根据战术动作生成推荐
        if plan.tactical_action == "enter_long" and plan.tactical_confidence > 0.6:
            for symbol in config.symbols[:3]:  # 最多3个标的
                if symbol in snapshot.markets:
                    price = snapshot.markets[symbol].price
                    recommendations.append({
                        "action": "BUY",
                        "symbol": symbol,
                        "entry_price": price,
                        "stop_loss": price * (1 - config.stop_loss_pct / 100),
                        "take_profit": price * (1 + config.take_profit_pct / 100),
                        "position_size_pct": config.max_position_pct * plan.tactical_confidence,
                        "confidence": plan.tactical_confidence,
                        "reason": f"中长期{plan.market_cycle}, 短期建议{plan.tactical_action}",
                    })
        
        elif plan.tactical_action == "enter_short" and plan.tactical_confidence > 0.6:
            for symbol in config.symbols[:3]:
                if symbol in snapshot.markets:
                    price = snapshot.markets[symbol].price
                    recommendations.append({
                        "action": "SELL",
                        "symbol": symbol,
                        "entry_price": price,
                        "stop_loss": price * (1 + config.stop_loss_pct / 100),
                        "take_profit": price * (1 - config.take_profit_pct / 100),
                        "position_size_pct": config.max_position_pct * plan.tactical_confidence,
                        "confidence": plan.tactical_confidence,
                        "reason": f"中长期{plan.market_cycle}, 短期建议{plan.tactical_action}",
                    })
        
        elif plan.tactical_action == "wait":
            recommendations.append({
                "action": "HOLD",
                "symbol": "ALL",
                "confidence": plan.tactical_confidence,
                "reason": "市场条件不明朗，建议观望",
            })
        
        return recommendations
    
    def to_dict(self, strategy: Strategy) -> Dict:
        """将策略转换为字典"""
        return {
            "strategy_id": strategy.strategy_id,
            "config": {
                "name": strategy.config.name,
                "description": strategy.config.description,
                "symbols": strategy.config.symbols,
                "horizon": strategy.config.horizon.value,
                "risk_profile": strategy.config.risk_profile.value,
                "max_position_pct": strategy.config.max_position_pct,
                "max_total_exposure": strategy.config.max_total_exposure,
                "max_daily_loss_pct": strategy.config.max_daily_loss_pct,
                "stop_loss_pct": strategy.config.stop_loss_pct,
                "take_profit_pct": strategy.config.take_profit_pct,
                "enabled_signal_pools": strategy.config.enabled_signal_pools,
                "min_signal_strength": strategy.config.min_signal_strength,
                "factor_weights": strategy.config.factor_weights,
                "auto_execute": strategy.config.auto_execute,
                "require_confirmation": strategy.config.require_confirmation,
                "max_leverage": strategy.config.max_leverage,
            },
            "plan": {
                "market_cycle": strategy.plan.market_cycle,
                "cycle_confidence": strategy.plan.cycle_confidence,
                "position_bias": strategy.plan.position_bias,
                "key_support": strategy.plan.key_support,
                "key_resistance": strategy.plan.key_resistance,
                "tactical_action": strategy.plan.tactical_action,
                "tactical_confidence": strategy.plan.tactical_confidence,
                "entry_timing": strategy.plan.entry_timing,
                "suggested_entry": strategy.plan.suggested_entry,
                "suggested_stop_loss": strategy.plan.suggested_stop_loss,
                "suggested_take_profit": strategy.plan.suggested_take_profit,
                "signal_consensus": strategy.plan.signal_consensus,
                "signal_strength": strategy.plan.signal_strength,
                "factor_score": strategy.plan.factor_score,
                "risk_score": strategy.plan.risk_score,
                "volatility_level": strategy.plan.volatility_level,
                "recommended_actions": strategy.plan.recommended_actions,
                "active_signals": strategy.plan.active_signals,
                "key_factors": strategy.plan.key_factors,
            },
            "execution": {
                "is_active": strategy.execution.is_active,
                "trades_today": strategy.execution.trades_today,
                "pnl_today": strategy.execution.pnl_today,
                "pnl_total": strategy.execution.pnl_total,
                "win_rate": strategy.execution.win_rate,
            },
            "phase": strategy.phase.value,
            "created_at": strategy.created_at.isoformat(),
            "updated_at": strategy.updated_at.isoformat(),
            "activated_at": strategy.activated_at.isoformat() if strategy.activated_at else None,
            "account_id": strategy.account_id,
            "environment": strategy.environment,
        }


# 全局单例
strategy_generator = StrategyGenerator()


def get_strategy_generator() -> StrategyGenerator:
    """获取策略生成器实例"""
    return strategy_generator
