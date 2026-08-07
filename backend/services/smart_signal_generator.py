"""
Smart Signal Generator Service

Generates optimal trading signals based on market analysis, pattern recognition,
and historical backtesting. This is the main entry point for intelligent signal creation.
"""

import logging
import numpy as np
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict, field
from datetime import datetime
from sqlalchemy.orm import Session

from backend.database.models import GeneratedSignalHistory, SignalDefinition
from backend.services.market_data_analyzer import market_data_analyzer, MarketAnalysisResult
from backend.services.pattern_recognition_service import (
    pattern_recognition_service,
    PatternTemplate,
    PatternCondition,
    SYSTEM_PATTERNS
)
from backend.services.market_regime_service import (
    get_market_regime,
    get_adaptive_trading_parameters,
    get_multi_timeframe_regime_consensus,
    get_regime_description,
    REGIME_BREAKOUT, REGIME_CONTINUATION, REGIME_ABSORPTION,
    REGIME_EXHAUSTION, REGIME_TRAP, REGIME_NOISE
)
from backend.services.signal_backtest_service import signal_backtest_service
from backend.services.backtest_performance_service import backtest_performance_service

logger = logging.getLogger(__name__)


@dataclass
class SignalCondition:
    """A single signal trigger condition"""
    metric: str
    operator: str
    threshold: float
    time_window: str
    description: Optional[str] = None


@dataclass
class GeneratedSignalConfig:
    """A complete generated signal configuration"""
    signal_name: str
    symbol: str
    description: str
    direction: str  # long/short
    trigger_condition: Dict[str, Any]  # Ready for signal system
    
    # Metadata
    strategy_type: str
    risk_level: str
    market_regime_at_creation: str
    
    # Backtest metrics
    backtest_metrics: Dict[str, Any]
    effectiveness_score: float
    
    # Recommendations
    recommended_position_size: float
    recommended_stop_loss_percent: float
    recommended_take_profit_percent: float
    
    # Additional context
    conditions_explanation: List[str]
    notes: str
    
    # AI Prompt Template for real-time signal judgment
    ai_prompt_template: Optional[str] = None


@dataclass
class GeneratedPoolConfig:
    """A generated signal pool configuration"""
    pool_name: str
    symbol: str
    description: str
    logic: str  # AND/OR
    signals: List[Dict[str, Any]]
    
    # Metadata
    strategy_type: str
    combined_backtest_metrics: Dict[str, Any]
    effectiveness_score: float
    notes: str


# Strategy type to indicator mapping
# Indicators fall into two categories:
# 1. Market flow indicators (can be backtested): oi_delta, cvd, order_imbalance, taker_ratio, depth_ratio, funding
# 2. Technical indicators (need AI prompts): rsi, macd, boll_position, ema_cross, stoch_k
STRATEGY_INDICATORS = {
    "trend": {
        "primary": ["ema_cross", "macd_histogram", "cvd"],
        "confirmation": ["rsi", "oi_delta"],
        "description": "趋势跟踪策略，适合明确的上涨或下跌趋势"
    },
    "reversal": {
        "primary": ["rsi", "boll_position", "stoch_k"],
        "confirmation": ["cvd", "order_imbalance"],
        "description": "均值回归策略，适合超买超卖后的反转"
    },
    "breakout": {
        "primary": ["boll_position", "oi_delta", "cvd"],
        "confirmation": ["order_imbalance", "taker_ratio"],
        "description": "突破策略，捕捉价格突破关键位的机会"
    },
    "scalping": {
        "primary": ["order_imbalance", "taker_ratio", "depth_ratio"],
        "confirmation": ["rsi", "cvd"],
        "description": "短线策略，利用订单流失衡快速交易"
    },
    "adaptive": {
        "primary": ["cvd", "oi_delta", "rsi"],
        "confirmation": ["order_imbalance", "boll_position"],
        "description": "自适应策略，根据市场状态自动选择最佳指标"
    }
}

# Indicators that can be backtested by signal_backtest_service
BACKTESTABLE_INDICATORS = {"oi_delta", "cvd", "order_imbalance", "taker_ratio", "depth_ratio", "funding", "oi"}

# Technical indicators that require AI prompt for real-time judgment
TECHNICAL_INDICATORS = {"rsi", "rsi7", "stoch_k", "macd_histogram", "boll_position", "ema_cross"}


