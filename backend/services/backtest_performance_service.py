"""
Backtest Performance Service

Provides comprehensive backtesting with performance metrics calculation.
Simulates trading based on signal triggers and calculates key performance indicators.
"""

import json
import logging
import math
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, asdict
from sqlalchemy.orm import Session
from sqlalchemy import text

logger = logging.getLogger(__name__)


@dataclass
class TradeResult:
    """A single trade result."""
    entry_time: int  # timestamp ms
    exit_time: int  # timestamp ms
    entry_price: float
    exit_price: float
    direction: str  # 'long' or 'short'
    pnl_percent: float  # PnL as percentage
    pnl_usd: float  # PnL in USD (based on position size)
    hold_duration_min: float  # Hold duration in minutes
    trigger_value: float  # Signal value at trigger
    trigger_threshold: float  # Signal threshold


@dataclass
class BacktestSummary:
    """Backtest performance summary."""
    # Basic stats
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    
    # PnL stats
    total_pnl_percent: float
    total_pnl_usd: float
    avg_pnl_percent: float
    avg_win_percent: float
    avg_loss_percent: float
    
    # Risk metrics
    profit_factor: float  # gross profit / gross loss
    max_drawdown_percent: float
    sharpe_ratio: float  # Simplified: avg return / std dev
    
    # Trade stats
    avg_hold_duration_min: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    
    # Time analysis
    best_trade_pnl: float
    worst_trade_pnl: float
    
    # Additional info
    start_time: int
    end_time: int
    period_days: int
    trades_per_day: float


@dataclass 
class BacktestConfig:
    """Backtest configuration."""
    position_size_usd: float = 1000.0  # Simulated position size
    take_profit_percent: float = 2.0  # Default TP
    stop_loss_percent: float = 1.0  # Default SL
    max_hold_bars: int = 20  # Max bars to hold before force exit
    use_trailing_stop: bool = False
    trailing_stop_percent: float = 0.5
    entry_delay_bars: int = 0  # Bars to wait after signal before entry
    commission_percent: float = 0.04  # Trading fee (0.04% = 4bps)


# Timeframe to milliseconds mapping
TIMEFRAME_MS = {
    "1m": 60 * 1000,
    "3m": 3 * 60 * 1000,
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "2h": 2 * 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
}


