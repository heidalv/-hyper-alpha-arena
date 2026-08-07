"""
Pattern Recognition Service

Identifies and manages trading patterns based on historical market data.
Provides pattern detection, backtesting, and pattern discovery capabilities.
"""

import logging
import numpy as np
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.database.models import PatternDefinition, CryptoKline
from backend.services.signal_backtest_service import signal_backtest_service, TIMEFRAME_MS
from backend.services.market_regime_service import (
    get_market_regime, 
    REGIME_BREAKOUT, REGIME_CONTINUATION, REGIME_ABSORPTION,
    REGIME_EXHAUSTION, REGIME_TRAP, REGIME_NOISE
)

logger = logging.getLogger(__name__)


@dataclass
class PatternCondition:
    """A single condition in a pattern"""
    metric: str
    operator: str  # greater_than, less_than, equals, between
    threshold: float
    threshold_high: Optional[float] = None  # For "between" operator
    time_window: str = "5m"


@dataclass
class PatternTemplate:
    """A trading pattern template"""
    name: str
    pattern_type: str  # reversal, continuation, breakout, momentum
    conditions: List[PatternCondition]
    direction: str  # long, short
    typical_hold_bars: int
    description: str
    best_regimes: List[str] = field(default_factory=list)


@dataclass
class DetectedPattern:
    """A pattern detected in current market"""
    pattern_name: str
    pattern_type: str
    direction: str
    conditions_met: List[Dict[str, Any]]
    confidence: float  # Based on how strongly conditions are met
    historical_win_rate: Optional[float]
    historical_avg_return: Optional[float]
    sample_count: Optional[int]


@dataclass
class PatternBacktestResult:
    """Results from backtesting a pattern"""
    pattern_name: str
    symbol: str
    period_days: int
    total_triggers: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_return_percent: float
    max_return_percent: float
    min_return_percent: float
    avg_hold_bars: float
    sharpe_ratio: float
    profit_factor: float
    triggers: List[Dict[str, Any]]


# ============================================================================
# Pre-defined High-Win-Rate Pattern Templates
# ============================================================================