class SmartSignalGenerator:
    """
    智能信号生成器
    
    Generates optimal trading signals based on:
    1. Current market regime
    2. Historical data analysis
    3. Pattern recognition
    4. Backtest validation
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def generate_optimal_signal(
        self,
        db: Session,
        symbol: str,
        direction: str = "auto",
        risk_level: str = "moderate",
        time_window: str = "5m",
        strategy_type: str = "adaptive",
        lookback_days: int = 14  # 新增参数：历史数据天数
    ) -> GeneratedSignalConfig:
        """
        Generate the optimal signal configuration for current market conditions.
        
        This is the main entry point for smart signal generation.
        
        Args:
            db: Database session
            symbol: Trading symbol (e.g., "BTC")
            direction: "auto", "long", or "short"
            risk_level: "conservative", "moderate", or "aggressive"
            time_window: Signal time window (e.g., "5m", "15m")
            strategy_type: Strategy type (trend/reversal/breakout/scalping/adaptive)
            lookback_days: Number of days of historical data to analyze (default 14)
            
        Returns:
            GeneratedSignalConfig with complete signal configuration
        """
        logger.info(f"Generating optimal signal for {symbol} {direction} {risk_level} using {lookback_days} days of history")
        
        # Step 1: Analyze current market with specified lookback period
        analysis = market_data_analyzer.analyze_period(db, symbol, time_window, lookback_days=lookback_days)
        
        # Step 2: Get market regime
        regime_data = get_market_regime(db, symbol, time_window)
        regime_type = regime_data.get("regime", REGIME_NOISE)
        regime_direction = regime_data.get("direction", "neutral")
        regime_confidence = regime_data.get("confidence", 0.5)
        
        # Step 3: Determine direction if auto
        if direction == "auto":
            direction = self._determine_direction(regime_type, regime_direction, analysis)
        
        # Step 4: Select appropriate strategy
        if strategy_type == "adaptive":
            strategy_type = self._select_strategy_for_regime(regime_type)
        
        # Step 5: Generate signal conditions
        conditions = self._generate_conditions(
            db, symbol, direction, strategy_type, risk_level, time_window, analysis
        )
        
        # Step 6: Build trigger condition for signal system
        trigger_condition = self._build_trigger_condition(conditions, time_window)
        
        # Step 7: Backtest the generated signal
        backtest_result = self._backtest_signal(db, symbol, trigger_condition, direction, lookback_days)
        
        # Step 8: Calculate effectiveness score
        effectiveness_score = self._calculate_effectiveness_score(backtest_result)
        
        # Step 9: Get adaptive parameters for recommendations
        adaptive_params = get_adaptive_trading_parameters(db, symbol, time_window)
        
        # Step 10: Build signal name
        signal_name = self._generate_signal_name(symbol, strategy_type, direction, time_window)
        
        # Step 11: Generate explanation
        explanations = self._generate_explanations(conditions, analysis, regime_type)
        
        # Step 12: Generate AI prompt template for real-time judgment
        ai_prompt = self._generate_ai_prompt_template(
            symbol, direction, strategy_type, conditions, regime_type, 
            adaptive_params, time_window, lookback_days
        )
        
        config = GeneratedSignalConfig(
            signal_name=signal_name,
            symbol=symbol,
            description=self._generate_description(strategy_type, direction, regime_type),
            direction=direction,
            trigger_condition=trigger_condition,
            strategy_type=strategy_type,
            risk_level=risk_level,
            market_regime_at_creation=regime_type,
            backtest_metrics=backtest_result,
            effectiveness_score=effectiveness_score,
            recommended_position_size=self._get_position_size(risk_level, adaptive_params),
            recommended_stop_loss_percent=adaptive_params.stop_loss_atr_multiple * 1.5,  # ATR-based estimate
            recommended_take_profit_percent=adaptive_params.stop_loss_atr_multiple * adaptive_params.take_profit_ratio * 1.5,
            conditions_explanation=explanations,
            notes=adaptive_params.notes,
            ai_prompt_template=ai_prompt
        )
        
        # Save to history
        self._save_to_history(db, config)
        
        return config
    
    def generate_signal_pool(
        self,
        db: Session,
        symbol: str,
        strategy_type: str,
        direction: str = "auto",
        max_signals: int = 3,
        time_window: str = "5m"
    ) -> GeneratedPoolConfig:
        """
        Generate a signal pool with multiple complementary signals.
        
        Args:
            db: Database session
            symbol: Trading symbol
            strategy_type: Strategy type
            direction: Trade direction
            max_signals: Maximum signals in pool
            time_window: Signal time window
            
        Returns:
            GeneratedPoolConfig with pool configuration
        """
        logger.info(f"Generating signal pool for {symbol} {strategy_type}")
        
        # Get market context
        regime_data = get_market_regime(db, symbol, time_window)
        regime_type = regime_data.get("regime", REGIME_NOISE)
        regime_direction = regime_data.get("direction", "neutral")
        
        # Determine direction
        if direction == "auto":
            direction = self._determine_direction(regime_type, regime_direction, None)
        
        # Get strategy indicators
        strategy_config = STRATEGY_INDICATORS.get(strategy_type, STRATEGY_INDICATORS["adaptive"])
        
        signals = []
        
        # Generate primary signal
        primary_indicators = strategy_config["primary"][:2]
        for indicator in primary_indicators:
            condition = self._create_condition_for_indicator(
                db, symbol, indicator, direction, time_window, "moderate"
            )
            if condition:
                signals.append({
                    "metric": condition.metric,
                    "operator": condition.operator,
                    "threshold": condition.threshold,
                    "time_window": condition.time_window,
                    "description": condition.description
                })
        
        # Add confirmation signals
        if len(signals) < max_signals:
            for indicator in strategy_config["confirmation"]:
                if len(signals) >= max_signals:
                    break
                condition = self._create_condition_for_indicator(
                    db, symbol, indicator, direction, time_window, "moderate"
                )
                if condition:
                    signals.append({
                        "metric": condition.metric,
                        "operator": condition.operator,
                        "threshold": condition.threshold,
                        "time_window": condition.time_window,
                        "description": condition.description
                    })
        
        # Build pool config for backtesting
        pool_trigger = {
            "logic": "AND",
            "conditions": signals
        }
        
        # Backtest the pool
        backtest_result = self._backtest_pool(db, symbol, signals, direction)
        effectiveness_score = self._calculate_effectiveness_score(backtest_result)
        
        pool_name = f"{symbol}_{strategy_type}_{direction}_{time_window}"
        
        return GeneratedPoolConfig(
            pool_name=pool_name,
            symbol=symbol,
            description=f"{strategy_config['description']} - {direction.upper()}方向",
            logic="AND",
            signals=signals,
            strategy_type=strategy_type,
            combined_backtest_metrics=backtest_result,
            effectiveness_score=effectiveness_score,
            notes=f"基于{regime_type}市场状态生成，建议在相似市场条件下使用"
        )
    
    def optimize_signal_parameters(
        self,
        db: Session,
        signal_config: Dict[str, Any],
        symbol: str,
        optimization_target: str = "sharpe",
        days: int = 30
    ) -> Dict[str, Any]:
        """
        Optimize signal parameters for better performance.
        
        Args:
            db: Database session
            signal_config: Current signal configuration
            symbol: Trading symbol
            optimization_target: "sharpe", "win_rate", "profit", "risk_adjusted"
            days: Days of history for optimization
            
        Returns:
            Optimized signal configuration with improvement metrics
        """
        logger.info(f"Optimizing signal for {symbol} targeting {optimization_target}")
        
        trigger_condition = signal_config.get("trigger_condition", {})
        if not trigger_condition:
            return {"error": "No trigger condition provided"}
        
        # Get current performance
        current_result = self._backtest_signal(
            db, symbol, trigger_condition, 
            signal_config.get("direction", "long")
        )
        current_score = self._get_optimization_score(current_result, optimization_target)
        
        best_config = trigger_condition.copy()
        best_score = current_score
        
        # Try different threshold variations
        metric = trigger_condition.get("metric")
        threshold = trigger_condition.get("threshold", 0)
        
        variations = [0.8, 0.9, 1.0, 1.1, 1.2]  # ±20% variations
        
        for var in variations:
            test_config = trigger_condition.copy()
            test_config["threshold"] = threshold * var
            
            result = self._backtest_signal(
                db, symbol, test_config,
                signal_config.get("direction", "long")
            )
            score = self._get_optimization_score(result, optimization_target)
            
            if score > best_score:
                best_score = score
                best_config = test_config
        
        improvement = ((best_score - current_score) / current_score * 100) if current_score > 0 else 0
        
        return {
            "original_config": trigger_condition,
            "optimized_config": best_config,
            "original_score": current_score,
            "optimized_score": best_score,
            "improvement_percent": round(improvement, 2),
            "optimization_target": optimization_target
        }
    
    def get_signal_suggestions(
        self,
        db: Session,
        symbol: str,
        time_window: str = "5m"
    ) -> List[Dict[str, Any]]:
        """
        Get a list of signal suggestions based on current market conditions.
        
        Returns multiple signal options with different risk/reward profiles.
        """
        suggestions = []
        
        # Get market context
        regime_data = get_market_regime(db, symbol, time_window)
        regime_type = regime_data.get("regime", REGIME_NOISE)
        
        # Get patterns suitable for current regime
        patterns = pattern_recognition_service.get_patterns_for_regime(db, regime_type)
        
        for pattern in patterns[:5]:  # Top 5 patterns
            # Backtest each pattern
            result = pattern_recognition_service.backtest_pattern(
                db, pattern, symbol, days=14
            )
            
            suggestions.append({
                "type": "pattern",
                "name": pattern.name,
                "direction": pattern.direction,
                "pattern_type": pattern.pattern_type,
                "win_rate": result.win_rate,
                "avg_return": result.avg_return_percent,
                "triggers": result.total_triggers,
                "conditions": [asdict(c) for c in pattern.conditions],
                "description": pattern.description
            })
        
        # Add regime-based suggestion
        adaptive_params = get_adaptive_trading_parameters(db, symbol, time_window)
        suggestions.append({
            "type": "regime_adaptive",
            "name": f"自适应{regime_type}策略",
            "direction": adaptive_params.suggested_direction,
            "recommended_strategy": adaptive_params.recommended_strategy,
            "risk_level": adaptive_params.risk_level,
            "description": adaptive_params.notes
        })
        
        return suggestions
    
    # ========================================================================
    # Private Helper Methods
    # ========================================================================
    
    def _determine_direction(
        self,
        regime_type: str,
        regime_direction: str,
        analysis: Optional[MarketAnalysisResult]
    ) -> str:
        """Determine trade direction based on market conditions"""
        
        # Trend-following regimes
        if regime_type in [REGIME_BREAKOUT, REGIME_CONTINUATION]:
            if regime_direction == "bullish":
                return "long"
            elif regime_direction == "bearish":
                return "short"
        
        # Counter-trend regimes
        elif regime_type in [REGIME_EXHAUSTION, REGIME_TRAP]:
            if regime_direction == "bullish":
                return "short"  # Fade the exhaustion
            elif regime_direction == "bearish":
                return "long"
        
        # Range-bound
        elif regime_type == REGIME_ABSORPTION:
            # Use RSI for direction in ranges
            if analysis and analysis.indicator_distributions.get("rsi"):
                rsi = analysis.indicator_distributions["rsi"].current_value
                if rsi < 35:
                    return "long"
                elif rsi > 65:
                    return "short"
        
        # Default to long in uncertain conditions (with reduced size)
        return "long"
    
    def _select_strategy_for_regime(self, regime_type: str) -> str:
        """Select best strategy type for current regime"""
        regime_to_strategy = {
            REGIME_BREAKOUT: "breakout",
            REGIME_CONTINUATION: "trend",
            REGIME_ABSORPTION: "reversal",
            REGIME_EXHAUSTION: "reversal",
            REGIME_TRAP: "reversal",
            REGIME_NOISE: "scalping"
        }
        return regime_to_strategy.get(regime_type, "adaptive")
    
    def _generate_conditions(
        self,
        db: Session,
        symbol: str,
        direction: str,
        strategy_type: str,
        risk_level: str,
        time_window: str,
        analysis: MarketAnalysisResult
    ) -> List[SignalCondition]:
        """Generate signal conditions based on strategy and market analysis"""
        conditions = []
        
        strategy_config = STRATEGY_INDICATORS.get(strategy_type, STRATEGY_INDICATORS["adaptive"])
        
        # Add primary indicator condition
        primary = strategy_config["primary"][0]
        primary_condition = self._create_condition_for_indicator(
            db, symbol, primary, direction, time_window, risk_level
        )
        if primary_condition:
            conditions.append(primary_condition)
        
        # Add confirmation indicator if moderate/aggressive
        if risk_level != "aggressive" and strategy_config["confirmation"]:
            confirm = strategy_config["confirmation"][0]
            confirm_condition = self._create_condition_for_indicator(
                db, symbol, confirm, direction, time_window, risk_level
            )
            if confirm_condition:
                conditions.append(confirm_condition)
        
        return conditions
    
    def _create_condition_for_indicator(
        self,
        db: Session,
        symbol: str,
        indicator: str,
        direction: str,
        time_window: str,
        risk_level: str
    ) -> Optional[SignalCondition]:
        """
        Create a signal condition for a specific indicator.
        
        Supports both:
        1. Market flow indicators (backtestable): oi_delta, cvd, order_imbalance, taker_ratio, depth_ratio, funding
        2. Technical indicators (AI prompt based): rsi, macd, boll_position, ema_cross, stoch_k
        """
        
        # Get optimal thresholds from analyzer
        threshold_data = market_data_analyzer.calculate_optimal_thresholds(
            db, symbol, indicator, time_window, 
            target_hit_rate=self._get_hit_rate_for_risk(risk_level)
        )
        
        # ========== Technical Indicators (AI Prompt Based) ==========
        
        # RSI - Relative Strength Index
        if indicator in ["rsi", "rsi7", "stoch_k"]:
            if direction == "long":
                operator = "less_than"
                raw_threshold = threshold_data.moderate_threshold
                if raw_threshold and 10 < raw_threshold < 50:
                    threshold = raw_threshold
                else:
                    threshold = 30  # Default oversold level
                description = f"RSI超卖(<{threshold:.0f})"
            else:
                operator = "greater_than"
                raw_threshold = threshold_data.moderate_threshold
                if raw_threshold and 50 < raw_threshold < 90:
                    threshold = raw_threshold
                else:
                    threshold = 70  # Default overbought level
                description = f"RSI超买(>{threshold:.0f})"
        
        # MACD Histogram
        elif indicator == "macd_histogram":
            if direction == "long":
                operator = "greater_than"
                threshold = 0
                description = "MACD柱状图为正(多头动能)"
            else:
                operator = "less_than"
                threshold = 0
                description = "MACD柱状图为负(空头动能)"
        
        # Bollinger Band Position (0-1, where 0=lower band, 1=upper band)
        elif indicator == "boll_position":
            if direction == "long":
                operator = "less_than"
                threshold = 0.2
                description = "价格接近布林带下轨(<20%)"
            else:
                operator = "greater_than"
                threshold = 0.8
                description = "价格接近布林带上轨(>80%)"
        
        # EMA Cross
        elif indicator == "ema_cross":
            if direction == "long":
                operator = "greater_than"
                threshold = 0  # EMA fast > EMA slow
                description = "EMA金叉(短期>长期)"
            else:
                operator = "less_than"
                threshold = 0  # EMA fast < EMA slow
                description = "EMA死叉(短期<长期)"
        
        # ========== Market Flow Indicators (Backtestable) ==========
        
        # CVD - Cumulative Volume Delta
        elif indicator == "cvd":
            if direction == "long":
                operator = "greater_than"
                raw_threshold = threshold_data.moderate_threshold
                threshold = raw_threshold if raw_threshold and raw_threshold > 0 else 10000
                description = f"CVD正向(>{threshold:,.0f})"
            else:
                operator = "less_than"
                raw_threshold = threshold_data.moderate_threshold
                threshold = -abs(raw_threshold) if raw_threshold else -10000
                description = f"CVD负向(<{threshold:,.0f})"
        
        # OI Delta - Open Interest Change Percentage
        elif indicator == "oi_delta":
            operator = "greater_than"
            raw_threshold = threshold_data.moderate_threshold
            threshold = max(raw_threshold if raw_threshold else 0.5, 0.5)
            description = f"OI增加(>{threshold:.1f}%)"
        
        # Order Imbalance - (bid - ask) / (bid + ask)
        elif indicator == "order_imbalance":
            if direction == "long":
                operator = "greater_than"
                raw_threshold = threshold_data.moderate_threshold
                threshold = max(raw_threshold if raw_threshold else 0.3, 0.2)
                description = f"买盘主导(>{threshold:.2f})"
            else:
                operator = "less_than"
                raw_threshold = threshold_data.moderate_threshold
                threshold = min(-raw_threshold if raw_threshold else -0.3, -0.2)
                description = f"卖盘主导(<{threshold:.2f})"
        
        # Taker Ratio - ln(buy/sell) log ratio
        elif indicator == "taker_ratio":
            if direction == "long":
                operator = "greater_than"
                threshold = 0.4
                description = f"主买强势(>{threshold:.2f})"
            else:
                operator = "less_than"
                threshold = -0.4
                description = f"主卖强势(<{threshold:.2f})"
        
        # Depth Ratio - bid_depth / ask_depth
        elif indicator == "depth_ratio":
            if direction == "long":
                operator = "greater_than"
                threshold = 1.2
                description = f"买盘深度占优(>{threshold})"
            else:
                operator = "less_than"
                threshold = 0.8
                description = f"卖盘深度占优(<{threshold})"
        
        # Funding Rate
        elif indicator == "funding":
            if direction == "long":
                operator = "less_than"
                threshold = -0.01
                description = f"资金费率负(<{threshold}%)"
            else:
                operator = "greater_than"
                threshold = 0.01
                description = f"资金费率正(>{threshold}%)"
        
        else:
            logger.warning(f"Unknown indicator: {indicator}")
            return None
        
        return SignalCondition(
            metric=indicator,
            operator=operator,
            threshold=threshold,
            time_window=time_window,
            description=description
        )
    
    def _build_trigger_condition(
        self,
        conditions: List[SignalCondition],
        time_window: str
    ) -> Dict[str, Any]:
        """
        Build trigger condition dict for signal system.
        
        Always returns a consistent structure with 'conditions' array,
        each containing: metric, operator, threshold, time_window, description
        """
        # Build condition list with complete fields
        condition_list = [
            {
                "metric": c.metric,
                "operator": c.operator,
                "threshold": c.threshold,
                "time_window": time_window,
                "description": c.description or f"{c.metric} {c.operator} {c.threshold}"
            }
            for c in conditions
        ]
        
        # Always return consistent structure with conditions array
        return {
            "logic": "AND" if len(conditions) > 1 else "SINGLE",
            "conditions": condition_list,
            # Also include top-level fields for backward compatibility
            "metric": conditions[0].metric if conditions else None,
            "operator": conditions[0].operator if conditions else None,
            "threshold": conditions[0].threshold if conditions else None,
            "time_window": time_window
        }
    
    def _backtest_signal(
        self,
        db: Session,
        symbol: str,
        trigger_condition: Dict[str, Any],
        direction: str,
        lookback_days: int = 14  # 新增参数：回测天数
    ) -> Dict[str, Any]:
        """Backtest a signal configuration"""
        try:
            result = backtest_performance_service.backtest_temp_signal_with_performance(
                db, symbol, trigger_condition, days=lookback_days  # 使用传入的天数
            )
            
            if "error" in result:
                return {
                    "total_triggers": 0,
                    "win_rate": 0,
                    "avg_return_percent": 0,
                    "sharpe_ratio": 0,
                    "profit_factor": 0,
                    "max_drawdown_percent": 0
                }
            
            summary = result.get("summary", {})
            return {
                "total_triggers": result.get("trigger_count", 0),
                "win_rate": summary.get("win_rate", 0) if summary else 0,
                "avg_return_percent": summary.get("avg_pnl_percent", 0) if summary else 0,
                "sharpe_ratio": summary.get("sharpe_ratio", 0) if summary else 0,
                "profit_factor": summary.get("profit_factor", 0) if summary else 0,
                "max_drawdown_percent": summary.get("max_drawdown_percent", 0) if summary else 0,
                "period_days": result.get("period_days", lookback_days)
            }
        except Exception as e:
            logger.warning(f"Backtest failed: {e}")
            return {
                "total_triggers": 0,
                "win_rate": 0,
                "avg_return_percent": 0,
                "sharpe_ratio": 0,
                "profit_factor": 0,
                "max_drawdown_percent": 0,
                "error": str(e)
            }
    
    def _backtest_pool(
        self,
        db: Session,
        symbol: str,
        signals: List[Dict],
        direction: str
    ) -> Dict[str, Any]:
        """Backtest a signal pool"""
        # For pool, we use the combined trigger condition
        trigger = {
            "logic": "AND",
            "conditions": signals
        }
        return self._backtest_signal(db, symbol, trigger, direction)
    
    def _calculate_effectiveness_score(self, backtest_result: Dict[str, Any]) -> float:
        """
        Calculate comprehensive effectiveness score (0-100).
        
        Weights:
        - Win rate (30%)
        - Profit factor (25%)
        - Sharpe ratio (25%)
        - Sample size (10%)
        - Max drawdown (10%)
        """
        win_rate = backtest_result.get("win_rate", 0)
        profit_factor = min(backtest_result.get("profit_factor", 0), 3)
        sharpe = min(backtest_result.get("sharpe_ratio", 0), 3)
        trades = min(backtest_result.get("total_triggers", 0), 50)
        max_dd = min(backtest_result.get("max_drawdown_percent", 20), 20)
        
        score = (
            win_rate * 30 +
            (profit_factor / 3) * 25 +
            (sharpe / 3) * 25 +
            (trades / 50) * 10 +
            (1 - max_dd / 20) * 10
        )
        
        return round(max(0, min(100, score)), 1)
    
    def _get_optimization_score(
        self,
        backtest_result: Dict[str, Any],
        target: str
    ) -> float:
        """Get score for optimization target"""
        if target == "sharpe":
            return backtest_result.get("sharpe_ratio", 0)
        elif target == "win_rate":
            return backtest_result.get("win_rate", 0)
        elif target == "profit":
            return backtest_result.get("avg_return_percent", 0)
        elif target == "risk_adjusted":
            sharpe = backtest_result.get("sharpe_ratio", 0)
            win_rate = backtest_result.get("win_rate", 0)
            return sharpe * 0.6 + win_rate * 0.4
        return 0
    
    def _get_hit_rate_for_risk(self, risk_level: str) -> float:
        """Get target hit rate based on risk level"""
        rates = {
            "conservative": 0.05,  # Top 5%
            "moderate": 0.10,  # Top 10%
            "aggressive": 0.20  # Top 20%
        }
        return rates.get(risk_level, 0.10)
    
    def _get_position_size(self, risk_level: str, adaptive_params) -> float:
        """Calculate recommended position size"""
        base_sizes = {
            "conservative": 0.10,
            "moderate": 0.15,
            "aggressive": 0.25
        }
        base = base_sizes.get(risk_level, 0.15)
        return round(base * adaptive_params.position_size_modifier, 2)
    
    def _generate_signal_name(
        self,
        symbol: str,
        strategy_type: str,
        direction: str,
        time_window: str
    ) -> str:
        """Generate a descriptive signal name"""
        strategy_names = {
            "trend": "趋势",
            "reversal": "反转",
            "breakout": "突破",
            "scalping": "短线",
            "adaptive": "自适应"
        }
        direction_names = {
            "long": "做多",
            "short": "做空"
        }
        
        strategy = strategy_names.get(strategy_type, strategy_type)
        dir_name = direction_names.get(direction, direction)
        
        return f"{symbol}_{strategy}_{dir_name}_{time_window}"
    
    def _generate_description(
        self,
        strategy_type: str,
        direction: str,
        regime_type: str
    ) -> str:
        """Generate human-readable description"""
        strategy_config = STRATEGY_INDICATORS.get(strategy_type, STRATEGY_INDICATORS["adaptive"])
        regime_desc = get_regime_description(regime_type, "neutral")
        
        return f"{strategy_config['description']}。基于{regime_desc}市场状态生成。"
    
    def _generate_explanations(
        self,
        conditions: List[SignalCondition],
        analysis: MarketAnalysisResult,
        regime_type: str
    ) -> List[str]:
        """Generate explanations for each condition"""
        explanations = []
        
        explanations.append(f"当前市场状态: {regime_type}")
        
        for c in conditions:
            if c.description:
                explanations.append(c.description)
            else:
                explanations.append(f"{c.metric} {c.operator} {c.threshold}")
        
        return explanations
    
    def _generate_ai_prompt_template(
        self,
        symbol: str,
        direction: str,
        strategy_type: str,
        conditions: List[SignalCondition],
        regime_type: str,
        adaptive_params,
        time_window: str,
        lookback_days: int = 14  # 新增参数：历史数据天数
    ) -> str:
        """
        Generate AI prompt template for real-time signal judgment.
        
        This prompt is designed to be used with an AI model for real-time
        trading decisions based on technical indicators and market conditions.
        """
        direction_zh = "做多" if direction == "long" else "做空"
        strategy_names = {
            "trend": "趋势跟踪",
            "reversal": "均值回归",
            "breakout": "突破交易",
            "scalping": "短线套利",
            "adaptive": "自适应"
        }
        strategy_zh = strategy_names.get(strategy_type, strategy_type)
        
        # Build conditions description
        conditions_desc = []
        technical_conditions = []
        market_flow_conditions = []
        
        for c in conditions:
            indicator_desc = {
                "rsi": ("RSI相对强弱指数", f"当 RSI {'低于' if c.operator == 'less_than' else '高于'} {c.threshold} 时触发"),
                "rsi7": ("RSI(7)短期相对强弱", f"当 RSI(7) {'低于' if c.operator == 'less_than' else '高于'} {c.threshold} 时触发"),
                "stoch_k": ("随机指标K值", f"当 Stoch K {'低于' if c.operator == 'less_than' else '高于'} {c.threshold} 时触发"),
                "macd_histogram": ("MACD柱状图", f"当 MACD柱状图 {'大于' if c.operator == 'greater_than' else '小于'} 0 时触发"),
                "boll_position": ("布林带位置", f"当价格位于布林带 {'下轨附近(<{:.0%})'.format(c.threshold) if c.operator == 'less_than' else '上轨附近(>{:.0%})'.format(c.threshold)} 时触发"),
                "ema_cross": ("EMA均线交叉", "EMA金叉(短期>长期)" if c.operator == 'greater_than' else "EMA死叉(短期<长期)"),
                "cvd": ("累计成交量差(CVD)", f"当 CVD {'大于' if c.operator == 'greater_than' else '小于'} {c.threshold:,.0f} 时触发"),
                "oi_delta": ("持仓量变化", f"当 OI变化 大于 {c.threshold:.1f}% 时触发"),
                "order_imbalance": ("订单失衡度", f"当订单失衡 {'大于' if c.operator == 'greater_than' else '小于'} {c.threshold:.2f} 时触发"),
                "taker_ratio": ("主动买卖比", f"当买卖比对数 {'大于' if c.operator == 'greater_than' else '小于'} {c.threshold:.2f} 时触发"),
                "depth_ratio": ("买卖深度比", f"当深度比 {'大于' if c.operator == 'greater_than' else '小于'} {c.threshold} 时触发"),
                "funding": ("资金费率", f"当资金费率 {'小于' if c.operator == 'less_than' else '大于'} {c.threshold}% 时触发"),
            }
            
            desc = indicator_desc.get(c.metric, (c.metric, c.description or f"{c.metric} {c.operator} {c.threshold}"))
            
            if c.metric in TECHNICAL_INDICATORS:
                technical_conditions.append(f"- {desc[0]}: {desc[1]}")
            else:
                market_flow_conditions.append(f"- {desc[0]}: {desc[1]}")
        
        # Build the AI prompt template
        prompt = f"""## 交易信号判断指令