class BacktestPerformanceService:
    """Service for comprehensive signal backtesting with performance analysis."""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def backtest_signal_with_performance(
        self,
        db: Session,
        signal_id: int,
        symbol: str,
        days: int = 7,
        config: Optional[BacktestConfig] = None,
    ) -> Dict[str, Any]:
        """
        Full backtest of a signal with simulated trading and performance metrics.
        
        Args:
            db: Database session
            signal_id: Signal definition ID
            symbol: Trading symbol (e.g., 'BTC')
            days: Number of days to backtest
            config: Backtest configuration
        """
        if config is None:
            config = BacktestConfig()
        
        # Get signal definition
        signal_def = self._get_signal_definition(db, signal_id)
        if not signal_def:
            return {"error": "Signal not found"}
        
        # Get trigger condition
        condition = signal_def.get("trigger_condition", {})
        time_window = condition.get("time_window", "5m")
        direction = self._determine_signal_direction(condition)
        
        # Calculate time range
        # 修时区 bug：用 UTC-aware 计算 Unix 毫秒
        end_time = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_time = end_time - (days * 24 * 60 * 60 * 1000)
        
        # Get triggers using existing backtest service
        from services.signal_backtest_service import signal_backtest_service
        trigger_result = signal_backtest_service.backtest_signal(
            db, signal_id, symbol, start_time, end_time
        )
        
        if "error" in trigger_result:
            return trigger_result
        
        triggers = trigger_result.get("triggers", [])
        if not triggers:
            return {
                "signal_id": signal_id,
                "signal_name": signal_def.get("signal_name"),
                "symbol": symbol,
                "period_days": days,
                "triggers": [],
                "trades": [],
                "summary": None,
                "message": "No triggers found in the specified period"
            }
        
        # Get kline data for price simulation
        klines = self._get_klines(symbol, time_window, start_time, end_time)
        if not klines:
            return {"error": "Failed to fetch price data"}
        
        # Simulate trades
        trades = self._simulate_trades(triggers, klines, direction, config, time_window)
        
        # Calculate performance metrics
        summary = self._calculate_summary(trades, start_time, end_time, days)
        
        # Generate equity curve
        equity_curve = self._generate_equity_curve(trades, config.position_size_usd)
        
        # Time distribution analysis
        time_analysis = self._analyze_trigger_distribution(triggers)
        
        return {
            "signal_id": signal_id,
            "signal_name": signal_def.get("signal_name"),
            "symbol": symbol,
            "period_days": days,
            "config": asdict(config),
            "trigger_count": len(triggers),
            "trade_count": len(trades),
            "trades": [asdict(t) for t in trades[:100]],  # Limit to 100 trades
            "summary": asdict(summary) if summary else None,
            "equity_curve": equity_curve,
            "time_analysis": time_analysis,
        }
    
    def backtest_pool_with_performance(
        self,
        db: Session,
        pool_id: int,
        symbol: str,
        days: int = 7,
        config: Optional[BacktestConfig] = None,
    ) -> Dict[str, Any]:
        """
        Full backtest of a signal pool with simulated trading.
        
        Args:
            db: Database session
            pool_id: Signal pool ID
            symbol: Trading symbol (e.g., 'BTC')
            days: Number of days to backtest
            config: Backtest configuration
        """
        if config is None:
            config = BacktestConfig()
        
        # Get pool definition
        pool_def = self._get_pool_definition(db, pool_id)
        if not pool_def:
            return {"error": "Pool not found"}
        
        pool_name = pool_def.get("pool_name")
        logic = pool_def.get("logic", "OR")
        direction = pool_def.get("direction", "long")
        signal_ids = pool_def.get("signal_ids", [])
        weights = pool_def.get("weights", {})
        weight_threshold = pool_def.get("weight_threshold", 0.5)
        
        if not signal_ids:
            return {"error": "Pool has no signals"}
        
        # Calculate time range
        # 修时区 bug：用 UTC-aware 计算 Unix 毫秒
        end_time = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_time = end_time - (days * 24 * 60 * 60 * 1000)
        
        # Get all signal triggers
        from services.signal_backtest_service import signal_backtest_service
        all_triggers = {}
        time_window = "5m"  # Default
        
        for sig_id in signal_ids:
            result = signal_backtest_service.backtest_signal(
                db, sig_id, symbol, start_time, end_time
            )
            if "triggers" in result:
                all_triggers[sig_id] = result.get("triggers", [])
                # Use time window from first signal
                if result.get("time_window"):
                    time_window = result.get("time_window")
        
        # Combine triggers based on pool logic
        combined_triggers = self._combine_triggers(
            all_triggers, logic, weights, weight_threshold, time_window
        )
        
        if not combined_triggers:
            return {
                "pool_id": pool_id,
                "pool_name": pool_name,
                "symbol": symbol,
                "period_days": days,
                "triggers": [],
                "trades": [],
                "summary": None,
                "message": "No combined triggers found"
            }
        
        # Get kline data
        klines = self._get_klines(symbol, time_window, start_time, end_time)
        if not klines:
            return {"error": "Failed to fetch price data"}
        
        # Simulate trades
        trades = self._simulate_trades(combined_triggers, klines, direction, config, time_window)
        
        # Calculate performance
        summary = self._calculate_summary(trades, start_time, end_time, days)
        
        # Generate equity curve
        equity_curve = self._generate_equity_curve(trades, config.position_size_usd)
        
        # Per-signal statistics
        signal_stats = self._calculate_signal_contribution(all_triggers, combined_triggers, signal_ids, db)
        
        return {
            "pool_id": pool_id,
            "pool_name": pool_name,
            "symbol": symbol,
            "period_days": days,
            "logic": logic,
            "config": asdict(config),
            "trigger_count": len(combined_triggers),
            "trade_count": len(trades),
            "trades": [asdict(t) for t in trades[:100]],
            "summary": asdict(summary) if summary else None,
            "equity_curve": equity_curve,
            "signal_stats": signal_stats,
        }
    
    def backtest_temp_signal_with_performance(
        self,
        db: Session,
        symbol: str,
        trigger_condition: Dict,
        days: int = 7,
        config: Optional[BacktestConfig] = None,
    ) -> Dict[str, Any]:
        """
        Backtest a temporary signal configuration with performance analysis.
        Used for template preview and AI-generated signals.
        """
        if config is None:
            config = BacktestConfig()
        
        time_window = trigger_condition.get("time_window", "5m")
        direction = self._determine_signal_direction(trigger_condition)
        
        # Calculate time range
        # 修时区 bug：用 UTC-aware 计算 Unix 毫秒
        end_time = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_time = end_time - (days * 24 * 60 * 60 * 1000)
        
        # Get triggers using temp backtest
        from services.signal_backtest_service import signal_backtest_service
        trigger_result = signal_backtest_service.backtest_temp_signal(
            db, symbol, trigger_condition, start_time, end_time
        )
        
        if "error" in trigger_result:
            return trigger_result
        
        triggers = trigger_result.get("triggers", [])
        if not triggers:
            return {
                "symbol": symbol,
                "period_days": days,
                "triggers": [],
                "trades": [],
                "summary": None,
                "message": "No triggers found"
            }
        
        # Get kline data
        klines = self._get_klines(symbol, time_window, start_time, end_time)
        if not klines:
            return {"error": "Failed to fetch price data"}
        
        # Simulate trades
        trades = self._simulate_trades(triggers, klines, direction, config, time_window)
        
        # Calculate performance
        summary = self._calculate_summary(trades, start_time, end_time, days)
        
        # Generate equity curve
        equity_curve = self._generate_equity_curve(trades, config.position_size_usd)
        
        return {
            "symbol": symbol,
            "period_days": days,
            "trigger_condition": trigger_condition,
            "config": asdict(config),
            "trigger_count": len(triggers),
            "trade_count": len(trades),
            "trades": [asdict(t) for t in trades[:100]],
            "summary": asdict(summary) if summary else None,
            "equity_curve": equity_curve,
        }
    
    def _get_signal_definition(self, db: Session, signal_id: int) -> Optional[Dict]:
        """Get signal definition from database."""
        result = db.execute(
            text("""
                SELECT id, signal_name, description, trigger_condition, enabled
                FROM signal_definitions WHERE id = :id
            """),
            {"id": signal_id}
        )
        row = result.fetchone()
        if not row:
            return None
        
        trigger_condition = row[3]
        if isinstance(trigger_condition, str):
            try:
                trigger_condition = json.loads(trigger_condition)
            except Exception:
                trigger_condition = {}
        
        return {
            "id": row[0],
            "signal_name": row[1],
            "description": row[2],
            "trigger_condition": trigger_condition,
            "enabled": row[4]
        }
    
    def _get_pool_definition(self, db: Session, pool_id: int) -> Optional[Dict]:
        """Get signal pool definition from database."""
        result = db.execute(
            text("""
                SELECT id, pool_name, enabled, signal_ids, logic,
                       weights, weight_threshold
                FROM signal_pools WHERE id = :id
            """),
            {"id": pool_id}
        )
        row = result.fetchone()
        if not row:
            return None
        
        signal_ids = row[3]
        if isinstance(signal_ids, str):
            try:
                signal_ids = json.loads(signal_ids)
            except Exception:
                signal_ids = []
        
        weights = row[5]
        if isinstance(weights, str):
            try:
                weights = json.loads(weights)
            except Exception:
                weights = {}
        
        return {
            "id": row[0],
            "pool_name": row[1],
            "direction": "long",  # Default direction, can be inferred from signals
            "enabled": row[2],
            "signal_ids": signal_ids or [],
            "logic": row[4] or "OR",
            "weights": weights or {},
            "weight_threshold": row[6] or 0.5
        }
    
    def _determine_signal_direction(self, condition: Dict) -> str:
        """Determine trade direction from signal condition."""
        # Check metric-based direction hints
        metric = condition.get("metric", "")
        operator = condition.get("operator", "")
        threshold = condition.get("threshold", 0)
        direction = condition.get("direction", "")
        
        if direction:
            return direction
        
        # Infer from condition
        if metric in ["taker_buy_ratio", "cvd_change", "order_imbalance"]:
            if operator in [">", ">=", "greater_than", "gte"]:
                return "long" if threshold > 0 else "short"
            else:
                return "short" if threshold > 0 else "long"
        
        return "long"  # Default
    
    def _get_klines(
        self, symbol: str, time_window: str, start_time: int, end_time: int
    ) -> List[Dict]:
        """Fetch kline data from Hyperliquid API."""
        try:
            # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止直连 HL
            # candleSnapshot，改为读数据中心 DB（query_klines 受 DC_ONLY 保护）。
            from backend.services.market_data import _dc_only_enabled
            if _dc_only_enabled():
                from backend.services.kline_data_service import kline_service
                rows = kline_service.query_klines(
                    symbol, time_window,
                    exchange="hyperliquid",
                    start_ts=start_time, end_ts=end_time,
                ) or []
                klines = []
                for k in rows:
                    klines.append({
                        "timestamp": int(k.get("timestamp", 0)),
                        "open": float(k.get("open", 0)),
                        "high": float(k.get("high", 0)),
                        "low": float(k.get("low", 0)),
                        "close": float(k.get("close", 0)),
                        "volume": float(k.get("volume", 0)),
                    })
                return sorted(klines, key=lambda x: x["timestamp"])

            import requests
            
            interval_map = {
                "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m",
                "30m": "30m", "1h": "1h", "2h": "2h", "4h": "4h"
            }
            interval = interval_map.get(time_window, "5m")
            
            url = "https://api.hyperliquid.xyz/info"
            payload = {
                "type": "candleSnapshot",
                "req": {
                    "coin": symbol.upper(),
                    "interval": interval,
                    "startTime": start_time,
                    "endTime": end_time,
                }
            }
            
            resp = requests.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            
            klines = []
            for candle in data:
                klines.append({
                    "timestamp": candle["t"],
                    "open": float(candle["o"]),
                    "high": float(candle["h"]),
                    "low": float(candle["l"]),
                    "close": float(candle["c"]),
                    "volume": float(candle.get("v", 0)),
                })
            
            return sorted(klines, key=lambda x: x["timestamp"])
        except Exception as e:
            logger.error(f"Failed to fetch klines: {e}")
            return []
    
    def _simulate_trades(
        self,
        triggers: List[Dict],
        klines: List[Dict],
        direction: str,
        config: BacktestConfig,
        time_window: str,
    ) -> List[TradeResult]:
        """Simulate trades based on triggers and kline data."""
        if not triggers or not klines:
            return []
        
        interval_ms = TIMEFRAME_MS.get(time_window, 300000)
        
        # Build kline lookup by timestamp
        kline_map = {k["timestamp"]: k for k in klines}
        sorted_kline_times = sorted(kline_map.keys())
        
        trades = []
        in_position = False
        position_entry = None
        last_trigger_time = 0
        cooldown_ms = interval_ms * 1  # 加密货币市场节奏快，仅需1bar冷却
        
        for trigger in sorted(triggers, key=lambda x: x["timestamp"]):
            trigger_time = trigger["timestamp"]
            
            # Skip if in position or within cooldown
            if in_position:
                continue
            if trigger_time - last_trigger_time < cooldown_ms:
                continue
            
            # Find entry kline (next bar after trigger)
            entry_idx = self._find_next_kline_index(trigger_time, sorted_kline_times)
            if entry_idx is None or entry_idx >= len(sorted_kline_times) - config.max_hold_bars:
                continue
            
            entry_time = sorted_kline_times[entry_idx]
            entry_kline = kline_map[entry_time]
            entry_price = entry_kline["open"]  # Enter at open of next bar
            
            # Simulate exit
            exit_result = self._simulate_exit(
                kline_map, sorted_kline_times, entry_idx, entry_price,
                direction, config
            )
            
            if exit_result:
                exit_time, exit_price, exit_reason = exit_result
                
                # Calculate PnL
                if direction == "long":
                    pnl_percent = ((exit_price - entry_price) / entry_price) * 100
                else:
                    pnl_percent = ((entry_price - exit_price) / entry_price) * 100
                
                # Deduct commission
                pnl_percent -= config.commission_percent * 2  # Entry + exit
                
                pnl_usd = (pnl_percent / 100) * config.position_size_usd
                hold_duration = (exit_time - entry_time) / 60000  # Convert to minutes
                
                trade = TradeResult(
                    entry_time=entry_time,
                    exit_time=exit_time,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    direction=direction,
                    pnl_percent=round(pnl_percent, 4),
                    pnl_usd=round(pnl_usd, 2),
                    hold_duration_min=round(hold_duration, 1),
                    trigger_value=trigger.get("value", trigger.get("ratio", 0)),
                    trigger_threshold=trigger.get("threshold", trigger.get("ratio_threshold", 0)),
                )
                trades.append(trade)
                last_trigger_time = exit_time
        
        return trades
    
    def _find_next_kline_index(self, timestamp: int, sorted_times: List[int]) -> Optional[int]:
        """Find index of the kline at or after the given timestamp."""
        import bisect
        idx = bisect.bisect_left(sorted_times, timestamp)
        if idx < len(sorted_times):
            # If exact match, use next bar
            if sorted_times[idx] == timestamp:
                idx += 1
            return idx if idx < len(sorted_times) else None
        return None
    
    def _simulate_exit(
        self,
        kline_map: Dict,
        sorted_times: List[int],
        entry_idx: int,
        entry_price: float,
        direction: str,
        config: BacktestConfig,
    ) -> Optional[Tuple[int, float, str]]:
        """Simulate trade exit with TP/SL logic."""
        tp_price = entry_price * (1 + config.take_profit_percent / 100) if direction == "long" else \
                   entry_price * (1 - config.take_profit_percent / 100)
        sl_price = entry_price * (1 - config.stop_loss_percent / 100) if direction == "long" else \
                   entry_price * (1 + config.stop_loss_percent / 100)
        
        max_exit_idx = min(entry_idx + config.max_hold_bars, len(sorted_times))
        trailing_high = entry_price if direction == "long" else entry_price
        
        for i in range(entry_idx + 1, max_exit_idx):
            kline = kline_map[sorted_times[i]]
            high, low, close = kline["high"], kline["low"], kline["close"]
            
            # Update trailing stop
            if config.use_trailing_stop:
                if direction == "long" and high > trailing_high:
                    trailing_high = high
                    sl_price = trailing_high * (1 - config.trailing_stop_percent / 100)
                elif direction == "short" and low < trailing_high:
                    trailing_high = low
                    sl_price = trailing_high * (1 + config.trailing_stop_percent / 100)
            
            # Check SL
            if direction == "long" and low <= sl_price:
                return (sorted_times[i], sl_price, "stop_loss")
            elif direction == "short" and high >= sl_price:
                return (sorted_times[i], sl_price, "stop_loss")
            
            # Check TP
            if direction == "long" and high >= tp_price:
                return (sorted_times[i], tp_price, "take_profit")
            elif direction == "short" and low <= tp_price:
                return (sorted_times[i], tp_price, "take_profit")
        
        # Force exit at max hold
        if max_exit_idx > entry_idx and max_exit_idx <= len(sorted_times):
            last_kline = kline_map[sorted_times[max_exit_idx - 1]]
            return (sorted_times[max_exit_idx - 1], last_kline["close"], "max_hold")
        
        return None
    
    def _combine_triggers(
        self,
        all_triggers: Dict[int, List[Dict]],
        logic: str,
        weights: Dict,
        weight_threshold: float,
        time_window: str,
    ) -> List[Dict]:
        """Combine triggers from multiple signals based on pool logic."""
        if not all_triggers:
            return []
        
        interval_ms = TIMEFRAME_MS.get(time_window, 300000)
        bucket_ms = interval_ms  # Use time window as bucket
        
        # Build time buckets for each signal
        signal_buckets = {}
        all_times = set()
        
        for sig_id, triggers in all_triggers.items():
            signal_buckets[sig_id] = set()
            for t in triggers:
                bucket = (t["timestamp"] // bucket_ms) * bucket_ms
                signal_buckets[sig_id].add(bucket)
                all_times.add(bucket)
        
        combined = []
        signal_ids = list(all_triggers.keys())
        
        for time_bucket in sorted(all_times):
            signals_triggered = []
            for sig_id in signal_ids:
                if time_bucket in signal_buckets.get(sig_id, set()):
                    signals_triggered.append(sig_id)
            
            should_trigger = False
            
            if logic == "AND":
                # All signals must trigger
                should_trigger = len(signals_triggered) == len(signal_ids)
            elif logic == "WEIGHTED":
                # Weighted sum must exceed threshold
                total_weight = sum(float(weights.get(str(sid), weights.get(sid, 1.0 / len(signal_ids)))) 
                                   for sid in signal_ids)
                triggered_weight = sum(float(weights.get(str(sid), weights.get(sid, 1.0 / len(signal_ids)))) 
                                       for sid in signals_triggered)
                weight_ratio = triggered_weight / total_weight if total_weight > 0 else 0
                should_trigger = weight_ratio >= weight_threshold
            else:  # OR
                should_trigger = len(signals_triggered) > 0
            
            if should_trigger:
                combined.append({
                    "timestamp": time_bucket,
                    "triggered_signals": signals_triggered,
                    "logic": logic,
                })
        
        return combined
    
    def _calculate_summary(
        self,
        trades: List[TradeResult],
        start_time: int,
        end_time: int,
        days: int,
    ) -> Optional[BacktestSummary]:
        """Calculate comprehensive performance summary."""
        if not trades:
            return None
        
        total_trades = len(trades)
        pnl_list = [t.pnl_percent for t in trades]
        
        winning_trades = sum(1 for p in pnl_list if p > 0)
        losing_trades = sum(1 for p in pnl_list if p < 0)
        
        wins = [p for p in pnl_list if p > 0]
        losses = [p for p in pnl_list if p < 0]
        
        total_pnl = sum(pnl_list)
        total_pnl_usd = sum(t.pnl_usd for t in trades)
        avg_pnl = total_pnl / total_trades if total_trades > 0 else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        
        # Profit factor
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf') if gross_profit > 0 else 0
        
        # Max drawdown
        max_drawdown = self._calculate_max_drawdown(pnl_list)
        
        # Sharpe ratio (simplified)
        if len(pnl_list) > 1:
            import statistics
            std_dev = statistics.stdev(pnl_list)
            sharpe = avg_pnl / std_dev if std_dev > 0 else 0
        else:
            sharpe = 0
        
        # Consecutive wins/losses
        max_wins, max_losses = self._calculate_consecutive_streaks(pnl_list)
        
        # Hold duration
        avg_hold = sum(t.hold_duration_min for t in trades) / total_trades if total_trades > 0 else 0
        
        return BacktestSummary(
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=round(winning_trades / total_trades * 100, 1) if total_trades > 0 else 0,
            total_pnl_percent=round(total_pnl, 2),
            total_pnl_usd=round(total_pnl_usd, 2),
            avg_pnl_percent=round(avg_pnl, 2),
            avg_win_percent=round(avg_win, 2),
            avg_loss_percent=round(avg_loss, 2),
            profit_factor=round(profit_factor, 2) if profit_factor != float('inf') else 999.99,
            max_drawdown_percent=round(max_drawdown, 2),
            sharpe_ratio=round(sharpe, 2),
            avg_hold_duration_min=round(avg_hold, 1),
            max_consecutive_wins=max_wins,
            max_consecutive_losses=max_losses,
            best_trade_pnl=round(max(pnl_list), 2) if pnl_list else 0,
            worst_trade_pnl=round(min(pnl_list), 2) if pnl_list else 0,
            start_time=start_time,
            end_time=end_time,
            period_days=days,
            trades_per_day=round(total_trades / days, 1) if days > 0 else 0,
        )
    
    def _calculate_max_drawdown(self, pnl_list: List[float]) -> float:
        """Calculate maximum drawdown from PnL list."""
        if not pnl_list:
            return 0
        
        cumulative = []
        running_sum = 0
        for pnl in pnl_list:
            running_sum += pnl
            cumulative.append(running_sum)
        
        peak = cumulative[0]
        max_dd = 0
        
        for value in cumulative:
            if value > peak:
                peak = value
            drawdown = peak - value
            if drawdown > max_dd:
                max_dd = drawdown
        
        return max_dd
    
    def _calculate_consecutive_streaks(self, pnl_list: List[float]) -> Tuple[int, int]:
        """Calculate max consecutive wins and losses."""
        max_wins = 0
        max_losses = 0
        current_wins = 0
        current_losses = 0
        
        for pnl in pnl_list:
            if pnl > 0:
                current_wins += 1
                current_losses = 0
                max_wins = max(max_wins, current_wins)
            elif pnl < 0:
                current_losses += 1
                current_wins = 0
                max_losses = max(max_losses, current_losses)
            else:
                current_wins = 0
                current_losses = 0
        
        return max_wins, max_losses
    
    def _generate_equity_curve(
        self, trades: List[TradeResult], initial_capital: float
    ) -> List[Dict]:
        """Generate equity curve data points."""
        equity_curve = [{"time": 0, "equity": initial_capital, "pnl": 0}]
        current_equity = initial_capital
        
        for trade in sorted(trades, key=lambda x: x.exit_time):
            current_equity += trade.pnl_usd
            equity_curve.append({
                "time": trade.exit_time,
                "equity": round(current_equity, 2),
                "pnl": trade.pnl_usd,
            })
        
        return equity_curve
    
    def _analyze_trigger_distribution(self, triggers: List[Dict]) -> Dict:
        """Analyze trigger time distribution."""
        if not triggers:
            return {}
        
        # Hour distribution (0-23)
        hour_dist = {i: 0 for i in range(24)}
        # Day of week distribution (0-6, Monday=0)
        day_dist = {i: 0 for i in range(7)}
        
        for t in triggers:
            ts = t["timestamp"] / 1000
            dt = datetime.utcfromtimestamp(ts)
            hour_dist[dt.hour] += 1
            day_dist[dt.weekday()] += 1
        
        return {
            "hourly_distribution": hour_dist,
            "daily_distribution": day_dist,
            "total_triggers": len(triggers),
        }
    
    def _calculate_signal_contribution(
        self,
        all_triggers: Dict[int, List[Dict]],
        combined_triggers: List[Dict],
        signal_ids: List[int],
        db: Session,
    ) -> List[Dict]:
        """Calculate each signal's contribution to pool triggers."""
        stats = []
        
        for sig_id in signal_ids:
            sig_triggers = all_triggers.get(sig_id, [])
            
            # Count how many combined triggers include this signal
            contribution = 0
            for ct in combined_triggers:
                if sig_id in ct.get("triggered_signals", []):
                    contribution += 1
            
            # Get signal name
            result = db.execute(
                text("SELECT signal_name FROM signal_definitions WHERE id = :id"),
                {"id": sig_id}
            )
            row = result.fetchone()
            signal_name = row[0] if row else f"Signal {sig_id}"
            
            stats.append({
                "signal_id": sig_id,
                "signal_name": signal_name,
                "total_triggers": len(sig_triggers),
                "pool_contribution": contribution,
                "contribution_percent": round(contribution / len(combined_triggers) * 100, 1) 
                    if combined_triggers else 0,
            })
        
        return stats


# Singleton instance
backtest_performance_service = BacktestPerformanceService()