SYSTEM_PATTERNS: Dict[str, PatternTemplate] = {
    # === Reversal Patterns ===
    "oversold_reversal_long": PatternTemplate(
        name="RSI超卖反转做多",
        pattern_type="reversal",
        conditions=[
            PatternCondition(metric="rsi", operator="less_than", threshold=30, time_window="5m"),
            PatternCondition(metric="boll_position", operator="less_than", threshold=0.15, time_window="5m"),
        ],
        direction="long",
        typical_hold_bars=5,
        description="RSI超卖+布林带下轨，等待反弹",
        best_regimes=[REGIME_ABSORPTION, REGIME_EXHAUSTION]
    ),
    
    "overbought_reversal_short": PatternTemplate(
        name="RSI超买反转做空",
        pattern_type="reversal",
        conditions=[
            PatternCondition(metric="rsi", operator="greater_than", threshold=70, time_window="5m"),
            PatternCondition(metric="boll_position", operator="greater_than", threshold=0.85, time_window="5m"),
        ],
        direction="short",
        typical_hold_bars=5,
        description="RSI超买+布林带上轨，等待回调",
        best_regimes=[REGIME_ABSORPTION, REGIME_EXHAUSTION]
    ),
    
    # === Breakout Patterns ===
    "breakout_momentum_long": PatternTemplate(
        name="突破动量做多",
        pattern_type="breakout",
        conditions=[
            PatternCondition(metric="boll_position", operator="greater_than", threshold=0.9, time_window="5m"),
            PatternCondition(metric="oi_delta_percent", operator="greater_than", threshold=0.5, time_window="5m"),
            PatternCondition(metric="cvd", operator="greater_than", threshold=0, time_window="5m"),
        ],
        direction="long",
        typical_hold_bars=10,
        description="价格突破布林带上轨，OI增加，CVD正向",
        best_regimes=[REGIME_BREAKOUT, REGIME_CONTINUATION]
    ),
    
    "breakout_momentum_short": PatternTemplate(
        name="突破动量做空",
        pattern_type="breakout",
        conditions=[
            PatternCondition(metric="boll_position", operator="less_than", threshold=0.1, time_window="5m"),
            PatternCondition(metric="oi_delta_percent", operator="greater_than", threshold=0.5, time_window="5m"),
            PatternCondition(metric="cvd", operator="less_than", threshold=0, time_window="5m"),
        ],
        direction="short",
        typical_hold_bars=10,
        description="价格突破布林带下轨，OI增加，CVD负向",
        best_regimes=[REGIME_BREAKOUT, REGIME_CONTINUATION]
    ),
    
    # === Momentum Patterns ===
    "strong_buy_pressure": PatternTemplate(
        name="强势买压做多",
        pattern_type="momentum",
        conditions=[
            PatternCondition(metric="cvd", operator="greater_than", threshold=5000000, time_window="5m"),
            PatternCondition(metric="order_imbalance", operator="greater_than", threshold=0.6, time_window="5m"),
            PatternCondition(metric="rsi", operator="greater_than", threshold=50, time_window="5m"),
        ],
        direction="long",
        typical_hold_bars=8,
        description="CVD强正向，订单簿买盘主导",
        best_regimes=[REGIME_BREAKOUT, REGIME_CONTINUATION]
    ),
    
    "strong_sell_pressure": PatternTemplate(
        name="强势卖压做空",
        pattern_type="momentum",
        conditions=[
            PatternCondition(metric="cvd", operator="less_than", threshold=-5000000, time_window="5m"),
            PatternCondition(metric="order_imbalance", operator="less_than", threshold=-0.6, time_window="5m"),
            PatternCondition(metric="rsi", operator="less_than", threshold=50, time_window="5m"),
        ],
        direction="short",
        typical_hold_bars=8,
        description="CVD强负向，订单簿卖盘主导",
        best_regimes=[REGIME_BREAKOUT, REGIME_CONTINUATION]
    ),
    
    # === Continuation Patterns ===
    "pullback_buy": PatternTemplate(
        name="回调做多",
        pattern_type="continuation",
        conditions=[
            PatternCondition(metric="rsi", operator="between", threshold=40, threshold_high=55, time_window="15m"),
            PatternCondition(metric="boll_position", operator="between", threshold=0.3, threshold_high=0.6, time_window="15m"),
            PatternCondition(metric="cvd", operator="greater_than", threshold=0, time_window="15m"),
        ],
        direction="long",
        typical_hold_bars=12,
        description="上涨趋势中的回调买入机会",
        best_regimes=[REGIME_CONTINUATION]
    ),
    
    "pullback_sell": PatternTemplate(
        name="反弹做空",
        pattern_type="continuation",
        conditions=[
            PatternCondition(metric="rsi", operator="between", threshold=45, threshold_high=60, time_window="15m"),
            PatternCondition(metric="boll_position", operator="between", threshold=0.4, threshold_high=0.7, time_window="15m"),
            PatternCondition(metric="cvd", operator="less_than", threshold=0, time_window="15m"),
        ],
        direction="short",
        typical_hold_bars=12,
        description="下跌趋势中的反弹做空机会",
        best_regimes=[REGIME_CONTINUATION]
    ),
    
    # === OI-Based Patterns ===
    "oi_surge_long": PatternTemplate(
        name="OI激增做多",
        pattern_type="momentum",
        conditions=[
            PatternCondition(metric="oi_delta_percent", operator="greater_than", threshold=1.0, time_window="5m"),
            PatternCondition(metric="cvd", operator="greater_than", threshold=0, time_window="5m"),
        ],
        direction="long",
        typical_hold_bars=6,
        description="持仓量快速增加，伴随买盘流入",
        best_regimes=[REGIME_BREAKOUT]
    ),
    
    "oi_surge_short": PatternTemplate(
        name="OI激增做空",
        pattern_type="momentum",
        conditions=[
            PatternCondition(metric="oi_delta_percent", operator="greater_than", threshold=1.0, time_window="5m"),
            PatternCondition(metric="cvd", operator="less_than", threshold=0, time_window="5m"),
        ],
        direction="short",
        typical_hold_bars=6,
        description="持仓量快速增加，伴随卖盘流入",
        best_regimes=[REGIME_BREAKOUT]
    ),
}