你是一个专业的加密货币交易分析师。请根据以下信号配置和实时市场数据，判断是否应该触发交易信号。

### 信号配置
- **交易对**: {symbol}/USDT
- **方向**: {direction_zh}
- **策略类型**: {strategy_zh}
- **时间周期**: {time_window}
- **当前市场状态**: {regime_type}

### 历史数据范围
- **分析周期**: 过去 {lookback_days} 天的数据
- **数据来源**: 包括K线、技术指标(RSI/MACD/布林带)和市场流指标(CVD/OI/订单簿)

### 信号触发条件
"""

        if technical_conditions:
            prompt += "\n**技术指标条件**:\n"
            prompt += "\n".join(technical_conditions)
            prompt += "\n"
        
        if market_flow_conditions:
            prompt += "\n**市场流动指标条件**:\n"
            prompt += "\n".join(market_flow_conditions)
            prompt += "\n"

        prompt += f"""
### 风险管理参数
- **建议止损**: {adaptive_params.stop_loss_atr_multiple * 1.5:.1f}%
- **建议止盈**: {adaptive_params.stop_loss_atr_multiple * adaptive_params.take_profit_ratio * 1.5:.1f}%
- **仓位系数**: {adaptive_params.position_size_modifier:.2f}x
- **风险等级**: {adaptive_params.risk_level}

