"""
Market Data Analyzer Service

Provides comprehensive historical market data analysis for smart signal generation.
Analyzes price behavior, volume, indicators, and identifies profitable patterns.
"""

import logging
import math
import numpy as np
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.database.models import CryptoKline, MarketTradesAggregated, MarketAssetMetrics
from backend.services.technical_indicators import calculate_indicators
from backend.services.market_flow_indicators import (
    get_flow_indicators_for_prompt,
    TIMEFRAME_MS,
    floor_timestamp
)
from backend.services.market_regime_service import classify_market_regime, get_default_config

logger = logging.getLogger(__name__)


# Timeframe mappings
TIMEFRAME_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "8h": 480, "12h": 720, "1d": 1440
}


@dataclass
class PriceAnalysis:
    """Price behavior analysis results"""
    trend_direction: str  # bullish/bearish/sideways
    trend_strength: float  # 0-1
    volatility_atr: float
    volatility_percentile: float  # Where current vol ranks (0-100)
    support_levels: List[float]
    resistance_levels: List[float]
    price_range_high: float
    price_range_low: float
    current_price: float


@dataclass
class VolumeAnalysis:
    """Volume and flow analysis results"""
    avg_volume: float
    volume_trend: str  # increasing/decreasing/stable
    cvd_cumulative: float
    cvd_direction: str  # positive/negative
    buy_sell_ratio: float
    abnormal_volume_count: int  # Count of >2 std dev volume bars


@dataclass
class IndicatorDistribution:
    """Statistical distribution of indicator values"""
    indicator_name: str
    current_value: float
    mean: float
    std: float
    min_value: float
    max_value: float
    percentile_25: float
    percentile_50: float
    percentile_75: float
    percentile_90: float
    percentile_95: float


@dataclass
class ThresholdSuggestion:
    """Suggested threshold for a metric"""
    metric: str
    operator: str
    conservative_threshold: float  # Top 5%
    moderate_threshold: float  # Top 10%
    aggressive_threshold: float  # Top 20%
    current_value: float
    description: str


@dataclass
class MarketAnalysisResult:
    """Complete market analysis result"""
    symbol: str
    period: str
    analysis_time: str
    lookback_days: int
    data_points: int
    price_analysis: PriceAnalysis
    volume_analysis: VolumeAnalysis
    indicator_distributions: Dict[str, IndicatorDistribution]
    threshold_suggestions: Dict[str, ThresholdSuggestion]
    regime_type: str
    regime_direction: str
    regime_confidence: float


@dataclass
class OptimalEntry:
    """An identified optimal entry point"""
    timestamp: int
    direction: str  # long/short
    max_profit_percent: float
    indicators_at_entry: Dict[str, float]
    regime_at_entry: str


@dataclass 
class ProfitablePattern:
    """A pattern that historically produced profits"""
    conditions: List[Dict[str, Any]]
    direction: str
    occurrences: int
    win_rate: float
    avg_return_percent: float
    max_return_percent: float
    avg_hold_bars: int