class PatternRecognitionService:
    """
    市场模式识别服务
    
    Provides:
    - Pattern detection in current market
    - Pattern backtesting
    - Pattern discovery from historical data
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def detect_current_patterns(
        self,
        db: Session,
        symbol: str,
        period: str = "5m",
        include_system_patterns: bool = True,
        include_user_patterns: bool = True
    ) -> List[DetectedPattern]:
        """
        Detect which patterns are currently active in the market.
        
        Args:
            db: Database session
            symbol: Trading symbol
            period: Timeframe
            include_system_patterns: Check system-defined patterns
            include_user_patterns: Check user-created patterns from DB
            
        Returns:
            List of detected patterns with confidence scores
        """
        logger.info(f"Detecting patterns for {symbol} {period}")
        
        detected = []
        
        # Get current indicator values
        current_values = self._get_current_indicator_values(db, symbol, period)
        if not current_values:
            logger.warning(f"No indicator data for {symbol} {period}")
            return []
        
        # Check system patterns
        if include_system_patterns:
            for pattern_key, pattern in SYSTEM_PATTERNS.items():
                # Filter by time window compatibility
                pattern_period = pattern.conditions[0].time_window if pattern.conditions else "5m"
                if pattern_period != period:
                    continue
                
                result = self._check_pattern(pattern, current_values, db)
                if result:
                    detected.append(result)
        
        # Check user patterns from database
        if include_user_patterns:
            user_patterns = db.query(PatternDefinition).filter(
                PatternDefinition.is_active == True
            ).all()
            
            for pattern_def in user_patterns:
                pattern = self._db_to_pattern(pattern_def)
                result = self._check_pattern(pattern, current_values, db, pattern_def)
                if result:
                    detected.append(result)
        
        # Sort by confidence
        detected.sort(key=lambda x: x.confidence, reverse=True)
        
        logger.info(f"Detected {len(detected)} patterns for {symbol}")
        return detected
    
    def backtest_pattern(
        self,
        db: Session,
        pattern: PatternTemplate,
        symbol: str,
        days: int = 30,
        hold_bars: Optional[int] = None
    ) -> PatternBacktestResult:
        """
        Backtest a pattern against historical data.
        
        Simulates entering at pattern trigger and holding for specified bars.
        
        Args:
            db: Database session
            pattern: Pattern to test
            symbol: Trading symbol
            days: Days of history to test
            hold_bars: Override for typical_hold_bars
            
        Returns:
            PatternBacktestResult with performance metrics
        """
        logger.info(f"Backtesting pattern '{pattern.name}' on {symbol} for {days} days")
        
        if hold_bars is None:
            hold_bars = pattern.typical_hold_bars
        
        # Get the time window from first condition
        period = pattern.conditions[0].time_window if pattern.conditions else "5m"
        interval_ms = TIMEFRAME_MS.get(period, 300000)
        
        # Fetch kline data
        klines = self._fetch_klines(db, symbol, period, days)
        if len(klines) < hold_bars + 50:
            return self._empty_backtest_result(pattern.name, symbol, days)
        
        # Calculate indicators for all bars
        indicators_history = self._calculate_indicators_history(db, symbol, period, days)
        
        # Find all trigger points
        triggers = []
        returns = []
        
        for i in range(len(klines) - hold_bars):
            timestamp = klines[i]['timestamp']
            
            # Get indicators at this timestamp
            current_values = self._get_indicators_at_timestamp(indicators_history, timestamp)
            if not current_values:
                continue
            
            # Check if pattern conditions are met
            if self._conditions_met(pattern.conditions, current_values):
                entry_price = klines[i]['close']
                
                # Calculate return after hold_bars
                exit_price = klines[i + hold_bars]['close']
                
                if pattern.direction == "long":
                    ret = (exit_price - entry_price) / entry_price * 100
                else:  # short
                    ret = (entry_price - exit_price) / entry_price * 100
                
                triggers.append({
                    "timestamp": timestamp,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "return_percent": ret,
                    "indicators": current_values
                })
                returns.append(ret)
        
        if not triggers:
            return self._empty_backtest_result(pattern.name, symbol, days)
        
        # Calculate metrics
        returns_arr = np.array(returns)
        wins = sum(1 for r in returns if r > 0)
        losses = len(returns) - wins
        
        win_rate = wins / len(returns) if returns else 0
        avg_return = float(np.mean(returns_arr))
        
        # Sharpe ratio (simplified, daily basis)
        sharpe = float(np.mean(returns_arr) / np.std(returns_arr)) if np.std(returns_arr) > 0 else 0
        
        # Profit factor
        gross_profit = sum(r for r in returns if r > 0)
        gross_loss = abs(sum(r for r in returns if r < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        return PatternBacktestResult(
            pattern_name=pattern.name,
            symbol=symbol,
            period_days=days,
            total_triggers=len(triggers),
            winning_trades=wins,
            losing_trades=losses,
            win_rate=round(win_rate, 4),
            avg_return_percent=round(avg_return, 4),
            max_return_percent=round(float(np.max(returns_arr)), 4) if len(returns_arr) > 0 else 0,
            min_return_percent=round(float(np.min(returns_arr)), 4) if len(returns_arr) > 0 else 0,
            avg_hold_bars=float(hold_bars),
            sharpe_ratio=round(sharpe, 4),
            profit_factor=round(profit_factor, 4) if profit_factor != float('inf') else 999,
            triggers=triggers[:100]  # Limit for API response
        )
    
    def discover_patterns(
        self,
        db: Session,
        symbol: str,
        period: str = "5m",
        direction: str = "long",
        min_occurrences: int = 10,
        min_win_rate: float = 0.55,
        days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Discover new effective patterns from historical data.
        
        Tests combinations of indicators and thresholds to find
        patterns with positive expectancy.
        
        Args:
            db: Database session
            symbol: Trading symbol
            period: Timeframe
            direction: Trade direction to optimize for
            min_occurrences: Minimum trigger count required
            min_win_rate: Minimum win rate required
            days: Days of history to analyze
            
        Returns:
            List of discovered patterns with performance metrics
        """
        logger.info(f"Discovering patterns for {symbol} {period} {direction}")
        
        # Define indicator ranges to test
        indicator_configs = self._get_indicator_search_space(direction)
        
        discovered = []
        
        # Test single-indicator patterns first
        for config in indicator_configs:
            pattern = PatternTemplate(
                name=f"discovered_{config['metric']}_{config['operator']}",
                pattern_type="discovered",
                conditions=[PatternCondition(**config)],
                direction=direction,
                typical_hold_bars=5,
                description="Auto-discovered pattern",
                best_regimes=[]
            )
            
            result = self.backtest_pattern(db, pattern, symbol, days)
            
            if (result.total_triggers >= min_occurrences and 
                result.win_rate >= min_win_rate):
                discovered.append({
                    "conditions": [config],
                    "direction": direction,
                    "occurrences": result.total_triggers,
                    "win_rate": result.win_rate,
                    "avg_return": result.avg_return_percent,
                    "sharpe": result.sharpe_ratio
                })
        
        # Test two-indicator combinations
        for i, config1 in enumerate(indicator_configs):
            for config2 in indicator_configs[i+1:]:
                # Skip same metric combinations
                if config1['metric'] == config2['metric']:
                    continue
                
                pattern = PatternTemplate(
                    name=f"discovered_{config1['metric']}_{config2['metric']}",
                    pattern_type="discovered",
                    conditions=[
                        PatternCondition(**config1),
                        PatternCondition(**config2)
                    ],
                    direction=direction,
                    typical_hold_bars=5,
                    description="Auto-discovered combination",
                    best_regimes=[]
                )
                
                result = self.backtest_pattern(db, pattern, symbol, days)
                
                if (result.total_triggers >= min_occurrences and 
                    result.win_rate >= min_win_rate):
                    discovered.append({
                        "conditions": [config1, config2],
                        "direction": direction,
                        "occurrences": result.total_triggers,
                        "win_rate": result.win_rate,
                        "avg_return": result.avg_return_percent,
                        "sharpe": result.sharpe_ratio
                    })
        
        # Sort by Sharpe ratio
        discovered.sort(key=lambda x: x['sharpe'], reverse=True)
        
        logger.info(f"Discovered {len(discovered)} patterns meeting criteria")
        return discovered[:20]  # Return top 20
    
    def save_pattern_to_db(
        self,
        db: Session,
        pattern: PatternTemplate,
        backtest_result: Optional[PatternBacktestResult] = None,
        is_system: bool = False
    ) -> PatternDefinition:
        """Save a pattern to the database"""
        conditions_json = [
            {
                "metric": c.metric,
                "operator": c.operator,
                "threshold": c.threshold,
                "threshold_high": c.threshold_high,
                "time_window": c.time_window
            }
            for c in pattern.conditions
        ]
        
        pattern_def = PatternDefinition(
            pattern_name=pattern.name,
            pattern_type=pattern.pattern_type,
            conditions=conditions_json,
            direction=pattern.direction,
            typical_hold_bars=pattern.typical_hold_bars,
            description=pattern.description,
            best_regimes=pattern.best_regimes,
            is_system=is_system,
            is_active=True
        )
        
        if backtest_result:
            pattern_def.historical_win_rate = backtest_result.win_rate
            pattern_def.historical_avg_return = backtest_result.avg_return_percent
            pattern_def.historical_sharpe = backtest_result.sharpe_ratio
            pattern_def.sample_count = backtest_result.total_triggers
            pattern_def.last_backtested_at = datetime.now(timezone.utc)
        
        db.add(pattern_def)
        db.commit()
        db.refresh(pattern_def)
        
        return pattern_def
    
    def get_patterns_for_regime(
        self,
        db: Session,
        regime_type: str,
        direction: Optional[str] = None
    ) -> List[PatternTemplate]:
        """
        Get patterns that work well in a specific market regime.
        
        Args:
            db: Database session
            regime_type: Market regime type
            direction: Optional filter by direction
            
        Returns:
            List of suitable patterns
        """
        suitable = []
        
        # Check system patterns
        for pattern in SYSTEM_PATTERNS.values():
            if regime_type in pattern.best_regimes:
                if direction is None or pattern.direction == direction:
                    suitable.append(pattern)
        
        # Check user patterns from DB
        user_patterns = db.query(PatternDefinition).filter(
            PatternDefinition.is_active == True
        ).all()
        
        for pattern_def in user_patterns:
            if pattern_def.best_regimes and regime_type in pattern_def.best_regimes:
                if direction is None or pattern_def.direction == direction:
                    suitable.append(self._db_to_pattern(pattern_def))
        
        return suitable
    
    # ========================================================================
    # Private Helper Methods
    # ========================================================================
    
    def _get_current_indicator_values(
        self,
        db: Session,
        symbol: str,
        period: str
    ) -> Dict[str, float]:
        """Get current indicator values for pattern matching"""
        from services.market_data_analyzer import market_data_analyzer
        
        values = {}
        
        try:
            # Get market analysis
            analysis = market_data_analyzer.analyze_period(db, symbol, period, lookback_days=7)
            
            # Extract indicator values
            for name, dist in analysis.indicator_distributions.items():
                values[name] = dist.current_value
            
            # Add price analysis values
            values['price'] = analysis.price_analysis.current_price
            values['atr'] = analysis.price_analysis.volatility_atr
            
        except Exception as e:
            logger.warning(f"Failed to get indicator values: {e}")
        
        return values
    
    def _check_pattern(
        self,
        pattern: PatternTemplate,
        current_values: Dict[str, float],
        db: Session,
        pattern_def: Optional[PatternDefinition] = None
    ) -> Optional[DetectedPattern]:
        """Check if a pattern is currently triggered"""
        conditions_met = []
        total_confidence = 0
        
        for condition in pattern.conditions:
            metric_value = current_values.get(condition.metric)
            if metric_value is None:
                return None  # Missing required indicator
            
            met, confidence = self._check_condition(condition, metric_value)
            if not met:
                return None  # All conditions must be met
            
            conditions_met.append({
                "metric": condition.metric,
                "operator": condition.operator,
                "threshold": condition.threshold,
                "current_value": metric_value,
                "confidence": confidence
            })
            total_confidence += confidence
        
        avg_confidence = total_confidence / len(pattern.conditions) if pattern.conditions else 0
        
        return DetectedPattern(
            pattern_name=pattern.name,
            pattern_type=pattern.pattern_type,
            direction=pattern.direction,
            conditions_met=conditions_met,
            confidence=round(avg_confidence, 2),
            historical_win_rate=pattern_def.historical_win_rate if pattern_def else None,
            historical_avg_return=pattern_def.historical_avg_return if pattern_def else None,
            sample_count=pattern_def.sample_count if pattern_def else None
        )
    
    def _check_condition(
        self,
        condition: PatternCondition,
        value: float
    ) -> Tuple[bool, float]:
        """
        Check if a condition is met and calculate confidence.
        
        Returns (is_met, confidence) where confidence indicates
        how strongly the condition is met (0-1).
        """
        if condition.operator == "greater_than":
            met = value > condition.threshold
            if met:
                # Confidence based on how far above threshold
                excess = (value - condition.threshold) / abs(condition.threshold) if condition.threshold != 0 else 1
                confidence = min(0.5 + excess * 0.5, 1.0)
            else:
                confidence = 0
        
        elif condition.operator == "less_than":
            met = value < condition.threshold
            if met:
                excess = (condition.threshold - value) / abs(condition.threshold) if condition.threshold != 0 else 1
                confidence = min(0.5 + excess * 0.5, 1.0)
            else:
                confidence = 0
        
        elif condition.operator == "between":
            met = condition.threshold <= value <= (condition.threshold_high or condition.threshold)
            if met:
                # Higher confidence in middle of range
                mid = (condition.threshold + (condition.threshold_high or condition.threshold)) / 2
                range_size = (condition.threshold_high or condition.threshold) - condition.threshold
                distance_from_mid = abs(value - mid)
                confidence = max(0.5, 1 - distance_from_mid / (range_size / 2)) if range_size > 0 else 0.7
            else:
                confidence = 0
        
        elif condition.operator == "equals":
            tolerance = abs(condition.threshold) * 0.05  # 5% tolerance
            met = abs(value - condition.threshold) <= tolerance
            confidence = 0.8 if met else 0
        
        else:
            met = False
            confidence = 0
        
        return met, confidence
    
    def _conditions_met(
        self,
        conditions: List[PatternCondition],
        values: Dict[str, float]
    ) -> bool:
        """Check if all conditions are met"""
        for condition in conditions:
            value = values.get(condition.metric)
            if value is None:
                return False
            met, _ = self._check_condition(condition, value)
            if not met:
                return False
        return True
    
    def _fetch_klines(
        self,
        db: Session,
        symbol: str,
        period: str,
        days: int
    ) -> List[Dict[str, Any]]:
        """Fetch kline data"""
        # 修时区 bug：用 UTC-aware 计算 Unix 毫秒
        # M1 收口：统一 K 线查询门面（数据中心）
        # 修复：timestamp 列以秒存储，原代码按毫秒过滤导致恒空
        from backend.services.kline_data_service import kline_service as _ks
        end_ts = int(datetime.now(timezone.utc).timestamp())
        start_ts = end_ts - (days * 24 * 60 * 60)
        records = _ks.query_klines(
            symbol.upper(), period,
            start_ts=start_ts, end_ts=end_ts, order="asc",
        )
        return records
    
    def _calculate_indicators_history(
        self,
        db: Session,
        symbol: str,
        period: str,
        days: int
    ) -> Dict[str, Dict[int, float]]:
        """Calculate indicator values for all timestamps"""
        # This is a simplified version - in production, would use cached values
        interval_ms = TIMEFRAME_MS.get(period, 300000)
        
        history = {
            "rsi": {},
            "boll_position": {},
            "cvd": {},
            "oi_delta_percent": {},
            "order_imbalance": {}
        }
        
        # Get flow indicator buckets
        try:
            cvd_buckets = signal_backtest_service._compute_all_bucket_values(
                db, symbol.upper(), "cvd", interval_ms
            )
            if cvd_buckets:
                history["cvd"] = cvd_buckets
            
            oi_buckets = signal_backtest_service._compute_all_bucket_values(
                db, symbol.upper(), "oi_delta", interval_ms
            )
            if oi_buckets:
                history["oi_delta_percent"] = oi_buckets
            
            imb_buckets = signal_backtest_service._compute_all_bucket_values(
                db, symbol.upper(), "order_imbalance", interval_ms
            )
            if imb_buckets:
                history["order_imbalance"] = imb_buckets
                
        except Exception as e:
            logger.warning(f"Failed to calculate indicator history: {e}")
        
        return history
    
    def _get_indicators_at_timestamp(
        self,
        history: Dict[str, Dict[int, float]],
        timestamp: int
    ) -> Dict[str, float]:
        """Get indicator values at a specific timestamp"""
        values = {}
        for indicator, data in history.items():
            if timestamp in data:
                values[indicator] = data[timestamp]
            else:
                # Find nearest timestamp
                nearest = min(data.keys(), key=lambda x: abs(x - timestamp), default=None)
                if nearest and abs(nearest - timestamp) < 60000:  # Within 1 minute
                    values[indicator] = data[nearest]
        return values
    
    def _get_indicator_search_space(self, direction: str) -> List[Dict[str, Any]]:
        """Get indicator configurations to test in pattern discovery"""
        configs = []
        
        if direction == "long":
            # RSI oversold conditions
            configs.extend([
                {"metric": "rsi", "operator": "less_than", "threshold": 30, "time_window": "5m"},
                {"metric": "rsi", "operator": "less_than", "threshold": 35, "time_window": "5m"},
                {"metric": "rsi", "operator": "between", "threshold": 40, "threshold_high": 55, "time_window": "5m"},
            ])
            # Positive CVD
            configs.extend([
                {"metric": "cvd", "operator": "greater_than", "threshold": 1000000, "time_window": "5m"},
                {"metric": "cvd", "operator": "greater_than", "threshold": 5000000, "time_window": "5m"},
            ])
            # OI increasing
            configs.extend([
                {"metric": "oi_delta_percent", "operator": "greater_than", "threshold": 0.5, "time_window": "5m"},
                {"metric": "oi_delta_percent", "operator": "greater_than", "threshold": 1.0, "time_window": "5m"},
            ])
            # Order book imbalance
            configs.extend([
                {"metric": "order_imbalance", "operator": "greater_than", "threshold": 0.5, "time_window": "5m"},
                {"metric": "order_imbalance", "operator": "greater_than", "threshold": 0.7, "time_window": "5m"},
            ])
        else:  # short
            configs.extend([
                {"metric": "rsi", "operator": "greater_than", "threshold": 70, "time_window": "5m"},
                {"metric": "rsi", "operator": "greater_than", "threshold": 65, "time_window": "5m"},
                {"metric": "cvd", "operator": "less_than", "threshold": -1000000, "time_window": "5m"},
                {"metric": "cvd", "operator": "less_than", "threshold": -5000000, "time_window": "5m"},
                {"metric": "oi_delta_percent", "operator": "greater_than", "threshold": 0.5, "time_window": "5m"},
                {"metric": "order_imbalance", "operator": "less_than", "threshold": -0.5, "time_window": "5m"},
            ])
        
        return configs
    
    def _db_to_pattern(self, pattern_def: PatternDefinition) -> PatternTemplate:
        """Convert database PatternDefinition to PatternTemplate"""
        conditions = [
            PatternCondition(
                metric=c.get("metric", ""),
                operator=c.get("operator", "greater_than"),
                threshold=c.get("threshold", 0),
                threshold_high=c.get("threshold_high"),
                time_window=c.get("time_window", "5m")
            )
            for c in (pattern_def.conditions or [])
        ]
        
        return PatternTemplate(
            name=pattern_def.pattern_name,
            pattern_type=pattern_def.pattern_type,
            conditions=conditions,
            direction=pattern_def.direction,
            typical_hold_bars=pattern_def.typical_hold_bars or 5,
            description=pattern_def.description or "",
            best_regimes=pattern_def.best_regimes or []
        )
    
    def _empty_backtest_result(
        self,
        pattern_name: str,
        symbol: str,
        days: int
    ) -> PatternBacktestResult:
        """Return empty backtest result"""
        return PatternBacktestResult(
            pattern_name=pattern_name,
            symbol=symbol,
            period_days=days,
            total_triggers=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0,
            avg_return_percent=0,
            max_return_percent=0,
            min_return_percent=0,
            avg_hold_bars=0,
            sharpe_ratio=0,
            profit_factor=0,
            triggers=[]
        )


# Singleton instance
pattern_recognition_service = PatternRecognitionService()