### 实时市场数据
{{market_data}}

### 判断要求
1. 分析当前市场数据是否满足上述触发条件
2. 评估市场环境是否适合该策略类型
3. 考虑当前波动率和流动性情况
4. 给出明确的交易建议

### 输出格式
请以JSON格式输出判断结果:
```json
{{
  "should_trigger": true/false,
  "confidence": 0.0-1.0,
  "direction": "long"/"short"/"none",
  "entry_price_suggestion": 价格或null,
  "stop_loss_price": 价格或null,
  "take_profit_price": 价格或null,
  "reasoning": "判断理由说明",
  "risk_warnings": ["风险提示1", "风险提示2"]
}}
```
"""
        return prompt
    
    def _save_to_history(
        self,
        db: Session,
        config: GeneratedSignalConfig
    ) -> None:
        """Save generated signal to history"""
        try:
            history = GeneratedSignalHistory(
                symbol=config.symbol,
                strategy_type=config.strategy_type,
                direction=config.direction,
                risk_level=config.risk_level,
                time_window=config.trigger_condition.get("time_window", "5m"),
                signal_config=config.trigger_condition,
                market_regime_at_creation=config.market_regime_at_creation,
                backtest_metrics=config.backtest_metrics,
                effectiveness_score=config.effectiveness_score,
                is_active=True
            )
            db.add(history)
            db.commit()
        except Exception as e:
            logger.warning(f"Failed to save to history: {e}")
            db.rollback()


# Singleton instance
smart_signal_generator = SmartSignalGenerator()