class MarketDataAnalyzer:
    """
    历史市场数据分析引擎
    
    Provides:
    - Period analysis (price, volume, indicators)
    - Optimal threshold calculation
    - Profitable pattern identification
    - Entry point analysis
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def analyze_period(
        self,
        db: Session,
        symbol: str,
        period: str = "5m",
        lookback_days: int = 30
    ) -> MarketAnalysisResult:
        """
        Comprehensive analysis of market data for a specified period.
        
        Args:
            db: Database session
            symbol: Trading symbol (e.g., "BTC")
            period: Timeframe (e.g., "5m", "15m", "1h")
            lookback_days: Days of historical data to analyze
            
        Returns:
            MarketAnalysisResult with price, volume, and indicator analysis
        """
        logger.info(f"Analyzing {symbol} {period} for {lookback_days} days")
        
        # Fetch kline data
        klines = self._fetch_klines(db, symbol, period, lookback_days)
        if not klines or len(klines) < 20:
            logger.warning(f"Insufficient kline data for {symbol} {period}")
            return self._empty_analysis_result(symbol, period, lookback_days)
        
        # Price analysis
        price_analysis = self._analyze_price(klines)
        
        # Volume analysis
        volume_analysis = self._analyze_volume(db, symbol, period, lookback_days)
        
        # Technical indicator distributions
        indicator_distributions = self._analyze_indicators(klines)
        
        # Flow indicator analysis
        flow_distributions = self._analyze_flow_indicators(db, symbol, period, lookback_days)
        indicator_distributions.update(flow_distributions)
        
        # Generate threshold suggestions
        threshold_suggestions = self._generate_threshold_suggestions(
            indicator_distributions, klines
        )
        
        # Current market regime
        regime_type, regime_direction, regime_confidence = self._get_current_regime(
            db, symbol, period
        )
        
        return MarketAnalysisResult(
            symbol=symbol,
            period=period,
            analysis_time=datetime.now(timezone.utc).isoformat(),
            lookback_days=lookback_days,
            data_points=len(klines),
            price_analysis=price_analysis,
            volume_analysis=volume_analysis,
            indicator_distributions=indicator_distributions,
            threshold_suggestions=threshold_suggestions,
            regime_type=regime_type,
            regime_direction=regime_direction,
            regime_confidence=regime_confidence
        )
    
    def calculate_optimal_thresholds(
        self,
        db: Session,
        symbol: str,
        metric: str,
        period: str = "5m",
        target_hit_rate: float = 0.1,
        lookback_days: int = 30
    ) -> ThresholdSuggestion:
        """
        Calculate optimal threshold for a specific metric.
        
        Args:
            db: Database session
            symbol: Trading symbol
            metric: Metric name (e.g., "oi_delta_percent", "cvd", "rsi")
            period: Timeframe
            target_hit_rate: Target percentage of signals (0.1 = top 10%)
            lookback_days: Days of history
            
        Returns:
            ThresholdSuggestion with conservative/moderate/aggressive thresholds
        """
        logger.info(f"Calculating optimal thresholds for {symbol} {metric} {period}")
        
        # Get metric values
        values = self._get_metric_values(db, symbol, metric, period, lookback_days)
        
        if not values or len(values) < 10:
            return ThresholdSuggestion(
                metric=metric,
                operator=">",
                conservative_threshold=0,
                moderate_threshold=0,
                aggressive_threshold=0,
                current_value=0,
                description="Insufficient data"
            )
        
        arr = np.array(values)
        current_value = arr[-1] if len(arr) > 0 else 0
        
        # For metrics where we look for high values (long signals)
        p80 = float(np.percentile(arr, 80))
        p90 = float(np.percentile(arr, 90))
        p95 = float(np.percentile(arr, 95))
        
        # Determine operator based on metric type
        operator = self._determine_operator(metric)
        
        return ThresholdSuggestion(
            metric=metric,
            operator=operator,
            conservative_threshold=p95,  # Top 5%
            moderate_threshold=p90,  # Top 10%
            aggressive_threshold=p80,  # Top 20%
            current_value=current_value,
            description=f"Based on {len(values)} data points over {lookback_days} days"
        )
    
    def identify_optimal_entries(
        self,
        db: Session,
        symbol: str,
        period: str = "5m",
        lookforward_bars: int = 10,
        min_profit_percent: float = 1.0,
        lookback_days: int = 30
    ) -> List[OptimalEntry]:
        """
        Identify historical points that would have been optimal entries.
        
        Uses hindsight to label bars where entering would have yielded
        at least min_profit_percent within lookforward_bars.
        
        Args:
            db: Database session
            symbol: Trading symbol
            period: Timeframe
            lookforward_bars: How many bars to look ahead for profit
            min_profit_percent: Minimum profit to qualify as optimal
            lookback_days: Days of history to scan
            
        Returns:
            List of OptimalEntry points with indicator snapshots
        """
        logger.info(f"Identifying optimal entries for {symbol} {period}")
        
        klines = self._fetch_klines(db, symbol, period, lookback_days)
        if not klines or len(klines) < lookforward_bars + 50:
            return []
        
        # Calculate indicators for all bars
        indicators = self._calculate_all_indicators(klines)
        
        optimal_entries = []
        
        for i in range(len(klines) - lookforward_bars):
            current = klines[i]
            future_klines = klines[i + 1:i + 1 + lookforward_bars]
            
            if not future_klines:
                continue
            
            close_price = float(current['close'])
            future_highs = [float(k['high']) for k in future_klines]
            future_lows = [float(k['low']) for k in future_klines]
            
            max_high = max(future_highs)
            min_low = min(future_lows)
            
            # Long profit potential
            long_profit = (max_high - close_price) / close_price * 100
            # Short profit potential
            short_profit = (close_price - min_low) / close_price * 100
            
            # Check for optimal long entry
            if long_profit >= min_profit_percent:
                entry = OptimalEntry(
                    timestamp=current['timestamp'],
                    direction="long",
                    max_profit_percent=long_profit,
                    indicators_at_entry=self._extract_indicators_at(indicators, i),
                    regime_at_entry=self._get_regime_at_index(indicators, i)
                )
                optimal_entries.append(entry)
            
            # Check for optimal short entry
            elif short_profit >= min_profit_percent:
                entry = OptimalEntry(
                    timestamp=current['timestamp'],
                    direction="short",
                    max_profit_percent=short_profit,
                    indicators_at_entry=self._extract_indicators_at(indicators, i),
                    regime_at_entry=self._get_regime_at_index(indicators, i)
                )
                optimal_entries.append(entry)
        
        logger.info(f"Found {len(optimal_entries)} optimal entry points")
        return optimal_entries
    
    def analyze_entry_characteristics(
        self,
        optimal_entries: List[OptimalEntry],
        direction: str = "long"
    ) -> Dict[str, Any]:
        """
        Analyze common characteristics of optimal entry points.
        
        Args:
            optimal_entries: List of identified optimal entries
            direction: Filter by direction (long/short/all)
            
        Returns:
            Statistics about indicator values at optimal entries
        """
        filtered = [e for e in optimal_entries 
                    if direction == "all" or e.direction == direction]
        
        if not filtered:
            return {"error": "No entries found for analysis"}
        
        # Collect indicator values
        indicator_values = {}
        for entry in filtered:
            for key, value in entry.indicators_at_entry.items():
                if key not in indicator_values:
                    indicator_values[key] = []
                if value is not None:
                    indicator_values[key].append(value)
        
        # Calculate statistics for each indicator
        stats = {}
        for indicator, values in indicator_values.items():
            if len(values) < 5:
                continue
            arr = np.array(values)
            stats[indicator] = {
                "mean": float(np.mean(arr)),
                "std": float(np.std(arr)),
                "median": float(np.median(arr)),
                "p25": float(np.percentile(arr, 25)),
                "p75": float(np.percentile(arr, 75)),
                "min": float(np.min(arr)),
                "max": float(np.max(arr)),
                "sample_count": len(values)
            }
        
        return {
            "direction": direction,
            "total_entries": len(filtered),
            "avg_profit": float(np.mean([e.max_profit_percent for e in filtered])),
            "indicator_stats": stats
        }
    
    def _fetch_klines(
        self,
        db: Session,
        symbol: str,
        period: str,
        days: int
    ) -> List[Dict[str, Any]]:
        """Fetch kline data from database
        
        注意：数据库 crypto_klines 表的 timestamp 字段是秒级（Unix seconds），
        不是毫秒。Hyperliquid 和 Binance 采集器都做了 /1000 转换后存入。
        
        交易所过滤：通过中央配置服务获取当前活跃交易所，确保只查对应数据。
        """
        from backend.services.exchange_config import get_active_exchange
        active_exchange = get_active_exchange()
        
        # 修时区 bug：用 UTC-aware 计算 Unix 秒，避免 naive utcnow 被当作本地时区
        end_time = int(datetime.now(timezone.utc).timestamp())   # 秒级
        start_time = end_time - (days * 24 * 60 * 60)            # 秒级
        
        # M1 收口：统一 K 线查询门面（数据中心）
        from backend.services.kline_data_service import kline_service as _ks
        return _ks.query_klines(
            symbol.upper(), period,
            exchange=active_exchange,
            start_ts=start_time, end_ts=end_time,
            order="asc",
        )
    
    def _analyze_price(self, klines: List[Dict]) -> PriceAnalysis:
        """Analyze price behavior"""
        if not klines:
            return PriceAnalysis(
                trend_direction="unknown",
                trend_strength=0,
                volatility_atr=0,
                volatility_percentile=0,
                support_levels=[],
                resistance_levels=[],
                price_range_high=0,
                price_range_low=0,
                current_price=0
            )
        
        closes = [k['close'] for k in klines]
        highs = [k['high'] for k in klines]
        lows = [k['low'] for k in klines]
        
        current_price = closes[-1]
        
        # Calculate ATR
        atrs = []
        for i in range(1, len(klines)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            atrs.append(tr)
        
        atr = np.mean(atrs[-14:]) if len(atrs) >= 14 else np.mean(atrs) if atrs else 0
        
        # ATR percentile (volatility rank)
        if len(atrs) >= 20:
            atr_percentile = (sum(1 for a in atrs if a <= atrs[-1]) / len(atrs)) * 100
        else:
            atr_percentile = 50
        
        # Trend analysis using EMA
        if len(closes) >= 20:
            ema20 = self._ema(closes, 20)
            ema50 = self._ema(closes, 50) if len(closes) >= 50 else ema20
            
            if current_price > ema20 > ema50:
                trend_direction = "bullish"
                trend_strength = min((current_price - ema50) / ema50 * 100 / 5, 1.0)
            elif current_price < ema20 < ema50:
                trend_direction = "bearish"
                trend_strength = min((ema50 - current_price) / ema50 * 100 / 5, 1.0)
            else:
                trend_direction = "sideways"
                trend_strength = 0.3
        else:
            trend_direction = "unknown"
            trend_strength = 0
        
        # Support/Resistance levels (simplified)
        recent_lows = sorted(lows[-50:])[:5] if len(lows) >= 50 else sorted(lows)[:3]
        recent_highs = sorted(highs[-50:], reverse=True)[:5] if len(highs) >= 50 else sorted(highs, reverse=True)[:3]
        
        return PriceAnalysis(
            trend_direction=trend_direction,
            trend_strength=float(trend_strength),
            volatility_atr=float(atr),
            volatility_percentile=float(atr_percentile),
            support_levels=recent_lows,
            resistance_levels=recent_highs,
            price_range_high=max(highs),
            price_range_low=min(lows),
            current_price=float(current_price)
        )
    
    def _analyze_volume(
        self,
        db: Session,
        symbol: str,
        period: str,
        days: int
    ) -> VolumeAnalysis:
        """Analyze volume and CVD"""
        interval_ms = TIMEFRAME_MS.get(period, 300000)
        # 修时区 bug：用 UTC-aware 计算 Unix 毫秒
        end_time = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_time = end_time - (days * 24 * 60 * 60 * 1000)
        
        records = db.query(
            MarketTradesAggregated.timestamp,
            MarketTradesAggregated.taker_buy_notional,
            MarketTradesAggregated.taker_sell_notional
        ).filter(
            MarketTradesAggregated.symbol == symbol.upper(),
            MarketTradesAggregated.timestamp >= start_time,
            MarketTradesAggregated.timestamp <= end_time
        ).order_by(MarketTradesAggregated.timestamp).all()
        
        if not records:
            return VolumeAnalysis(
                avg_volume=0,
                volume_trend="unknown",
                cvd_cumulative=0,
                cvd_direction="neutral",
                buy_sell_ratio=1.0,
                abnormal_volume_count=0
            )
        
        # Aggregate by period
        buckets = {}
        for ts, buy, sell in records:
            bucket_ts = floor_timestamp(ts, interval_ms)
            if bucket_ts not in buckets:
                buckets[bucket_ts] = {"buy": 0, "sell": 0}
            buckets[bucket_ts]["buy"] += float(buy or 0)
            buckets[bucket_ts]["sell"] += float(sell or 0)
        
        volumes = []
        cvd_values = []
        total_buy = 0
        total_sell = 0
        
        for ts in sorted(buckets.keys()):
            buy = buckets[ts]["buy"]
            sell = buckets[ts]["sell"]
            volumes.append(buy + sell)
            cvd_values.append(buy - sell)
            total_buy += buy
            total_sell += sell
        
        if not volumes:
            return VolumeAnalysis(
                avg_volume=0,
                volume_trend="unknown",
                cvd_cumulative=0,
                cvd_direction="neutral",
                buy_sell_ratio=1.0,
                abnormal_volume_count=0
            )
        
        avg_volume = float(np.mean(volumes))
        std_volume = float(np.std(volumes))
        
        # Volume trend (comparing recent vs historical)
        if len(volumes) >= 20:
            recent_avg = np.mean(volumes[-10:])
            historical_avg = np.mean(volumes[:-10])
            if recent_avg > historical_avg * 1.2:
                volume_trend = "increasing"
            elif recent_avg < historical_avg * 0.8:
                volume_trend = "decreasing"
            else:
                volume_trend = "stable"
        else:
            volume_trend = "unknown"
        
        # CVD cumulative
        cvd_cumulative = sum(cvd_values)
        cvd_direction = "positive" if cvd_cumulative > 0 else "negative"
        
        # Buy/sell ratio
        buy_sell_ratio = total_buy / total_sell if total_sell > 0 else 1.0
        
        # Abnormal volume count (>2 std dev)
        threshold = avg_volume + 2 * std_volume
        abnormal_count = sum(1 for v in volumes if v > threshold)
        
        return VolumeAnalysis(
            avg_volume=avg_volume,
            volume_trend=volume_trend,
            cvd_cumulative=float(cvd_cumulative),
            cvd_direction=cvd_direction,
            buy_sell_ratio=float(buy_sell_ratio),
            abnormal_volume_count=abnormal_count
        )
    
    def _analyze_indicators(
        self,
        klines: List[Dict]
    ) -> Dict[str, IndicatorDistribution]:
        """Calculate and analyze technical indicators"""
        import pandas as pd
        
        if len(klines) < 20:
            return {}
        
        # Convert to DataFrame for indicator calculation
        df = pd.DataFrame(klines)
        
        # Calculate indicators
        indicators = calculate_indicators(
            df,
            ['RSI14', 'RSI7', 'MACD', 'BOLL', 'ATR14', 'STOCH']
        )
        
        distributions = {}
        
        # RSI14
        if 'RSI14' in indicators and indicators['RSI14']:
            rsi_values = [v for v in indicators['RSI14'] if v and v != 50]
            if rsi_values:
                distributions['rsi'] = self._calc_distribution('rsi', rsi_values)
        
        # RSI7
        if 'RSI7' in indicators and indicators['RSI7']:
            rsi7_values = [v for v in indicators['RSI7'] if v and v != 50]
            if rsi7_values:
                distributions['rsi7'] = self._calc_distribution('rsi7', rsi7_values)
        
        # MACD histogram
        if 'MACD' in indicators and indicators['MACD']:
            macd = indicators['MACD']
            if 'histogram' in macd:
                hist_values = [v for v in macd['histogram'] if v is not None]
                if hist_values:
                    distributions['macd_histogram'] = self._calc_distribution(
                        'macd_histogram', hist_values
                    )
        
        # Bollinger %B (price position within bands)
        if 'BOLL' in indicators and indicators['BOLL']:
            boll = indicators['BOLL']
            upper = boll.get('upper', [])
            lower = boll.get('lower', [])
            closes = [k['close'] for k in klines]
            
            if upper and lower and len(upper) == len(closes):
                percent_b = []
                for i in range(len(closes)):
                    if upper[i] and lower[i] and upper[i] != lower[i]:
                        pb = (closes[i] - lower[i]) / (upper[i] - lower[i])
                        percent_b.append(pb)
                
                if percent_b:
                    distributions['boll_position'] = self._calc_distribution(
                        'boll_position', percent_b
                    )
        
        return distributions
    
    def _analyze_flow_indicators(
        self,
        db: Session,
        symbol: str,
        period: str,
        days: int
    ) -> Dict[str, IndicatorDistribution]:
        """Analyze market flow indicators"""
        from services.signal_backtest_service import signal_backtest_service
        
        distributions = {}
        interval_ms = TIMEFRAME_MS.get(period, 300000)
        
        # OI Delta
        try:
            oi_buckets = signal_backtest_service._compute_all_bucket_values(
                db, symbol.upper(), "oi_delta", interval_ms
            )
            if oi_buckets:
                values = [v for v in oi_buckets.values() if v is not None]
                if values:
                    distributions['oi_delta_percent'] = self._calc_distribution(
                        'oi_delta_percent', values
                    )
        except Exception as e:
            logger.warning(f"Failed to analyze OI delta: {e}")
        
        # CVD
        try:
            cvd_buckets = signal_backtest_service._compute_all_bucket_values(
                db, symbol.upper(), "cvd", interval_ms
            )
            if cvd_buckets:
                values = [v for v in cvd_buckets.values() if v is not None]
                if values:
                    distributions['cvd'] = self._calc_distribution('cvd', values)
        except Exception as e:
            logger.warning(f"Failed to analyze CVD: {e}")
        
        # Order Imbalance
        try:
            imb_buckets = signal_backtest_service._compute_all_bucket_values(
                db, symbol.upper(), "order_imbalance", interval_ms
            )
            if imb_buckets:
                values = [v for v in imb_buckets.values() if v is not None]
                if values:
                    distributions['order_imbalance'] = self._calc_distribution(
                        'order_imbalance', values
                    )
        except Exception as e:
            logger.warning(f"Failed to analyze order imbalance: {e}")
        
        return distributions
    
    def _calc_distribution(
        self,
        name: str,
        values: List[float]
    ) -> IndicatorDistribution:
        """Calculate distribution statistics"""
        arr = np.array(values)
        return IndicatorDistribution(
            indicator_name=name,
            current_value=float(arr[-1]) if len(arr) > 0 else 0,
            mean=float(np.mean(arr)),
            std=float(np.std(arr)),
            min_value=float(np.min(arr)),
            max_value=float(np.max(arr)),
            percentile_25=float(np.percentile(arr, 25)),
            percentile_50=float(np.percentile(arr, 50)),
            percentile_75=float(np.percentile(arr, 75)),
            percentile_90=float(np.percentile(arr, 90)),
            percentile_95=float(np.percentile(arr, 95))
        )
    
    def _generate_threshold_suggestions(
        self,
        distributions: Dict[str, IndicatorDistribution],
        klines: List[Dict]
    ) -> Dict[str, ThresholdSuggestion]:
        """Generate threshold suggestions based on distributions"""
        suggestions = {}
        
        for name, dist in distributions.items():
            operator = self._determine_operator(name)
            
            if operator == ">":
                # For "greater than" operators, use upper percentiles
                suggestions[name] = ThresholdSuggestion(
                    metric=name,
                    operator=operator,
                    conservative_threshold=dist.percentile_95,
                    moderate_threshold=dist.percentile_90,
                    aggressive_threshold=dist.percentile_75,
                    current_value=dist.current_value,
                    description=f"Mean: {dist.mean:.4f}, Std: {dist.std:.4f}"
                )
            elif operator == "<":
                # For "less than" operators, use lower percentiles
                p5 = 2 * dist.mean - dist.percentile_95  # Approximate P5
                p10 = 2 * dist.mean - dist.percentile_90  # Approximate P10
                p25 = dist.percentile_25
                
                suggestions[name] = ThresholdSuggestion(
                    metric=name,
                    operator=operator,
                    conservative_threshold=p5,
                    moderate_threshold=p10,
                    aggressive_threshold=p25,
                    current_value=dist.current_value,
                    description=f"Mean: {dist.mean:.4f}, Std: {dist.std:.4f}"
                )
        
        return suggestions
    
    def _determine_operator(self, metric: str) -> str:
        """Determine default operator for a metric"""
        # Metrics where we typically look for high values (long signals)
        high_value_metrics = [
            'oi_delta_percent', 'cvd', 'order_imbalance', 
            'macd_histogram', 'boll_position'
        ]
        
        # Metrics with special handling
        if metric in ['rsi', 'rsi7']:
            return ">"  # Can be both > for overbought, < for oversold
        
        if metric in high_value_metrics:
            return ">"
        
        return ">"  # Default
    
    def _get_metric_values(
        self,
        db: Session,
        symbol: str,
        metric: str,
        period: str,
        days: int
    ) -> List[float]:
        """Get historical values for a specific metric"""
        from services.signal_backtest_service import signal_backtest_service
        
        interval_ms = TIMEFRAME_MS.get(period, 300000)
        
        # Map metric names to backtest service metrics
        metric_map = {
            'oi_delta_percent': 'oi_delta',
            'oi_delta': 'oi_delta',
            'cvd': 'cvd',
            'order_imbalance': 'order_imbalance',
            'depth_ratio': 'depth_ratio',
            'taker_ratio': 'taker_ratio'
        }
        
        backtest_metric = metric_map.get(metric, metric)
        
        try:
            buckets = signal_backtest_service._compute_all_bucket_values(
                db, symbol.upper(), backtest_metric, interval_ms
            )
            if buckets:
                return [v for v in buckets.values() if v is not None]
        except Exception as e:
            logger.warning(f"Failed to get metric values for {metric}: {e}")
        
        return []
    
    def _get_current_regime(
        self,
        db: Session,
        symbol: str,
        period: str
    ) -> Tuple[str, str, float]:
        """Get current market regime classification"""
        try:
            result = classify_market_regime(db, symbol, period)
            if result:
                return (
                    result.get('regime', 'noise'),
                    result.get('direction', 'neutral'),
                    result.get('confidence', 0.5)
                )
        except Exception as e:
            logger.warning(f"Failed to get market regime: {e}")
        
        return ('noise', 'neutral', 0.5)
    
    def _calculate_all_indicators(
        self,
        klines: List[Dict]
    ) -> Dict[str, List[float]]:
        """Calculate all indicators for the full kline series"""
        import pandas as pd
        
        df = pd.DataFrame(klines)
        indicators = calculate_indicators(
            df,
            ['RSI14', 'RSI7', 'MACD', 'BOLL', 'ATR14', 'STOCH', 'EMA20', 'EMA50']
        )
        
        result = {
            'close': [k['close'] for k in klines],
            'volume': [k['volume'] for k in klines]
        }
        
        if 'RSI14' in indicators:
            result['rsi'] = indicators['RSI14']
        if 'RSI7' in indicators:
            result['rsi7'] = indicators['RSI7']
        if 'MACD' in indicators and indicators['MACD']:
            result['macd_histogram'] = indicators['MACD'].get('histogram', [])
        if 'BOLL' in indicators and indicators['BOLL']:
            upper = indicators['BOLL'].get('upper', [])
            lower = indicators['BOLL'].get('lower', [])
            closes = result['close']
            if upper and lower:
                result['boll_position'] = [
                    (closes[i] - lower[i]) / (upper[i] - lower[i])
                    if upper[i] and lower[i] and upper[i] != lower[i] else 0.5
                    for i in range(len(closes))
                ]
        if 'ATR14' in indicators:
            result['atr'] = indicators['ATR14']
        if 'STOCH' in indicators and indicators['STOCH']:
            result['stoch_k'] = indicators['STOCH'].get('k', [])
        if 'EMA20' in indicators:
            result['ema20'] = indicators['EMA20']
        if 'EMA50' in indicators:
            result['ema50'] = indicators['EMA50']
        
        return result
    
    def _extract_indicators_at(
        self,
        indicators: Dict[str, List],
        index: int
    ) -> Dict[str, float]:
        """Extract indicator values at a specific index"""
        result = {}
        for name, values in indicators.items():
            if values and index < len(values) and values[index] is not None:
                result[name] = float(values[index])
        return result
    
    def _get_regime_at_index(
        self,
        indicators: Dict[str, List],
        index: int
    ) -> str:
        """Estimate regime at a specific index based on indicators"""
        rsi = indicators.get('rsi', [])
        macd_hist = indicators.get('macd_histogram', [])
        boll_pos = indicators.get('boll_position', [])
        
        if not rsi or index >= len(rsi):
            return 'unknown'
        
        rsi_val = rsi[index] if rsi[index] else 50
        
        # Simple regime estimation
        if rsi_val > 70:
            return 'overbought'
        elif rsi_val < 30:
            return 'oversold'
        elif boll_pos and index < len(boll_pos):
            bp = boll_pos[index] if boll_pos[index] else 0.5
            if bp > 0.8:
                return 'breakout_up'
            elif bp < 0.2:
                return 'breakout_down'
        
        return 'neutral'
    
    def _ema(self, values: List[float], period: int) -> float:
        """Calculate EMA"""
        if len(values) < period:
            return sum(values) / len(values) if values else 0
        
        multiplier = 2 / (period + 1)
        ema = sum(values[:period]) / period
        
        for value in values[period:]:
            ema = (value * multiplier) + (ema * (1 - multiplier))
        
        return ema
    
    def _empty_analysis_result(
        self,
        symbol: str,
        period: str,
        days: int
    ) -> MarketAnalysisResult:
        """Return empty analysis result"""
        return MarketAnalysisResult(
            symbol=symbol,
            period=period,
            analysis_time=datetime.now(timezone.utc).isoformat(),
            lookback_days=days,
            data_points=0,
            price_analysis=PriceAnalysis(
                trend_direction="unknown",
                trend_strength=0,
                volatility_atr=0,
                volatility_percentile=0,
                support_levels=[],
                resistance_levels=[],
                price_range_high=0,
                price_range_low=0,
                current_price=0
            ),
            volume_analysis=VolumeAnalysis(
                avg_volume=0,
                volume_trend="unknown",
                cvd_cumulative=0,
                cvd_direction="neutral",
                buy_sell_ratio=1.0,
                abnormal_volume_count=0
            ),
            indicator_distributions={},
            threshold_suggestions={},
            regime_type="unknown",
            regime_direction="neutral",
            regime_confidence=0
        )


# Singleton instance
market_data_analyzer = MarketDataAnalyzer()
